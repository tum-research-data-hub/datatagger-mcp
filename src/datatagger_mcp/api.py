import mimetypes
import os
import json
import time
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from contextvars import ContextVar
from contextlib import asynccontextmanager

import httpx

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings

from . import USER_AGENT
from .jwt_token import encode_token, decode_token

# --- FastMCP Instance ---
mcp = FastMCP(
    "datatagger",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "datatagger-mcp.duckdns.org",
            "researchmcp.duckdns.org",
            "localhost",
            "127.0.0.1",
        ]
    ),
)

# --- Session & Global Config ---
session_key_var: ContextVar[Optional[str]] = ContextVar("session_key", default=None)
session_base_url_var: ContextVar[Optional[str]] = ContextVar("session_base_url", default=None)
SESSION_AUTH: Dict[str, Dict[str, str]] = {}


def get_session_id(ctx: Optional[Context]) -> Optional[str]:
    """Extract session ID from the MCP context if available."""
    if not ctx:
        return None
    try:
        return str(id(ctx.request_context.session))
    except Exception:
        return None


# --- Background Tasks ---
async def session_cleanup_loop():
    """Legacy background task — kept for interface compatibility, no-op now."""
    while True:
        await asyncio.sleep(3600)

# --- Background Tasks ---

def get_auth_config(ctx: Optional[Context] = None) -> Tuple[str, str]:
    """
    Unified authentication resolver.
    """
    # 1. Hosted Mode (via Middleware/ContextVar)
    token = session_key_var.get()
    base_url = session_base_url_var.get()

    if token and base_url:
        return token, base_url.rstrip("/")

    # 2. Manual Session Store (via legacy configure_auth tool call in stdio)
    session_id = get_session_id(ctx)
    if session_id and session_id in SESSION_AUTH:
        auth = SESSION_AUTH[session_id]
        return auth["token"], auth["base_url"].rstrip("/")

    # 3. Local Mode (Environment Variables)
    env_token = os.environ.get("FDM_TOKEN", "")
    env_base_url = os.environ.get("FDM_BASE_URL", "https://datatagger.ub.tum.de")

    if env_token:
        return env_token, env_base_url.rstrip("/")

    raise ValueError(
        "No authentication configured. \n"
        "- In local mode: set FDM_TOKEN environment variable.\n"
        "- In hosted mode: Visit /register to generate a URL with a token."
    )


async def make_fdm_request(
    endpoint: str,
    method: str = "GET",
    params: Optional[dict] = None,
    json_payload: Optional[dict] = None,
    ctx: Optional[Context] = None,
) -> dict[str, Any] | str | None:
    """Make a generic HTTP request to the FDM API."""
    try:
        token, base_url = get_auth_config(ctx)
    except ValueError as e:
        return str(e)

    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint

    url = f"{base_url}{endpoint}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    if params:
        params = {k: v for k, v in params.items() if v is not None}

    async with httpx.AsyncClient() as client:
        try:
            req_kwargs = {"headers": headers, "timeout": 30.0}
            if params:
                req_kwargs["params"] = params
            if json_payload is not None:
                req_kwargs["json"] = json_payload

            response = await client.request(method, url, **req_kwargs)
            response.raise_for_status()

            if response.status_code == 204:
                return "Operation successful (204 No Content)"

            content_type = response.headers.get("content-type", "")
            if "json" in content_type.lower():
                return response.json()
            else:
                return response.text
        except httpx.HTTPStatusError as e:
            return f"API Error ({e.response.status_code}): {e.response.text}"
        except Exception as e:
            return f"Error making {method} request to API: {e}"


def format_json_response(data: Any) -> str:
    """Serialize API response data to a formatted JSON string."""
    if isinstance(data, str):
        return data
    import json

    return json.dumps(data, indent=2)


async def download_fdm_file(
    endpoint: str, dest_path: str, overwrite: bool = False, ctx: Optional[Context] = None
) -> str:
    """Stream a file from the FDM API to a local destination path."""
    if os.path.exists(dest_path) and not overwrite:
        return f"Error: File already exists at {dest_path} and overwrite is False."

    try:
        token, base_url = get_auth_config(ctx)
    except ValueError as e:
        return str(e)

    url = f"{base_url}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", url, headers=headers, timeout=300.0
            ) as response:
                response.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
        return f"File successfully downloaded to: {dest_path}"
    except Exception as e:
        return f"Error downloading file: {e}"


async def upload_fdm_file_tus(
    endpoint: str, file_path: str, ctx: Optional[Context] = None
) -> str:
    """Upload a local file to the FDM API using TUS resumable upload protocol.

    The TUS protocol (POST to /tus/ + PATCH to /tus/{id}/) is the upload method
    used by the DataTagger web UI. Unlike the legacy multipart POST to /file/,
    TUS correctly finalises the dataset so it appears in the folder (is_published=True).
    """
    import base64
    import mimetypes

    if not os.path.exists(file_path):
        return f"Error: File not found exactly at {file_path}."

    try:
        token, base_url = get_auth_config(ctx)
    except ValueError as e:
        return str(e)

    api_base = base_url.rstrip("/") + "/api/v1"
    upload_endpoint = endpoint.lstrip("/")
    # Strip /api/v1/ prefix if present (some callers include it)
    if upload_endpoint.startswith("api/v1/"):
        upload_endpoint = upload_endpoint[7:]
    if not upload_endpoint.startswith("uploads-dataset/"):
        return f"Error: TUS upload requires an uploads-dataset endpoint, got: {endpoint}"
    # Extract dataset_id from endpoint like "uploads-dataset/{dataset_id}/file/"
    ds_id = upload_endpoint.split("/")[1]

    filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    fname_b64 = base64.b64encode(filename.encode()).decode()
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"
    ftype_b64 = base64.b64encode(mime_type.encode()).decode()

    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
    }

    try:
        async with httpx.AsyncClient() as client:
            # Step 1: POST to /tus/ to initialise the upload
            tus_init_headers = {
                **headers,
                "Tus-Resumable": "1.0.0",
                "Upload-Length": str(file_size),
                "Upload-Metadata": f"filename {fname_b64},filetype {ftype_b64}",
            }
            init_resp = await client.post(
                f"{api_base}/uploads-dataset/{ds_id}/tus/",
                headers=tus_init_headers,
                timeout=30.0,
            )
            init_resp.raise_for_status()

            location = init_resp.headers.get("Location", "")
            if not location:
                return "Error: TUS init returned no Location header."

            tus_url = location
            if tus_url.startswith("/"):
                tus_url = f"{base_url.rstrip('/')}{tus_url}"

            # Step 2: PATCH to the TUS URL with the raw file data
            with open(file_path, "rb") as f:
                file_data = f.read()

            patch_headers = {
                **headers,
                "Tus-Resumable": "1.0.0",
                "Upload-Offset": "0",
                "Content-Type": "application/offset+octet-stream",
            }
            patch_resp = await client.patch(
                tus_url,
                headers=patch_headers,
                content=file_data,
                timeout=600.0,
            )
            patch_resp.raise_for_status()

            return f"File uploaded successfully via TUS: {filename} (dataset {ds_id})"
    except Exception as e:
        return f"Error uploading file via TUS: {e}"


# --- SECTION: SEARCH ---


@mcp.tool()
async def search_datatagger(term: str, limit: int = 100, ctx: Optional[Context] = None) -> str:
    """Global search across projects, folders, and uploads."""
    payload = {
        "search_text": term,
        "limit": limit,
        "result_types": [
            "project",
            "folder",
            "dataset",
            "dataset_version",
            "file",
            "template",
            "template_version",
        ],
    }
    return format_json_response(
        await make_fdm_request(
            "/api/v1/search/global/", method="POST", json_payload=payload, ctx=ctx
        )
    )


# --- SECTION: PROJECTS ---


@mcp.tool()
async def list_projects(
    limit: int = 100, offset: int = 0, search: str = "", ctx: Optional[Context] = None
) -> str:
    """List Datatagger projects."""
    params = {"limit": limit, "offset": offset}
    if search:
        params["search"] = search
    return format_json_response(
        await make_fdm_request("/api/v1/project/", params=params, ctx=ctx)
    )


@mcp.tool()
async def get_project(project_id: str, ctx: Optional[Context] = None) -> str:
    """Get details of a Datatagger project."""
    return format_json_response(
        await make_fdm_request(f"/api/v1/project/{project_id}/", ctx=ctx)
    )


@mcp.tool()
async def create_project(name: str, ctx: Optional[Context] = None) -> str:
    """Create a new project."""
    payload = {"name": name}
    return format_json_response(
        await make_fdm_request(
            "/api/v1/project/", method="POST", json_payload=payload, ctx=ctx
        )
    )


@mcp.tool()
async def update_project(
    project_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> str:
    """Update a project."""
    payload = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if not payload:
        return "No fields provided to update."
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/project/{project_id}/",
            method="PATCH",
            json_payload=payload,
            ctx=ctx,
        )
    )


@mcp.tool()
async def delete_project(
    project_id: str, confirm_danger: bool = False, ctx: Optional[Context] = None
) -> str:
    """Delete a project. REQUIRED: confirm_danger=True."""
    if not confirm_danger:
        return "ERROR: Deletion rejected. You must set confirm_danger=True."
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/project/{project_id}/", method="DELETE", ctx=ctx
        )
    )


# --- SECTION: FOLDERS ---


@mcp.tool()
async def list_folders(
    project: str = "",
    limit: int = 100,
    offset: int = 0,
    search: str = "",
    ctx: Optional[Context] = None,
) -> str:
    """List folders."""
    params = {"limit": limit, "offset": offset}
    if project:
        params["project"] = project
    if search:
        params["search"] = search
    return format_json_response(
        await make_fdm_request("/api/v1/folder/", params=params, ctx=ctx)
    )


@mcp.tool()
async def get_folder(folder_id: str, ctx: Optional[Context] = None) -> str:
    """Get details of a folder."""
    return format_json_response(
        await make_fdm_request(f"/api/v1/folder/{folder_id}/", ctx=ctx)
    )


@mcp.tool()
async def create_folder(
    project_id: str, name: str, ctx: Optional[Context] = None
) -> str:
    """Create a new folder inside a project."""
    payload = {"project": project_id, "name": name}
    return format_json_response(
        await make_fdm_request(
            "/api/v1/folder/", method="POST", json_payload=payload, ctx=ctx
        )
    )


@mcp.tool()
async def update_folder(
    folder_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> str:
    """Update a folder."""
    payload = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if not payload:
        return "No fields provided to update."
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/folder/{folder_id}/",
            method="PATCH",
            json_payload=payload,
            ctx=ctx,
        )
    )


@mcp.tool()
async def delete_folder(
    folder_id: str, confirm_danger: bool = False, ctx: Optional[Context] = None
) -> str:
    """Delete a folder. REQUIRED: confirm_danger=True."""
    if not confirm_danger:
        return "ERROR: Deletion rejected. You must set confirm_danger=True."
    return format_json_response(
        await make_fdm_request(f"/api/v1/folder/{folder_id}/", method="DELETE", ctx=ctx)
    )


# --- SECTION: DATASETS & VERSIONS ---


@mcp.tool()
async def list_datasets(
    folder_id: str = "",
    limit: int = 100,
    offset: int = 0,
    search: str = "",
    ctx: Optional[Context] = None,
) -> str:
    """List dataset entries. Filter by folder_id optional."""
    params = {"limit": limit, "offset": offset}
    if folder_id:
        params["folder"] = folder_id
    if search:
        params["search"] = search
    return format_json_response(
        await make_fdm_request("/api/v1/uploads-dataset/", params=params, ctx=ctx)
    )


@mcp.tool()
async def create_dataset(
    name: str, folder_id: Optional[str] = None, ctx: Optional[Context] = None
) -> str:
    """Create a new dataset entry.
    
    - With folder_id: creates a dataset inside that folder (appears under projects/.../folders/.../files/)
    - Without folder_id: creates a free-standing dataset (appears under /drafts/...)
    """
    payload = {"name": name}
    if folder_id:
        payload["folder"] = folder_id
    return format_json_response(
        await make_fdm_request(
            "/api/v1/uploads-dataset/", method="POST", json_payload=payload, ctx=ctx
        )
    )


@mcp.tool()
async def delete_dataset(
    dataset_id: str, confirm_danger: bool = False, ctx: Optional[Context] = None
) -> str:
    """Delete a dataset. REQUIRED: confirm_danger=True."""
    if not confirm_danger:
        return "ERROR: Deletion rejected. set confirm_danger=True."
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/uploads-dataset/{dataset_id}/", method="DELETE", ctx=ctx
        )
    )


@mcp.tool()
async def publish_dataset(dataset_id: str, ctx: Optional[Context] = None) -> str:
    """Finalize/Commit a dataset (often referred to as 'publishing' internally)."""
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/uploads-dataset/{dataset_id}/publish/",
            method="POST",
            json_payload={},
            ctx=ctx,
        )
    )


@mcp.tool()
async def restore_dataset_version(
    dataset_id: str, uploads_version_id: str, ctx: Optional[Context] = None
) -> str:
    """Restore a dataset to a previous historical version."""
    payload = {"uploads_version": uploads_version_id}
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/uploads-dataset/{dataset_id}/restore/",
            method="POST",
            json_payload=payload,
            ctx=ctx,
        )
    )


@mcp.tool()
async def compare_dataset_versions(
    version_id: str, compare_to_id: str, ctx: Optional[Context] = None
) -> str:
    """Get the diff/comparison between two dataset versions."""
    payload = {"compare": compare_to_id}
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/uploads-version/{version_id}/diff/",
            method="POST",
            json_payload=payload,
            ctx=ctx,
        )
    )


# --- SECTION: UPLOAD & DOWNLOAD FILE DATA ---


@mcp.tool()
async def download_version_file(
    version_id: str, dest_path: str, overwrite: bool = False, ctx: Optional[Context] = None
) -> str:
    """Download a version file dynamically to your local computer's absolute path."""
    return await download_fdm_file(
        f"/api/v1/uploads-version/{version_id}/download/", dest_path, overwrite, ctx=ctx
    )


@mcp.tool()
async def upload_dataset_file(
    dataset_id: str, source_path: str, ctx: Optional[Context] = None
) -> str:
    """Upload a raw file from your local computer into a dataset.

    Uses the TUS resumable upload protocol (same as the DataTagger web UI)
    so the dataset is properly finalised and visible in the folder.
    """
    return await upload_fdm_file_tus(
        f"/api/v1/uploads-dataset/{dataset_id}/file/", source_path, ctx=ctx
    )


# --- SECTION: PERMISSIONS ---


@mcp.tool()
async def set_folder_permissions(
    folder_id: str,
    folder_users: List[Dict[str, Any]],
    ctx: Optional[Context] = None,
) -> str:
    """Set the user permissions array for a folder."""
    payload = {"folder_users": folder_users}
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/folder/{folder_id}/permissions/",
            method="PUT",
            json_payload=payload,
            ctx=ctx,
        )
    )


@mcp.tool()
async def get_folder_permissions(
    folder_id: str, ctx: Optional[Context] = None
) -> str:
    """List all the active user permissions for a folder."""
    return format_json_response(
        await make_fdm_request(
            "/api/v1/folder-permission/", params={"folder": folder_id}, ctx=ctx
        )
    )


# --- SECTION: METADATA ---


@mcp.tool()
async def list_metadata(
    search: str = "", limit: int = 100, ctx: Optional[Context] = None
) -> str:
    """List available metadata template mappings across Data Tagger."""
    params = {"limit": limit, "search": search}
    return format_json_response(
        await make_fdm_request("/api/v1/metadata/", params=params, ctx=ctx)
    )


@mcp.tool()
async def add_metadata_to_dataset(
    dataset_id: str,
    metadata_items: List[Dict[str, Any]],
    ctx: Optional[Context] = None,
) -> str:
    """Add a batch of metadata item tags (via json) to an existing dataset."""
    payload = {"metadata": metadata_items}
    return format_json_response(
        await make_fdm_request(
            f"/api/v1/uploads-dataset/{dataset_id}/version/",
            method="POST",
            json_payload=payload,
            ctx=ctx,
        )
    )
