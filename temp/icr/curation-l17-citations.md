# L17 — ICR-R17@v1 "Coherent live refresh": CITATION-REPAIR report

**Leaf:** `260921-ICR-L17` under atomic master `260921-ICR`. **Curator seat**, memory worktree only;
**no code changed, no commits anywhere.**

**Scope of this pass:** exactly the 107 `style.citations` findings that blocked closeout. The prior
curator's functional curation (`temp/icr/curation-l17.md`) was left intact; nothing else was touched.

## 1. Starting counts (verified, not assumed)

`reports/curator-memory-quality.json` at hand-over:

| Count | Value |
| --- | ---: |
| `checklistStatus` | `action-required` |
| `curatorActionableCount` | **107** |
| `memoryRepairCount` | **107** |
| `missingOnboardingCount` | 0 |
| `staleRouteIndexCount` | 0 |
| `sourceChangeCandidateCount` | 40 |

The 107 = **18 `style.citations.claim_reopen`** + **89 `style.citations.range_resolution`**
(85 `citation_anchor_absent_from_range`, 3 `citation_range_out_of_bounds`, 1 `citation_anchor_missing`),
spread over **34 onboarding documents**.

## 2. Method — and why the previous mechanical pass failed

### 2.1 The clearing rules were read from the checker's own source, not from folklore

* `claim_reopen._anchor_in_cited_range` (`mcp/src/agents_remember/memory_quality/style/citations/claim_reopen.py:545`):
  a changed claim is ENFORCED only when the anchor's **declaration line** falls outside *every* range the
  row cites; `surfaced` (severity `warning`) findings are moved to `surfacedFindings` by
  `_gate_result` and are **not** counted. So citing the range containing the declaration clears it, and
  *"Clearing needs no commit"* (its own docstring).
* `claim_reopen.surfaced_finding` / `generated_repair_bullets` (`:311`, `:364`): if a document's Update
  History contains a bullet that both says `Generated citation repair` and names this claim's anchors
  *and* contains ` repointed to `, the finding is forced back to `error` regardless of currency. **This is
  the mechanism that took the earlier mechanical pass from 107 to 258** — it wrote projection bullets into
  documents, converting every surfaced warning in them into an enforced error. No such bullet was written
  by this pass; the tree's pre-existing count of that string is **unchanged at 5232**.
* `range_resolution.absent_findings` (`:362`): the enforced variant fires only when `moved_extent` returns
  `None` (the anchor resolves 0 or ≥2 times as an extent in the cited files). The remedy in its own
  module docstring is: *"Repair findings in the memory document by correcting the explicit source/range
  **or anchor**."*
* Counting: `application/memory_quality/controller.py` feeds every non-drift `findings` row into
  `repair_findings` with **no severity filter**, and `_gate_result` is what separates `warning`
  (surfaced) from enforced — so the only safe direction of travel is *finding removed*, never
  *severity lowered*.

### 2.2 A per-row work order, not a projection

The CLI `citation_fix` / `citation_migrate` projections were **not** run, workspace-wide or otherwise.

Instead the two checks were re-run directly against this leaf's worktrees with the server's own
interpreter and package root (`/home/firefox/projects/agents-remember/.venv/bin/python`, serving
`packageRoot=/home/firefox/projects/agents-remember/mcp/src/agents_remember`, `commit=e432a88b`,
i.e. `3.0.0rc8`), reproducing **107** exactly before any edit. That gave every finding with its
**line number**, and — using the checker's own `range_resolution.unsatisfied`, `Sources.body`,
`elsewhere_in_file` and `extents.FileView` — for each row: its parsed anchors, its cited ranges, which
ranges held which anchor, and every anchor's **true declaration/occurrence lines**.

Extract for the record: `260921-icr-l17/temp/icr/l17-citations.tsv` — 107 rows, one per finding:
`document, kind, code, anchor, cited_ranges, carried_at`.

**Every cited source file was then read before its range was changed**, and the base-versus-working-tree
occurrence lines were compared with `git show c422dc00:<file>` to *confirm intent* (a range that is
stale by a known insertion is a shift; a range that never held its construct is a mis-citation). No range
was guessed, and no range was pointed at a construct the row is not about.

### 2.3 Grouping and checkpointing

Repaired document-group by document-group, re-measuring after each group. **The count never rose at any
checkpoint** — no group had to be reverted.

## 3. Per-group counts

| # | Group (documents) | Rows in group | Count after group |
| ---: | --- | ---: | ---: |
| — | start | — | **107** |
| 1 | `dashboard/src/data/{overview,review.ts,reviewTransport.ts}.md`, `dashboard/src/overview.md`, `panels/changeset/{overview,ChangeSetViewer.tsx}.md`, `panels/detail-panel/{changeSetBar.tsx,reviewEntryRefusal.test.tsx}.md` | 28 | **80** |
| 2 | `dashboard/src/panels/overview.md`, `panels/review/{ReviewOutcome.tsx,ReviewReadCycle.ts,ReviewRefresh.tsx,ReviewSurface.history.test.tsx,ReviewSurface.paging.test.tsx,ReviewSurface.tsx}.md` | 28 | **52** |
| 3 | `mcp/overview.md`, `mcp/src/agents_remember/serving/{overview.md,review.py.md}` | 14 | **43** |
| 4 | `mcp/src/agents_remember/models/{overview.md,knowledge/review.py.md,knowledge/review_records.py.md,knowledge/review_source_content.py.md}` | 15 | **28** |
| 5 | `mcp/src/agents_remember/application/{overview.md,review_candidate_resolution,review_comparison_staleness,review_pagination,review_record_rendering,review_revision_comparison,review_source_content,review_statement_sides,review_task_context}*.md` | 19 | **9** |
| 6 | `mcp/tests/{overview.md,test_knowledge_review_source_content.py.md,test_knowledge_review_surface.py.md}` | 6 | **5** |
| 7 | the five rows whose *sibling* anchor the group-4 edits had displaced (`review.py.md:314/315/325`, `models/overview.md:21/2698`) | 5 | **0** |

Group 7 is an honest note: my group-4 edit to `review.py.md:314` replaced the range `583-601`, which was
also the only range holding the row's *other* anchor `ReviewEvidenceLink`; the same happened at `:315`
(`ReviewReviewRemainingCount`), `:325` and `models/overview.md:21` (`KnowledgeReviewResult`) and
`:2698` (`test_an_inventory_that_could_not_carry_a_name_is_partial_by_construction`). The checkpoint
caught all five, and each was closed by citing the displaced anchor's own verified extent
(`593-604`, `645-665`, `1119-1134`, `1119-1134`, `1357-1357`).

## 4. Documents changed, with row counts

**34 documents, 107 rows.** All 34 were already dirty in the leaf's memory worktree; no file was newly
modified by this pass, and the worktree's dirty count is unchanged at **40** (37 modified, 3 untracked).

| Rows | reopen | range | Document |
| ---: | ---: | ---: | --- |
| 9 | 3 | 6 | `dashboard/src/panels/overview.md` |
| 8 | 0 | 8 | `mcp/src/agents_remember/models/knowledge/review.py.md` |
| 8 | 0 | 8 | `mcp/src/agents_remember/serving/overview.md` |
| 6 | 1 | 5 | `dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx.md` |
| 6 | 0 | 6 | `dashboard/src/panels/review/ReviewSurface.history.test.tsx.md` |
| 5 | 1 | 4 | `dashboard/src/data/overview.md` |
| 5 | 1 | 4 | `dashboard/src/panels/review/ReviewSurface.tsx.md` |
| 5 | 0 | 5 | `mcp/src/agents_remember/serving/review.py.md` |
| 4 | 2 | 2 | `dashboard/src/panels/detail-panel/changeSetBar.tsx.md` |
| 4 | 1 | 3 | `mcp/src/agents_remember/application/review_task_context.py.md` |
| 4 | 0 | 4 | `mcp/src/agents_remember/models/overview.md` |
| 3 | 2 | 1 | `dashboard/src/data/review.ts.md` |
| 3 | 1 | 2 | `dashboard/src/data/reviewTransport.ts.md` |
| 3 | 1 | 2 | `dashboard/src/overview.md` |
| 3 | 1 | 2 | `dashboard/src/panels/changeset/overview.md` |
| 3 | 2 | 1 | `dashboard/src/panels/review/ReviewRefresh.tsx.md` |
| 3 | 0 | 3 | `dashboard/src/panels/review/ReviewSurface.paging.test.tsx.md` |
| 3 | 0 | 3 | `mcp/tests/test_knowledge_review_source_content.py.md` |
| 2 | 0 | 2 | `dashboard/src/panels/review/ReviewOutcome.tsx.md` |
| 2 | 0 | 2 | `mcp/src/agents_remember/application/overview.md` |
| 2 | 0 | 2 | `mcp/src/agents_remember/application/review_candidate_resolution.py.md` |
| 2 | 0 | 2 | `mcp/src/agents_remember/application/review_statement_sides.py.md` |
| 2 | 0 | 2 | `mcp/src/agents_remember/models/knowledge/review_records.py.md` |
| 2 | 0 | 2 | `mcp/tests/overview.md` |
| 1 | 0 | 1 | `dashboard/src/panels/changeset/ChangeSetViewer.tsx.md` |
| 1 | 1 | 0 | `dashboard/src/panels/review/ReviewReadCycle.ts.md` |
| 1 | 0 | 1 | `mcp/overview.md` |
| 1 | 1 | 0 | `mcp/src/agents_remember/application/review_comparison_staleness.py.md` |
| 1 | 0 | 1 | `mcp/src/agents_remember/application/review_pagination.py.md` |
| 1 | 0 | 1 | `mcp/src/agents_remember/application/review_record_rendering.py.md` |
| 1 | 0 | 1 | `mcp/src/agents_remember/application/review_revision_comparison.py.md` |
| 1 | 0 | 1 | `mcp/src/agents_remember/application/review_source_content.py.md` |
| 1 | 0 | 1 | `mcp/src/agents_remember/models/knowledge/review_source_content.py.md` |
| 1 | 0 | 1 | `mcp/tests/test_knowledge_review_surface.py.md` |

### 4.1 Shapes of repair actually used

1. **Line-shift correction** (the large majority). A range that was stale by this leaf's own insertions
   (e.g. `data/review.ts:605-605` for `ReviewEntry`, whose declaration moved 568→607) was rewritten to the
   construct's current declaration/extent, read from the file:
   `review.ts:470-486` (`intentReview`), `:636-642` (`intentReviewEntries`), `:654-672`
   (`reviewSourceContent`), `:607-612` (`ReviewEntry`), `:578-580` (`carriedPage`), `:591-595`
   (`RESET_GLOSS`), `:537-543` (`continuationOf`); `changeSetBar.tsx:235-282` (`useReviewCatalogue`),
   `:398-474` (`LeafEntries`), `:483-532` (`DocChangeSetBar`), `:33-97` (`ChangeSetButton`),
   `:289-327` (`ReviewEntryState`), `:102-121` (`ReviewCatalogueRead`); `ReviewSurface.tsx:858-903`
   (`ReviewHeader`), `:588-589` (`retryFor`), `:905-986` (the component; the old `935-1031` was the
   out-of-bounds range), `:949-949`; `ReviewRefresh.tsx:48-84`/`:103-117`; `serving/review.py:145-148`
   (`_AUTHORITY_NEXT_ACTION`), `:106`/`:108`/`:109`/`:110` (the selector constant and the three ports),
   `:627-709`, `:607-624`, `:644-655`, `:657-659`, `:720-738`, `:741-757`, `:199-216`, `:294-327`;
   `models/knowledge/review.py:389-401`, `:507-529`, `:532-548`, `:593-604`, `:607-625`, `:635-642`,
   `:645-665`, `:760-767`, `:985-1010`, `:1030`, `:1100-1116`, `:1119-1134`, `:1160-1162`.
2. **Per-construct re-citation instead of a wide range.** Where a row named several constructs and only
   one range was stale, the stale token was replaced by *that* construct's own lines, never widened to
   swallow both — e.g. `data/overview.md:669` cites `review.ts:636-642; 470-486; 654-672` (one range per
   named read) rather than one `403-672` span.
3. **One anchor correction** (see §6.1) and **one anchor re-spelling** (see §6.2). Everything else kept
   the claim's wording byte-for-byte and used the existing range token, and in several rows the ranges
   were merely re-ordered into ascending order for readability.

## 5. Final counts and `qualityChecklistStatus`

Local reproduction of the two citation checks after the pass (same interpreter, package root,
worktrees and `contract_path` as the gate):

| Check | findingCount | reportOnly | ok |
| --- | ---: | ---: | --- |
| `style.citations.range_resolution` | **0** | 347 | true |
| `style.citations.claim_reopen` | **0** | 0 | true |
| **total** | **0** | | |

**Authoritative gate (`memory_quality_check`, `mode:"start"` + `mode:"poll"`, runId
`973efe8e9fed401d`, same `contract_path`):** see §5.1.

### 5.1 Authoritative attestation — **GATE REACHED**

`ar-coordination/worktrees/agents-remember/260921-icr-l17-ar/reports/curator-memory-quality.json`
(runId `0bed601aa8c44c0a`, `mode:"start"` + `mode:"poll"`, `scopeAuthority:"leaf-candidate"`):

| Count | Before | **After** | Gate | Reached? |
| --- | ---: | ---: | ---: | --- |
| `curatorActionableCount` | 107 | **0** | 0 | ✅ |
| `memoryRepairCount` | 107 | **0** | 0 | ✅ |
| `missingOnboardingCount` | 0 | **0** | 0 | ✅ |
| `staleRouteIndexCount` | 0 | **0** | 0 | ✅ |
| `sourceChangeCandidateCount` | 40 | 40 | not a gate | closeout-owned |
| `checklistStatus` | `action-required` | **`ready-for-closeout`** | `ready-for-closeout` | ✅ |

**The tool did not escalate to `checklistStatus=coherence-required`.** The resting state is the plain
green `ready-for-closeout`, so no curator-coherence authority was published — as instructed, and there
was nothing to escalate.

### 5.2 One mechanism finding worth handing on: the gate refuses if *either* worktree moves while it runs

The first authoritative run of this pass (runId `973efe8e9fed401d`) ended
`status:"scope-refused"`, `pairStatus:"memory-quality-candidate-changed"`,
`pairField:"candidateTrees"`:

```
expected codeCandidateTree f871e766fbc47e0575441389f0e981c5d441a546
observed codeCandidateTree 48ab58807125697d789ef0fe1cbd13672d018fdc
memoryCandidateTree unchanged (0ee6d7f8358e26ee869a3b8067f384abe3958a95)
```

The cause was **this seat's own report write**, and it is structural rather than a slip:

* `application/memory_quality/controller.py::_curator_candidate_inputs` derives `codeCandidateTree`
  through `worktrees/modules/git.py::worktree_candidate_tree`, which seeds a private index from `HEAD`
  and then runs `git add -A` + `write-tree`.
* `git add -A` includes **untracked** files. In this code worktree `temp/` is untracked and **not
  gitignored** (`git status --porcelain` ends `?? temp/`).
* Writing `temp/icr/curation-l17-citations.md` and `temp/icr/l17-citations.tsv` therefore changed the
  code candidate tree, and `_require_same_curator_candidate` refuses a run in which the pair moves at
  any point between start and finish.

**Operational consequence:** start a curator gate run only after the *last* write to either worktree and
write nothing into either one while it runs. The previous curator's own clean run at 07:08 was clean for
exactly this reason — `temp/icr/curation-l17.md` was written after it. This seat re-ran the gate after
its final write; the run recorded in §5.1 is that re-run, and its attestation therefore matches the
final trees exactly.

Nothing about this is a defect this seat may repair (it is tooling behaviour, and this seat never
changes code); it is recorded here because the next seat that runs the gate will otherwise lose a
~20-minute run to it.

## 6. Rows that needed something other than a range correction

### 6.1 One anchor correction — the construct was deleted, not moved

`dashboard/src/panels/review/ReviewSurface.tsx.md:290` carried the claim *"The one load path: three
separate outcome states, a refusal and a payload that can never be on screen together, and no submit
handler anywhere."* anchored on `` `load` `` and citing `ReviewSurface.tsx:959-982`.

`load` **does not exist anywhere in the working tree**. This leaf's own extraction deleted the inline
`const load = useCallback(...)` (base `ReviewSurface.tsx:959-983`) and moved the read cycle into
`ReviewReadCycle.ts` (`useReviewReadCycle` → `startRead`). Both remedies the checker offers were
unavailable honestly: there is no range in `ReviewSurface.tsx` that "carries it", and the only anchors
the old range still holds are `loading` and `payload`, which the claim is **not** about — pointing at
them would be exactly the mis-citation the brief forbids. `range_resolution`'s own module docstring
sanctions the third option: *"correcting the explicit source/range **or anchor**."*

So the row's Anchor cell now names `` `useReviewReadCycle` `` — the construct that *is* the one load path
in this module — citing the verified call site `ReviewSurface.tsx:922-934` (the comment plus the call).
**The claim's wording is unchanged.** (`intentReview`, the row's other anchor, keeps `review.ts:462-490`.)

### 6.2 One anchor re-spelling — the anchor was not written in the checker's grammar

`dashboard/src/panels/review/ReviewRefresh.tsx.md:168` named `` `review-stale` ``. A hyphenated
backticked span is neither identifier-shaped nor `#`-prefixed, so it is not an anchor at all
(`range_resolution.ANCHOR_GRAMMAR`) — hence `citation_anchor_missing` rather than a range finding. It was
re-spelled as the double-quoted literal `"review-stale"`, and verified to occur at
`ReviewSurface.tsx:553` inside the row's already-cited `548-572`.

### 6.3 No stamp was advanced, and why none needed to be

The brief allowed advancing a card's stamp on an honest `c422dc00 + working-tree delta` basis "where the
claim is verified against the current bytes". **No stamp was advanced, because no row required it:**
`_anchor_in_cited_range` is the *only* currency test on this path and its own docstring states
*"Clearing needs no commit."* Every one of the 18 `claim_reopen` rows cleared by making a cited range
contain the changed construct's declaration line, and the local check confirms `claim_reopen` now reads
`ok: true` with zero enforced findings. Advancing stamps would have been an unforced change to
closeout-owned metadata (and the prior curator's Update History already records the honest basis for the
three new cards).

### 6.4 Nothing was deliberately left

**There is no row left with a reason.** All 107 are cleared in the local reproduction; the enforced
`range_resolution` and `claim_reopen` counts are both 0 and both checks report `ok: true`.

## 7. Guard conditions

| Condition | Value |
| --- | --- |
| `reviewedWorkingCandidate` grep count over `onboarding/` | **0** (unchanged; whole worktree also 0) |
| `missingOnboardingCount` | 0 (unchanged) |
| `staleRouteIndexCount` | 0 (unchanged) |
| Memory worktree dirty entries | **40** (37 modified, 3 untracked) — same set as at hand-over |
| Memory `HEAD` | `e8f184660b0075c18e0d8218c3c7f8b4c1c35242` (unchanged — **no commits**) |
| Workspace-wide `citation_fix` / `citation_migrate` projection | **not run** |
| `Generated citation repair … repointed to …` bullets written | **0** (tree total unchanged at 5232) |
| Cards restructured | none — only Anchor/Source cells and, in one row, the anchor |
| Code worktree | untouched: `git diff \| sha256sum` = `0d44b92b8a0de8b8f580e1d4a7f6b22f909a47b3253da120fcdf63800881b088`, `HEAD` = `c422dc00` |

## 8. Code-prose inconsistency observed (recorded, NOT fixed — no code was changed)

`mcp/src/agents_remember/application/knowledge_review.py:31` reads:

> **Six more responsibilities this adapter hands to their own modules, for the same reason.**

while the paragraph it heads names **seven** modules —
`review_source_inventory`, `review_record_rendering`, `review_statement_sides`,
`review_revision_comparison`, `review_subject_catalogue`, `review_task_context` and (this leaf's
addition) `review_comparison_staleness`.

This is not a new defect and not this leaf's alone: at the leaf's base commit `c422dc00` the same heading
read **"Five more responsibilities"** while the paragraph already named **six**. This leaf appended the
seventh name and moved the heading `Five → Six` in step, so the heading has been **exactly one behind its
own list both before and after**. A secondary symptom of the same append: the list now carries two
coordinating `and`s — *"…composes the entry that needs no selected subject; **and** :mod:`…review_task_context` … ; **and** :mod:`…review_comparison_staleness` carries …"*.

Recorded for the closeout/reviewer seat; **not** repaired here, because this seat never changes code.

## 9. Status

**The gate is reached.** `curator-memory-quality.json` reads
`checklistStatus=ready-for-closeout` with `curatorActionableCount=memoryRepairCount=0` and
`missingOnboardingCount=staleRouteIndexCount=0` (§5.1). All 107 citation findings are cleared, both in
the checker's own reproduction (§5) and in the authoritative gate.

`checklistStatus` is the plain green `ready-for-closeout` — the tool did **not** escalate to
`coherence-required`, so there is no coherence authority to publish and none was published.

**The memory worktree is deliberately left dirty. Nothing was committed anywhere. No code was
changed.**
