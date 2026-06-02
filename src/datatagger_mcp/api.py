import mimetypes
import os
import json
import time
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from contextvars import ContextVar
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.responses import HTMLResponse
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Scope, Receive, Send

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

async def register_page_handler(request: Request):
    """Registration page — issues self-contained JWT tokens (no server storage)."""
    if request.method == "POST":
        form = await request.form()
        api_key = str(form.get("api_key", "")).strip()
        base_url = str(form.get("base_url", "https://datatagger.ub.tum.de")).strip()

        if not api_key:
            return HTMLResponse("ERROR: API Key is required", status_code=400)

        try:
            token = encode_token(base_url, api_key)
        except RuntimeError as e:
            return HTMLResponse(f"Server misconfiguration: {e}", status_code=500)

        # Detect protocol behind reverse proxy
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        host = request.headers.get("host", "localhost:8000")
        url_prefix = os.environ.get("URL_PREFIX", "")
        if not url_prefix or not url_prefix.strip():
            url_prefix = "/dt"
        elif not url_prefix.startswith("/"):
            url_prefix = "/" + url_prefix
        url_prefix = url_prefix.rstrip("/")
        personal_url = f"{forwarded_proto}://{host}{url_prefix}/mcp/?token={token}"
        register_url = f"{url_prefix}/register"

        return HTMLResponse(
            f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Registration Successful</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f1a; color: #e8edf5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
.card {{ background: #131827; border: 1px solid #1f2b40; border-radius: 14px; padding: 2rem; max-width: 560px; width: 90%; }}
h2 {{ font-size: 1.15rem; font-weight: 700; margin-bottom: 0.75rem; }}
p {{ font-size: 0.85rem; color: #8898b4; line-height: 1.5; }}
.url-box {{ background: #1a2236; border: 1px solid #1f2b40; border-radius: 8px; padding: 0.85rem; font-family: monospace; font-size: 0.78rem; word-break: break-all; margin: 0.85rem 0; color: #e8edf5; }}
.note {{ font-size: 0.78rem; color: #5c6f8c; margin-top: 1rem; }}
a {{ color: #3b82f6; text-decoration: none; font-size: 0.82rem; }}
a:hover {{ text-decoration: underline; }}
</style></head><body>
<div class="card">
<h2>Registration Successful</h2>
<p>Use the following URL in your MCP client (any MCP agent):</p>
<div class="url-box">{personal_url}</div>
<p class="note">Token expires in 30 days. Revisit this page to generate a new one. Survives server restarts.</p>
<a href="{register_url}">&larr; Register another key</a>
</div>
</body></html>"""
        )

    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DataTagger MCP Registration</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0b0f1a; color: #e8edf5; min-height: 100vh; display: flex; align-items: center; justify-content: center; }}
.card {{ background: #131827; border: 1px solid #1f2b40; border-radius: 14px; padding: 2rem; max-width: 480px; width: 90%; }}
h2 {{ font-size: 1.15rem; font-weight: 700; margin-bottom: 0.3rem; }}
p {{ font-size: 0.85rem; color: #8898b4; margin-bottom: 1.25rem; }}
label {{ display: block; font-size: 0.82rem; font-weight: 600; margin-bottom: 0.3rem; color: #8898b4; }}
input {{ width: 100%; padding: 0.6rem 0.75rem; background: #1a2236; border: 1px solid #1f2b40; border-radius: 8px; color: #e8edf5; font-size: 0.9rem; margin-bottom: 0.85rem; outline: none; }}
input:focus {{ border-color: #3b82f6; }}
button {{ width: 100%; padding: 0.6rem; background: #3b82f6; color: #fff; border: none; border-radius: 8px; font-size: 0.9rem; font-weight: 600; cursor: pointer; }}
button:hover {{ opacity: 0.9; }}
a {{ color: #3b82f6; text-decoration: none; font-size: 0.82rem; }}
a:hover {{ text-decoration: underline; }}
</style></head><body>
<div class="card">
<h2>DataTagger MCP Registration</h2>
<p>Enter your API token to generate a personal MCP session URL.</p>
<form method="post">
<label>API Token</label>
<input type="password" name="api_key" placeholder="Paste your token here" required>
<label>Data Tagger Base URL</label>
<input type="text" name="base_url" value="https://datatagger.ub.tum.de">
<button type="submit">Generate MCP URL</button>
</form>
</div>
</body></html>"""
    )


# --- Background Tasks ---
async def session_cleanup_loop():
    """Background task to remove expired sessions."""
    while True:
        try:
            cleanup_expired_sessions()
        except Exception:
            pass
        await asyncio.sleep(300)


# --- Final App Construction (FastAPI) ---

# Create the internal MCP app first
mcp_app = mcp.streamable_http_app()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # CRITICAL: Initialize the MCP app's lifespan (TaskGroup, etc.)
    async with mcp_app.router.lifespan_context(app):
        # Start our own background tasks
        asyncio.create_task(session_cleanup_loop())
        yield

# Main app with combined lifespan
app = FastAPI(lifespan=lifespan)

# Token Middleware for FastAPI — decodes self-contained JWT tokens
class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = request.query_params.get("token")
        if token:
            payload = decode_token(token)
            if payload is not None:
                session_key_var.set(payload["k"])
                session_base_url_var.set(payload["u"])
        return await call_next(request)

app.add_middleware(TokenAuthMiddleware)


class URLPrefixFixMiddleware(BaseHTTPMiddleware):
    """Fix 307 redirect Location headers from the MCP StreamableHTTP session
    manager so they include the URL_PREFIX (e.g. /dt) when running behind a
    reverse proxy that strips the prefix before forwarding."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code == 307:
            location = response.headers.get("location", "")
            url_prefix = os.environ.get("URL_PREFIX", "").strip()
            if url_prefix and location:
                from urllib.parse import urlparse
                parsed = urlparse(location)
                if not parsed.path.startswith(url_prefix):
                    new_path = url_prefix.rstrip("/") + "/" + parsed.path.lstrip("/")
                    response.headers["location"] = parsed._replace(path=new_path).geturl()
        return response


app.add_middleware(URLPrefixFixMiddleware)

# Registration Route
@app.api_route("/register", methods=["GET", "POST"])
async def register_route(request: Request):
    return await register_page_handler(request)

# Mount the MCP app on / (it already contains /mcp routes internally)
app.mount("/", mcp_app)


# --- Authentication & Core Helpers ---


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


async def upload_fdm_file(
    endpoint: str, file_path: str, ctx: Optional[Context] = None
) -> str:
    """Upload a local file to the FDM API endpoint as multipart form data."""
    if not os.path.exists(file_path):
        return f"Error: File not found exactly at {file_path}."

    try:
        token, base_url = get_auth_config(ctx)
    except ValueError as e:
        return str(e)

    url = f"{base_url}{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    try:
        filename = os.path.basename(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        async with httpx.AsyncClient() as client:
            with open(file_path, "rb") as f:
                files = {"file": (filename, f, mime_type)}
                response = await client.post(
                    url, headers=headers, files=files, timeout=600.0
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "json" in content_type.lower():
                    import json

                    return json.dumps(response.json(), indent=2)
                return response.text
    except Exception as e:
        return f"Error uploading file: {e}"


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
    folder_id: str, name: str, ctx: Optional[Context] = None
) -> str:
    """Create a new dataset entry inside a folder."""
    payload = {"folder": folder_id, "name": name}
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
    """Upload a raw file from your local computer into a dataset."""
    return await upload_fdm_file(
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
