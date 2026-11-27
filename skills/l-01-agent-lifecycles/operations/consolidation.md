<!-- ar:roles all -->
# Operation — Consolidation

Consolidate one leaf's `consolidationList` with unchanged code tree (no route review; knowledge gate + coherence required).

## Workflow

Check `worktree_status` and `consolidationList`. Run full contract-scoped `memory_quality_check` to persist worklist (`consolidation_target` items, deduplicated).

1. Read listed families together (guarantee + members), then loose rules; read code/decisions/cards; run named or newly-linked proof tests only (temp in scratch).
2. Write rows via mounted `knowledge_write` (`commit: false` -> `commit: true`, no producer list): `stands`, corrected, or `unresolved`, with `testsRun`/`testsNotRun`/`noProofRecorded`. Zero-change pass lands history alone.
3. Verify `git status --porcelain` empty, `git diff --check`, `knowledge_integrity_check`, full `memory_quality_check` (`curatorActionableCount=0`, `ready-for-closeout`), and `knowledge_read(view="dependent", staleOnly=true, compact=true)` (full for evidence; 0 stale).
4. On `coherence-required`, run `curator_coherence` `prepare`->`publish`->`validate` (never re-validate post-closeout trees).
5. Write report (outcomes, test commands, `unresolved`, gaps) and send memory candidate to Reviewer via `role_message` (sibling role, leaf refs; releases held Reviewer; names files, never pastes). Repair Reviewer findings via `knowledge_write` and rerun checks/coherence.

## Notices

Notify Manager on seat/delivery, authority, beyond-limit passes, disputes, reserved sync, and `unresolved` or gap items (routed to Architect for issues).
