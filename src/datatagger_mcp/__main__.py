"""Entry point — stdio by default, Streamable HTTP on request.

The hosted deployment (multi-user, JWT registration) lives in the separate
datatagger-proxy service; this entry point serves a single credential set.
"""
import argparse
import os
import sys

from .api import ALLOWED_HOSTS, mcp as mcp_server, transport_security_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datatagger-mcp")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="stdio (default, for local MCP clients) or streamable-http",
    )
    parser.add_argument(
        "--host", default=os.environ.get("MCP_HOST", "127.0.0.1"),
        help="bind address for streamable-http (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MCP_PORT", "8000")),
        help="bind port for streamable-http (default: 8000)",
    )
    parser.add_argument(
        "--endpoint", default=os.environ.get("MCP_ENDPOINT", "/mcp"),
        help="HTTP path for streamable-http (default: /mcp)",
    )
    parser.add_argument(
        "--stateful", action="store_true",
        help="streamable-http with Mcp-Session-Id sessions "
             "(default: stateless, MCP revision 2026-07-28)",
    )
    parser.add_argument(
        "--json-response", action="store_true",
        help="streamable-http answers with plain JSON instead of SSE streams",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)

    if args.transport == "stdio":
        print("Starting datatagger-mcp in stdio mode", file=sys.stderr)
        mcp_server.run(transport="stdio")
        return

    allowed = ", ".join(ALLOWED_HOSTS)
    print(
        f"Starting datatagger-mcp on http://{args.host}:{args.port}{args.endpoint} "
        f"({'stateful' if args.stateful else 'stateless'} streamable-http; "
        f"allowed hosts: {allowed})",
        file=sys.stderr,
    )
    mcp_server.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path=args.endpoint,
        stateless_http=not args.stateful,
        json_response=args.json_response,
        transport_security=transport_security_settings(),
    )


if __name__ == "__main__":
    main()
