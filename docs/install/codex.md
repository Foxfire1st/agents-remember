# Install For Codex

Codex needs two things:

1. workspace instructions through `AGENTS.md`
2. skills exposed in a Codex-visible skills folder such as `.codex/skills` or `~/.codex/skills`

## Workspace Instructions

At the root of the projects folder, create `AGENTS.md`:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

Use an absolute path in the `@...` include when the coordination runtime is outside the workspace.

## Skills

Install the runtime first through the MCP server:

```text
runtime_install(dry_run=false)
```

Place the MCP settings under the Codex registration folder, such as
`.codex/mcp/`. The skill target is inferred as the sibling `.codex/skills/`
folder. Then expose the packaged skill tree:

```text
skills_install(dry_run=false)
```

The default tree layout copies:

```text
<install-root>/agents-remember-md/
```

That keeps the harness pointed at an MCP-installed skill package instead of ad hoc copied source folders.
