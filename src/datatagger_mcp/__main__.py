"""Entry point — local/stdio mode only. Hosted mode moved to datatagger-proxy."""
from .api import mcp as mcp_server

def main():
    import sys
    print("Starting datatagger-mcp in stdio mode", file=sys.stderr)
    mcp_server.run(transport="stdio")

if __name__ == "__main__":
    main()
