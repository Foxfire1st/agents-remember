# Agents Remember MCP

`agents-remember-mcp` is the installable Model Context Protocol server for
Agents Remember. It lets an MCP-capable coding harness call Agents Remember
operations from the host instead of asking the model to edit or execute
coordinator scripts directly.

Source: [github.com/Foxfire1st/agents-remember-md](https://github.com/Foxfire1st/agents-remember-md)

## Quickstart

Setup is agent-driven. Ask your agent to:

1. **Install and wire Agents Remember MCP** — set it to run via
   `uvx agents-remember-mcp --config <absolute-path>/agents-remember-settings.json`,
   help you fill in that settings file (starter below), and register it with this
   harness. **Then restart the harness** so it loads the server.
2. **Install Agents Remember** — run `runtime_install`, then `skills_install`
   (scaffolding, skills, and — if providers are enabled and Docker is running —
   the provider images).
3. **Onboard your project** — run the `C-13-install-and-onboard` skill: it
   pre-checks the setup, installs the start hook (or places the directive for
   harnesses without one), sets up the memory repo (it will ask: scaffold a new
   one or use an existing one), bootstraps onboarding, and starts the providers
   indexing your code and memory.

The only hands-on steps for you: ask, restart once after step 1, and answer the
new-vs-existing memory question in step 3.

## Requirements

- Python 3.11 or newer
- an MCP-capable coding harness
- [uv](https://docs.astral.sh/uv/) (for `uvx`) or pip
- Git for repository and memory ledger operations
- Docker when provider tools are enabled (plus Ollama for the grepai embedder)

## Install And Run

The simplest path is `uvx`, which fetches and runs the server on demand — no
manual virtualenv or PATH setup:

```text
uvx agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

Or install with pip and use the console command:

```text
python -m pip install agents-remember-mcp
agents-remember-mcp --config /absolute/path/to/agents-remember-settings.json
```

The config path must be absolute, and the settings file must live outside the
`ar-coordination/` runtime folder.

## Settings

A minimal starter `agents-remember-settings.json` (your agent can fill this in):

```json
{
  "version": 1,
  "coordinationRoot": "/absolute/path/to/ar-coordination",
  "workspaceRoot": "/absolute/path/to/workspace",
  "repositories": {
    "<your-repo-name>": {}
  },
  "providers": {
    "codegraphcontext-code": {},
    "grepai-memory": {}
  }
}
```

`coordinationRoot` is where the runtime and memory repos live (populated by
`runtime_install`). `workspaceRoot` holds your code repos. List each repo you
want Agents Remember to manage under `repositories`. Omit or empty the
`providers` block if you do not want the Docker-backed providers. Full field
reference:
[settings-json.md](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/settings-json.md).

## Harness Setup

Register the MCP server with your harness by pointing it at `uvx` (or the
installed console command) and the absolute settings path:

```json
{
  "command": "uvx",
  "args": [
    "agents-remember-mcp",
    "--config",
    "/absolute/path/to/agents-remember-settings.json"
  ]
}
```

After installing or changing the MCP server registration, restart the harness so
it reloads the server and discovers the tool list.

## First Operations

For a new workspace, the usual first MCP calls (Quickstart step 2) are:

```text
server_info()
runtime_install(dry_run=false)
skills_install(dry_run=false)
context_packet(repo_id="<repo-id>", include_providers=true)
```

Then run the installed `C-13-install-and-onboard` skill (Quickstart step 3) to
install the start hook, set up the memory repo, bootstrap onboarding, and start
provider indexing.

## Tool Surface

The server exposes tools for:

- startup context and drift checks
- runtime and skill installation
- memory initialization, memory quality checks, and route index refresh
- provider status, watcher control, GrepAI search, and CodeGraphContext queries
- chat/direct closeout and worktree-backed task workflows
- benchmark preparation and execution

Provider tools only work when the MCP settings enable the provider and the
required Docker services are available. Full tool list:
[MCP Tool Reference](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/mcp-tools.md).

## More

- [Project README](https://github.com/Foxfire1st/agents-remember-md/blob/main/README.md)
- [Getting Started](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/getting-started.md)
- [Settings Reference](https://github.com/Foxfire1st/agents-remember-md/blob/main/docs/reference/settings-json.md)
