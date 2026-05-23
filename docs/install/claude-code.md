# Install For Claude Code

Claude Code separates always-loaded project instructions from native skills.

- Use `CLAUDE.md` for workspace instructions.
- Use `.claude/skills` or `~/.claude/skills` for Claude Code skills.

Official reference: [Claude Code skills](https://code.claude.com/docs/en/skills).

## Workspace Instructions

At the root of the projects folder, create `CLAUDE.md`:

```markdown
# Workspace Agent Instructions

Read and follow `ar-coordination/AGENTS.md` before working in any sibling project.
Treat these rules as workspace instructions!

@ar-coordination/AGENTS.md
```

If `ar-coordination` is outside the workspace, point at the actual readable path.

## Skills

Install the runtime through the MCP server:

```text
runtime_install(dry_run=false)
```

Place the MCP settings under the Claude Code registration folder, such as
`.claude/mcp/`. The skill target is inferred as the sibling `.claude/skills/`
folder. Then expose packaged skills:

```text
skills_install(dry_run=false)
```

Claude Code supports project and personal skill folders and discovers nested `.claude/skills` directories. The default namespace layout is usually enough:

```text
.claude/skills/agents-remember-md/
```
