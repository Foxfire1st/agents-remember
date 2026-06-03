# Install For OpenClaw

OpenClaw uses an agent workspace with workspace files and skill folders. MCP
server definitions live in the OpenClaw config registry under `mcp.servers`.

Official references:

- [OpenClaw Agent Workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- [OpenClaw System Prompt](https://docs.openclaw.ai/concepts/system-prompt)
- [OpenClaw MCP](https://docs.openclaw.ai/cli/mcp)
- [OpenClaw Configuration Reference](https://docs.openclaw.ai/gateway/configuration-reference)
- [OpenClaw Hooks](https://docs.openclaw.ai/automation/hooks)
- [OpenClaw Gateway](https://docs.openclaw.ai/cli/gateway)
- [OpenClaw Skills](https://openclawcn.com/en/docs/tools/skills/)

## Root Starter Package

The repository includes an OpenClaw starter package at `.openclaw/`. Copy that
folder to your shared workspace root, replace every placeholder, including
`<PATH/TO/YOUR/PROJECTS_FOLDER>` and `<YOUR_REPOSITORY_FOLDER_NAME>`, merge
`.openclaw/openclaw.merge.json` into `~/.openclaw/openclaw.json`, then restart
the gateway or start OpenClaw.

The package contains:

- `.openclaw/openclaw.merge.json` - safe merge template for
  `~/.openclaw/openclaw.json`; it points `agents.defaults.workspace` at the
  package workspace and registers the Agents Remember MCP server under
  `mcp.servers`.
- `.openclaw/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.openclaw/workspace/AGENTS.md` - OpenClaw workspace operating instruction.
- `.openclaw/workspace/skills/` - Agents Remember skills in OpenClaw's
  highest-precedence workspace skill root.

If you already have an OpenClaw workspace, copy the contents of
`.openclaw/workspace/` into that workspace instead of changing
`agents.defaults.workspace`.

After the restart, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Workspace Instructions

Put the Agents Remember instruction in the OpenClaw workspace `AGENTS.md`, pointing at the actual coordination runtime path OpenClaw can read:

```markdown
# Workspace Agent Instructions

Read and follow `/path/to/ar-coordination/AGENTS.md` before working in any target project.
Treat these rules as workspace instructions!

@/path/to/ar-coordination/AGENTS.md
```

OpenClaw workspaces may contain other standing instruction files. Keep Agents Remember focused on repository memory and task workflow rules; do not put secrets in workspace docs.

Do not overwrite a target repository's root `AGENTS.md` just to wire Agents
Remember; root `AGENTS.md` is project-specific. The starter package uses the
OpenClaw agent workspace file instead.

OpenClaw has internal hooks and typed plugin hooks, but the documented
file-based hook surface is for Gateway automation and lifecycle events, not a
minimal project-local MCP starter. The starter package therefore uses the
documented workspace `AGENTS.md` bootstrap file as the first-action authority.

OpenClaw loads bootstrap files from the configured agent workspace. If the
workspace path or bootstrap files change while the gateway is running, restart
the gateway.

## MCP

OpenClaw stores third-party MCP server definitions under `mcp.servers` in
`~/.openclaw/openclaw.json`. The starter package includes this merge template:

```json
{
  "mcp": {
    "servers": {
      "agents-remember": {
        "command": "uvx",
        "args": [
          "--refresh-package",
          "agents-remember-mcp",
          "agents-remember-mcp@latest",
          "--config",
          "<PATH/TO/YOUR/PROJECTS_FOLDER>/.openclaw/mcp/agents-remember-settings.json"
        ]
      }
    }
  }
}
```

After merging, use these OpenClaw commands to inspect the saved registry:

```bash
openclaw mcp status --verbose
openclaw mcp doctor
```

`openclaw mcp probe agents-remember` opens a live connection and is useful when
you want proof that the MCP server starts correctly.

## Runtime And Skills

After the restart, the copied `c-13-install-and-onboard` skill runs or verifies:

```text
runtime_install()
```

OpenClaw loads bundled skills, managed/local skills under `~/.openclaw/skills`,
and workspace skills under `<workspace>/skills`, with workspace skills taking
precedence. The starter package uses `.openclaw/workspace/skills`.

Do not run `skills_install()` for first-run setup. The copied starter package
already provides one flat folder per skill under
`.openclaw/workspace/skills/`. `skills_install()` remains available for manual
maintenance and non-package installs.

You can inspect OpenClaw skill visibility with:

```bash
openclaw skills list
openclaw skills check
```

## Long-running Turns

Deep reasoning and large onboarding waves usually fit inside OpenClaw's current
agent runtime timeout. If a local or remote model stops streaming long enough to
hit the provider idle watchdog, prefer a provider-scoped timeout:

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

Use `agents.defaults.timeoutSeconds` only when the entire embedded agent run
needs a different runtime cap. Restart the gateway after config changes that do
not hot-apply:

```bash
openclaw gateway restart
```
