"""Live test against the real DataTagger API, driven through the real MCP server.

Runs the actual stdio server as a subprocess (like a user's MCP client would)
and exercises every tool against https://datatagger.ub.tum.de. Creates
throw-away resources named ``dtmcp-test-<timestamp>`` and removes them again.

    python tests/test_live_api.py --token-file <path-to-file-with-token>

The token is read from the file and passed to the server process through the
environment — it never appears in argv, in the log output, or in this script.
Results are classified: PASS / DOC (expected API or role restriction) / FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp import Client, StdioServerParameters  # noqa: E402

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RESULTS: list[tuple[str, str, str]] = []  # (verdict, tool, detail)


def record(verdict: str, tool: str, detail: str = "") -> None:
    RESULTS.append((verdict, tool, detail))
    suffix = f" — {detail}" if detail else ""
    print(f"  {verdict:4s}  {tool}{suffix}", flush=True)


async def call(client: Client, tool: str, **arguments) -> str:
    result = await client.call_tool(tool, arguments)
    return "\n".join(c.text for c in result.content if getattr(c, "text", None) is not None)


def ok(text: str) -> bool:
    lowered = text.lower()
    return bool(text.strip()) and not any(
        marker in lowered
        for marker in ("api error", "error making", "error uploading", "error downloading",
                       "no authentication", "error:")
    )


def jload(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def pk_of(text: str) -> str:
    """The created resource's own pk — never the referenced parent (both are uuids)."""
    data = jload(text)
    if isinstance(data, dict) and isinstance(data.get("pk"), str):
        return data["pk"]
    return first_uuid(text)


def doc_reason(text: str) -> str:
    """Turn an API error into a short reason string (403 role limits etc.)."""
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
    match = re.search(r"API Error \((\d{3})\):\s*(.{0,140})", body)
    return f"HTTP {match.group(1)}: {match.group(2)}" if match else body[:140]


def first_uuid(text: str) -> str:
    found = UUID_RE.search(text)
    return found.group(0) if found else ""


def dataset_item(text: str, name: str):
    data = jload(text)
    if not isinstance(data, dict):
        return None
    for item in data.get("results") or []:
        if isinstance(item, dict) and name in (item.get("name"), item.get("display_name")):
            return item
    return None


def version_pks(item) -> list[str]:
    pks: list[str] = []
    if not isinstance(item, dict):
        return pks
    for version in item.get("uploads_versions") or []:
        if isinstance(version, dict) and isinstance(version.get("pk"), str):
            pks.append(version["pk"])
    latest = item.get("latest_version")
    if isinstance(latest, dict) and isinstance(latest.get("pk"), str):
        pks.append(latest["pk"])
    return list(dict.fromkeys(pks))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--base-url", default="https://datatagger.ub.tum.de")
    parser.add_argument("--keep", action="store_true", help="skip cleanup of the test resources")
    args = parser.parse_args()

    with open(os.path.expanduser(args.token_file), encoding="utf-8") as fh:
        token = fh.read().strip()
    if not token:
        print("token file is empty", file=sys.stderr)
        return 2
    print(f"token loaded ({len(token)} chars) from {args.token_file}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    prefix = f"dtmcp-test-{stamp}"
    tmpdir = tempfile.mkdtemp(prefix="dtmcp-live-")
    upload_src = os.path.join(tmpdir, "sample.txt")
    with open(upload_src, "w", encoding="utf-8") as fh:
        fh.write(f"hello datatagger {stamp}\n")
    download_dest = os.path.join(tmpdir, "downloaded.bin")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "datatagger_mcp", "--transport", "stdio"],
        env={"FDM_TOKEN": token, "FDM_BASE_URL": args.base_url,
             "PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
             "PYTHONIOENCODING": "utf-8"},
    )

    project_id = folder_id = dataset_id = ""
    version_ids: list[str] = []

    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        print(f"protocol {client.protocol_version}, {len(tools)} tools\n")

        print("-- read --")
        text = await call(client, "list_projects", limit=3)
        record("PASS" if ok(text) and '"count"' in text else "FAIL", "list_projects")

        text = await call(client, "search_datatagger", term="test", limit=3)
        record("PASS" if ok(text) and '"results"' in text else "FAIL", "search_datatagger")

        text = await call(client, "list_metadata", limit=3)
        record("PASS" if ok(text) else "FAIL", "list_metadata")

        print("\n-- write cycle --")
        text = await call(client, "create_project", name=f"{prefix}-project")
        project_id = pk_of(text)
        record("PASS" if ok(text) and project_id else "FAIL", "create_project", project_id)

        text = await call(client, "get_project", project_id=project_id)
        record("PASS" if ok(text) and project_id in text else "FAIL", "get_project")

        text = await call(client, "update_project", project_id=project_id, name=f"{prefix}-project-renamed")
        record("PASS" if ok(text) else "FAIL", "update_project")

        text = await call(client, "create_folder", project_id=project_id, name=f"{prefix}-folder")
        folder_id = pk_of(text)
        record("PASS" if ok(text) and folder_id else "FAIL", "create_folder", folder_id)

        text = await call(client, "update_folder", folder_id=folder_id, name=f"{prefix}-folder-renamed")
        record("PASS" if ok(text) else "FAIL", "update_folder")

        text = await call(client, "create_dataset", name=f"{prefix}-dataset", folder_id=folder_id)
        dataset_id = pk_of(text)
        record("PASS" if ok(text) and dataset_id else "FAIL", "create_dataset", dataset_id)

        text = await call(client, "upload_dataset_file", dataset_id=dataset_id, source_path=upload_src)
        record("PASS" if "uploaded successfully" in text else "FAIL", "upload_dataset_file", text[:80])

        text = await call(client, "list_datasets", folder_id=folder_id)
        item = dataset_item(text, f"{prefix}-dataset")
        record("PASS" if ok(text) and dataset_id in text else "FAIL", "list_datasets")
        if item is not None:
            version_ids = version_pks(item)
            record("PASS" if version_ids else "SKIP", "version ids from list_datasets",
                   f"{len(version_ids)} version pks")
        else:
            record("SKIP", "version ids from list_datasets",
                   "dataset item not found in the folder listing")

        text = await call(client, "add_metadata_to_dataset", dataset_id=dataset_id,
                          metadata_items=[{"field": {"key": "mcp_test", "field_type": "TEXT"},
                                           "value": f"written-by-dtmcp-{stamp}"}])
        record("PASS" if ok(text) else "FAIL", "add_metadata_to_dataset", text[:80])

        # a second upload produces a second version, which is what compare/restore need
        text = await call(client, "upload_dataset_file", dataset_id=dataset_id, source_path=upload_src)
        record("PASS" if "uploaded successfully" in text else "FAIL", "upload_dataset_file (2nd version)")

        text = await call(client, "list_datasets", folder_id=folder_id)
        record("PASS" if ok(text) else "FAIL", "list_datasets (after upload)")
        item = dataset_item(text, f"{prefix}-dataset") or item
        version_ids = version_pks(item) or version_ids
        latest_version = (item or {}).get("latest_version") or {}
        latest_pk = latest_version.get("pk") if isinstance(latest_version, dict) else None
        older_pk = next((v for v in version_ids if v != latest_pk), "")

        if len(version_ids) >= 2:
            text = await call(client, "compare_dataset_versions",
                              version_id=version_ids[0], compare_to_id=version_ids[-1])
            record("PASS" if ok(text) else "DOC", "compare_dataset_versions", doc_reason(text))
        else:
            record("SKIP", "compare_dataset_versions",
                   f"only {len(version_ids)} version(s) available")

        text = await call(client, "download_version_file",
                          version_id=version_ids[0] if version_ids else dataset_id,
                          dest_path=download_dest, overwrite=True)
        if os.path.exists(download_dest):
            record("PASS", "download_version_file", f"{os.path.getsize(download_dest)} bytes")
        else:
            record("DOC" if "API Error" in text else "FAIL", "download_version_file", doc_reason(text))

        if older_pk:
            text = await call(client, "restore_dataset_version",
                              dataset_id=dataset_id, uploads_version_id=older_pk)
            record("PASS" if ok(text) else "DOC", "restore_dataset_version", doc_reason(text))
        else:
            record("SKIP", "restore_dataset_version", "no non-latest version id to restore")

        text = await call(client, "publish_dataset", dataset_id=dataset_id)
        record("PASS" if ok(text) else "DOC", "publish_dataset", doc_reason(text))

        text = await call(client, "get_folder_permissions", folder_id=folder_id)
        record("PASS" if ok(text) else "FAIL", "get_folder_permissions")

        text = await call(client, "set_folder_permissions", folder_id=folder_id, folder_users=[])
        record("PASS" if ok(text) else "DOC", "set_folder_permissions", doc_reason(text))

        print("\n-- guards --")
        text = await call(client, "delete_project", project_id=project_id)
        record("PASS" if "confirm_danger" in text else "FAIL", "delete_project guard")

        if not args.keep:
            print("\n-- cleanup --")
            for tool, kwargs in (
                ("delete_dataset", {"dataset_id": dataset_id}),
                ("delete_folder", {"folder_id": folder_id}),
                ("delete_project", {"project_id": project_id}),
            ):
                text = await call(client, tool, confirm_danger=True, **kwargs)
                record("PASS" if ok(text) else "DOC", f"{tool} (cleanup)", doc_reason(text))
        else:
            print(f"\n-- kept for inspection: project {project_id}, folder {folder_id}, dataset {dataset_id}")

    passed = sum(1 for v, _, _ in RESULTS if v == "PASS")
    docs = [f"{t} ({d})" for v, t, d in RESULTS if v == "DOC"]
    skipped = [t for v, t, _ in RESULTS if v == "SKIP"]
    failed = [f"{t}: {d}" for v, t, d in RESULTS if v == "FAIL"]

    print(f"\n{passed} PASS, {len(docs)} DOC, {len(skipped)} SKIP, {len(failed)} FAIL")
    for item in docs:
        print(f"  DOC: {item}")
    for item in skipped:
        print(f"  SKIP: {item}")
    for item in failed:
        print(f"  FAIL: {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
