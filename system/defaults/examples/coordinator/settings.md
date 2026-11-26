# Coordinator Settings Example

Use this file as the human-facing `ar-coordination/system/settings.md` starter
for a local coordinator.

The coordinator owns workspace-wide instructions, tools, workflow state, and
routing that can apply across multiple code repositories. It does not replace a
per-repository memory layer's `system/settings.md` or `system/settings.json`.

## Scope

The coordinator may store:

- global agent instructions that apply across repositories
- shared tool and command conventions
- workspace-wide source registries
- task roots
- worktree roots
- local notes and scratch paths
- selected external memory repo locations
- local operator conventions that are not durable repo policy

Rules that are valid only for one code repository belong in that repository's
selected memory layer, usually `ar-coordination/memory-repos/ar-<repo>/system/`
for external memory repos.

## Local Layout

```text
ar-coordination/
├── system/
│   └── settings.md
├── memory-repos/
├── tasks/
├── notes/
└── worktrees/
```

## Memory Repo Routing

Agents should resolve the target code repository through the product (`context_packet`; see the [tool reference](skill://agents-remember/skills/l-01-agent-lifecycles/reference/knowledge/commands.md)). Product resolution identifies
the active memory root and returns the memory layer's settings, tools, sources,
onboarding, and ledger paths.

Coordinator guidance may provide global defaults. Repository-specific memory
guidance is more specific and should win when the target repository has its own
rule.
