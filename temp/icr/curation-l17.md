# L17 — ICR-R17@v1 "Coherent live refresh": CURATION report (round 2, after fix round 2)

**Leaf:** `260921-ICR-L17` under atomic master `260921-ICR`. **Curator seat**, memory worktree only; no
code changed, no commits anywhere.

## Status

**GATE NOT REACHED — curation is complete and the memory worktree is deliberately left dirty, but the
memory-quality gate still reads `action-required`.** The shortfall is a set of citation ranges that a
mechanical repair could not clear without making the count worse; the exact numbers and the nature of
the remainder are below.

## The verdict I gated on

`<code leaf>/temp/icr/verify-l17-round3.md` — **Verdict: `pass`**, **Findings: None** (both round-2
findings closed). The only `fail` string in the file is prose about the two *cases* failing against
preserved pre-fix bytes, not a verdict, and there is no `blocking` marker. I gated **only** on round 3;
`verify-l17.md` (`fail`) and `verify-l17-round2.md` (`pass-with-findings`) were treated as superseded.

## Code fingerprints

| Point | `git -C <code leaf> diff \| sha256sum` |
| --- | --- |
| before I started | `0d44b92b8a0de8b8f580e1d4a7f6b22f909a47b3253da120fcdf63800881b088` |
| after I finished | `0d44b92b8a0de8b8f580e1d4a7f6b22f909a47b3253da120fcdf63800881b088` |

**Unchanged** — the candidate never moved while I worked, so no card needs re-checking on that account.
`HEAD` stayed `c422dc00273d4ae7a5d8c9c8db97365b8c85d640`. The three new source modules hash exactly as
the verifier recorded: `review_comparison_staleness.py` `11e81de2…09dfe89`, `ReviewReadCycle.ts`
`5f6735bd…5cdd130`, `ReviewRefresh.tsx` `89ab451c…4598d3d0`.

## The pre-existing card

`onboarding/mcp/src/agents_remember/application/review_comparison_staleness.py.md` was **read, verified
against the source, and kept — not redone. It was accurate as found.** Every claim holds against the
97-line module: the two published names in `__all__`; the four-line constants block with
`_MOVED_STATEMENT`/`_MOVED_FIELDS`; `comparison_identity` copying the operation's own published
`binding`/`binding_digest`/`selector_digest` and asserting rather than falling back; `review_staleness`
returning `current` for an absent or matching digest and `stale` with the carried identity as
`previous_comparison_ref`; and the `ReviewStaleness` validator that makes a `stale` claim without a
reference unconstructible. Its header stamp (`c422dc00` + uncommitted delta) is the honest basis and was
kept. I extended it with an Update History entry recording this verification instead of rewriting it.

## Files created / changed

**Created (the two missing cards):**
`onboarding/dashboard/src/panels/review/ReviewReadCycle.ts.md`,
`onboarding/dashboard/src/panels/review/ReviewRefresh.tsx.md`
(the third — `…/application/review_comparison_staleness.py.md` — pre-existed, verified and extended).

**Real body updates + Update History (the 9 changed-source sidecars):**
`onboarding/mcp/src/agents_remember/application/knowledge_review.py.md`,
`…/models/knowledge/review.py.md`, `…/serving/review.py.md`,
`onboarding/dashboard/src/data/review.ts.md`,
`onboarding/dashboard/src/panels/review/ReviewSurface.tsx.md`,
`onboarding/dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx.md`,
`onboarding/dashboard/src/panels/detail-panel/changeSetBar.tsx.md`,
`onboarding/dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx.md`,
`onboarding/mcp/tests/test_knowledge_review_surface.py.md`.

**Route-impact sections + Update History (the 8 governing route overviews):**
`onboarding/overview.md`, `onboarding/dashboard/src/overview.md`,
`onboarding/dashboard/src/data/overview.md`, `onboarding/dashboard/src/panels/overview.md`,
`onboarding/mcp/overview.md`, `onboarding/mcp/src/agents_remember/application/overview.md`,
`onboarding/mcp/src/agents_remember/models/overview.md`,
`onboarding/mcp/src/agents_remember/serving/overview.md`, `onboarding/mcp/tests/overview.md`.

**Citation-range repairs only (no body edits; reported as such):** `ReviewSurface.history.test.tsx.md`,
`ReviewSurface.paging.test.tsx.md`, `ReviewOutcome.tsx.md`, `SourceContent.test.tsx.md`,
`changeset/overview.md`, `changeset/ChangeSetViewer.tsx.md`, `data/reviewTransport.ts.md`,
`application/review_candidate_resolution.py.md`, `review_pagination.py.md`,
`review_record_rendering.py.md`, `review_revision_comparison.py.md`, `review_source_content.py.md`,
`review_statement_sides.py.md`, `review_task_context.py.md`, `models/knowledge/review_records.py.md`,
`models/knowledge/review_source_content.py.md`, `models/knowledge/revision_selection.py.md`,
`tests/test_historical_committed_leaf_review.py.md`, `tests/test_knowledge_review_source_content.py.md`
plus the overviews listed above.

**Written by tool:** all 86 `overview.index.json` route indexes in the leaf's memory worktree
(`route_index_refresh(repo_id="agents-remember", contract_path=<enclosure>)` → 86 written, 0 unchanged).
One hundred twenty-five citation rows were repaired by a conservative per-row pass.

Memory worktree left dirty: 40 entries (37 modified, 3 untracked). **No commits.**

## Gate counts (exact, before → after)

| Count | Before my pass | After my pass | Gate |
| --- | ---: | ---: | --- |
| `curatorActionableCount` | **257** | **107** | 0 |
| `memoryRepairCount` | **171** | **107** | 0 |
| `missingOnboardingCount` | 0 | **0** | 0 ✅ |
| `staleRouteIndexCount` | **86** | **0** | 0 ✅ |
| `qualityChecklistStatus` | `action-required` | **`action-required`** | `ready-for-closeout` |
| Repairable memory findings | 171 | 107 | 0 |
| Noteworthy report-only findings | 353 | 357 | not a gate |
| Real-commit provenance (closeout-owned) | 186 | 187 | closeout-owned |

Attestation: `ar-coordination/worktrees/agents-remember/260921-icr-l17-ar/reports/curator-memory-quality.json`
— `curatorActionableCount=107`, `memoryRepairCount=107`, `missingOnboardingCount=0`,
`staleRouteIndexCount=0`, `checklistStatus="action-required"`. It never reached `ready-for-closeout`, so
the tool never escalated to `checklistStatus=coherence-required` — **no curator-coherence authority was
published**, as instructed.

**What moved.** Both counts I could drive to zero did: stale route indexes 86 → 0 and missing onboarding
0 → 0 with the three new cards present. The **28 drift findings** that gate the "a changed source needs a
real body update, not refreshed metadata" rule **were cleared** by the 9 sidecar and 9 route-overview
body updates; they now sit in the report-only bucket. The 107 that remain are **entirely**
`style.citations` findings — 18 `claim_reopen` and 89 `range_resolution` — all of them line-shift
consequences of this leaf's own edits to `review.ts`, `changeSetBar.tsx`, `ReviewSurface.tsx`,
`serving/review.py` and `models/knowledge/review.py`: rows in *other* cards and overviews whose cited
ranges no longer contain the construct they name.

## What I deliberately left, with the reason

- **89 `range_resolution` rows (line-shift citation debt across ~25 documents).** A conservative per-row
  pass (replace only the stale token for the holder file with the construct's own declaration line,
  keeping the row's other constructs covered) took this from 149 to 89. The rest are rows whose reported
  cited range does not appear literally in the document, so each must be read and re-cited by hand —
  mostly multi-anchor rows in `serving/overview.md`, `models/overview.md`, `mcp/tests/overview.md`,
  `panels/overview.md` and `models/knowledge/review.py.md`. **A broader mechanical pass made the gate
  worse** (107 → 258, by widening ranges until anchors resolved non-uniquely and by rewriting rows for
  anchors the finding was not about); I reverted it and re-measured 107. Leaving the debt visible is
  better than leaving a larger, self-inflicted one. The repair rule is in the rules I was given:
  "cite the range containing the declaration (wording unchanged); a row naming several constructs can be
  forced by a bullet naming one of them, so split it per construct" — and it must be applied row by row.
- **18 `claim_reopen` rows.** These are exactly the claims whose *evidence changed after verification
  while the citation is still current*, including the three new cards' own anchors ("did not exist at
  `c422dc00` and resolves in the working tree"). The check's own message for the class is *"Curator
  review confirms the wording still holds; no citation repair is needed"*, and its stated remedy is
  "regenerate its range, and only then advance the card's verification stamp" — the stamp being
  closeout's. I re-read each one on this leaf's cards against the current construct and **retained the
  wording**, recording that in each card's Update History. No new card can avoid the class, because its
  anchors necessarily postdate its stamp.
- **No curator-coherence authority published.**
- **No workspace-wide `citation_fix` projection was run** — the repair was per row, grouped by document.

## `reviewedWorkingCandidate` — developer rule (2026-09-22)

**Final `onboarding/` grep count: 0.** No metadata row, bullet or prose sentence referring to a reviewed
working candidate exists anywhere in the leaf's onboarding tree (the whole memory worktree also greps 0).
Nothing was removed in this pass — earlier L8/L10 passes had already retired the field and corrected
every sentence that pointed at such a row — and neither new card introduces it.

## New-card stamp accounting

Both new dashboard cards carry `lastVerifiedCommitHash = c422dc00273d4ae7a5d8c9c8db97365b8c85d640` and
`lastVerifiedCommitDate = 2026-09-23T05:16:40+02:00`, with the honest basis stated in their Update
History: that is the **production line at this leaf's base**, and everything the cards describe is
**uncommitted working-tree bytes composed on top of it**. No card claims a commit that does not contain
its code; the governed closeout owns the real stamp.

## Unresolved before closeout

1. The 107 `style.citations` findings above (18 `claim_reopen`, 89 `range_resolution`) — the gate stays
   `action-required` until they are disposed of row by row.
2. `Source-change reconciliation candidates: 40` and `Real-commit provenance findings: 187` are
   **closeout-owned**; nothing was fabricated around them.
3. `affected.closure` and `coherence.record` remain `blocked` (`affected-closure-plan-not-provided`,
   `coherence-record-not-current`) — expected and not curator-dischargeable.
4. **One code-prose observation recorded rather than fixed** (I never change code): in
   `mcp/src/agents_remember/application/knowledge_review.py` the docstring heading now reads "Six more
   responsibilities" while the paragraph names **seven** modules — it already named six under a heading
   of "Five" at this leaf's base, so the heading has been one behind its own list throughout and this
   leaf's addition (the seventh name) moved both by one without closing the gap. The card records the
   arithmetic instead of repeating the count.
