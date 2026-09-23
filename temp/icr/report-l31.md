# ICR-L31 worker report — comparison-bound family review context (ICR-R31@v1)

Leaf `260921-ICR-L31` of atomic master `260921-ICR`. Code worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l31-ar/260921-icr-l31`
(branch `ar/260921-icr-l31`), base `4c000b11c5243e4a8e77c08e87984fff00c1d94b` (ICR-R23, L23).
Nothing was committed; the enclosure is left dirty for the orchestrator, and nothing was written into
the memory worktree. No dashboard file was touched (see §7).

## 1. What was built

The packet's Required Behavior 1–6 is implemented as one composition over the existing owners, plus
the typed values it publishes, plus the two seams the transport and the adapter needed.

**New: `mcp/src/agents_remember/models/knowledge/review_family_context.py`** (512 L) — the typed
family-context vocabulary, and the only place the sentences about it are decided:

| Line | Value | What it refuses |
| --- | --- | --- |
| `:114` | `ReviewFamilyGuarantee` | one family revision's own stored guarantee, version, provenance and seal — no field could hold a derived guarantee or a verdict |
| `:134` | `ReviewFamilyMemberSource` | a recorded realization claim as an address-observed reference; address and resolution travel together |
| `:166` | `ReviewFamilyMember` | one membership row + its exact member revision; `recorded` ⇔ the page carried that revision's content |
| `:225` | `ReviewFamilyRosterPage` | the read owner's own counts, its `enumeration_complete` and its cursor as one statement |
| `:271` | `ReviewFamilyRevisionContext` | a recorded side names its exact revision, its guarantee and its page; a non-recorded side carries no roster and counts none |
| `:323` | `ReviewFamilyContextEntry` | the entry's state, its `ICR-R07@v1` selection, its candidates and its two sides cannot disagree |
| `:410` | `ReviewFamilyContextReferences` | where the relationship union, the source inventory, the evidence and the assessments are owned, and the join key (`invariant_revision_id`) |
| `:432` | `ReviewFamilyContext` | the family counts and the two membership counts are checked against the entries they describe |

States are five distinct facts, never merged: `recorded`, `partial`, `no_family_recorded` (a measured
zero), `no_subject_selected` (the task-context review) and `unavailable` (no scope read at all);
per side: `recorded`, `not_recorded`, `not_resolved`, `unreadable`.

**New: `mcp/src/agents_remember/application/review_family_context.py`** (725 L) — the composition:
`:186` `review_family_context(sources, continuation=…)` → `FamilyContextOutcome(context, page,
refusal)`; `:398` `_applicable` (one shipped read per side with the reviewed identity's own seed →
the policy's frozen `directly_containing_families`, complete whatever page bound applied); `:433`
`_entry`; `:501` `_heads` (calls `ICR-R07@v1`'s own `revision_heads`); `:524` `_selection` (R07's own
value and vocabulary, `ambiguous`/`unresolved` carried with reasons); `:638` `_unresolved_entry`
(every head of either side as an inspectable candidate with its own guarantee); `:354` `_no_family`
(the measured zero, or the stated failure to measure it).

**New: `mcp/src/agents_remember/application/review_family_rosters.py`** (668 L) — the roster read
seam: `:145` `open_family_side`, `:187` `read_family_roster` (one `read_knowledge_scope` call per
selected family revision, at the request's page bound, with the request's cursor), `:314`
`_roster_page` (the owner's counts/cursor stated as the page), `:383` `family_guarantee` (the family
owner, seal verified), `:404` `_members` + `:456` `_member` (roster rows, member content,
`other_family_revision_ids` from the membership owner, movement references from R08's own values),
`:584` `family_member_page` (R10's page value for the family roster), `:609`
`family_collection_refusal`, `:630` `family_context_cursor_refusal`.

**Modified (thin, as the seam policy requires):**

| File | Line | Change |
| --- | --- | --- |
| `application/knowledge_review.py` | `:534` | one call to the composition, with the resolved pair, the reviewed selector and R08's movements |
| | `:555` | the family collection's own page/refusal become the payload's when the request named it |
| | `:589` | `family_context=family.context` on the payload |
| | `:109` | the one import the new module's refusal helper needs |
| `models/knowledge/review.py` | `:171` | `ReviewPagedCollection` gains `"family_members"` (the third bounded collection) |
| | `:1035` | `KnowledgeReviewPayload.family_context: ReviewFamilyContext` (required) |
| `application/review_task_context.py` | `:157` | the subjectless review states `no_subject_selected` |
| `mcp/tests/evidence-lifecycle.toml` | two rows | the census-derived consumers of the new test module |
| `mcp/tests/test-evidence-lanes.toml` | two rows | the new modules' lane rows |
| `mcp/tests/test_dependency_ownership_ast_helpers.py` | `:46` | `LIFECYCLE_CATALOG_SHA256` re-pinned, with the recorded reason |

## 2. Seam report (policy: `notes/03-adapter-seam.md`)

- **Responsibility moved out of the adapter:** the comparison-bound family-context composition. It was
  not in the adapter before; it is *placed* in `application/review_family_context.py` +
  `application/review_family_rosters.py` (purpose-named adjacent modules) rather than added to the
  1,073-line adapter, exactly as the packet's Scope asks ("a focused family-context projection beside
  those owners").
- **Adapter lines:** 1,073 at base → 1,112 now (+39: one import, one call block, the page selection,
  one payload field, and their comments). It was already over the 1,200/900 soft rail at base and is
  still under the 1,200 hard rail. No feature logic lives in it: it resolves, calls and assembles.
- **One implementation, one public entry:** `review_family_context` is the only composition; the
  roster read (`read_family_roster`) is the only roster read. Nothing is duplicated from
  `memory/knowledge/read.py`, `families.py`, `memberships.py`, `review_revision_comparison.py`
  (R07's `revision_heads` is *called*) or `review_relationship_movement.py` (R08's values are
  *referenced* by identity).
- Two modules rather than one because the composition-plus-reads exceeded the 1,200-line hard rail
  when written as one file; the split is by responsibility (decide which revisions ↔ read one
  revision's roster), not by size alone.

## 3. Exact commands and results (all from the leaf worktree)

Leaf environment every time:
`PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support /home/firefox/projects/agents-remember/mcp/.venv/bin/python`

| Command | Result |
| --- | --- |
| `pytest mcp/tests/test_review_family_context.py mcp/tests/test_review_family_context_values.py -q -m '' -n0 -p no:randomly` | **20 passed** in 24.5 s |
| `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m '' -n0` | **5 passed** in 59.1 s (16 contracts / 66 artifacts, re-pinned digest matches) |
| `pytest` over the 24 sibling review/family modules (surface, subject catalogue, bounded pagination, relationship movement/reach/line, subject isolation, source endpoints/content, evidence channels, one-sided statements, comparison generation, final-output receipt, sync rebinding/read, external Git movement, historical committed leaf, assessments, route refusals, review state, family composition/revision, revision selection) `-q -m '' -n0` | **336 passed, 3 subtests passed** in 296 s |
| `pytest mcp/tests/test_layering.py mcp/tests/test_single_owner_primitives.py mcp/tests/test_application_guards.py -q -m '' -n0` | **16 passed** |
| `pytest mcp/tests/test_file_size_detector.py -q -m '' -n0` | **2 passed** |
| `ruff check` on the 8 touched/added Python files | **All checks passed** (no new `# noqa`: verified by grep over the diff and the new files) |
| `ruff format --check` on the same 8 files | **8 files already formatted** |
| `pyright --pythonpath <venv>/bin/python` on the same 8 files | **0 errors, 0 warnings** |
| whole-tree hard-rail census (`file_size.measure` over `mcp/src`, `mcp/tests`, `mcp/test_support`, `dashboard/src`) at candidate vs base | **26 vs 26**: no new offender. Post-fix sizes: composition 929 L, roster 685 L, model 526 L, case modules 946 / 210 / 343 L; `models/knowledge/review.py` 1,175 → 1,189 (under the 1,200 hard rail); adapter 1,073 → 1,112. **Soft rail (900): case module 946 and composition module 929 exceed it — disclosed, see §9** |
| case budgets (`pytest mcp/tests --collect-only -q -m "not integration"` / `-m integration`) | **2,726 / 4,000 unit** and **462 / 1,000 integration** after fix round 1 (+5 cases this round) |

## 4. Base-defect reproduction

Scratch checkout at the base commit, no candidate code:
`git worktree add --detach /home/firefox/projects/ar-coordination/temp/l31-base/repo 4c000b11`
(HEAD = `4c000b11 ICR-R23: explicit raw-Git support boundary (260921-ICR-L23)`).

**(a) The real production composition at base, over the same enclosure and the same authored family
movement the leaf's cases author** — `temp/icr/probe-l31-base-defect.py`, run with the *base*
checkout's own `mcp/src` on the path (exact output in `temp/icr/base-defect-l31.txt`):

```
review state                     : review
payload carries family context   : False
payload fields                   : candidate,comparison,evidence,external_git_movement,knowledge,
                                   limitations,page,page_refusal,source,staleness,submission,
                                   surface_version,sync_movement
membership movements in payload  : 6
movement carries a guarantee text : False
successor family revision shown  : True
successor member revision shown  : True
guarantee text shown anywhere    : False
member statement shown anywhere  : False
successor roster the store records: 2 membership(s)
roster memberships the payload shows: 2
```

That is the defect as a measurement: the store holds the successor family revision's authored
guarantee and both members' own revision content, and the base payload publishes **neither** — no
`family_context` value exists at all, and the R08 movements carry membership identities without the
family's guarantee text or any member statement. The accepted reviewer cannot be built from that
payload without the frontend inventing the guarantee or the member content, which is exactly what
the packet's non-conforming example forbids.

**(b) The leaf's new tests copied into that base checkout** (the two test modules and their lane row
only):

```
$ cp <leaf>/mcp/tests/test_review_family_context{,_values}.py <base>/mcp/tests/
$ cp <leaf>/mcp/tests/test-evidence-lanes.toml <base>/mcp/tests/
$ PYTHONPATH=<base>/mcp/src:<base>/mcp/test_support <venv>/bin/python -m pytest \
      <base>/mcp/tests/test_review_family_context.py <base>/mcp/tests/test_review_family_context_values.py -q -m '' -n0
ERROR mcp/tests/test_review_family_context.py
ERROR mcp/tests/test_review_family_context_values.py
E   ModuleNotFoundError: No module named 'agents_remember.models.knowledge.review_family_context'
2 errors in 12.14s
```

Honest reading: on base bytes the new cases cannot even be collected (the composition does not
exist), so (a) is the substantive witness and (b) is the literal one. Both are recorded.

## 5. Guard bite-proofs (master rule 4)

`temp/icr/mutations-l31.py` applies one mutation, runs the named case, and reverts; the file digest is
printed before and after each (all five reverted byte-identically). Output in
`temp/icr/mutations-l31.txt`; run with `<venv>/bin/python temp/icr/mutations-l31.py` → `all guards bit`.

| Mutation (what it removes) | Named case | Observed |
| --- | --- | --- |
| **M1** `_selection_state` returns `compared` whenever both sides have a revision (several heads no longer refuse a winner) | `-k ambiguous_family_lineage` | `FAILED test_an_ambiguous_family_lineage_names_its_candidates_and_chooses_no_revision` |
| **M2** `_roster_page` hard-codes `complete=True` (a truncated roster reads as whole) | `-k truncated_roster` | `FAILED test_a_truncated_roster_stays_partial_with_the_owners_counts_and_continuation` |
| **M3** `_no_family` reports `no_subject_selected` (a measured zero becomes a review that selected nothing) | `-k measured_zero` | `FAILED test_a_selection_in_no_recorded_family_is_a_measured_zero` |
| **M4** `family_guarantee` returns the selected revision's predecessor's guarantee (a neighbouring revision's text) | `-k own_guarantees` | `FAILED test_a_selected_invariant_resolves_both_recorded_families_with_their_own_guarantees` |
| **M5** `ReviewFamilyContext` stops checking `unique_member_revision_total` against its rosters | `-k counts_do_not_describe` | `FAILED test_a_context_whose_counts_do_not_describe_its_roster_is_refused` |

## 6. Boundary and identity assertions the cases make (packet clause → case)

| Packet clause | Case(s) |
| --- | --- |
| RB1 family identity, exact before/after family revisions, authored guarantee + provenance, member revision content, through the normal application/HTTP contract | `…resolves_both_recorded_families_with_their_own_guarantees`, `…family_that_did_not_move…`, `…reaches_the_client_over_the_real_review_route` (real `TestClient` over the real owners: `family_context.state`, both family ids, the successor revision id, the guarantee text, the unique/row counts, and `pageOf=family_members` admitted) |
| RB2 selected invariant resolves every applicable family on both sides; selected family exposes all members of its selected revisions; unchanged siblings; before-only/removed/reassigned members; one canonical identity for a shared member; memberships authored, never inherited | `…resolves_both_recorded_families…`, `…successor_family_revision_is_read_from_its_own_rows_not_inherited` (the dropped member is absent after and retained before; the moved member keeps its own statement), `…shared_member_is_one_canonical_revision_referenced_in_two_contexts` (`other_family_revision_ids` names the other family revision; `unique_member_revision_total < membership_rows_total`) |
| RB2/RB7 ambiguity preserved, no head chosen by label/order | `…ambiguous_family_lineage_names_its_candidates_and_chooses_no_revision` (selection `ambiguous`, both sets of heads, every head a candidate with its own guarantee, no side presents a guarantee) |
| RB3 five distinct states, side/reason, complete/partial/remaining | `…selection_in_no_recorded_family_is_a_measured_zero`, `…family_only_the_candidate_records_is_a_one_sided_context_with_a_stated_absence` (`not_recorded`, `members_total=0`, no page), `…missing_knowledge_is_the_reviews_refusal_not_an_empty_family_context` (the review is refused `candidate_dataset_absent`), `…task_context_review_states_that_it_selected_no_subject`, `…truncated_roster_stays_partial…` |
| RB4 comparison-bound continuation, R10 conventions, total/returned/remaining, no generation mixing | `…published_cursor_continues_exactly_the_walk_that_minted_it`, `…cursor_that_binds_no_walk_is_refused_beside_the_first_pages` (`comparison_page_reset` + every first page still served), `…token_that_is_not_a_roster_cursor…` (`comparison_page_unreadable`), `…naming_the_roster_collection_without_a_cursor_is_refused` |
| RB5 inspectable references, owners and status dimensions preserved | `family_context.references` names `source.relationships`, `source.inventory`, `evidence.*`, `knowledge.applicability` and the join key; members carry `movement_reference` (R08's own identity) and `sources` (claim id, role, rationale, observed address); `…member_change_makes_its_family_context_available_and_concludes_nothing` asserts no verdict field exists |
| RB6 complete source inventory independent of membership; unique counts do not grow | `…complete_source_population_does_not_grow_with_a_repeated_membership` (one more family context, same `inventory.listed_total`, same attributed/unattributed path lists, same unique member count, one more row) |

## 7. Limitations, boundaries and open items

1. **No dashboard file was touched, deliberately.** The server side of the contract is complete: the
   typed payload field and the third bounded collection are served over the real route, and the JSON
   the browser reads is measured in this leaf's own HTTP case. `dashboard/src/data/review.ts` (and the
   pane control that maps `REVIEW_PAGED_COLLECTIONS`) belongs to **L24**, the single UI owner, whose
   intake checklist already says "inspect current ICR source and R31's real payload contract".
   Concretely, L24 should mirror `ReviewPagedCollection = "knowledge" | "records" | "family_members"`,
   add the `family_context` field to its payload interface, and decide whether the pane's collection
   control should offer the roster walk (this leaf intentionally does not put a UI change in L24's
   lane). No dashboard toolchain was prepared or run.
2. **One bounded collection continues one walk, by design.** `pageOf=family_members` is a set of
   per-family roster walks; a request names it *with* the cursor of the walk it continues, and naming
   it without one earns `comparison_page_unreadable` with a true action sentence rather than an
   arbitrary walk's page. The payload's `page` docstring states this exception, so "present exactly
   when the request named one" remains true. If L24's interaction wants "the first page of the whole
   family collection" as one control, that is a wire-shape decision for its own leaf (or a ruling),
   not a silent change here.
3. **Roster reads are per family revision and bounded by the request's page size.** A family with
   more selected items than the page bound renders `partial` with the owner's own counts and cursor;
   the composition never loops to completeness, which is what keeps "an initial page is never
   presented as all members" true, and what the packet's RB4 asks for.
4. **The applicable-family set is complete per snapshot but work is one bounded read per applicable
   family revision.** For a selected invariant the set is the policy's own frozen
   `directly_containing_families` (bounded by the read operation's declared 5,000-item selection
   bound, which refuses rather than truncating). A pathological store with thousands of families
   citing one invariant would therefore cost thousands of small indexed reads in one response; the
   realistic curated case is a handful. No new bound was invented for it, and this is the honest
   limit of the composition as delivered.
5. **`resolve_family_side` states a side it could not open.** If one half's file opens but its
   authored-edge read fails, `open_family_side` reports the side `unreadable` with the owner's own
   error text and the family context becomes `partial`/`unavailable`; the review itself is not
   refused by that path (the pair preflight already ran). Reproducing it needs a mid-read failure, so
   it is exercised by construction and by the model guard (`state == "unreadable"` is the only state
   that carries no page), not by a fixture this leaf builds.
6. **Historical/reopened reviews** are not covered by a case in this leaf: the composition reads the
   datasets the *resolution* bound, which for `history=recorded` are the leaf's recorded halves, so
   the same code path serves them; `mcp/tests/test_historical_committed_leaf_review.py` (which drives
   recorded history through this composition) passes in the regression run above. A recorded-history
   family case would duplicate R12's enclosure machinery and is not claimed here.
7. **`application/knowledge_review.py` grew 1,073 → 1,112 lines.** It was already over the 900 soft
   rail at base; it is still under the 1,200 hard rail and carries no feature logic of its own. If the
   master wants the adapter pulled further under the soft rail, the next extraction candidates are its
   refusal-message helpers (`_absent_pair_refusal`, `_comparison_refusal`) — neither is this leaf's
   responsibility and neither was touched.
8. **Ruling not requested for anything in the packet.** No schema, store, selection-policy,
   publication or assessment authority was added; the composition calls the shipped read operation and
   the family/membership owners and stores nothing. The only contract-shaped decisions are the two
   stated in §7.1/§7.2, both inside R31's own "wire-shape choice inside those owners needs no new
   product ruling".

## 8. Candidate fingerprint (recipe, and proof it reproduces)

Recipe — `temp/icr/fingerprint-l31.sh`, stated so anyone can recompute it exactly:

1. `git diff | sha256sum` — the tracked delta. It is blind to untracked files (the L21 lesson), so it
   is never read alone.
2. `git status --porcelain` — entry count + the LC_ALL=C-sorted path list.
3. one `sha256sum` per changed/untracked file, rendered `<sha256>  <path>`, LC_ALL=C sorted by path,
   concatenated with newlines and hashed whole. **Exclusions in the round-0 readings below:
   `__pycache__/` and `*.pyc` only** (build artifacts); `temp/icr/`'s evidence was part of the digest
   list, and the round-0 digests recorded here are exactly that form -- the verifier recomputed them
   byte for byte. **Fix round 1 narrowed the digest list to the deliverable paths** (excluding `temp/`
   as well) so that writing this evidence file cannot invalidate the reading it records; the fix-round
   reading is in the addendum below, and reading 2's status list still names every `temp/` path.

Measured on the candidate (full reading in `temp/icr/fingerprint-l31.txt`):

```
== 1. tracked delta ==
69136411c9138172d1dcabdf458f7703b1eb0e626414d5121adc4b36eff194d9  -
== 2. status entries ==
6 modified (M) + 5 new deliverable files (??) + 6 temp/icr evidence files (??) = status_entry_count=17
== 3. deliverable per-file digests, sorted by path ==
65373258e91b02346ecdc13c6a01749778862b623b81da773ed46a4d55f316de  application/knowledge_review.py
a9b410812f9fde5ce0efabef52dfb8d63501e2fe34d0244a59757247aede8159  application/review_family_context.py
2cda1d023cea90b617f8f1742da3a914d6aa49cdb35d86678f541ba906c112dd  application/review_family_rosters.py
4df1c560d1d649334894fe94f8615c1a789c83634c28f60d46a2dd63cd63b9e9  application/review_task_context.py
d43ceb7c824acc0f541313f23356144aa866612abb1412dd26d2d5eb35cd336a  models/knowledge/review.py
57da690a2a9ab31ff0f4574d639ef895291d903a74de9a5f8f36c93228051caf  models/knowledge/review_family_context.py
b1c38ed84fb847bc76ddd0497319cfad7059d35f3fdf78cd425169d4f49cbd48  tests/evidence-lifecycle.toml
16fbe97605e7d70362f5dd638c52c654396a5f9dcafe1a4ef30f18e5e1590ec9  tests/test-evidence-lanes.toml
7d95898ede64682be8c5918fea6da46feff2b7c07ffcf94bd7bc6713240fceb4  tests/test_dependency_ownership_ast_helpers.py
375b90f94f66f86172b8f22b171f73ce51dcec03a1228176ce7a8a0bdb72f0e8  tests/test_review_family_context.py
a47decda08c953f81da595aad7604a5e80a5ec0fbba684a7446ab709fe89d52f  tests/test_review_family_context_values.py
5e80ce61602e0be3e2073a2927d9904ca903799173615ce5268d40a6d3c78e5a  (whole-list sha256)
```

**Proof it reproduces:** two readings were taken >90 s apart with no writes between them and are
byte-identical (34 lines each, `diff` exit 0). The tracked delta (reading 1) is stable because no
tracked file changed after the readings; the deliverable digests (reading 3) were re-measured after
the evidence file was written and are unchanged, because reading 3 excludes `temp/` by construction
and the note above says so.

## 9. Test-catalog registration (master rule 7)

- New test modules: `mcp/tests/test_review_family_context.py` (**946 L**, corrected in fix round 1
  from a stale 874 L: the module grew by the HTTP-route case and the ambiguity case after that number
  was taken), `mcp/tests/test_review_family_context_values.py` (210 L after fix round 1) and
  `mcp/tests/test_review_family_context_population.py` (343 L, added in fix round 1). All three are
  under the 1,200 hard rail. **The soft rail (900) is exceeded, and this report discloses it rather
  than claiming otherwise:** the case module is 946 L and the composition module is 929 L; the split
  into a third case module was made to stop the case module growing *further*, not to bring it under
  900. The whole-tree ≥1,200 census is unchanged (26 at base, 26 at the candidate over the same
  scope), so no hard-rail offender was added — the verifier's wider scope reports the same 27/27
  identical path sets.
- Consumer rows **derived from the census's own finding**, not guessed:
  `mcp/tests/diff_scope_test_support.py` and `mcp/tests/read_scope_test_support.py` each reported
  `missing=['mcp/tests/test_review_family_context.py'], unsupported=[]`; each gained exactly that one
  path. The values module imports no support module, so it derives no row.
- Lane rows added in `mcp/tests/test-evidence-lanes.toml` (`unit-regression`, beside
  `test_review_bounded_pagination.py`).
- `LIFECYCLE_CATALOG_SHA256` re-pinned from a fresh `sha256sum mcp/tests/evidence-lifecycle.toml`
  (`caf1b9ee…` → `b1c38ed8…`), with the deliberate-re-pin note recorded above the constant.
- Population proven unchanged and green:
  `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''`
  → **5 passed**, at **16 contracts / 66 artifacts**, and the second module's own digest was measured
  from a fresh `sha256sum` two independent ways (the pin check and the wrapper's own `sha256sum`
  line both printed `b1c38ed84fb847bc76ddd0497319cfad7059d35f3fdf78cd425169d4f49cbd48`).

---

# Fix round 1 (after `temp/icr/verify-l31.md`, verdict `fail`: F1/F2/F3 blocking, F4 medium)

All three blocking findings share one locus and the verifier's own recommendation was followed rather
than a new pattern invented: **the family revision population of a family selection is now the family
owner's own revision list**, not the membership-bearing rows the shipped read happens to emit. F4's
two false numbers are corrected above.

## What changed

| File | Change |
| --- | --- |
| `application/review_family_context.py` | `_applicable` splits by axis: `_applicable_family` (`:538`) reads `families.list_family_revision_ids` and, for a `FamilyRevisionSeed`, the exact named revision; `_applicable_invariant` (`:565`) keeps the policy's frozen family set **and** reads each applicable family's own revision list for the counts. `_Applicable` gains `recorded` (the owner's list per family) and `population()`/`revisions()` accessors. `_FamilyRevisions` gains `before_recorded`/`after_recorded`/`axis` and `other_recorded()`; `_axis` (`:422`) and `_not_recorded_detail` (`:433`) state each of the three axes' own recorded fact. `_selection_sentence` (`:768`) states the family owner's measured per-side revision counts and the number of other recorded revisions; `_unresolved_sentence` (`:806`) states the axis's own reason. `_no_family` (`:461`) keeps `no_family_recorded` for a family neither snapshot records, with a sentence that says it is a zero of the family population. |
| `application/review_family_rosters.py` | `RosterContext` (`:107`) replaces the bare revision argument: it carries the revision, the family owner's recorded revision list and the axis's own "this snapshot selected no revision because …" sentence. `read_family_roster(side, family_id, context, request)` (`:209`) publishes `recorded_revision_ids` on the recorded side context. |
| `models/knowledge/review_family_context.py` | `ReviewFamilyRevisionContext.recorded_revision_ids` (`:292`) publishes the family owner's list, with a validator: **a recorded side's selected revision is one of them** (`:312`). |
| `tests/test_review_family_context_population.py` | **New** (343 L): the four required cases (a)–(d). |
| `tests/test_review_family_context_values.py` | +1 case: a recorded side may not name a revision its family does not record. |
| `tests/test-evidence-lanes.toml`, `tests/evidence-lifecycle.toml`, `tests/test_dependency_ownership_ast_helpers.py` | the third module's lane row, the two census-derived consumer rows (`diff_scope_test_support.py`, `read_scope_test_support.py`), and the Twenty-eighth deliberate re-pin `b1c38ed8…` → `23dd7c0f85b50585e8968122f60f50e87e15bfbfa241b28b77b7c71b2a41e252` at 16 contracts / 66 artifacts. |

## The three blocking findings, closed

**F1 — forced determinacy.** For a family selection the head rule now runs over *every revision the
family owner records in that snapshot*, so a memberless successor is a head. The canonical shape
(two successors of one predecessor, neither citing a member) is `ambiguous` with all three candidate
heads and no revision chosen; the pre-fix bytes reported `compared` and named the superseded
revision. Case (a):
`test_the_canonical_memberless_successor_shape_is_an_ambiguity`.

**F2 — a false sentence.** The history is now measured from the family owner on both snapshots:
`{b} before and {a} after revision(s) of this family, and {n} other recorded revision(s) …`, where
`n` counts the *distinct* recorded revisions of the family the selection did not choose (so a
revision both snapshots retain is counted once, and a memberless revision is counted at all). Case
(c) drives the shape the pre-fix bytes got wrong — the parent's membership withdrawn on the
candidate, a memberless successor added — and the pre-fix run printed exactly
`…; 0 other recorded revision(s) of this family remain selectable history` while the owner recorded
three revisions.

**F3 — a recorded family rendered as no family.** A family selection whose snapshot records the
family now composes a `recorded` side: the selected revision, its guarantee with provenance and seal,
and a **measured** empty roster (`members_total = 0`, a complete page, the sentence "was read whole:
0 recorded membership(s), all carried here"). `no_family_recorded` is kept strictly for a family
neither snapshot records (and for the invariant-in-no-family contrast, unchanged). Case (b):
`test_a_recorded_family_with_no_members_is_recorded_not_absent`.

## Required cases, and their pre-fix failure proof

`temp/icr/fix-round-1-prefix-failures.txt` — the pre-fix bytes are the verifier's own frozen files
(`/home/firefox/projects/ar-coordination/temp/verify-l31/frozen/files/`, digest `a9b41081…` for the
composition and `57da690a…` for the model) overlaid on a scratch checkout at `4c000b11`; the new test
module and its sibling are copied in so the cases can run at all:

```
$ PYTHONPATH=<prefix>/mcp/src:<prefix>/mcp/test_support pytest mcp/tests/test_review_family_context_population.py -q -m "" -n0 --tb=line
FFF.                                                                     [100%]
E   AssertionError: … (a) after_heads is only the membered parent where the store records three heads
E   AssertionError: [] (b) the composed context has no entry at all for a recorded memberless family
E   AssertionError: assert '1 before and 3 after revision(s)' in '…; 0 other recorded revision(s) of
    this family remain selectable history'   (c)
FAILED test_the_canonical_memberless_successor_shape_is_an_ambiguity
FAILED test_a_recorded_family_with_no_members_is_recorded_not_absent
FAILED test_the_history_sentence_is_measured_against_the_family_owner
3 failed, 1 passed in 16.45s

## The same module on the post-fix bytes:
....                                                                     [100%]
4 passed in 16.36s
```

**(d) is the over-correction guard and passes on both byte sets by design** — that is what it is for.
It measures both directions: an invariant in no family still reads `no_family_recorded` with the
sentence naming the selected invariant, and a family neither snapshot records is refused by the
comparison (`comparison_refused` / `selector_absent`) before any context exists, rather than being
rendered as an empty one.

## Guard bite-proofs after the fix (master rule 4)

`temp/icr/mutations-l31.py`, re-run with the new guard added; every mutated file was restored
byte-identically (the script prints the digest before and after each mutation):

| Mutation | Named case | Observed |
| --- | --- | --- |
| M1 head rule stops refusing several heads | `-k ambiguous_family_lineage` | FAILED |
| M2 roster page claims completeness | `-k truncated_roster` | FAILED |
| M3 measured zero becomes "no subject" | `-k measured_zero` | FAILED |
| M4 guarantee read from the predecessor | `-k own_guarantees` | FAILED |
| **M5 (new) recorded side stops checking its selected revision against the family's own list** | `-k does_not_record` | FAILED |
| M6 unique-member check disabled | `-k counts_do_not_describe` | FAILED |

`all guards bit`.

## Rails re-run on the post-fix bytes (exact results)

| Command | Result |
| --- | --- |
| the three delivery modules, `-q -m '' -n0 -p no:randomly` | **25 passed** in 25.7 s |
| 26-module sibling review/family regression set, `-q -m '' -n0` | **342 passed, 3 subtests passed** in 281.6 s |
| `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m '' -n0` | **5 passed** in 58.6 s — 16 contracts / 66 artifacts, pin `23dd7c0f…` matches a fresh `sha256sum` |
| `pytest mcp/tests/test_layering.py mcp/tests/test_single_owner_primitives.py mcp/tests/test_application_guards.py mcp/tests/test_file_size_detector.py -q -m '' -n0` | **18 passed** |
| `ruff check` (9 files) | **All checks passed!** |
| `ruff format --check` (9 files) | **9 files already formatted** |
| `pyright --pythonpath <venv>/bin/python` (9 files) | **0 errors, 0 warnings, 0 informations** |
| new/changed `# noqa` | **0** (`grep -rn noqa` over the six new/changed files) |
| whole-tree ≥1,200 census | **26** at the candidate over the same scope, **26** at base — no new offender |
| case budgets | unit **2,726 / 4,000**, integration **462 / 1,000** |

**Method note, disclosed rather than hidden:** my first post-fix regression run overlapped the
mutation script's own run, so it read momentarily mutated production bytes and reported two spurious
failures (`compared` where the case expects `ambiguous`) — the M1 mutation's symptom exactly. I
verified the restored digests (see "Fix round 2" below for the corrected triple: `29661d3b…`,
`e517443a…`, `9d71d9e0…`, matching the mutation script's
own before/after readings), re-ran the two modules alone (**21 passed**) and then the whole set with
no mutation running (**342 passed**). The spurious result is not reported as a finding.

## Fix-round fingerprint (recipe unchanged, new reading, reproduces)

The recipe is §8's, unchanged: (1) `git diff | sha256sum`; (2) `git status --porcelain` count + sorted
paths; (3) one `sha256sum` per **deliverable** changed/untracked file (excluding `temp/` and
`__pycache__`/`*.pyc`), sorted by path, joined by newlines, hashed whole. Two readings taken >90 s
apart with no writes between them are byte-identical; both are recorded in
`temp/icr/fingerprint-l31.txt` together with the tracked delta and the full deliverable digest list.
This report's own addendum was written **after** the readings, and the recipe excludes `temp/` from
the digest list by construction, so the recorded deliverable digests still recompute exactly.


## Fix round 2 (evidence only — no production byte changed)

**E1 (low, closed).** The guard-bite evidence for **M2** and **M4** cited mutation digest
`81f5e4a9a5a5` and this report quoted `81f5e4a9…` as a restored post-fix digest, while the delivered
`application/review_family_rosters.py` is `e517443a546e…`. The cause is a real ordering mistake and
not a hidden failure: the mutation set was last run **before** the `ruff format` pass that reflowed
one call in that module, so the evidence recorded the pre-format bytes and the digest it named no
longer existed in the tree. Both guards still bit, so nothing was concealed — the artifact simply
disagreed with the delivered file.

**The corrected digest triple** (the mutated files' digests before *and* after each mutation, since
every one reverted byte-identically):

| File | Digest (frozen = post-run) | Mutations applied to it |
| --- | --- | --- |
| `application/review_family_context.py` | `29661d3b57f8e0ddb1129a47f97fa0d57654379a278b39cb20f92096bbb7bbd0` | M1, M3 |
| `application/review_family_rosters.py` | `e517443a546e1f997c015dfb485504d9872874d07a813f92fbc54fce6deb95bf` | **M2, M4** (was `81f5e4a9a5a5…`) |
| `models/knowledge/review_family_context.py` | `9d71d9e04626f6bc3658728ab565eee92e67ec25b233deda56d0e85e0fe45a2b` | M5, M6 |

**Re-run over the frozen bytes** (`temp/icr/mutations-l31.txt` is that run's output; nothing else was
running, so no bytes could be read while mutated):

```
M1 … mutation digest 29661d3b57f8 -> 29661d3b57f8 (reverted-bytes-identical)  → FAILED
M2 … mutation digest e517443a546e -> e517443a546e (reverted-bytes-identical)  → FAILED
M3 … mutation digest 29661d3b57f8 -> 29661d3b57f8 (reverted-bytes-identical)  → FAILED
M4 … mutation digest e517443a546e -> e517443a546e (reverted-bytes-identical)  → FAILED
M5 … mutation digest 9d71d9e04626 -> 9d71d9e04626 (reverted-bytes-identical)  → FAILED
M6 … mutation digest 9d71d9e04626 -> 9d71d9e04626 (reverted-bytes-identical)  → FAILED
all guards bit
```

**Frozen-vs-post-run equality, checked explicitly after the run** (`sha256sum`, per file):

```
29661d3b57f8e0ddb1129a47f97fa0d57654379a278b39cb20f92096bbb7bbd0  mcp/src/agents_remember/application/review_family_context.py
e517443a546e1f997c015dfb485504d9872874d07a813f92fbc54fce6deb95bf  mcp/src/agents_remember/application/review_family_rosters.py
9d71d9e04626f6bc3658728ab565eee92e67ec25b233deda56d0e85e0fe45a2b  mcp/src/agents_remember/models/knowledge/review_family_context.py
```

Every one equals the frozen deliverable digest the fingerprint's reading 3 lists for that path, so
the mutation run left no byte behind. **The fingerprint is unchanged**: recomputed after the run, the
tracked delta is `3d55a9273d62c7d829ed03cc88e30f03b6d01391cf12fe08d72ae5519aa8a3d4` and the
deliverable whole-list digest is `1a86bb47f4d3feeb25da3e38412d71abb32b1b5f2cf03f080476b0619fa0c6e8`
— exactly the values recorded before the run. Only `temp/` evidence (the mutation output and this
note) changed, and the recipe excludes `temp/` from its digest list, so a curator working on these
bytes sees no movement.
