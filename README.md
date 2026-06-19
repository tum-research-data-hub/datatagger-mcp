# DataTagger MCP Library

A pure MCP tool library for interacting with the TUM DataTagger API.
This is a **library only** — no server, no HTTP handling, no Docker deployment.
For the server/proxy layer, see [unified-researchdata-mcp](https://github.com/harrytyp/unified-researchdata-mcp).

## Features

23 MCP tools for DataTagger, logically grouped:

### Read (7 tools)
- `search_datatagger` — Global search across projects, folders, datasets
- `list_projects` / `get_project` — Browse and retrieve projects
- `list_folders` / `get_folder` — Browse and retrieve folders
- `list_datasets` — List datasets inside folders

### Write (16 tools)
- `create_project` / `update_project` / `delete_project`
- `create_folder` / `update_folder` / `delete_folder`
- `create_dataset` / `delete_dataset`
- `publish_dataset` / `restore_dataset_version` / `compare_dataset_versions`
- `upload_dataset_file` / `download_fdm_file`
- `get_folder_permissions` / `set_folder_permissions`
- `add_metadata_to_dataset` / `list_metadata`

> Destructive operations (`delete_*`) require `confirm_danger=True`.

## Usage

### As a library (import)

```python
from datatagger_mcp.api import mcp

# Set credentials on the FastMCP instance
mcp.state.api_key = "your-token"
mcp.state.base_url = "https://datatagger.ub.tum.de"

# List tools
tools = await mcp.list_tools()

# Call a tool
result = await mcp.call_tool("search_datatagger", {"term": "example", "limit": 5})
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

- `mcp[cli]>=1.2.0`
- `httpx>=0.28.0`

## Server / Proxy

This library contains **only** the MCP tools. The server layer (registration, JWT auth,
multi-user support, web UI) lives in a separate repository:

→ [harrytyp/unified-researchdata-mcp](https://github.com/harrytyp/unified-researchdata-mcp)

The server imports this library via `from datatagger_mcp.api import mcp as mcp_server`.
