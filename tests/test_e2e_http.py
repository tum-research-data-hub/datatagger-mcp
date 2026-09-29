"""End-to-end test: the stateless Streamable-HTTP transport.

Starts `datatagger-mcp --transport streamable-http` as a subprocess and talks to
it two ways:

* with the SDK's `Client` over the URL, and
* with raw HTTP requests in the 2026-07-28 shape (per-request `_meta`,
  `Mcp-Method`/`Mcp-Name` headers, no `initialize`, no `Mcp-Session-Id`),

against the stub DataTagger API from `dt_stub.py`.

    python tests/test_e2e_http.py

Exit code 0 = all checks passed.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402
from dt_stub import (  # noqa: E402
    DATASET_ID, PROJECT_ID, Checker, calls, server_env, start_stub, tool_ok,
)
from mcp import Client  # noqa: E402

MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "raw-http-test", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def rpc(url: str, method: str, params: dict | None = None, name: str | None = None,
        timeout: float = 20.0) -> tuple[httpx.Response, dict | None]:
    """One self-contained 2026-07-28 request: no handshake, no session."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if name:
        headers["Mcp-Name"] = name
    body = {"jsonrpc": "2.0", "id": 1, "method": method,
            "params": {**(params or {}), "_meta": MODERN_META}}
    with httpx.Client(timeout=timeout) as client:  # fresh connection per call
        resp = client.post(url, headers=headers, json=body)

    payload = None
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
                break
    elif "json" in content_type and resp.text.strip():
        payload = resp.json()
    else:
        payload = json.loads(resp.text) if resp.text.strip() else None
    return resp, payload


def wait_for_server(url: str, proc: subprocess.Popen, deadline: float = 40.0) -> bool:
    """Poll the endpoint (no blind sleep) until it answers or the deadline passes."""
    started = time.time()
    while time.time() - started < deadline:
        if proc.poll() is not None:
            return False
        try:
            _, payload = rpc(url, "tools/list", timeout=5.0)
            if payload and "result" in payload:
                return True
        except Exception:
            time.sleep(0.5)
    return False


async def main() -> int:
    stub, base_url = start_stub()
    check = Checker()
    print(f"stub DataTagger API on {base_url}")

    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    log_path = os.path.join(tempfile.mkdtemp(prefix="dt-mcp-http-log-"), "server.log")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "datatagger_mcp", "--transport", "streamable-http",
         "--host", "127.0.0.1", "--port", str(port)],
        env=server_env(base_url),
        stdout=log_file, stderr=subprocess.STDOUT, text=True,
    )
    try:
        if not wait_for_server(url, proc):
            log_file.flush()
            print(f"server did not come up; log ({log_path}):")
            print(open(log_path, encoding="utf-8", errors="replace").read()[-3000:])
            check.check(False, "streamable-http server answers requests")
            return check.summary()
        check.check(True, f"streamable-http server up on {url}")

        print("\n-- SDK client over HTTP --")
        async with Client(url) as client:
            print(f"negotiated protocol: {client.protocol_version}")
            check.check(client.protocol_version == "2026-07-28",
                        "URL client negotiates MCP 2026-07-28")
            tools = (await client.list_tools()).tools
            check.check(len(tools) == 23, f"tools/list over HTTP exposes 23 tools (got {len(tools)})")
            result = await client.call_tool("list_projects", {"limit": 3})
            text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
            check.check(tool_ok(text) and PROJECT_ID in text,
                        "tools/call over HTTP reaches the DataTagger API")

        print("\n-- raw 2026-07-28 requests, no handshake, no session --")
        resp, payload = rpc(url, "tools/list")
        names = [t["name"] for t in (payload or {}).get("result", {}).get("tools", [])]
        check.check(resp.status_code == 200 and len(names) == 23,
                    f"sessionless tools/list -> 200 with 23 tools (got {resp.status_code}, {len(names)})")
        check.check("mcp-session-id" not in {k.lower() for k in resp.headers},
                    "no Mcp-Session-Id is issued (stateless)")

        resp, payload = rpc(url, "tools/call", {"name": "list_projects", "arguments": {"limit": 2}},
                            name="list_projects")
        text = json.dumps((payload or {}).get("result", {}))
        check.check(resp.status_code == 200 and PROJECT_ID in text,
                    "second, independent request (new connection) -> tools/call works")

        resp, payload = rpc(url, "tools/call",
                            {"name": "get_project",
                             "arguments": {"project_id": "99999999-9999-4999-8999-999999999999"}},
                            name="get_project")
        text = json.dumps((payload or {}).get("result", {}))
        check.check(resp.status_code == 200 and "99999999" in text,
                    "third independent request reaches the API with its own arguments")

        print("\n-- server side --")
        check.check(bool(calls("GET", "/project/")), "stub recorded the API calls made through HTTP")
        check.check(proc.poll() is None, "server process still running after the requests")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        stub.shutdown()

    return check.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
