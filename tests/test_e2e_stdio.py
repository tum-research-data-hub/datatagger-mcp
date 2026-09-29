"""End-to-end test: the real stdio MCP server against a stub DataTagger API.

Runs the actual `datatagger-mcp` stdio server (MCP Python SDK v2) as a
subprocess, drives it with the SDK's own `Client`, and checks all 23 tools
through the local stub in `dt_stub.py`. No real credentials, no writes against
the real API.

    python tests/test_e2e_stdio.py

Exit code 0 = all checks passed.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dt_stub import (  # noqa: E402
    COMPARE_ID, DATASET_ID, FOLDER_ID, PROJECT_ID, TOKEN, VERSION_ID,
    Checker, calls, server_env, start_stub, tool_ok,
)
from mcp import Client, StdioServerParameters  # noqa: E402


async def run_tool(client: Client, name: str, arguments: dict) -> str:
    result = await client.call_tool(name, arguments)
    texts = [c.text for c in result.content if getattr(c, "text", None) is not None]
    return "\n".join(texts)


async def main() -> int:
    stub, base_url = start_stub()
    check = Checker()
    print(f"stub DataTagger API on {base_url}")

    tmpdir = tempfile.mkdtemp(prefix="dt-mcp-e2e-")
    upload_src = os.path.join(tmpdir, "sample.txt")
    with open(upload_src, "w", encoding="utf-8") as fh:
        fh.write("hello datatagger\n")
    download_dest = os.path.join(tmpdir, "downloaded.bin")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "datatagger_mcp", "--transport", "stdio"],
        env=server_env(base_url),
    )

    try:
        async with Client(params) as client:
            print(f"\nnegotiated protocol: {client.protocol_version} | server: {client.server_info}")
            check.check(client.protocol_version == "2026-07-28",
                        "client negotiates MCP 2026-07-28 (stateless core)")

            tools = (await client.list_tools()).tools
            names = sorted(t.name for t in tools)
            check.check(len(names) == 23, f"tools/list exposes 23 tools (got {len(names)})")
            ctx_leak = [t.name for t in tools if "ctx" in (t.input_schema.get("properties") or {})]
            check.check(not ctx_leak, "no tool leaks its `ctx` parameter into the input schema")

            print("\n-- read tools --")
            out = await run_tool(client, "search_datatagger", {"term": "stub", "limit": 5})
            check.check(tool_ok(out) and calls("POST", "/search/global/"),
                        "search_datatagger -> POST /search/global/")
            out = await run_tool(client, "list_projects", {"limit": 5})
            check.check(tool_ok(out) and PROJECT_ID in out, "list_projects -> GET /project/")
            out = await run_tool(client, "get_project", {"project_id": PROJECT_ID})
            check.check(tool_ok(out) and PROJECT_ID in out, "get_project -> GET /project/{id}/")
            out = await run_tool(client, "list_folders", {"project": PROJECT_ID})
            check.check(tool_ok(out) and calls("GET", "/folder/"), "list_folders -> GET /folder/")
            out = await run_tool(client, "get_folder", {"folder_id": FOLDER_ID})
            check.check(tool_ok(out) and FOLDER_ID in out, "get_folder -> GET /folder/{id}/")
            out = await run_tool(client, "list_datasets", {"folder_id": FOLDER_ID})
            check.check(tool_ok(out) and DATASET_ID in out, "list_datasets -> GET /uploads-dataset/")
            out = await run_tool(client, "get_folder_permissions", {"folder_id": FOLDER_ID})
            check.check(tool_ok(out) and calls("GET", "/folder-permission/"),
                        "get_folder_permissions -> GET /folder-permission/")
            out = await run_tool(client, "list_metadata", {})
            check.check(tool_ok(out) and calls("GET", "/metadata/"), "list_metadata -> GET /metadata/")
            out = await run_tool(client, "download_version_file",
                                 {"version_id": VERSION_ID, "dest_path": download_dest})
            check.check(tool_ok(out) and os.path.exists(download_dest),
                        "download_version_file -> streamed to disk")

            print("\n-- write tools --")
            out = await run_tool(client, "create_project", {"name": "stub-project"})
            check.check(tool_ok(out) and calls("POST", "/project/"), "create_project -> POST /project/")
            out = await run_tool(client, "update_project", {"project_id": PROJECT_ID, "name": "renamed"})
            check.check(tool_ok(out) and calls("PATCH", f"/project/{PROJECT_ID}/"),
                        "update_project -> PATCH /project/{id}/")
            out = await run_tool(client, "create_folder", {"project_id": PROJECT_ID, "name": "stub-folder"})
            check.check(tool_ok(out) and calls("POST", "/folder/"), "create_folder -> POST /folder/")
            out = await run_tool(client, "update_folder", {"folder_id": FOLDER_ID, "name": "renamed"})
            check.check(tool_ok(out) and calls("PATCH", f"/folder/{FOLDER_ID}/"),
                        "update_folder -> PATCH /folder/{id}/")
            out = await run_tool(client, "create_dataset", {"name": "stub-dataset", "folder_id": FOLDER_ID})
            check.check(tool_ok(out) and calls("POST", "/uploads-dataset/"),
                        "create_dataset -> POST /uploads-dataset/")
            out = await run_tool(client, "create_dataset", {"name": "stub-draft"})
            check.check(tool_ok(out), "create_dataset without folder_id (draft mode)")

            out = await run_tool(client, "upload_dataset_file",
                                 {"dataset_id": DATASET_ID, "source_path": upload_src})
            check.check("uploaded successfully" in out
                        and calls("POST", f"/uploads-dataset/{DATASET_ID}/tus/")
                        and calls("PATCH", f"/uploads-dataset/{DATASET_ID}/tus/"),
                        "upload_dataset_file -> POST /tus/ + PATCH /tus/{guid}/ (TUS)")

            out = await run_tool(client, "publish_dataset", {"dataset_id": DATASET_ID})
            check.check(tool_ok(out) and calls("POST", f"/uploads-dataset/{DATASET_ID}/publish/"),
                        "publish_dataset -> POST /publish/")
            out = await run_tool(client, "restore_dataset_version",
                                 {"dataset_id": DATASET_ID, "uploads_version_id": VERSION_ID})
            check.check(tool_ok(out) and calls("POST", f"/uploads-dataset/{DATASET_ID}/restore/"),
                        "restore_dataset_version -> POST /restore/")
            out = await run_tool(client, "compare_dataset_versions",
                                 {"version_id": VERSION_ID, "compare_to_id": COMPARE_ID})
            diff_gets = calls("GET", "/diff/")
            check.check(not calls("POST", "/diff/") and diff_gets
                        and COMPARE_ID in out and "compare=" in diff_gets[0][2],
                        "compare_dataset_versions -> GET /uploads-version/{id}/diff/?compare=... (no POST)")
            out = await run_tool(client, "set_folder_permissions", {
                "folder_id": FOLDER_ID,
                "folder_users": [{"email": "a@b.de", "can_edit": True,
                                  "is_folder_admin": False, "is_metadata_template_admin": False}],
            })
            check.check(tool_ok(out) and calls("PUT", f"/folder/{FOLDER_ID}/permissions/"),
                        "set_folder_permissions -> PUT /folder/{id}/permissions/")
            out = await run_tool(client, "add_metadata_to_dataset", {
                "dataset_id": DATASET_ID,
                "metadata_items": [{"field": {"key": "sample", "field_type": "TEXT"}, "value": "stub"}],
            })
            check.check(tool_ok(out) and calls("POST", f"/uploads-dataset/{DATASET_ID}/version/"),
                        "add_metadata_to_dataset -> POST /uploads-dataset/{id}/version/")

            print("\n-- destructive guard --")
            before = len(calls())
            out = await run_tool(client, "delete_project", {"project_id": PROJECT_ID})
            check.check("confirm_danger" in out and len(calls()) == before,
                        "delete_project without confirm_danger performs no HTTP call")
            out = await run_tool(client, "delete_project", {"project_id": PROJECT_ID, "confirm_danger": True})
            check.check(tool_ok(out) and calls("DELETE", f"/project/{PROJECT_ID}/"),
                        "delete_project(confirm_danger=True) -> DELETE /project/{id}/")
            out = await run_tool(client, "delete_folder", {"folder_id": FOLDER_ID, "confirm_danger": True})
            check.check(tool_ok(out) and calls("DELETE", f"/folder/{FOLDER_ID}/"),
                        "delete_folder(confirm_danger=True) -> DELETE /folder/{id}/")
            out = await run_tool(client, "delete_dataset", {"dataset_id": DATASET_ID, "confirm_danger": True})
            check.check(tool_ok(out) and calls("DELETE", f"/uploads-dataset/{DATASET_ID}/"),
                        "delete_dataset(confirm_danger=True) -> DELETE /uploads-dataset/{id}/")

        print("\n-- auth plumbing --")
        all_calls = calls()
        with_auth = [c for c in all_calls if c[3] == f"Bearer {TOKEN}"]
        check.check(bool(all_calls) and len(with_auth) == len(all_calls),
                    f"every one of the {len(all_calls)} API calls carried the Bearer token")
    finally:
        stub.shutdown()

    return check.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
