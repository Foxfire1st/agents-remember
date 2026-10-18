---
name: c-11-memory-carryover-from-branch
description: "Carry converted onboarding into a recovery leaf after its code lands; the curator answers its worklist, then normal closeout publishes."
---

# c-11-memory-carryover-from-branch Memory Carryover From Branch

Carryover is selective memory reconciliation, not a Git merge. It proves which source-branch code changes landed, then carries their richer onboarding into the exact open external-memory recovery leaf. Its clean code worktree must stand at the selected official tip. It never writes an official checkout or moves protected refs.

## Plan, then apply

```text
memory_carryover_plan(repo_id="<repo-id>", contract_path="<open-recovery-leaf-contract>", source_memory="<source-memory-repo>", official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>")
memory_carryover_apply(repo_id="<repo-id>", contract_path="<open-recovery-leaf-contract>", source_memory="<source-memory-repo>", official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>", intent_note="<developer intent>")
```

Start the recovery leaf from the landed code/memory pair. Plan is read-only and files no report. Review its candidates before an approval-gated apply. Both memory trees must hold valid `knowledge/layout.json` markers with the same conversion-format version; otherwise nothing further is read. Unconverted memory is refused: use `agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY` for first conversion, or the crossing sync (`worktree_sync` with `merge-memory`) from a converted line.

## Candidate units and selection

Markdown and its JSON sidecar are compared, selected and written **together**. `include_review_required` keeps these selection keys:

- `file-sidecar`: a code path in the diff; key = that path.
- `route-overview`: an overview covering landed paths; key = the normalized route (`.` for root). A differing body needs review.
- `memory-only-doc`: a document changed only in memory; key = its source path or route.
- `entity-catalog`: `onboarding/entities.md` and its sidecar; key = `entity-catalog`. A differing catalog needs whole-unit review, not entry-level merging.

Existing different target onboarding needs `replace_existing=true` or explicit review-required inclusion. Neither permits overwriting unrelated uncommitted edits or differing held records.

## Code evidence and anchors

- `exact-landed-commit`: **every** source commit touching the path is an official ancestor; one landed commit is not enough.
- `patch-id-match`: old-base-to-source and old-base-to-official patches match.
- `final-content-match`: source and official file content match.
- `same-path-changed`: both changed it without equivalent-code proof; review-required.
- `not-landed`: the code did not land; reject, never carry.

Only proven code tiers auto-carry. Every sidecar anchor is observed at the **official code commit**, never the source or an ambient checkout. Current anchors follow the code evidence; content unchanged at a moved blob/line is re-recorded at apply. Stale, unresolved or unreadable anchors require review with the entries/references and failure named; a failed Git read is never current.

A memory-only document auto-carries only as `memory-only-anchors-current`: it has anchors, every anchor is current, and the target has not changed either unit member since the memory merge base. No anchors, no merge base, or an independently changed target needs review.

## Records and history

Each candidate lists missing records named by its sidecar (`realizes`, `proves`, record-reference targets) and their transitive record links. Apply carries the source JSON and paired Markdown. A held record with different bytes is **never overwritten**: `record-differs` makes the candidate review-required; the curator changes it through the writer.

History files are never carried. The plan lists source rows for carried subjects as reading aids, not as the recovery leaf's answers. The recovery leaf's curator authors its own rows through the writer and answers the recomputed worklist before publishing.

## Apply and recovery

Apply writes selected units/missing records, regenerates route indexes and validates the **exact worktree tree** against the leaf's memory base, paired with official code, using the writer's `writer_reports` treatment. On refusal it returns violations and restores the pre-apply memory bytes, including existing edits. It creates no commit and does not refresh a ledger cache; `memory_commit_message` is not an apply argument.

- `would-carryover`: the read-only plan.
- `carried-into-worktree`: validated uncommitted content; written paths and recomputed worklist summary are returned.
- `nothing-to-carryover`: no selected payload needs writing; the whole tree still validates.

Retry skips files already equal to the intended payload. A different uncommitted target is `target-edited`, never overwritten. After interruption, retry skips completed writes and validates the whole tree; a refusal names carried paths differing from memory `HEAD`.

The recovery leaf closes and integrates normally. Its closeout makes the one memory commit from the tree its invariant gate judged, attributed to the leaf's code commit (the official tip). `carryover-pending` still means landed memory has not reached its line: cleanup/finalization wait for that normal recovery publication.

See `docs/guides/carryover-from-branch.md`. The skill tree is instruction-only; installed and development workflows use the existing MCP/package route.
