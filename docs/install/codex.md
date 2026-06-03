# Install For Codex

Codex needs two things:

1. the Agents Remember MCP server in Codex config
2. Codex-visible skills and startup instructions from the `.codex/` starter package

Official references:

- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex MCP](https://developers.openai.com/codex/mcp)
- [Codex hooks](https://developers.openai.com/codex/hooks)
- [Codex agent skills](https://developers.openai.com/codex/skills)
- [Codex AGENTS.md instructions](https://developers.openai.com/codex/guides/agents-md)

## Root Starter Package

The repository includes a Codex starter package at `.codex/`. Copy that folder
to your workspace root, replace every placeholder, including
`<PATH/TO/YOUR/PROJECTS_FOLDER>` and `<YOUR_REPOSITORY_FOLDER_NAME>`, then
restart Codex once.

The package contains:

- `.codex/config.toml` - MCP registration and `SessionStart` hook registration.
- `.codex/hooks/agents-remember-session-start.*` - startup directive emitted as
  session context.
- `.codex/mcp/agents-remember-settings.json` - Agents Remember MCP authority
  settings.
- `.codex/skills/` - Agents Remember skills in Codex's project skill root.

After the restart, invoke:

```text
c-13-install-and-onboard
```

That skill runs or verifies `runtime_install()` and then handles memory,
onboarding, and providers.

## Workspace Instructions

Do not overwrite a repository's root `AGENTS.md` just to wire Agents Remember;
root `AGENTS.md` is project-specific. Codex can read `AGENTS.md` when a project
already owns one, but the starter package uses the copied `SessionStart` hook as
the first-run instruction surface.

The hook emits this directive:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Use an absolute path in the `@...` include when the coordination runtime is outside the workspace.

The starter package already includes the Codex `SessionStart` hook. `c-13-install-and-onboard`
does not install hooks during first-run setup.

## Skills

The copied starter package already includes one flat folder per skill under
`.codex/skills/`:

```text
.codex/skills/<skill-name>/
```

Do not run `skills_install()` for first-run setup. It remains available for
manual maintenance and non-package installs.
