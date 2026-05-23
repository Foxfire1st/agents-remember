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

Install the runtime through the MCP server:

```text
runtime_install(dry_run=false)
```

OpenClaw commonly uses global skills under `~/.openclaw/skills/` and workspace skills under `<workspace>/skills/`, with workspace skills taking precedence.

Agents Remember skills should be installed through the MCP `skills_install`
tool. Use the default nested/tree install layout. Do not use flat layout unless
OpenClaw requires direct `<skill-name>/SKILL.md` folders.

Install workspace skills with the default nested layout:

```text
skills_install(install_root="/absolute/path/to/openclaw-workspace/skills", dry_run=false)
```

For shared global skills:

```text
skills_install(install_root="/absolute/path/to/.openclaw/skills", dry_run=false)
```

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
