---
name: c-11-memory-carryover-from-branch
description: "Carry richer onboarding memory from a source branch into official memory only when the corresponding code has landed on the official branch."
---

# c-11-memory-carryover-from-branch Memory Carryover From Branch

Use this skill when a protected official code branch receives delayed or batched merges, while a developer has accumulated richer onboarding on another branch. The source branch may be a personal workbench branch, an individual feature branch, or a handpicked branch processed as part of a larger reconciliation pass.

The `c-11-memory-carryover-from-branch` skill is not a Git merge. It is a selective memory reconciliation step. It proves which source-branch code changes are represented in the official branch, then carries only the corresponding onboarding into official memory and refreshes verification metadata against the official code commit.

## MCP Tools

Use the Agents Remember MCP memory carryover tools as the normal installed
runtime entry point:

```text
memory_carryover_plan(repo_id="<repo-id>", source_memory="<source-memory-repo>", official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>")
memory_carryover_apply(repo_id="<repo-id>", source_memory="<source-memory-repo>", official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>", intent_note="<developer intent>")
```

Run `memory_carryover_plan` first. Use `memory_carryover_apply` only after
reviewing the candidate report. Apply mutates official memory only; it does not
move code branches. The skill tree is instruction-only; installed and
development workflows use the MCP/package route.

## Evidence Tiers

- `exact-landed-commit`: at least one commit that touched the source path on the source branch is an ancestor of the official code ref.
- `patch-id-match`: the old-base-to-source-branch patch for the source path matches the old-base-to-official patch.
- `final-content-match`: the source file content at the source branch ref matches the content at the official ref.
- `same-path-changed`: both branches changed the same source path, but the `c-11-memory-carryover-from-branch` skill did not prove equivalent code.
- `not-landed`: the source path changed on the source branch but not on the official branch.

Only proven tiers are auto-carry candidates. Same-path overlap is review-required by default because another developer may have changed the same file independently.

## Output States

- `would-carryover`: dry-run plan with candidate decisions.
- `carried-over`: official memory content and ledger commits were created.
- `nothing-to-carryover`: no selected candidate changed official memory.
- `ledger-mapped-head`: nothing was actionable, but an unmapped official code HEAD (e.g. a PR merge commit) was mapped to the current memory content commit and the ledger committed.
- `blocked`: apply was requested without approval, dirty official memory, missing ledger, or missing required candidate data.

Every apply result also reports `memory_main_advance` (GitHub #54): after the
carryover commits exist, memory `main` is fast-forwarded to the official
checkout tip — code `main` advances via the PR merge, but memory has no PR
flow, so non-main cycles previously left memory `main` behind indefinitely.
States: `fast-forwarded`, `already-current` (main checked out or already at
tip), `diverged` (independent commits on main — reported, never forced),
`failed` (e.g. main checked out in another worktree), `skipped` (no main
branch). After carryover, push memory `main` per the repo's git workflow when
the developer approves.

## Boundaries

1. The `c-11-memory-carryover-from-branch` skill must not copy source branch memory for code that did not land.
2. The `c-11-memory-carryover-from-branch` skill must not copy source branch ledger rows into official memory.
3. The `c-11-memory-carryover-from-branch` skill must refresh carried onboarding metadata to the official code commit, not the source branch commit.
4. The `c-11-memory-carryover-from-branch` skill must not auto-carry same-path-only evidence.
5. The `c-11-memory-carryover-from-branch` skill must not overwrite existing different official onboarding unless `replace_existing=true` or explicit review-required inclusion is used.
6. The `c-02-memory-quality-control` skill remains the branch-accuracy drift detector; the `c-11-memory-carryover-from-branch` skill only imports richer memory whose code validity has been proven or explicitly approved.
