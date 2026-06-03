# Install For Cursor

Cursor supports native Agent Skills, project rules, project hooks, and
project-local MCP configuration.

Official references:

- [Cursor Rules](https://cursor.com/docs/rules.md)
- [Cursor Hooks](https://cursor.com/docs/hooks.md)
- [Cursor MCP](https://cursor.com/docs/mcp.md)
- [Cursor Agent Skills](https://cursor.com/docs/skills.md)

## Root Starter Package

The repository includes a Cursor starter package at `.cursor/`. Copy that folder
to your workspace root, replace every placeholder, including
`<PATH/TO/YOUR/PROJECTS_FOLDER>` and `<YOUR_REPOSITORY_FOLDER_NAME>`,
install/register the MCP server, then open or restart the workspace in Cursor
once.

The package contains:

- `.cursor/rules/agents-remember.mdc` - always-applied first-action instruction.
- `.cursor/hooks.json` and `.cursor/hooks/agents-remember-session-start.py` -
  project `sessionStart` hook that injects the same startup directive.
- `.cursor/mcp.json` - project-local Cursor MCP registration.
- `.cursor/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.cursor/skills/` - Agents Remember skills for Cursor Agent.

After the restart, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Persistent Instructions

Use the packaged Cursor project rule. Do not overwrite a repository's root
`AGENTS.md` just to wire Agents Remember; root `AGENTS.md` is project-specific.

The starter package includes `.cursor/rules/agents-remember.mdc`:

```markdown
---
description: Agents Remember memory system conventions
alwaysApply: true
---

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Use an absolute path when `ar-coordination` is outside the workspace.

Cursor also supports project hooks. The starter package uses a `sessionStart`
hook to inject the startup directive, but the always-applied project rule remains
the dependable instruction surface because Cursor documents `sessionStart` as
fire-and-forget.

## MCP

Cursor project MCP config lives at `.cursor/mcp.json`.

```json
{
  "mcpServers": {
    "agents-remember": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--refresh-package",
        "agents-remember-mcp",
        "agents-remember-mcp@latest",
        "--config",
        "<PATH/TO/YOUR/PROJECTS_FOLDER>/.cursor/mcp/agents-remember-settings.json"
      ]
    }
  }
}
```

## Runtime And Skills

After the restart, the copied `c-13-install-and-onboard` skill runs or verifies:

```text
runtime_install()
```

Cursor loads skills from `.agents/skills`, `.cursor/skills`, `~/.agents/skills`,
and `~/.cursor/skills`; it also supports Claude and Codex compatibility folders.
The copied starter package already provides the Agents Remember skills in
`.cursor/skills/`.

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.
