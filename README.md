# DataTagger MCP Library

A pure MCP tool library for interacting with the TUM DataTagger API.
This is a **library only** — no server, no HTTP handling, no Docker deployment.
For the server/proxy layer, see [unified-researchdata-mcp](https://github.com/harrytyp/unified-researchdata-mcp).

## Features

23 MCP tools for DataTagger, logically grouped:

### Read (9 tools)
- `search_datatagger` — Global search across projects, folders, datasets
- `list_projects` / `get_project` — Browse and retrieve projects
- `list_folders` / `get_folder` — Browse and retrieve folders
- `list_datasets` — List datasets inside folders
- `download_version_file` — Stream a dataset version file to a local path
- `get_folder_permissions` — List the folder's user permissions
- `list_metadata` — List available metadata template mappings

### Write (14 tools)
- `create_project` / `update_project` / `delete_project`
- `create_folder` / `update_folder` / `delete_folder`
- `create_dataset` / `delete_dataset`
- `publish_dataset` / `restore_dataset_version` / `compare_dataset_versions`
- `upload_dataset_file`
- `set_folder_permissions`
- `add_metadata_to_dataset`

> **`create_dataset`** supports two modes — pass `folder_id` to place the dataset inside a folder
> (appears under `/projects/.../folders/.../files/`), or omit it to create a free-standing draft
> (appears under `/drafts/...`). See [Upload modes](#upload-modes) below.

> Destructive operations (`delete_*`) require `confirm_danger=True`.

## Upload modes

The DataTagger API uses a single mechanism — **datasets** (`uploads-dataset`) — for both free-standing drafts and folder-bound uploads. The difference is whether the dataset has a `folder` reference:

| Mode | `folder_id` | API payload | Appears at |
|---|---|---|---|
| **Draft** | omitted / `None` | `{"name": "..."}` | `/drafts/{dataset_id}` |
| **Folder upload** | folder UUID | `{"name": "...", "folder": "..."}` | `/projects/{pid}/folders/{fid}/files/{file_id}` |

**Example workflow — Folder upload:**

1. `create_dataset(name="my-data", folder_id="<folder-uuid>")` → dataset inside the folder
2. `upload_dataset_file(dataset_id="<ds-uuid>", source_path="/path/to/file")` → file lands in the folder

**Example workflow — Draft upload:**

1. `create_dataset(name="my-draft")` → free-standing dataset (no folder)
2. `upload_dataset_file(dataset_id="<ds-uuid>", source_path="/path/to/file")` → file appears under `/drafts/`

## Usage

### As a library (import)

```python
from datatagger_mcp.api import mcp

# Credentials are resolved per call, in this order:
#   1. the ContextVars session_key_var / session_base_url_var (hosted proxy)
#   2. the legacy SESSION_AUTH store of the stdio session
#   3. the FDM_TOKEN / FDM_BASE_URL environment variables
import os
os.environ["FDM_TOKEN"] = "your-token"
os.environ["FDM_BASE_URL"] = "https://datatagger.ub.tum.de"

# List tools
tools = await mcp.list_tools()          # -> list[MCPTool], use .input_schema

# Call a tool
result = await mcp.call_tool("search_datatagger", {"term": "example", "limit": 5})
print(result.content[0].text)           # -> CallToolResult
```

### As a local MCP client (STDIO)

```json
{
  "mcpServers": {
    "datatagger": {
      "command": "datatagger-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "FDM_BASE_URL": "https://datatagger.ub.tum.de",
        "FDM_TOKEN": "your-token-here"
      }
    }
  }
}
```

## Installation

```bash
git clone https://github.com/harrytyp/datatagger-mcp.git
cd datatagger-mcp
pip install -e .
```

## Dependencies

- `mcp[cli]>=2.2.0,<3` — MCP Python SDK v2, speaks protocol revision **2026-07-28**
  (stateless core) and still serves every older revision from the same server
- `httpx>=0.28.0` — used for the DataTagger REST/TUS calls (independent of the
  SDK's own HTTP client)

## Streamable HTTP (opt-in)

The library is stdio-first, but the same server object can be served over the
stateless Streamable-HTTP transport (no `Mcp-Session-Id`, every request
self-describing — any request may land on any replica):

```bash
datatagger-mcp --transport streamable-http --host 0.0.0.0 --port 8000
# --stateful for the legacy session-based mode, --json-response to skip SSE
```

Programmatically:

```python
from datatagger_mcp.api import build_http_app
app = build_http_app(stateless=True)   # Starlette ASGI app, endpoint /mcp
```

`TransportSecuritySettings` (DNS-rebinding guard) applies to this transport
only; the allowed hosts live in `datatagger_mcp.api.ALLOWED_HOSTS`.

## Server / Proxy

This library contains **only** the MCP tools. The server layer (registration, JWT auth,
multi-user support, web UI) lives in a separate repository:

→ [harrytyp/unified-researchdata-mcp](https://github.com/harrytyp/unified-researchdata-mcp)

The server imports this library via `from datatagger_mcp.api import mcp as mcp_server`.

## License

MIT, see `LICENSE`.
