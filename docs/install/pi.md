# Install For Pi.dev

Pi supports context files, project settings, project-local extensions, and package-managed
extensions. Pi core has no built-in MCP client, so the project settings load `pi-mcp-adapter`.
Agents Remember needs that registration and one call; its skills arrive from the tool server, so
`.pi/settings.json` lists no skill folder.

Official references:

- [Pi Using Pi](https://pi.dev/docs/latest/usage)
- [Pi Settings](https://pi.dev/docs/latest/settings)
- [Pi Extensions](https://pi.dev/docs/latest/extensions)
- [Pi Packages](https://pi.dev/docs/latest/packages)
- [pi-mcp-adapter](https://pi.dev/packages/pi-mcp-adapter)

## Register The MCP Server

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then write your workspace `.pi/settings.json` and `.pi/mcp.json`:

```json
{
  "packages": [
    "npm:pi-mcp-adapter"
  ],
  "extensions": [
    "extensions/agents-remember-start.ts"
  ]
}
```

```json
{
  "mcpServers": {
    "agents-remember": {
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.pi/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

The files under `.pi/` in this repository are those two documents with
`<PATH/TO/YOUR/PROJECTS_FOLDER>` markers to replace by hand; no script renders them. Pi installs a
missing project package on startup. Use `/mcp` to inspect adapter status. Restart or `/reload` Pi
once so it starts the server.

## Start With One Call

In a Pi chat, name no task and no repository:

```text
role_start(role="bootstrap", request_id="<a UUID you generate>")
```

That one call prepares the runtime scaffold, provisions and starts the Paseo host, verifies the
Agents Remember plug-in, and starts one instructed Bootstrap seat. Its answer names the seat and
the dashboard address of its chat; continue setup there. A retry with the same `request_id`
reconciles to the same seat instead of a second one.

Bootstrap ends at memory setup and tells you to start the Architect, which organises the knowledge
foundation as ordinary task work. Bootstrap starts no role and authors no onboarding or knowledge.

## Skills

Pi loads skills from folders it scans; Agents Remember writes none. The tool server publishes each
skill as an MCP resource (the SEP-2640 skills extension) and Pi reads them from the running server
through the adapter, so its settings name no skill folder and there is no skill tree to copy,
render or keep in sync.

## Optional: First-Action Extension

Pi runs `before_agent_start` before an agent turn and lets a project extension inject the
first-action directive into the system prompt. Copy `.pi/extensions/` into your workspace and
replace `<PATH/TO/YOUR/PROJECTS_FOLDER>` in `agents-remember-start.ts` with your absolute path. If
you edit the extension while Pi is running, use `/reload`.

Do not overwrite a repository's root `AGENTS.md` just to wire Agents Remember; root `AGENTS.md` is
project-specific. Pi also supports global context under `~/.pi/agent/AGENTS.md`, which this setup
does not need.
