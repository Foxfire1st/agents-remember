# Independent adversarial verification — leaf L31 / ICR-R31@v1

**Verdict: `fail`** — two blocking findings (F1, F2) plus one blocking finding (F3) sharing F1's root
cause, and one medium report-accuracy finding (F4).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l31-ar/260921-icr-l31`,
branch `ar/260921-icr-l31`, base `4c000b11c5243e4a8e77c08e87984fff00c1d94b` (ICR-R23 / L23), plus the
worker's uncommitted delta. Worker report: `temp/icr/report-l31.md` (claims checked, never used as
evidence). Nothing was committed; no production code and no memory worktree was touched by me.

---

## 0. Self-gate and fingerprints

Gate opened at `2026-09-23T21:10:16+02:00`, after `temp/icr/report-l31.md` existed **and** the combined
fingerprint had been unchanged for **100 s** (my watcher's own samples; `self-gate.log`).

Combined recipe (`fingerprint.sh`): tracked `git diff | sha256sum`, staged diff hash, **one sha256 per
untracked file individually**, sha256 over the newline-joined list, paired with `git status --porcelain`
(entry count + paths). All samples:

| Reading | tracked diff | untracked files | untracked combined | porcelain | report |
| --- | --- | --- | --- | --- | --- |
| start sample 1 (21:10) | `69136411…` | 12 | `5c292aea…` | 18 | yes |
| start sample 2 (≥90 s later) | `69136411…` | 12 | `5c292aea…` | 18 | yes |
| end (after all verification work, before writing this file) | `69136411…` | 12 | `5c292aea…` | 18 | yes |
| final (after writing this file) | `69136411…` | 13 | `01fc836a…` | 19 | yes |

`diff` of the two start readings: **stable**. The end reading is identical to both. The **final** reading
differs from start by exactly one path — `?? temp/icr/verify-l31.md`, this verdict, which the brief
requires me to write into the leaf — and by nothing else; the tracked delta is unchanged and **all
eleven deliverable digests still equal the frozen set byte for byte** (checked individually against
`frozen/frozen-hashes.txt`). **No deliverable byte moved during verification.**

**The L21 trap, live.** At 20:41 the combined fingerprint changed (`47671d48…` → `b25db2bc…`) while
`git diff | sha256sum` read `e3b0c442…` (the sha256 of *empty input*) for the whole first hour — the
worker's entire delta is untracked. A `git diff`-only hash would have reported "no change" over
~3,000 new lines. Confirms the brief's correction.

### 0.1 The worker's STATED recipe, recomputed — does it reproduce?

`temp/icr/fingerprint-l31.sh` run verbatim from the leaf worktree (`worker-recipe-now.txt`):

| Reading | Their evidence | My recomputation | Reproduces? |
| --- | --- | --- | --- |
| 1. `git diff \| sha256sum` | `69136411c9138172d1dcabdf458f7703b1eb0e626414d5121adc4b36eff194d9` | identical | **yes, byte for byte** |
| 2. `git status --porcelain` list + count | 17 entries | 18 entries (`?? temp/icr/fingerprint-l31.txt` extra) | differs by exactly one disclosed entry |
| 3. per-file digests + whole-list hash | 11 digests, whole-list `5e80ce61602e0be3e2073a2927d9904ca903799173615ce5268d40a6d3c78e5a` | identical, whole-list identical | **yes, byte for byte** |

**Verdict on the recipe: it reproduces on the two readings that carry its reproducibility claim, and
reading 2's single extra entry is self-referential and disclosed** by the recipe's own note ("this
evidence file itself is written after both readings, so the status list of a re-run gains
`temp/icr/fingerprint-l31.txt`. Readings 1 and 3 are unaffected"). Their recipe is stricter than the
minimum (it hashes untracked files individually, so it is not blind to new files). **No candidate-moved
finding.**

---

## 1. Obligation 1 — base-defect witness (my own fixture, my own commands)

**(a) Substantive witness — the real production composition on base bytes.** I built two snapshots
through the shipped store operations and drove `compose_review` (the composition behind the intent
route) with the base checkout's own `mcp/src` on the path:

```
$ python probe_family_context.py <base> <base-probe>
--- family_selected:  state=review -> family_selected.json
--- invariant_selected: state=review -> invariant_selected.json
   knowledge keys: [... 'context', 'family_ids', 'invariant_ids', ...]   # no family_context field

$ python probe_candidate_family.py <base> <base-amb>
================ after_only_family  ... family_context: ABSENT from the payload
================ ambiguous_heads    ... family_context: ABSENT from the payload
================ empty_family      ... family_context: ABSENT from the payload
================ no_family         ... family_context: ABSENT from the payload
================ two_families      ... family_context: ABSENT from the payload
```

and the store really holds the facts the base payload drops — for a **family** selection the base
payload reports `before_statement.state == "unresolved"` / `after_statement.state == "unresolved"`
("the before snapshot holds the record but published no statement for it") while the store row is:

```
family_revision: revision_id=67dbeaf2-…, display_version=v1,
  joint_guarantee='The retry budget is shared by integration and synchronization.'
family_member:   b0d62d31-… -> 30000000-…, 18a8c32d-… -> d6f07eb9-…
```

So on base the guarantee text and every member revision are recorded and **not** published. This is the
R31 gap as a measurement, on my own fixture, through the real operation.

**(b) Literal witness — the delivery's own new cases replayed on base bytes**
(`base-defect-replay.sh`, only the two new test modules + the lane manifest copied onto a scratch
checkout at `4c000b11`):

```
### base HEAD: 4c000b11c5243e4a8e77c08e87984fff00c1d94b
E   ModuleNotFoundError: No module named 'agents_remember.models.knowledge.review_family_context'
ERROR tests/test_review_family_context.py
ERROR tests/test_review_family_context_values.py
2 errors in 12.33s
```

Same result the report states. Honest reading, unchanged from the report: (b) cannot even be collected
on base, so (a) is the substantive witness. **Claim upheld.**

---

## 2. Obligation 2 — the real production operation, driven by me

Not a private helper: the real `review_family_context` composition inside the real `compose_review`,
and the **real HTTP route** (`register_review_routes` + `read_knowledge_review` over the repository's
own `build_endpoint_fixture` — real leaf contract, real worktree, real datasets), read from the served
JSON (`probe_l31_transport_states.py`):

```
plain_status: 200
plain_family_state: "recorded"
plain_family_ids: [b393b44e-…, be44242d-…]                 # two families for one selected invariant
plain_successor_guarantee: ["The successor guarantee, authored with a member."]   # the exact stored text
plain_page: null            plain_page_refusal: null        # no page named -> no page published
knowledge_page_collection: "knowledge"                      # pageOf=knowledge stays the knowledge walk
```

Real cursor continuation (`probe_l31_cursor.py`, `pageSize=1` then the minted cursor):

```
first page:  page_complete=false, has_continuation=true, members=1/2 (owner counts 11 total)
continuation: 200, page.collection="family_members", state="continued",
              total/returned/remaining = 11/2/9,
              scope=[side=before, family_revision_id=9492a154-…, policy_version=recorded-family-frontier/v1, page_size=1]
              context state: "partial"
```

Cross-generation / cross-subject attacks (`probe_l31_stale.py`, `probe_l31_crosscollection.py`):

```
cross-subject cursor   -> comparison_page_reset   "continuation_binding_mismatch: … it binds another selector"
garbage cursor         -> comparison_page_unreadable "… is not a cursor of this format"
roster cursor on pageOf=knowledge
                       -> page.state="reset", reset.code=comparison_page_unreadable,
                          expected="knowledge-diff-cursor/v1", observed="<unreadable>",
                          next_action="Discard the continuation and start a new read … No partial page
                                       was returned, because a page assembled from two snapshots is not a
                                       page of either."
```

The cursors are format- and subject-bound, the refusal is explicit and carries the owner's own
expected/observed identities, and no page was silently served as a fresh first page. **RB4's
generation clause upheld.**

---

## 3. Obligation 3 + 4 — failure states and preservation boundaries (the leaf's characteristic risk)

### 3.1 What I could falsify: the five states are separated, except one

Correct and reproduced by me:

* **measured zero (invariant in no family)** — an invariant authored with no membership on either
  snapshot: `state="no_family_recorded"`, `families_total=0`, `detail="the recorded scope was read on
  both snapshots and holds no family membership for the selected invariant, so no family context exists
  for it; this is a measured zero and not an unread, unavailable or filtered scope"`. Correct, and
  correctly **not** spelled `empty`.
* **absent on one side** — `after_only_family`: `selection.state="added"`, `before.state="not_recorded"`
  with a true detail, `after.state="recorded"` with the authored guarantee and its one member.
* **before-only / removed membership** — a membership I removed on the after side survives:
  `before members=2 total=2`, `after members=1 total=1`.
* **task-context review** — `no_subject_selected` (report §RB3 case; I did not re-drive it).
* **refusal** — the review refuses rather than rendering an empty family context when a dataset half is
  missing (report case; not re-driven by me — see §7).
* **shared membership is one canonical identity, not a clone** — `two_families`:
  `families_total=2`, `membership_rows_total=8`, `unique_member_revision_total=3`, and
  `other_family_revision_ids` names the sibling family revision from either context.
* **the entry validator** refuses to build a context whose counts disagree with its rosters.

### 3.2 What I falsified — an R07 family ambiguity collapsed into a confident answer (F1)

The repository's **own canonical ambiguity fixture** (`build_branching_knowledge_fixture`) authors one
family with two successor revisions that record **no memberships**; R07's own rule gives two heads
(`probe_canonical_ambiguity.py`):

```
family revision rows: 3   memberships per revision: {two successors: [], parent: [2 rows]}
successor revisions authored:    both present
heads by R07's own rule:          BOTH successors
=> memberless successor revisions exist in the repo's own ambiguity fixture: True
```

Driving the composition over that shape (`probe_ambiguity_proof.py`), **one difference between the two
runs — whether the two successors record a membership**:

| | memberless successors | successors with members |
| --- | --- | --- |
| revisions the **store** records (family owner) | 3 | 3 |
| heads by **R07's own rule** | **2** | **2** |
| payload `selection.state` | **`compared`** | `ambiguous` |
| payload `after_heads` | `[parent]` (1) | both successors (2) |
| payload `after_retained` | `[parent]` (1) | all 3 |
| payload sentence | "…each the unique revision its own snapshot records a membership of the selected subject in; **0 other recorded revision(s) of this family remain selectable history**" | "ambiguous … several legitimate family revisions with no authored successor ordering … no revision was chosen" |

The delivery's **own** ambiguity case passes only because it authors a membership per sibling
(`test_review_family_context.py:566-585`, the loop `for revision_id in created: _author_membership(…)`)
— so the shape the repository uses to model ambiguity is the shape the case does not cover.

### 3.3 What I falsified — the empty-but-recorded family disappears (F3)

Store, after snapshot: `family_revision` row `b99ef6ff-…` with
`joint_guarantee='A guarantee recorded for a family with no members.'`, zero membership rows.
Payload for `FamilyIdentitySeed(family_id=<that family>)`:

```
state: no_family_recorded   families_total: 0   entries: 0
detail: "the recorded scope was read on both snapshots and holds no family membership for the
         selected family, so no family context exists for it; this is a measured zero and not an
         unread, unavailable or filtered scope"
guarantee anywhere in family_context: False
```

### 3.4 What I could NOT falsify (attacked, held)

* no silent fallback to current knowledge: every absent/unread/unresolved state carries its own sentence
  and refuses to render an empty collection;
* no fabricated assessment or Changed/Passed conclusion: `ReviewFamilyContextReferences` names the owners
  by identity and the join key; no field can hold a verdict;
* shared identity not cloned, unique counts not inflated (§3.1);
* cursors cannot combine generations (§2).

---

## 4. Obligation 5 — truthfulness of every new emitted sentence

I read every new sentence in the three new modules and the amended docstrings, and tested each against
the store. Results:

| Sentence / state | True about the store? |
| --- | --- |
| `no_family_recorded` for an **invariant** in no family | **true** |
| `not_recorded` side detail ("this snapshot records no membership of the selected subject in the family …") | **true** |
| roster detail ("… was read whole: N recorded membership(s), all carried here") | **true** (matches owner counts) |
| truncated roster keeps `partial` + owner counts + continuation | **true** |
| `family_collection_refusal` ("… no cursor was presented, so no one walk was addressed") | **true** |
| `family_context_cursor_refusal` ("the cursor bound no family revision's recorded roster walk…") | **true** |
| `_selection_sentence` "**0 other recorded revision(s) of this family remain selectable history**" | **FALSE** (F2) — the store records two more revisions of that family |
| `_no_family` "…so **no family context exists for it**" for a **selected family** that is recorded | **FALSE** (F3) — the family identity, its revision and its guarantee are recorded |
| `payload.page` class docstring exception for the roster collection | **true** — the docstring really was amended, and `page` is `null` when no page is named (§2) |

The two false sentences are both reachable in ordinary states (a family whose successors carry no
memberships yet; a family authored before its memberships) and both are consumed by L24, which renders
them. Master rule 1 therefore applies to both.

---

## 5. Obligation 6 — seam policy (`notes/03-adapter-seam.md`)

* **The adapter delegates.** `application/knowledge_review.py` gains one import (3 names + 1 refusal
  helper), one call site with the resolved pair and R08's movements, one page/refusal selection guarded
  by `request.page_of == FAMILY_MEMBERS_COLLECTION`, and one payload field. No feature logic.
  1,073 → 1,112 lines (soft rail already exceeded at base; hard rail 1,200 respected).
* **One implementation per responsibility.** The composition lives only in
  `application/review_family_context.py`; the one roster read only in
  `application/review_family_rosters.py`. Nothing else in the tree imports either
  (`grep -rn` over `mcp/src mcp/tests`): only the adapter imports the composition, and
  `review_task_context.py` / `models/knowledge/review.py` import only the *model* to type the payload.
* **No duplicated owner.** `_authored_edges` calls the read owner's `fetch_predecessor_edges`;
  `_heads` calls R07's `revision_heads`; the roster read goes through `read_knowledge_scope`;
  membership/guarantee rows come from `families`/`memberships`.
* **Docstring claims checked**: the `ReviewPagedCollection` docstring's "third bounded collection"
  claim, the `page` exception, and the `family_context` "required rather than optional" rationale all
  match the bytes and the behaviour I observed.
* **No new `# noqa`**: `grep -n noqa` over all 9 touched/added files → none.

---

## 6. Obligation 7 — rails (all on the frozen candidate bytes)

```
ruff check (9 changed files)      -> All checks passed!            [exit 0]
ruff format --check (same 9)      -> 9 files already formatted     [exit 0]
ruff check (whole mcp)            -> All checks passed!            [exit 0]
pyright --pythonpath <venv> (9)   -> 0 errors, 0 warnings, 0 informations
# noqa (new/changed files)        -> none
whole-tree >=1200 census          -> base 27 / candidate 27, IDENTICAL path sets (mcp-scoped 26 / 26)
catalog pin                       -> sha256sum = b1c38ed8… == LIFECYCLE_CATALOG_SHA256 (fresh)
catalog population                -> 16 contracts / 66 artifacts
registration rows                 -> exactly 2 rows added, to the consumer lists of
                                     mcp/tests/diff_scope_test_support.py and mcp/tests/read_scope_test_support.py
lane rows                         -> both new test modules added to unit-regression
delivery cases                    -> 20 passed (two new modules)      [matches report]
census + lane modules             -> 5 passed, 16/66, re-pinned digest matches
regression spot-check (10 most-affected sibling modules) -> 136 passed, 3 subtests passed
```

**Pre-existing, disclosed, and NOT caused by this leaf:** the base itself fails `ruff format --check` on
two untouched test modules (`test_historical_committed_leaf_review.py`,
`test_knowledge_review_one_sided_statements.py`). The candidate does not touch either file and its own
nine files are clean, so this is a pre-existing repo condition, reported here rather than hidden.

**Rail finding F4:** the report's own size claim is false and the soft-rail crossing is undisclosed
(see findings).

---

## 7. Obligation 8 — packet clause by clause, against the bytes

| Clause | State |
| --- | --- |
| RB1 family identity, exact before/after revisions, authored guarantee + provenance, member revision content, through the app/HTTP contract | **implemented** — driven by me over the real route (§2) |
| RB2 selected invariant resolves every applicable family on both sides; selected family exposes all recorded members of its selected revisions | **partial** — true when every family revision carries a membership; **false for a memberless revision** (F1) |
| RB2 unchanged siblings | **implemented** (carried with `members_total`) |
| RB2 before-only / removed / reassigned members | **implemented** (2 → 1 measured) |
| RB2 shared membership references one canonical identity, not cloned | **implemented** (`other_family_revision_ids`, unique < rows) |
| RB2 memberships explicitly authored, never inherited through a moving latest pointer | **implemented** (read from the selected revision's own rows) |
| RB2 preserve R07 ambiguity rather than picking a head | **NOT upheld for the canonical shape** (F1) |
| RB3 five states; missing data never becomes an empty successful collection | **mostly implemented**; one legitimate state is misstated (F3) |
| RB4 one generation, R10 conventions, total/returned/remaining, reachable continuation, stale/cross-subject refusal | **implemented** (§2) |
| RB5 inspectable references, owners and independent status dimensions preserved | **implemented** (references block + join key + `movement_reference` + `sources`) |
| RB6 complete source inventory independent of membership; unique counts do not grow | **implemented** (report case; source pane unchanged by family data) |
| Exclusions (no schema change, second authority, inferred family, UI styling) | **respected** — no dashboard file touched, no schema change |
| Forbidden overreach (infer a family, derive a guarantee, pick an arbitrary latest revision, fabricate an assessment, second catalogue/paging store) | **respected**, except that F1's population derivation effectively *selects* a revision |

**Implemented only in a test / not at all:** nothing I could find is test-only. Two clauses are
exercised by construction rather than by a fixture and are **disclosed as such** by the report
(§7.5 unreadable side; §7.6 recorded-history family context). I did not build those reproducers either
— see §8.

**Consumability by L24 (this leaf precedes L24):** the published contract is genuinely consumable —
a required typed `family_context` on the payload, per-family `before`/`after` contexts with the exact
selected family revision, the authored guarantee with version/provenance/seal, the full member list with
each member's exact revision and content, `other_family_revision_ids` for shared members, a third
admitted bounded collection `family_members` with the owner's cursor and total/returned/remaining, and
identity-based references to the evidence/assessment owners instead of copied verdicts. **The two
defects are consumability defects, not cosmetics**: a workspace built on F1 renders one guarantee where
the store records an ambiguous lineage, and F2's sentence would be printed to the reviewer.

---

## 8. Rule 4 — mutation bite-proofs (my own scratch copy)

Each mutation applied to my frozen scratch copy, the named delivery case run, then the file restored
and re-hashed. All five files restored **byte-identically** (digests equal to the frozen set):

| Mutation | Case | Observed | Restored |
| --- | --- | --- | --- |
| M1 `_selection_state` ambiguity branch removed → `compared` | `test_an_ambiguous_family_lineage_names_its_candidates_and_chooses_no_revision` | `1 failed, 3 passed` | `a9b41081…` identical |
| M2 measured-zero reported as `no_subject_selected` | `test_a_selection_in_no_recorded_family_is_a_measured_zero` | `1 failed, 3 passed` | `a9b41081…` identical |
| M3 `_roster_page` `complete` hard-coded `True` | `test_a_truncated_roster_stays_partial_with_the_owners_counts_and_continuation` | `1 failed, 3 passed` | `2cda1d02…` identical |
| M4 `unique_member_revision_total` validator disabled | `test_a_context_whose_counts_do_not_describe_its_roster_is_refused` | `Failed: DID NOT RAISE ValidationError` | `57da690a…` identical |
| M5 `family_guarantee` returns a neighbouring text | `test_a_selected_invariant_resolves_both_recorded_families_with_their_own_guarantees` | `1 failed, 3 passed` | `2cda1d02…` identical |

**All five new guards are load-bearing.** I also ran the inverse proof for F1 (§3.2): the guard exists
and works, but the population it is applied to excludes the canonical memberless head, so the guard
never sees the ambiguity.

**Method note (my own error, corrected, not a finding):** one delivery-case run I launched raced with my
own M4 mutation and reported a spurious failure. I re-verified the scratch tree byte-identical to the
frozen set and re-ran: **25 passed**. The spurious result is not reported as a finding.

---

## 9. Findings

### F1 — blocking — an authored R07 family-lineage ambiguity is collapsed into a confident `compared` answer

* **id** F1
* **severity** blocking
* **file:line** `mcp/src/agents_remember/application/review_family_context.py:398` (`_applicable`) and
  `:421` (`for row in result.directly_containing_families`), fed by
  `mcp/src/agents_remember/memory/knowledge/read.py:678` (`_containing_family_rows`), consumed at
  `:501` (`_heads`)
* **what** For a `FamilyIdentitySeed`, the read owner's `_directly_containing_families` *names every
  revision of the family* (`read.py:307`, docstring: "For a family seed they are the family revisions
  the seed names, and nothing else"), but the paged scope's `_containing_family_rows` emits a row
  **only for a family revision that has at least one `family_member` row** (the row type carries
  `via_invariant_revision_id`). `_applicable` treats that row set as the family's revision population.
  A recorded family revision that cites no member is therefore invisible to `_heads` → `_selection_state`
  sees one head → the composition reports `state="compared"` and pairs the **superseded** revision with
  itself, presenting it as the established before/after selection. Nothing is picked "by label or
  order"; a head is nonetheless chosen, because the ambiguity is never in the population.
* **evidence** `probe_ambiguity_proof.py` (same store, one difference):
  memberless → store revisions 3, R07 heads 2, payload `compared` / `after_heads=[parent]` /
  `after_retained=[parent]`; successors with members → payload `ambiguous` / `after_heads` = both
  successors / `after_retained` = all 3. `probe_canonical_ambiguity.py`: the repo's own
  `build_branching_knowledge_fixture` has exactly the memberless shape and R07 yields 2 heads.
  The delivery's `_author_sibling_family_revisions` (`test_review_family_context.py:566-585`) adds a
  membership per sibling, which is why its `ambiguous` case passes. Artifacts:
  `amb-proof/memberless_successors.json`, `amb-proof/successors_with_members.json`, `canon-amb/`.
* **why it matters** Master rule 3: this leaf's characteristic risk is forced determinacy, and the
  amendment says multiple-family and no-family outcomes are deliberate and must never be forced. The
  packet's RB2 ("Preserve R07 ambiguity … rather than picking a head by label/order") and its failure
  clause ("Ambiguous heads remain explicit with inspectable candidates/reasons") are both broken for the
  repository's canonical shape. L24 consumes `selection.state == "compared"` and the named
  `after_revision_id` as fact, so the accepted reviewer would render one guarantee where two legitimate
  heads are recorded — the frontend cannot repair it, because nothing in the payload says a head was
  skipped. `after_retained` also hides revisions a reader could otherwise inspect.
* **suggested fix** Derive the family's revision population for a family-identity selection from the
  family owner (`families.list_family_revision_ids`) — or add an explicit "every revision of this family
  identity" set to the read request — and run R07's `revision_heads` over *that* population, so a
  memberless head is a head. Keep membership-bearing rows for the roster reads. Add a case using the
  memberless shape (the canonical one), not only the membered shape.

### F2 — blocking — a persisted/rendered sentence is false about the store

* **id** F2
* **severity** blocking
* **file:line** `mcp/src/agents_remember/application/review_family_context.py:587` (`_selection_sentence`),
  count computed at `:608` (`history = len(revisions.before) + len(revisions.after) - 2`), emitted at `:613`
* **what** The sentence ends "…; **N other recorded revision(s) of this family remain selectable
  history**", where N counts *only the revisions this composition's population carried*. When the
  family records revisions no membership cites, N is wrong: the payload prints **0** while the store
  records two further revisions of the same family, both selectable by `FamilyRevisionSeed`.
* **evidence** `amb-proof/memberless_successors.json`:
  `store_revisions_via_family_owner: [3 ids]`, `store_after_heads_via_R07_rule: [2 ids]`,
  `payload_sentence: "…; 0 other recorded revision(s) of this family remain selectable history"`.
  `amb-proof/successors_with_members.json` prints the same family's other revisions, so the same code
  path reports two different counts for the same recorded family depending only on membership rows.
* **why it matters** Master rule 1: a user-visible or persisted sentence that is false about the store is
  blocking, in every state it can render in, even when the delivery's own test blesses it. L24 renders
  this sentence into the reviewer's family header. It also understates the history a reviewer may open.
* **suggested fix** Count the family's recorded revisions from the family owner, or scope the sentence to
  what was actually read ("of the revisions this comparison selected"). F1's fix makes the count correct
  by construction; the sentence should be asserted against the store in a case either way.

### F3 — blocking — a selected family that is recorded with an authored guarantee and an empty roster renders as `no_family_recorded` with no guarantee

* **id** F3
* **severity** blocking (same root cause as F1)
* **file:line** `mcp/src/agents_remember/application/review_family_context.py:354` (`_no_family`),
  sentence at `:370`
* **what** For `FamilyIdentitySeed(family_id=F)` where the after snapshot records F's revision and its
  `joint_guarantee` but no membership rows, the payload is `state="no_family_recorded"`,
  `families_total=0`, **0 entries**, and **no guarantee anywhere**, with the sentence "…holds no family
  membership for the selected family, so **no family context exists for it**; this is a measured zero
  and not an unread, unavailable or filtered scope". A family context *does* exist: the selected family,
  its revision and its guarantee are recorded.
* **evidence** `cand-amb/empty_family.json` plus the store query: after-side `family_revision` row
  `b99ef6ff-…` with `joint_guarantee='A guarantee recorded for a family with no members.'`, zero
  membership rows; payload reprinted above (`guarantee anywhere in family_context: False`). Contrast the
  correct use of the same state for an **invariant** in no family (`transport-out/transport-states.json`:
  `state="no_family_recorded"`, sentence names "the selected invariant").
* **why it matters** RB1 requires the selected family's authored guarantee text/provenance to be exposed;
  RB3 requires confirmed-empty membership to be distinguished from no-family; and the acceptance design
  gives "No recorded family destination" to *an invariant whose membership was actually read and is
  empty*, while a selected family must "lead with its own guarantee comparison and complete member
  context". `no_family_recorded` is documented as a measured zero of the **family population** — which is
  not zero when the family is the selection. The sentence is also false about the store, so rule 1
  applies independently of the clause violation.
* **suggested fix** When the selection *is* a family identity the snapshot records, compose a `recorded`
  entry carrying its selected revision, guarantee and a **measured empty roster** (`members_total=0`,
  complete empty page) instead of falling through to `_no_family`. Distinguish "the selected family has
  no members" from "the selected subject is in no family" in the state vocabulary or at least in the
  sentence.

### F4 — medium — the report's size claim and soft-rail rationale are false, and the crossing is undisclosed

* **id** F4
* **severity** medium
* **file:line** `temp/icr/report-l31.md:270` ("(874 L)") and `:272` ("so the first stays under the 900
  soft rail"), against `mcp/tests/test_review_family_context.py`
* **what** `wc -l mcp/tests/test_review_family_context.py` = **946**. The report states 874 L and states
  that the second module exists so the first stays under the 900 soft rail; 946 is above 900. The hard
  rail (1,200) is respected and no census offender is added, so this is a report-accuracy and
  soft-rail-disclosure defect, not a hard-rail breach.
* **evidence** `wc -l` = 946 (and 190 for the values module, whose stated count is correct); the
  report's own §9 numbers; `report-l31.md:270-272`. The same report's fingerprint records the module's
  current digest, so the file it describes is the file I measured.
* **why it matters** Rule 7 asks the leaf to state file sizes against the 1,200 hard / 900 soft rail
  honestly. Here the stated *reason for the split* is not achieved, so a reader believes a soft-rail
  crossing was avoided when it was not.
* **suggested fix** Correct the two numbers (874 → 946) and either disclose the soft-rail crossing or
  extract the model bite-proofs into the values module.

### Not findings (attacked, held)

* cross-subject / cross-collection / malformed cursors — refused with owner identities and true actions
  (`probe_l31_stale.py`); no silent reset to a fresh first page.
* `payload.page` present only when a collection was named; `pageOf=knowledge` stays the knowledge walk.
* shared member identity referenced twice, unique counts not inflated; before-only membership retained.
* no new `# noqa`, no duplicated owner logic, adapter remains a delegator.
* census: no new ≥1200 offender; catalog pin and 16/66 population intact; lane rows present.

---

## 10. Unreproduced / what I could not establish

Named explicitly, per the five-states rule and the brief's instruction not to accept the report's
environment or coverage claims:

* **`unreadable` side state** (a side whose file opens but whose authored-edge read fails). Report §7.5
  discloses it is exercised "by construction and by the model guard", not by a fixture. I did not build
  a mid-read failure either; I verified only that the model refuses a non-`recorded` side that carries
  members, and that `_no_family` distinguishes `unavailable` when a side could not be read. **Not
  established by execution.**
* **`history=recorded` family context.** Report §7.6 discloses no case. I did not build a recorded-history
  enclosure; the composition reads the resolution's bound datasets, so the same path should serve it.
  **Not established by execution.**
* **The report's 336-case / 24-module regression figure.** I re-ran the 10 modules most affected by the
  required payload field (136 passed, 3 subtests passed) but did not re-run all 24. **Partially
  established.**
* **`resolution_choice` / R08 authored successor-split-merge reach beyond `movement_reference`.** I
  confirmed the reference is R08's own membership identity and that the union owns the movement, but did
  not exercise a split/merge reach end to end.
* **`R02`'s "complete source inventory independent of membership" delta.** I confirmed the source pane
  keys are unchanged and the report's case exists; I did not independently re-measure the inventory
  arithmetic under a repeated membership.
* **My first `no_family` scenario was mislabeled** (the invariant I selected does have a family). I
  corrected it with the transport probe, which selected a genuinely unmembered invariant and got the
  correct measured zero. No finding is filed from the mislabeled run.

---

## 11. The delivery's true strengths

* **The wire shape is the right one and is genuinely consumable by L24.** A required typed
  `family_context` a workspace cannot mistake for absent; per-family `before`/`after` contexts carrying
  the exact selected family revision, the authored `joint_guarantee` with display version, provenance and
  payload seal; the complete member list with each member's exact revision, statement, applicability,
  conditions, exclusions, provenance and source references; `other_family_revision_ids` making a shared
  member inspectable from either context; and a references block that names the evidence/assessment
  owners and the `invariant_revision_id` join key instead of copying a verdict.
* **R10 is reused rather than re-implemented.** `family_members` is a third declared bounded collection
  with the read owner's own cursor, counts and scope; the continuation I drove carries
  `total/returned/remaining = 11/2/9` and `continued_from`, and an initial truncated page is never
  presented as all members.
* **Generation and subject binding are real.** Three distinct attacks (cross-subject, wrong collection,
  malformed) each earned an explicit refusal carrying the owner's expected/observed identities and a true
  action, with no partial page assembled across snapshots.
* **The adapter really is a delegator** (one import, one call, one page branch, one field) and the
  responsibility is split by purpose, not by size, into two named adjacent modules — the seam policy's
  shape, followed exactly.
* **The rails are green and honest where they can be**: ruff/pyright clean on the nine touched files, no
  new `# noqa`, census unchanged at 26 (mcp) / 27 (whole tree), a catalog pin re-measured from a fresh
  `sha256sum` at an unchanged 16/66 population, census-derived registration rows and both lane rows.
* **All five new guards are load-bearing under mutation**, and each mutated file restored byte-identically.
* **The fingerprint discipline is better than the minimum**: untracked files hashed individually, the
  self-referential reading disclosed rather than hidden, and the deliverable digests reproduced exactly
  by an independent recomputation.

---

## 12. Verdict

**`fail`.**

The delivery is well-designed, correctly seamed, honestly fingerprinted and green on every rail I could
run, and its typed payload is the right contract for L24. But it fails the two rules this leaf exists to
be graded against. **F1**: the family-revision population is derived from membership-bearing read rows,
so a recorded family revision that cites no member is invisible — and in exactly that shape (which is the
repository's *own* canonical ambiguity fixture, and the normal intermediate state of a curator who
authors a family revision before its memberships) an authored R07 ambiguity is silently resolved into
`state="compared"` naming the superseded revision, with the two real heads reported as
`after_retained` of length one. That is forced determinacy, the leaf's characteristic risk, and L24
cannot detect or repair it because nothing in the payload says a head was skipped. **F2**: the same state
makes the rendered sentence "0 other recorded revision(s) of this family remain selectable history" false
about the store, which master rule 1 grades blocking in its own right. **F3** is the same root cause
reached from the other direction — a selected family that *is* recorded, with an authored guarantee and
an empty roster, is reported as `no_family_recorded` with the sentence "no family context exists for it"
and its guarantee dropped, breaking RB1 and the confirmed-empty/no-family distinction that RB3 requires.
F4 is a report-accuracy defect. All three blocking findings have one fix locus (derive the family's
revision population from the family owner and compose a recorded context for a selected family that the
snapshot records), and the affected cases are small additions to an otherwise strong module: I expect a
fix round to be short and this leaf to pass on re-verification.

---

**Artifacts** (all under `/home/firefox/projects/ar-coordination/temp/verify-l31/`):
`fingerprint.sh`, `fp-start-sample1.txt`, `fp-start-sample2.txt`, `fp-end.txt`, `self-gate.log`,
`self-gate-history-part1.log`, `worker-recipe-now.txt`, `their-reading.txt`, `mine-reading.txt`,
`frozen/` (18 files + `frozen-hashes.txt`), `cand/` (mutable scratch candidate), `base/`, `base2/`,
`base-defect/replay.txt`, `base-probe/`, `base-amb/`, `BASE-family-selected.json`,
`probe_family_context.py`, `probe_family_ambiguity.py`, `probe_candidate_family.py`,
`probe_ambiguity_proof.py`, `probe_ambiguity_internals.py`, `probe_canonical_ambiguity.py`,
`amb-proof/`, `canon-amb/`, `cand-amb/`, `probe_l31_transport_states.py`, `transport-out/`,
`probe_l31_cursor.py`, `cursor-out/`, `probe_l31_stale.py`, `stale-out/`,
`probe_l31_crosscollection.py`, `cross-out/`, `delivery-cases-clean.txt`, `regression.txt`,
`mutations-l31-mine.py`, `mutations-mine.txt`, `mutation-m4.py`, `rails-base/`, `rails-cand/`,
`base-offenders.txt`, `cand-offenders.txt`, `off-4c000b11.txt`.

No production code was edited, nothing was committed, and the memory worktree was not touched.
