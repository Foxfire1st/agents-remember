# Install For Cursor

Cursor supports project rules, project hooks, project-local MCP configuration, and native Agent
Skills. Agents Remember needs the MCP registration and one call; its skills arrive from the tool
server as MCP resources, so no skill folder is copied into the workspace.

Official references:

- [Cursor Rules](https://cursor.com/docs/rules.md)
- [Cursor Hooks](https://cursor.com/docs/hooks.md)
- [Cursor MCP](https://cursor.com/docs/mcp.md)

## Register The MCP Server

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then add the server to `.cursor/mcp.json` in your workspace:

```json
{
  "mcpServers": {
    "agents-remember": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.cursor/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

`.cursor/mcp.json` in this repository is that registration with
`<PATH/TO/YOUR/PROJECTS_FOLDER>` markers to replace by hand; no script renders it. Restart Cursor
once so it starts the server.

## Start With One Call

In a Cursor chat, name no task and no repository:

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

Cursor discovers skills from folders it scans; Agents Remember writes none. The tool server
publishes each skill as an MCP resource (the SEP-2640 skills extension) and Cursor reads them from
the running server, so there is no skill tree to copy, render or keep in sync.

## Optional: Always-Applied Rule

An always-applied project rule is the dependable instruction surface: Cursor documents
`sessionStart` hooks as fire-and-forget, so the rule is what a chat reliably reads.

Copy `.cursor/rules/agents-remember.mdc` into your workspace `.cursor/rules/`, replace
`<PATH/TO/YOUR/PROJECTS_FOLDER>` with your absolute path, and keep the front matter:

```markdown
---
description: Agents Remember memory system conventions
alwaysApply: true
---

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Do not overwrite a repository's root `AGENTS.md` just to wire Agents Remember; root `AGENTS.md` is
project-specific. If you also want the session-start hook, copy `.cursor/hooks/` and
`.cursor/hooks.json` the same way and replace the same marker there.
