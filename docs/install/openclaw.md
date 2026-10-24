# Install For OpenClaw

OpenClaw uses an agent workspace with workspace files, and MCP server definitions live in the
OpenClaw config registry under `mcp.servers`. Agents Remember needs that registration and one call;
its skills arrive from the tool server, so nothing is copied into a workspace skill folder.

Official references:

- [OpenClaw Agent Workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- [OpenClaw MCP](https://docs.openclaw.ai/cli/mcp)
- [OpenClaw Configuration Reference](https://docs.openclaw.ai/gateway/configuration-reference)
- [OpenClaw Gateway](https://docs.openclaw.ai/cli/gateway)

## Register The MCP Server

Write an MCP authority settings file (copy `examples/mcp/settings.example.json` and replace its
paths), then merge this into `~/.openclaw/openclaw.json`:

```json
{
  "mcp": {
    "servers": {
      "agents-remember": {
        "command": "uvx",
        "args": [
          "--refresh-package",
          "agents-remember-mcp@latest",
          "--config",
          "<PATH/TO/YOUR/PROJECTS_FOLDER>/.openclaw/mcp/agents-remember-settings.json"
        ]
      }
    }
  }
}
```

`.openclaw/openclaw.merge.json` in this repository is that merge template, with
`<PATH/TO/YOUR/PROJECTS_FOLDER>` markers to replace by hand; no script renders it. It also points
`agents.defaults.workspace` at the agent workspace. Restart the gateway after merging.

After merging, inspect the saved registry:

```bash
openclaw mcp status --verbose
openclaw mcp doctor
```

`openclaw mcp probe agents-remember` opens a live connection and is useful when you want proof that
the MCP server starts correctly.

## Start With One Call

In an OpenClaw chat, name no task and no repository:

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

OpenClaw loads skills from the places it scans; Agents Remember writes none there. The tool server
publishes each skill as an MCP resource (the SEP-2640 skills extension) and OpenClaw reads them from
the running server, so there is no workspace skill copy to keep current and no skill-sync step to
follow.

## Optional: Workspace Instructions

Put the Agents Remember instruction in the OpenClaw workspace `AGENTS.md`, pointing at the
coordination runtime path OpenClaw can read. Copy `.openclaw/workspace/AGENTS.md` and replace
`<PATH/TO/YOUR/PROJECTS_FOLDER>` with your absolute path:

```markdown
# Workspace Agent Instructions

Read and follow `<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` before working in any target project.
Treat these rules as workspace instructions!

@<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md
```

OpenClaw workspaces may contain other standing instruction files. Keep Agents Remember focused on
repository memory and task workflow rules; do not put secrets in workspace docs. Do not overwrite a
target repository's root `AGENTS.md` just to wire Agents Remember.

OpenClaw has internal hooks and typed plugin hooks, but the documented file-based hook surface is
for Gateway automation and lifecycle events, not a project-local MCP starter, so the workspace
`AGENTS.md` is the instruction surface to rely on. If the workspace path or bootstrap files change
while the gateway is running, restart the gateway.

## Long-running Turns

Deep reasoning and large onboarding waves usually fit inside OpenClaw's current agent runtime
timeout. If a local or remote model stops streaming long enough to hit the provider idle watchdog,
prefer a provider-scoped timeout:

```json5
{
  models: {
    providers: {
      "<provider-id>": {
        timeoutSeconds: 300
      },
    },
  },
}
```

Use `agents.defaults.timeoutSeconds` only when the entire embedded agent run needs a different
runtime cap. Restart the gateway after config changes that do not hot-apply:

```bash
openclaw gateway restart
```
