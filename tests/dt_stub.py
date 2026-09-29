"""Shared stub for the DataTagger end-to-end tests.

Mimics the DataTagger REST/TUS responses the MCP library talks to, records every
incoming request, and enforces the Bearer token. No real API is ever contacted.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "test-token"
PROJECT_ID = "11111111-1111-4111-8111-111111111111"
FOLDER_ID = "22222222-2222-4222-8222-222222222222"
DATASET_ID = "33333333-3333-4333-8333-333333333333"
VERSION_ID = "44444444-4444-4444-8444-444444444444"
COMPARE_ID = "55555555-5555-4555-8555-555555555555"

CALLS: list[tuple[str, str, str, str]] = []  # (method, path, query, authorization header)
CALLS_LOCK = threading.Lock()


def calls(method: str | None = None, needle: str | None = None) -> list[tuple[str, str, str, str]]:
    with CALLS_LOCK:
        out = list(CALLS)
    if method:
        out = [c for c in out if c[0] == method]
    if needle:
        out = [c for c in out if needle in c[1] + "?" + c[2]]
    return out


class Checker:
    """Tiny PASS/FAIL collector so the tests stay dependency-free."""

    def __init__(self) -> None:
        self.results: list[tuple[bool, str]] = []

    def check(self, ok: bool, label: str) -> None:
        self.results.append((bool(ok), label))
        print(f"  {'PASS' if ok else 'FAIL'}  {label}", flush=True)

    def summary(self) -> int:
        failed = [label for ok, label in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed", flush=True)
        for label in failed:
            print(f"  FAILED: {label}", flush=True)
        return 1 if failed else 0


def tool_ok(output: str) -> bool:
    """A tool failed if it returned an auth/transport/API error string."""
    lowered = output.lower()
    return bool(output.strip()) and not any(
        marker in lowered
        for marker in ("api error", "error making", "error downloading", "error uploading",
                       "no authentication", "unexpected error", "internal error")
    )


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the test output clean
        pass

    def _record(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        with CALLS_LOCK:
            CALLS.append((self.command, parsed.path, parsed.query,
                          self.headers.get("authorization", "")))

    def _send(self, status: int, body: dict | bytes | str | None = None) -> None:
        if body is None:
            payload = b""
        elif isinstance(body, bytes):
            payload = body
        elif isinstance(body, str):
            payload = body.encode()
        else:
            payload = json.dumps(body).encode()
        self.send_response(status)
        if payload:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("content-length", "0") or 0)
        return self.rfile.read(length) if length else b""

    def _route(self) -> None:
        self._record()
        if self.headers.get("authorization", "") != f"Bearer {TOKEN}":
            self._send(401, {"detail": "Invalid token."})
            return
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        body = self._read_body()
        payload: dict = {}
        if body and "json" in (self.headers.get("content-type", "") or ""):
            payload = json.loads(body)
        parts = path.split("/")[3:]  # drop "", "api", "v1"

        if self.command == "POST" and path == "/api/v1/search/global":
            self._send(200, {"count": 1, "next_cursor": None, "previous_cursor": None,
                             "results": [{"result_type": "project",
                                          "item": {"pk": PROJECT_ID, "name": "stub-project"}}]})
        elif parts[0] == "project":
            if len(parts) == 1:
                if self.command == "GET":
                    self._send(200, {"count": 1, "results": [{"pk": PROJECT_ID, "name": "stub-project"}]})
                else:
                    self._send(201, {"pk": PROJECT_ID, "name": payload.get("name")})
            elif self.command == "GET":
                self._send(200, {"pk": parts[1], "name": "stub-project"})
            elif self.command == "PATCH":
                self._send(200, {"pk": parts[1], **payload})
            else:
                self._send(204)
        elif parts[0] == "folder-permission":
            self._send(200, {"count": 1, "results": [
                {"pk": "66666666-6666-4666-8666-666666666666", "email": "a@b.de"}]})
        elif parts[0] == "folder":
            if len(parts) == 1:
                if self.command == "GET":
                    self._send(200, {"count": 1, "results": [{"pk": FOLDER_ID, "name": "stub-folder"}]})
                else:
                    self._send(201, {"pk": FOLDER_ID, "name": payload.get("name")})
            elif parts[2:] == ["permissions"]:
                self._send(200, {"folder_users": payload.get("folder_users", [])})
            elif self.command == "GET":
                self._send(200, {"pk": parts[1], "name": "stub-folder"})
            elif self.command == "PATCH":
                self._send(200, {"pk": parts[1], **payload})
            else:
                self._send(204)
        elif parts[0] == "uploads-dataset":
            if len(parts) == 1:
                if self.command == "GET":
                    self._send(200, {"count": 1, "results": [{"pk": DATASET_ID, "name": "stub-dataset"}]})
                else:
                    self._send(201, {"pk": DATASET_ID, "name": payload.get("name")})
            elif parts[2:] == ["tus"] and self.command == "POST":
                location = f"/api/v1/uploads-dataset/{parts[1]}/tus/77777777-7777-4777-8777-777777777777/"
                self.send_response(201)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif len(parts) == 3 and parts[2].startswith("tus") and self.command in ("PATCH", "PUT"):
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif parts[2:] == ["version"]:
                self._send(201, {"pk": VERSION_ID, "metadata": payload.get("metadata", [])})
            elif parts[2:] in (["publish"], ["restore"]):
                self._send(200, {"pk": parts[1], "detail": f"{parts[2]} ok"})
            else:
                self._send(204)
        elif parts[0] == "uploads-version":
            if parts[2:3] == ["diff"] and self.command == "GET":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                self._send(200, {"pk": parts[1], "compare": (query.get("compare") or [None])[0]})
            elif parts[2:3] == ["download"] and self.command == "GET":
                self._send(200, b"stub-file-bytes")
            else:
                self._send(405, {"detail": "Method not allowed"})
        elif parts[0] == "metadata":
            self._send(200, {"count": 1, "results": [{"pk": VERSION_ID, "name": "stub-metadata"}]})
        else:
            self._send(404, {"detail": f"no stub route for {path}"})

    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _route


def start_stub() -> tuple[ThreadingHTTPServer, str]:
    """Start the stub on a free port; returns (server, base_url)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def server_env(base_url: str, **extra: str) -> dict[str, str]:
    """Minimal environment for a stdio/HTTP server subprocess."""
    env = {
        "FDM_BASE_URL": base_url,
        "FDM_TOKEN": TOKEN,
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "PYTHONIOENCODING": "utf-8",
    }
    env.update(extra)
    return env
