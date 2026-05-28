# Agents Remember MCP

`agents-remember-mcp` is the installable Model Context Protocol server for
Agents Remember. It lets an MCP-capable coding harness call Agents Remember
operations from the host instead of asking the model to edit or execute
coordinator scripts directly.

Source checkout: [github.com/Foxfire1st/agents-remember-md](https://github.com/Foxfire1st/agents-remember-md)

The full project documentation lives one directory up in the source checkout:

- [Agents Remember README](../README.md)
- [Getting Started](../docs/getting-started.md)
- [Settings Reference](../docs/reference/settings-json.md)

## Requirements

- Python 3.11 or newer
- an MCP-capable coding harness
- Git for repository and memory ledger operations
- Docker when provider tools are enabled

## Install

Install from PyPI:

```text
python -m pip install agents-remember-mcp
```

The installed console command is:

```text
agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

The config path must be absolute. The MCP authority settings file must live
outside the `ar-coordination/` runtime folder. A starter settings file is
available in the source checkout at
`examples/mcp/settings.example.json`.

## Harness Setup

Register the MCP server with your harness by pointing it at the installed
command and the trusted settings file:

```json
{
  "command": "agents-remember-mcp",
  "args": [
    "--config",
    "/absolute/path/to/agents-remember-settings.json"
  ]
}
```

After installing or changing the MCP server registration, restart the harness
so it reloads the server and discovers the tool list.

Once the MCP server is running, ask your model to use Agents Remember. Normal
use should be agent-driven through MCP tool calls; manual tool calls are mainly
for setup and troubleshooting.

## First Operations

For a new workspace, the usual first MCP calls are:

```text
server_info()
runtime_install(dry_run=false)
skills_install(dry_run=false)
context_packet(repo_id="<repo-id>", include_providers=true)
```

Then ask the model to initialize memory for the target repository and bootstrap
onboarding through the installed Agents Remember skills.

## Tool Surface

The server exposes tools for:

- startup context and drift checks
- runtime and skill installation
- memory initialization, memory quality checks, and route index refresh
- provider status, watcher control, GrepAI search, and CodeGraphContext queries
- chat/direct closeout and worktree-backed task workflows
- benchmark preparation and execution

Provider tools only work when the MCP settings enable the provider and the
required Docker services are available.
