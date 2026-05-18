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

Agents Remember skills should stay linked to the installed runtime under
`ar-coordination/skills`. Use the default nested/tree install layout. Do not
work around OpenClaw skill discovery by copying the skills into the OpenClaw root
or by using flat layout: several bundled scripts use their runtime layout to find
shared modules and default coordination paths. A copied or flattened tree can
make the skills visible while making commands such as
`C-08-ar-coordination-context-resolver` resolve the wrong coordination root.

Install workspace skills with the default nested layout:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root /path/to/openclaw-workspace/skills
```

For shared global skills:

```bash
/path/to/ar-coordination/scripts/install-skills.sh \
  --install-root ~/.openclaw/skills
```

OpenClaw resolves symlink and junction targets before loading skills. Without an
explicit trust entry, it may report a `symlink-escape` or skip the linked runtime
because the real path points outside the configured skill root. Keep the links
and add a narrow `allowSymlinkTargets` entry to the OpenClaw config:

```json5
{
  skills: {
    load: {
      extraDirs: ["/path/to/openclaw-workspace/skills"],
      allowSymlinkTargets: ["/path/to/ar-coordination/skills"],
    },
  },
}
```

For a Windows workspace using `.agents/skills`, that looks like:

```json5
{
  skills: {
    load: {
      extraDirs: ["C:/ew/.agents/skills"],
      allowSymlinkTargets: ["C:/ew/ar-coordination/skills"],
    },
  },
}
```

Keep `allowSymlinkTargets` scoped to the real `ar-coordination/skills` directory.
Do not allow a broad parent such as the whole workspace or home directory.

## Long-running Turns

Deep reasoning and large onboarding waves can exceed OpenClaw's default patience
windows. If the TUI reports that a response is taking longer than expected, or
if model requests abort while DeepSeek is still thinking, increase both the
agent run timeout and the provider idle timeout:

```json5
{
  agents: {
    defaults: {
      timeoutSeconds: 1800,
    },
  },
  models: {
    providers: {
      deepseek: {
        timeoutSeconds: 900,
      },
    },
  },
}
```

Restart the gateway after changing timeout settings:

```bash
openclaw gateway restart
```

You can inspect OpenClaw skill visibility with:

```bash
openclaw skills list
openclaw skills check
```
