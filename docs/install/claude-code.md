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

Install the runtime:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
```

Expose installed skills to the project:

```bash
./ar-coordination/scripts/install-skills.sh \
  --install-root ./.claude/skills
```

For user-wide Claude Code skills:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.claude/skills
```

Claude Code supports project and personal skill folders and discovers nested `.claude/skills` directories. The default namespace symlink layout is usually enough:

```text
.claude/skills/agents-remember-md -> ar-coordination/skills
```
