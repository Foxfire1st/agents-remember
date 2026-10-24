# Install For VS Code + GitHub Copilot

VS Code supports always-on repository instructions, workspace MCP configuration, and agent hooks
for GitHub Copilot Chat. Agents Remember needs the MCP registration and one call; its skills arrive
from the tool server as MCP resources, so no skill folder is copied into the workspace.

Official references:

- [Use custom instructions in VS Code](https://code.visualstudio.com/docs/copilot/customization/custom-instructions)
- [Agent hooks in VS Code](https://code.visualstudio.com/docs/copilot/customization/hooks)
- [Add and manage MCP servers in VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers)

## Register The MCP Server

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then add the server to `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "agents-remember": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.vscode/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

This repository keeps the templates under `.github-vscode/` and `.vscode/` so they do not collide
with its own `.github/workflows/`. Copy `.vscode/` to your workspace root if you use it, and replace
the `<PATH/TO/YOUR/PROJECTS_FOLDER>` markers by hand; no script renders them. Restart the window
once so VS Code starts the server.

## Start With One Call

In a Copilot Chat session, name no task and no repository:

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

VS Code discovers project skills from folders it scans; Agents Remember writes none. The tool
server publishes each skill as an MCP resource (the SEP-2640 skills extension) and Copilot reads
them from the running server, so there is no skill tree to copy, render or keep in sync.

## Optional: Repository Instructions And Hook

Copilot supports repository instructions at `.github/copilot-instructions.md`. Copy
`.github-vscode/copilot-instructions.md` into your workspace `.github/` folder, replace
`<PATH/TO/YOUR/PROJECTS_FOLDER>` with your absolute path, and keep repository-specific guidance
separate when the repository already owns that file.

If you want the first-action directive injected as session context, copy
`.github-vscode/hooks/` into `.github/hooks/` the same way. VS Code discovers workspace hooks from
`.github/hooks/*.json`, and the template registers a `SessionStart` command with per-platform
overrides:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "python3 .github/hooks/agents-remember-session-start.py",
        "windows": "py -3 .github\\hooks\\agents-remember-session-start.py",
        "linux": "python3 .github/hooks/agents-remember-session-start.py",
        "osx": "python3 .github/hooks/agents-remember-session-start.py",
        "timeout": 60
      }
    ]
  }
}
```
