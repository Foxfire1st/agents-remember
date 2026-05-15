# Install For OpenClaw

OpenClaw uses an agent workspace with workspace files and skill folders.

Official references:

- [OpenClaw Agent Workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- [OpenClaw Skills](https://openclawcn.com/en/docs/agent/skills/)

## Workspace Instructions

Put the Agents Remember instruction in the OpenClaw workspace `AGENTS.md`, pointing at the actual coordination runtime path OpenClaw can read:

```markdown
# Workspace Agent Instructions

Read and follow `/path/to/ar-coordination/AGENTS.md` before working in any target project.
Treat these rules as workspace instructions!

@/path/to/ar-coordination/AGENTS.md
```

OpenClaw workspaces may contain other standing instruction files. Keep Agents Remember focused on repository memory and task workflow rules; do not put secrets in workspace docs.

## Skills

Install the runtime:

```bash
python3 agents-remember-md/installer/install-runtime.py /path/to/ar-coordination
```

OpenClaw commonly uses global skills under `~/.openclaw/skills/` and workspace skills under `<workspace>/skills/`, with workspace skills taking precedence.

Install workspace skills with flat layout:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root /path/to/openclaw-workspace/skills \
  --layout flat
```

For shared global skills:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.openclaw/skills \
  --layout flat
```

You can inspect OpenClaw skill visibility with:

```bash
openclaw skills list
openclaw skills check
```
