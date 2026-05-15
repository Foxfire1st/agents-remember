# Install For Codex

Codex needs two things:

1. workspace instructions through `AGENTS.md`
2. skills exposed in a Codex-visible skills folder such as `.agents/skills` or `~/.agents/skills`

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

Install the runtime first:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
```

Then expose the installed skill tree:

```bash
./ar-coordination/scripts/install-skills.sh \
  --install-root ./.agents/skills
```

For user-wide skills:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.agents/skills
```

The default tree layout creates:

```text
<install-root>/agents-remember-md -> <ar-coordination>/skills
```

That keeps skill helper paths resolved from the installed runtime instead of copied source folders.
