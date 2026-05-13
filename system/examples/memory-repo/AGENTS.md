# AGENTS.md

This directory is a durable Agents Remember memory layer for one code
repository. Its instructions are valid for that repository, not globally across
every repository attached to the same coordinator.

Before relying on this memory, resolve context with
`C-08-ar-coordination-context-resolver` and run the onboarding drift gate with
`C-02-onboarding-drift-detection`.

## Read First

- `system/settings.md` for human and agent instructions.
- `system/settings.json` for storage, path-rule, and cross-repo policy.
- `system/tools.md` for repo-specific checks, branch workflow, and local command
  notes.
- `system/sources.md` for domain documentation and external references.
- `system/coding-guidelines.md` when present for repo-specific coding rules.

## Branch And Workflow Notes

Repo-specific branching strategies belong in `system/tools.md` so agents can
discover them before using worktree integration commands. If a workflow helper
has generic integration behavior, prefer the repository-specific branch notes
when they are more restrictive.

Coordinator-wide guidance may still apply as a default, but this memory layer is
the more specific authority for its code repository.
