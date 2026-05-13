# AGENTS.md

This coordinator stores workspace-wide Agents Remember instructions, tools, and
workflow state that may apply across all code repositories attached to this
coordination root.

Before using tasks, worktrees, notes, docs, or memory repos from this
coordinator, resolve the active repository context with
`C-08-ar-coordination-context-resolver`.

## Routing

- Use `system/settings.md` for global agent instructions, cross-repo defaults,
  layout, and operator notes.
- Use `system/settings.json` for machine-readable coordinator layout hints.
- Use `system/tools.md` for tools and commands that are valid across all or many
  repositories.
- Use `system/sources.md` for workspace-wide source registries.
- Do not put rules that are valid for only one code repository in coordinator
  files; put them in that repository's memory layer.
- After C-08 resolves a `memory_root`, read that memory layer's `AGENTS.md` when
  present, then read its `system/settings.md` and `system/tools.md`.

## Boundaries

- Do not move protected branches unless the developer explicitly asks.
- Do not create, close out, integrate, push, or clean up worktrees without the
  approval gates required by the selected workflow.
- When coordinator-wide guidance and memory-layer guidance conflict, prefer the
  memory-layer rule for that repository.
