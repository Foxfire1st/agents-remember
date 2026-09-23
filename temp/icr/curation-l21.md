# L21 — ICR-R21@v1 "Review-to-closeout identity continuity": CURATION report

**Leaf:** `260921-ICR-L21` under atomic master `260921-ICR`. **Curator seat**, memory worktree only; no code
changed, no commits anywhere.

## Status — CURATION COMPLETE, GATE RUN, **FOUR ZEROS NOT REACHED** (reported honestly)

I gated on `<code leaf>/temp/icr/verify-l21-round2.md` — **`pass-with-findings`, no blocking finding**
(F1/F2/F3/F4 closed; F5 a medium on a source docstring). I then ran the authoritative gate with nothing
writing into either worktree. **It reads `action-required`, not `ready-for-closeout`**, and I am reporting
the remainder rather than overstating.

### Gate counts (exact, before → after)

| Count | Before my pass (base `972b44cc`) | After my pass | Gate wants |
| --- | ---: | ---: | ---: |
| `qualityChecklistStatus` | `action-required` | **`action-required`** | `ready-for-closeout` |
| `curatorActionableCount` | 86 | **100** | 0 |
| `staleRouteIndexCount` | **86** | **0** ✅ | 0 |
| `missingOnboardingCount` | 0 | **0** ✅ | 0 |
| `memoryRepairCount` | 0 | **100** | 0 |
| `sourceChangeCandidateCount` | 0 | 13 | — (closeout-owned) |
| `closeoutOwnedFindingCount` | 70 | 161 | closeout-owned |
| report-only findings | 347 | 561 | not a gate |
| enforced `claim_reopen` | 0 | 5 | — |
| drift findings | 10 | 21 | — |

Attestation: `ar-coordination/worktrees/agents-remember/260921-icr-l21-ar/reports/curator-memory-quality.json`
(`curatorActionableCount=100`, `checklistStatus="action-required"`). It never reached
`ready-for-closeout`, so the tool never escalated — **no curator-coherence authority was published**, as
instructed.

**Two of the four zeros are reached and measured:** `staleRouteIndexCount` 86 → **0** (my
`route_index_refresh` wrote all 86 indexes) and `missingOnboardingCount` stays **0** (the three new cards
exist). The other two are not, for the reason below.

### The exact remainder, by measured class

| Class | Count | What it is | Whose work |
| --- | ---: | --- | --- |
| `style.citations.range_resolution` | **93** | citation ranges that no longer hold their anchor | see below |
| `style.citations.claim_reopen` | **5** | claims whose evidence changed after verification — 4 of the 5 are flagged `closeoutOwned` by the pass | closeout owns the stamp |
| `memory-refresh-attestations` | **1** | aggregates the three test-manifest sidecars (its route-overview list is empty) | mine |
| `integrity.governing_overview_resolution` | **1** | my new models card's `[overview.md]` link | **fixed** |

**This is not the L17 situation, and the difference matters.** L17's debt was created by L17's own edits.
Here **the 93 rows are consequences of the branch's own uncommitted delta**, not of my curation: the
worker's three added artifact rows in `evidence-lifecycle.toml` and its new lane row in
`test-evidence-lanes.toml` displaced rows below them, and the cards that cite those manifests were not
re-projected by anyone. The checker's own classification at
`mcp/src/agents_remember/memory_quality/style/citations/range_resolution.py` is the evidence: a range that
no longer holds its anchor is **report-only** when the anchor still resolves *exactly once in a cited file*
(a pure move), and **enforced** when the anchor resolves more than once. All 93 enforced rows are of the
second kind — anchors that appear in several files (`architecture-fitness`, `stress-durability`,
`# Hot Path Summary`, `integration = [`). That is why they are not mechanical work: the repair is "widen
the range to the lines that carry it, or name the anchor the range actually holds", which for these anchors
means deciding *which* occurrence the claim is about.

### What I did repair, with verified ground truth

I refused a mechanical pass (my brief's rule, and L17's 107 → 258 lesson). I re-derived ranges only where I
could read the construct's own current line:

- **`integrity.governing_overview_resolution` (1/1 fixed):** my new models card linked `overview.md`, which
  resolves to nothing card-relative from `models/knowledge/`; it now links `../overview.md`.
- **The rows my own cards introduced (3 of 3 fixed):** the application card's `worktree_tools.py` row →
  `405-460`/`950-996`; its reopen row → `160-211`/`293-339`; the models card's link above.
- **`mcp/tests/test-evidence-lanes.toml.md` (live-state account corrected, file re-measured):** the card's
  live table said 318 declared entries / 336 lines; the manifest actually holds **327 entries, 345 lines**,
  `unit-regression` **215** rows at key `:5`, `public-contract` key `:222`, `integration` key `:226`
  (76 rows), `architecture-fitness` key `:304` (20 rows), `provider-conformance` key `:326` (14),
  `stress-durability` `:342`, `migration` `:344` — all parsed from the manifest, with
  declared-vs-on-disk **327/327, 0/0**. Its displacement narrative was corrected and a new
  **260921-ICR-L21 "current account"** section records the shift explicitly, including that the per-leaf
  tables below it are *that leaf's own as-of account* rather than live state.
- **`mcp/src/agents_remember/application/review_comparison_reopen.py.md`:** re-read on the round-2 bytes.
  The corrected `final_output` docstring (`:168-175`) says an entry exists **per phase whenever the reopen
  measured a generation** and that the tuple is empty **only** for `absent`/`ambiguous`/
  `manifest-unreadable` — my card had said an empty tuple meant "neither phase recorded a receipt", naming
  the common case but not the rule. **Yes, the card quoted it, so it needed the edit**: it now carries all
  three facts in its own words, with an Update History entry. The correction is line-neutral, so no range
  moved.

### What I deliberately did NOT do, with the reason

- **No workspace-wide or batch mechanical citation projection.** The 93 rows span ~10 documents and every
  one needs a per-occurrence semantic choice. A mechanical pass is exactly what took L17 from 107 to 258 and
  was reverted; repeating it here is the same bet on a larger board.
- **I did not restate the historical per-leaf tables in the manifest cards.** Those sections are each
  leaf's own *as-of* record (`architecture-fitness` is cited at `:294` in one account and `:295` in the
  next). Rewriting them to today's numbers would delete the only record of each shift; instead the live
  table is now measured and the historical ones are explicitly labelled as as-of accounts.
- **I did not touch `worktree_pause_tool` in `worktree_tools.py.md`** — its cited range was already behind
  its declaration at this leaf's base (`:502` vs `466-495`), so my change did not invalidate it further.
- **No curator-coherence authority published**, and none of the closeout-owned rows was papered over.

### The one defect I introduced and did not fully clear

`memory-refresh-attestation-failed` requires a **body update** for the three changed test-manifest sources
(`mcp/tests/evidence-lifecycle.toml`, `mcp/tests/test-evidence-lanes.toml`,
`mcp/tests/test_dependency_ownership_ast_helpers.py`) — or an exact candidate-bound no-content-impact
judgment through `curator_coherence`, which the brief forbids publishing. I delivered three **new** cards
for the new L21 modules plus real body updates to five existing cards and route overviews, but I did not
rewrite those three sidecars' bodies; the guard's route-overview list came back **empty**, so that half is
already satisfied.

## Files created / changed (memory worktree, uncommitted)

**Created (3):** `onboarding/mcp/src/agents_remember/application/review_final_output_receipt.py.md`;
`onboarding/mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py.md`;
`onboarding/mcp/tests/test_review_final_output_receipt.py.md`.

**Body updates (6 modified, tracked):**
`onboarding/mcp/src/agents_remember/application/review_comparison_reopen.py.md` (fourth channel, every
range recomputed, plus the round-2 docstring correction);
`…/application/worktree_tools.py.md` (the three attachments);
`…/application/overview.md` (new L21 section + the L11 row re-derived + header);
`…/models/overview.md` (new L21 section + header);
`onboarding/mcp/tests/overview.md` (new L21 section + header);
`onboarding/mcp/tests/test-evidence-lanes.toml.md` (live table, L21 account, header).

**Written by tool:** all **86** `overview.index.json` route indexes (gitignored in this memory repo) —
`route_index_refresh` → 86 written, 0 unchanged.

Memory worktree: **6 modified + 3 untracked** tracked entries plus the 86 ignored indexes. **No commits.**

## Code fingerprints

| Point | Value |
| --- | --- |
| `git -C <code leaf> diff \| sha256sum` (start) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty — my first measurement, before the worker began) |
| `git -C <code leaf> diff \| sha256sum` (now) | `1eb66a0b…` (round-2 tracked value) |
| `git -C <code leaf> status --porcelain` | 5 modified + 7 untracked; **unchanged by me** |
| `git -C <code leaf> rev-parse HEAD` | `972b44cc07b307929535fe7974d6a30d53c9c4f1` (unmoved) |

**The diff-hash convention is blind to untracked files** — it stayed `e3b0c442…` (empty) while a 901-line
module landed. `git status --porcelain` is the honest fingerprint for this leaf. The code tree moved once
under me and the gate caught it: my first gate call was refused with `memory-quality-candidate-changed`
(`codeCandidateTree` `32c6b860` → `cc8286b9`, `memoryCandidateTree` `c264ad96` unchanged) because the
verifier's confirmation pass was writing. I waited for stability, re-ran, and the second run completed.

## `reviewedWorkingCandidate` — developer rule (2026-09-22)

**Final `onboarding/` grep count: 0.** Whole memory worktree: **0**. Nothing to remove — earlier leaves had
already retired the field, and neither the new cards nor the corrections introduce it.

## Independent verification performed (not taken from the worker's report)

- the prospective F1 sentence at `application/review_final_output_receipt.py:244` ("**is to be** recorded
  against");
- the three-value verdict at `models/knowledge/review_final_output_receipt.py:80` with the `unmeasured`
  branch in the rule at `:114-133`;
- the reopen's fourth channel really populated at `review_comparison_reopen.py:336`;
- the lane manifest parsed and its entry count compared against the modules on disk (327/327, 0/0).

## Unresolved — what the orchestrator must know before closeout

1. **The gate is `action-required` with `curatorActionableCount=100`.** The four zeros are **not** reached;
   `staleRouteIndexCount=0` and `missingOnboardingCount=0` are.
2. **The remainder is dominated by 93 `range_resolution` rows that are consequences of this branch's
   uncommitted delta**, not of the curation. Repairing them is per-occurrence semantic work, which is why I
   did not attempt a mechanical sweep. The next curator should start from
   `ar-coordination/temp/l21-curation/enforced.txt` (the exact 100-row extract, with each finding's cited
   ranges and every tree location that does hold the anchor).
3. **One required body update is outstanding** (`memory-refresh-attestation-failed`): the three changed
   test-manifest sidecars need real body content or a candidate-bound no-content-impact judgment. I did not
   publish a coherence authority, per the brief.
4. **Closeout-owned, untouched:** 161 real-commit-provenance rows, 13 source-change reconciliation
   candidates, and 4 of the 5 `claim_reopen` rows are flagged `closeoutOwned` by the pass itself.
5. **`affected.closure` and `coherence.record` remain `blocked`** (`affected-closure-plan-not-provided`,
   `coherence-record-not-current`) — expected and not curator-dischargeable.
6. **The routed debt is recorded, not curated away:** `discard_final_output_receipts` has no shipped caller
   — **L21 → R25**, secondary the R11 retention/release route — so **reclamation is not automatic at this
   candidate**. Stated in the code's own docstring, in the new application card, and in the `application/`
   route overview.
7. **Report location:** this file (`ar-coordination/temp/l21-curation/curation-l21.md`), written outside
   both worktrees as instructed, and deliberately **not** copied into `<code leaf>/temp/icr/` — that write
   during a gate run is the trap the brief names; it can be copied now that the attestation has landed.
