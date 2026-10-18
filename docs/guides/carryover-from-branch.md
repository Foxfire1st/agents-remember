# Carry Memory From a Branch

Carry richer onboarding after its code lands into an open external-memory recovery leaf. This is **not a Git merge** and never writes an official checkout or moves protected refs.

## Plan, then apply

The leaf starts at the landed pair; its clean code stays at official tip. Both memory markers (`knowledge/layout.json`) must have the **same conversion version**, before further reads. Otherwise convert with `knowledge-convert`, or crossing sync (`worktree_sync` / `merge-memory`) from a converted line.

```text
memory_carryover_plan(repo_id="<repo-id>", contract_path="<open-recovery-leaf-contract>", source_memory="<source-memory-repo>",
  official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>")
memory_carryover_apply(repo_id="<repo-id>", contract_path="<open-recovery-leaf-contract>", source_memory="<source-memory-repo>",
  official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>", intent_note="<developer intent>")
```

Plan is read-only, with no report. Review candidates before approved apply. `replace_existing` and `include_review_required` cannot override target edits or differing held records.

## Carried units

File cards, overviews, memory-only documents and catalogs travel as **Markdown plus JSON**, together. Keys: source path, normalized route (`.` for root), memory-only path/route, literal `entity-catalog`. A differing overview/catalog needs whole-unit review.

| Code evidence | Meaning | Decision |
| --- | --- | --- |
| `exact-landed-commit` | every source commit touching the path is an official ancestor | auto-carry |
| `patch-id-match` | old-base→source and old-base→official patches match | auto-carry |
| `final-content-match` | source and official file bytes match | auto-carry |
| `same-path-changed` | both changed the path without equivalent-code proof | review-required |
| `not-landed` | changed on source but did not land | reject |

Observe every sidecar anchor at **official code**. Current follows code evidence; moved blobs/lines are re-recorded at apply. Stale/unverifiable anchors need review naming entries/references and failures. Failed reads are never current.

A memory-only document auto-carries only with `memory-only-anchors-current`: anchors exist, every anchor is current, and the target has not changed either member since the memory merge base. Without anchors or a resolvable merge base it needs review.

## Records, validation and publication

The plan names missing records of `realizes`, `proves`, reference record targets and their transitive links. Apply carries their JSON and paired Markdown. A differing record already on target is never overwritten: the candidate shows `record-differs` and needs review; the curator changes the record through the writer. Source history rows are reading aids only; **history files never travel**.

Apply writes selected units/missing records and regenerates route indexes. It validates the exact resulting worktree tree against the leaf's memory base, paired with official code, with the writer's `writer_reports` rules. A refusal returns violations and restores the memory worktree byte for byte, including preexisting edits. Apply commits nothing, has no `memory_commit_message` argument and does not refresh the ledger cache.

Retry skips equal files. Other uncommitted bytes are `target-edited`, never overwritten. After interruption, retry skips already completed writes and validates the whole tree; refusal names carried paths differing from memory `HEAD`.

| State | Meaning |
| --- | --- |
| `would-carryover` | read-only plan |
| `carried-into-worktree` | validated uncommitted writes, with written paths and recomputed worklist summary |
| `nothing-to-carryover` | no payload needs writing; whole-tree validation still runs |

The curator answers the worklist with **its own rows**. Closeout commits the gate-judged tree once, attributed to the leaf code commit (official tip), then integrates. `carryover-pending` still blocks cleanup/finalization until that memory reaches its line.

See `c-11-memory-carryover-from-branch`; [Adopt Existing Memory](adopt-existing-memory.md) for baseline; c-02 checks.
