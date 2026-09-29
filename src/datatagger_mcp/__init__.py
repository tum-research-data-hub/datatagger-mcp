"""Datatagger MCP server package."""

USER_AGENT = "fdmMCP/1.0"


def __getattr__(name):
    """Expose the library's single MCPServer instance as ``datatagger_mcp.mcp``.

    The instance lives in :mod:`datatagger_mcp.api` (that is where the tools are
    registered); this lazy re-export avoids building a second, tool-less server
    at import time.
    """
    if name == "mcp":
        from .api import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
