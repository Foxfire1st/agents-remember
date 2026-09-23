# Independent adversarial verification — leaf L31 / ICR-R31@v1 — ROUND 2 (fix round 1)

**Verdict: `pass-with-findings`** — no blocking finding. F1, F2, F3 and F4 are all closed and
independently reproduced by me on the frozen post-fix bytes. One low finding (E1) records that the
guard-bite evidence for two mutations was taken on an earlier revision of one file than the delivered
one.

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l31-ar/260921-icr-l31`,
branch `ar/260921-icr-l31`, base `4c000b11c5243e4a8e77c08e87984fff00c1d94b` (ICR-R23 / L23), plus the
fix-round-1 delta. My round-1 verdict (`temp/icr/verify-l31.md`, `fail`) is the baseline I diff
against. Report: `temp/icr/report-l31.md` (419 lines, §"Fix round 1" from line 300). Nothing committed;
no production code and no memory worktree was touched by me.

---

## 0. Fingerprints — the worker's recipe reproduces, and the candidate never moved

Combined recipe (`fingerprint.sh`, as in round 1): tracked `git diff | sha256sum`; staged diff hash;
one sha256 per untracked file; sha256 over the newline-joined list; paired with `git status --porcelain`.

| Reading | tracked diff | untracked files | untracked combined | porcelain |
| --- | --- | --- | --- | --- |
| start sample 1 | `3d55a927…` | 15 | `51441a19…` | 21 |
| start sample 2 (≥90 s later) | `3d55a927…` | 15 | `51441a19…` | 21 |
| end (after all round-2 work, before writing this file) | `3d55a927…` | 15 | `51441a19…` | 21 |
| final (after writing this file) | `3d55a927…` | 16 | `4855aeaa…` | 22 |

`diff` of the two start readings: **identical**; the end reading is identical to both. The **final**
reading differs from start by exactly one path — `?? temp/icr/verify-l31-round2.md`, this verdict, which
the brief requires me to write into the leaf — and by nothing else; the tracked delta is unchanged and
all **12 deliverable digests** still equal the frozen set (`frozen-r2/frozen-hashes.txt`) byte for byte.
**No deliverable byte moved during round 2.**

### Their stated recipe, recomputed

`temp/icr/fingerprint-l31.sh` run verbatim from the leaf worktree: **39 lines, byte-for-byte identical**
to `temp/icr/fingerprint-l31.txt` (`diff` exit 0; `mine-r2-norm.txt` vs `theirs-r2-norm.txt`). That
covers all three readings, including the status entry count of **21** — this time no self-referential
gap, because the evidence file already existed when the readings were taken.

* reading 1 `git diff | sha256sum` = **`3d55a9273d62c7d829ed03cc88e30f03b6d01391cf12fe08d72ae5519aa8a3d4`** — matches the stated value;
* reading 3 deliverable whole-list = **`1a86bb47f4d3feeb25da3e38412d71abb32b1b5f2cf03f080476b0619fa0c6e8`** — matches (12 files now, the new population module included);
* two readings >90 s apart is exactly what my own two start samples reproduce independently.

The narrowing to deliverable paths (excluding `temp/`) is stated in the recipe's own header and in the
report, and reading 2 still names every `temp/icr/` path, so the narrowing hides nothing.

---

## 1. Check 1 — F1 (forced determinacy): CLOSED

**My own one-variable experiment re-run on the post-fix bytes** (`probe_ambiguity_proof.py`, the same
store, the only difference being whether each successor records a membership):

| | memberless successors | successors with members |
| --- | --- | --- |
| revisions the store records (family owner) | 3 | 3 |
| heads by R07's own rule | 2 | 2 |
| payload `selection.state` | **`ambiguous`** | **`ambiguous`** |
| payload `after_heads` | both successors | both successors |
| payload `after_retained` | all 3 | all 3 |
| chosen revision | **none** | **none** |

The two variants now agree, which is the point: the population is the family owner's own revision list,
not the membership-bearing read rows.

Deeper check of what a consumer receives (`probe_r2_f1.py`, canonical memberless shape):

```
store_recorded_revisions : 3
store_R07_heads          : [0dc63599…, 8a42534b…]        # the two memberless successors
entry_state              : unresolved
selection_state          : ambiguous
before_revision_id       : None      after_revision_id : None      <- NO revision chosen
after_heads              : [0dc63599…, 8a42534b…]
candidates               : [ {16997591…, "The retry budget is shared by integration and synchronization."},
                             {0dc63599…, "guarantee-right"},
                             {8a42534b…, "guarantee-left"} ]        <- EVERY head, each with its OWN guarantee
side_guarantees          : {before: None, after: None}              <- no guarantee presented as the family's own
context_state            : partial
sentence                 : "ambiguous family revision selection … several legitimate head revisions …
                            no authored successor ordering between them … so no revision was chosen;
                            each candidate's own guarantee is listed instead"
```

So L24 receives everything it needs to detect and render the ambiguity: the state, both head sets, the
chosen-revision fields explicitly `None`, and every candidate head with its own authored guarantee text
— rather than one guarantee silently chosen.

**Over-correction guard — a genuinely linear lineage still reads `compared`** (both variants):

| | linear, memberless successor | linear, successor with a member |
| --- | --- | --- |
| store revisions | 2 | 2 |
| R07 heads (after) | 1 | 1 |
| `selection.state` | **`compared`** | **`compared`** |
| before / after chosen | parent / successor | parent / successor |
| candidates | `[]` | `[]` |
| side guarantees | both revisions' own texts | both revisions' own texts |

The fix has not over-corrected: a one-head lineage is still an established pair, and the family's
guarantee is still presented per side.

**Independently reproduced the worker's pre-fix failure proof on MY OWN round-1 frozen bytes.**
`prefix-mine/` = a scratch checkout at base overlaid with my round-1 frozen deliverables (composition
digest `a9b410812f9f…` = my frozen round-1 `a9b41081…`; model `57da690a2a9a…` = my frozen round-1
`57da690a…`) plus the new case modules:

```
$ pytest mcp/tests/test_review_family_context_population.py -q -m '' -n0 --tb=line
E  AssertionError: assert {'77503376-…', …} == {'300d90a8-…', …}          # (a) heads differ
E  AssertionError: []        assert 0 == 1                                # (b) no entry at all
E  AssertionError: assert '1 before and 3 after revision(s)' in
   '…records a membership of the selected subject in; 0 other recorded revision(s) of this family
    remain selectable history'                                            # (c) the false sentence
3 failed, 1 passed
```

Exactly the worker's figure, on bytes **I** froze and verified in round 1, with the (d) over-correction
guard passing on both byte sets by design.

---

## 2. Check 2 — F2 (the history sentence): CLOSED, and it survived my falsification attempts

`probe_r2_f2_f3.py` (chain whose interior revision is recorded by **both** snapshots):

```
before owner-recorded : [mid, parent]                    (2)
after  owner-recorded : [mid, head, parent]              (3)
selection.state       : compared   before=mid   after=head
sentence              : "… the two snapshots record 2 before and 3 after revision(s) of this family,
                         and 1 other recorded revision(s) of this family remain selectable history"
```

Truth check against the store: union of recorded = `{parent, mid, head}` = 3; chosen = `{mid, head}`;
other = `{parent}` = **1**. `parent` is recorded by **both** snapshots and is counted **once**.

**Sharpest double-count attempt** (`probe_r2_f2_doublecount.py`): a four-revision chain
`p → a → b → h` authored identically on **both** snapshots, so all three interior revisions are recorded
twice:

```
before owner-recorded: 4        after owner-recorded: 4
selection.state: compared       chosen: h / h
ACTUAL SENTENCE: "… the two snapshots record 4 before and 4 after revision(s) of this family, and
                  3 other recorded revision(s) of this family remain selectable history"
assert '4 before and 4 after': True      assert '3 other': True
```

`3`, not `6`: the count is over the **distinct** recorded revisions, so a revision present in both
snapshots is not double-counted, and memberless revisions do count (all three interior ones are
memberless here). **Both falsification attempts failed to falsify: the sentence is true about the
store in every state I built.**

---

## 3. Check 3 — F3 (a recorded family rendered as no family): CLOSED, with both contrasts intact

`probe_r2_f2_f3.py`, a family the after snapshot records with an authored `joint_guarantee` and **zero**
membership rows:

```
context_state : recorded          entry_state : recorded
before: state=not_recorded  rev=None  members=0/0  page=None  guarantee=None
        detail="this snapshot records no revision of the family this selection names, so no roster is
                claimed for it and no guarantee is presented as the family's own"     <- TRUE
after : state=recorded  rev=101a056f…  members=0  members_total=0  page.complete=True   <- MEASURED empty
        recorded_revision_ids=[101a056f…]
        guarantee={joint_guarantee="A guarantee recorded for a family with no members.",
                   display_version="1", state_at_origin="proposed",
                   provenance_actor="agent:read-fixture", payload_digest="56be1a7c…"}   <- provenance + seal
        detail="the after snapshot's recorded roster of family revision 101a056f… was read whole:
                0 recorded membership(s), all carried here"                            <- TRUE
```

So: `recorded`, the guarantee exposed with its provenance and payload seal, and a **measured** empty
roster (a complete page carrying 0 of 0) — not `no_family_recorded`.

**Contrasts still correct:**

* an invariant recorded in no family on either snapshot → `state="no_family_recorded"`,
  `families_total=0`, 0 entries, sentence naming **"the selected invariant"**, "a measured zero and not
  an unread, unavailable or filtered scope" (unchanged; re-verified over the real HTTP route too);
* a family **neither** snapshot records → the review is **refused** before any context exists:
  `state=refused`, `code=comparison_refused`, detail `selector_absent: the selector names no family
  identity in the selected snapshot …` — not converted into `empty`, not converted into `recorded`;
* an added family (after-only): `selection.state="added"`, `before.state="not_recorded"`,
  `after.state="recorded"` — unchanged from round 1.

`_no_family` now has two honestly-worded branches (family axis: "a measured zero of the family
population"; invariant axis: "holds no family membership for the selected invariant"), and I found no
sentence in the module that is false about the store.

---

## 4. Check 4 — the new guard `recorded_revision_ids`: load-bearing, proven both ways

The guard is `if recorded and self.family_revision_id not in self.recorded_revision_ids: raise`
(`models/knowledge/review_family_context.py:312`). My own mutations, each restored and re-hashed:

| Mutation | Case | Observed | Restored |
| --- | --- | --- | --- |
| N1 validator branch disabled | `test_a_recorded_side_may_not_name_a_revision_its_family_does_not_record` | `FAILED` | `9d71d9e0…` identical |
| N2 **production** published list emptied for a recorded side (`review_family_rosters.py:262` → `()`) | `test_a_recorded_family_with_no_members_is_recorded_not_absent` | `FAILED` | `e517443a…` identical |
| N2 same mutation | `test_the_history_sentence_is_measured_against_the_family_owner` | `FAILED` | `e517443a…` identical |

N2 matters more than N1: it proves the guard is **reachable from the production path** (the side cannot
be built at all if the composition publishes a list that omits the revision it selected), not merely a
hand-built-context validator.

**Honest note on my own first attempt:** my initial N2 pointed at the *canonical ambiguity* case, which
**passed** under the mutation — because that case's sides are `not_resolved`, so the `recorded` branch
legitimately does not apply. That is correct behaviour, not a gap, and I re-ran N2 against cases whose
sides are `recorded`. I record it because it is the same class of mistake the worker disclosed.

---

## 5. Check 5 — F4: CLOSED, and the other stated numbers re-measured rather than trusted

| Claim | Where | My measurement | Agrees? |
| --- | --- | --- | --- |
| case module 946 L (corrected from 874) | `report-l31.md:274` | `wc -l` = **946** | yes |
| values module 210 L | `:276` | **210** | yes |
| population module 343 L (new) | `:277`, `:314` | **343** | yes |
| composition module 929 L, soft rail **exceeded and disclosed** | `:279`, `:99` | **929** | yes |
| roster module 685 L | `:99` | **685** | yes |
| model 526 L | `:99` | **526** | yes |
| adapter 1,073 → 1,112 L | `:99` | **1,112** | yes |
| `models/knowledge/review.py` 1,189 L (under 1,200) | `:99` | **1,189** | yes |
| catalog pin `23dd7c0f…`, 16 contracts / 66 artifacts | `:389` | fresh `sha256sum` = **`23dd7c0f85b50585e8968122f60f50e87e15bfbfa241b28b77b7c71b2a41e252`**, **16 / 66** | yes |

The soft-rail disclosure F4 asked for is now explicit in two places (the rails table and §9) and names
both modules, and it correctly distinguishes the hard rail (respected, no new census offender) from the
soft rail. **F4 is closed.**

---

## 6. Check 6 — regression and rails on the post-fix bytes

All run by me against the frozen bytes (`cand-r2`, a scratch checkout carrying the 12 deliverables).

```
sibling + delivery set, 29 modules, -m '' -n0   -> 374 passed, 3 subtests passed in 292.6 s
census + lane pair, -m ''                       -> 5 passed in 59.2 s
layering / single-owner / app-guards / file-size-> 18 passed in 11.6 s
ruff check (10 changed files)                   -> All checks passed!        [exit 0]
ruff format --check (same 10)                   -> 10 files already formatted [exit 0]
ruff check (whole mcp)                          -> All checks passed!        [exit 0]
pyright --pythonpath <venv> (10 files)          -> 0 errors, 0 warnings, 0 informations
suppressions (new/changed + tracked diff)       -> none (no noqa / type: ignore / pyright: ignore)
whole-tree >=1200 census                        -> 27 / candidate 27, IDENTICAL path sets (mcp-scoped 26 / 26)
case budgets                                    -> unit 2,726 / 4,000 ; integration 462 / 1,000
```

**On the 374 vs 342 + 25:** my glob selects **29** modules (the worker's counted set is 26 siblings plus
the 3 delivery modules). 374 − 25 (delivery) = 349 over my 26 siblings, against their 342 over their 26;
my glob additionally picks up `test_knowledge_family_composition_boundaries.py` and
`test_knowledge_family_integrity_pipeline.py`, which their listed set does not name. Either way:
**0 failures**, and my set is a superset in coverage terms. Their "25 passed" for the three delivery
modules I reproduce exactly.

**Case budgets measured exactly as claimed.** The budget hook splits the collected population by the
`integration` marker (`tests/conftest.py:149-158`), with `unit_case_budget = 4000` and
`integration_case_budget = 1000` in `pyproject.toml:278-279`. Collection: **3,188 collected, 462
integration → 2,726 unit**. Both under budget, matching the worker's figures.

**Census-derived rows are genuinely derived, proven by perturbation.** The two rows are
`[artifact #52] mcp/tests/diff_scope_test_support.py` and
`[artifact #53] mcp/tests/read_scope_test_support.py`, each having gained **both** new case modules
(`test_review_family_context.py` and `test_review_family_context_population.py`). Removing exactly one
of those four added paths from the catalog:

```
$ pytest tests/test_dependency_ownership_ast_helpers.py -q -m '' -n0
- mcp/tests/diff_scope_test_support.py: consumer proof differs from source-derived ownership;
  missing=['mcp/tests/test_review_family_context_population.py'], unsupported=[]
FAILED test_repository_inputs_reach_their_supported_consumers
FAILED test_production_proof_adds_no_governed_evidence_artifact
2 failed in 49.33s
```

and after restoring the file byte-identically (`23dd7c0f…`): **2 passed**. So the rows are derived from
the census's own finding, not guessed, and the re-pin is load-bearing.

**Guard bite-proofs, re-run by me on the frozen bytes** (every mutated file restored byte-identically;
scratch verified pristine against the frozen digests afterwards):

| Mutation | Case | Result |
| --- | --- | --- |
| M1 head rule stops refusing several heads | `…ambiguity_names_its_candidates_and_chooses_no_revision` | FAILED |
| M1b same mutation | `test_the_canonical_memberless_successor_shape_is_an_ambiguity` (**new**) | FAILED |
| M2 roster page claims completeness | `…truncated_roster_stays_partial…` | FAILED |
| M3 measured zero → `no_subject_selected` | `…measured_zero` **and** the new `…measured_zero_and_the_absent_family_stay_distinct` | both FAILED |
| M4 guarantee from a neighbouring revision | `…both_recorded_families_with_their_own_guarantees` | FAILED |
| M6 unique-member validator disabled | `…counts_do_not_describe_its_roster_is_refused` | FAILED |
| N1/N2 the new `recorded_revision_ids` guard (see §4) | two recorded-side cases | FAILED |

The worker's substantive conclusion — "all guards bit" — holds on the delivered bytes.

---

## 7. Check 7 — the worker's disclosed instrument artefact: judged, and it stands

Their disclosure: the first post-fix regression run overlapped the mutation script and read momentarily
mutated bytes, producing two spurious failures with M1's symptom (`compared` where the case expects
`ambiguous`); the reverts were digest-verified; the clean re-run is the 342-pass figure.

Judged on the bytes and on my own re-run:

* `temp/icr/mutations-l31.txt` records **six** mutations, each `reverted-bytes-identical` with the
  digest printed before and after — so every mutation was reverted, and the instrument cannot have left
  mutated bytes behind;
* my own clean re-run of a **superset** of their set (29 modules) gives **374 passed, 0 failed** — no
  real failure is hidden behind the artefact;
* the symptom they report is exactly reproducible: **I hit the same class of artefact myself twice** in
  this round (a stale pytest process from a killed run still executing while I mutated, and an N2
  mutation pointed at a case whose sides are `not_resolved`). Both were resolved the same way — verify
  the tree against the frozen digests, re-run clean — and neither is reported as a finding;
* I re-verified the scratch tree byte-identical to the frozen set after every mutation round.

**Conclusion: a genuine instrument artefact, correctly disclosed, with nothing hidden behind it.** It is
not a finding.

One accuracy defect does sit next to it, filed as E1: the recorded digests for M2 and M4 are not the
delivered bytes.

---

## 8. Finding

### E1 — low — the recorded guard-bite evidence for M2 and M4 cites an earlier revision of `review_family_rosters.py`, and the report presents that stale digest as a restored post-fix digest

* **id** E1
* **severity** low
* **file:line** `temp/icr/mutations-l31.txt:6` and `:14` (both `mutation digest 81f5e4a9a5a5 ->
  81f5e4a9a5a5`), cited again in `temp/icr/report-l31.md:407`
  ("verified the restored digests (`29661d3b…`, `81f5e4a9…`, `9d71d9e0…`, matching the mutation
  script's own before/after readings)")
* **what** M2 and M4 mutate `mcp/src/agents_remember/application/review_family_rosters.py`. The
  delivered file's digest is **`e517443a546e1f997c015dfb485504d9872874d07a813f92fbc54fce6deb95bf`**
  (confirmed by my own recomputation of their recipe and by the frozen set), not `81f5e4a9…`. The other
  two mutated files do match the delivered bytes (`29661d3b…` composition, `9d71d9e0…` model), so this
  is specific to one file: the roster module changed after those two mutations were run, and the
  evidence and the method note were not re-taken.
* **evidence** `sha256sum mcp/src/agents_remember/application/review_family_rosters.py` = `e517443a…`;
  their own fix-round reading 3 lists the same `e517443a…` for that path, so the two artifacts in the
  delivery disagree with each other; `81f5e4a9…` appears nowhere in the current tree.
* **why it matters** The claim "all guards bit" is only as good as the bytes it was taken on, and as
  recorded it does not cover the delivered bytes for two of the six mutations — a reader cannot tell
  that M2/M4 were proven on a different revision of that file. It also contradicts the report's own
  fingerprint block, which is the artifact a later leaf trusts. **I verified the conclusion still
  holds**: re-running both mutations myself on the frozen bytes fails the named cases (M2
  `…truncated_roster_stays_partial…`, M4 `…both_recorded_families_with_their_own_guarantees`), and each
  file restored byte-identically. So nothing is hidden — this is an evidence-accuracy defect, not a
  correctness one, which is why it is low and not blocking.
* **suggested fix** Re-run `mutations-l31.py` over the frozen post-fix bytes and re-record
  `mutations-l31.txt` (the script already prints before/after digests), then correct the digest triple
  quoted at `report-l31.md:407` to `29661d3b…`, `e517443a…`, `9d71d9e0…` — or state explicitly which
  revision each row was taken on.

---

## 9. Unreproduced / what I did not establish

Named rather than accepted from the report:

* **`unreadable` side state** (a side whose file opens but its authored-edge read fails). Still
  construction-only, as the report discloses; I did not build a mid-read failure either. The model
  guard and the `unavailable`/`partial` composition paths are inspected, not executed.
* **`history=recorded` family context.** Still no case, as the report discloses. Not executed by me.
* **The report's exact 342 / 26-module figure.** I ran a 29-module superset (374 passed, 0 failed) and
  the 3 delivery modules separately (25 passed); I did not reproduce their precise 26-module partition.
* **R08 split/merge reach** beyond the `movement_reference` identity — unchanged from round 1: the
  reference is verified to be R08's own membership identity, but a split/merge reach is not driven end
  to end.
* **The two population-module cases (a) and (b) as the worker wrote them** — I verified their *substance*
  independently through my own probes on my own fixtures; I also reproduced their pre-fix failure on my
  own frozen round-1 bytes, which is stronger than re-reading their case code.

---

## 10. The delivery's strengths, as they stand after the fix

* **The fix is at the right locus and is not a patch over the symptom.** The family revision population
  now comes from the family owner (`families.list_family_revision_ids`) on both axes, the read still
  supplies the roster, and `recorded_revision_ids` publishes the owner's own list so a consumer can see
  the history the selected revision was chosen from. One root cause, one fix, three findings closed.
* **F1's repair is complete for a consumer, not just for a test.** No revision is chosen in the
  ambiguous state, both head sets are published, every head is a candidate with its own guarantee, and
  neither side presents a guarantee as the family's own — which is exactly what L24 needs to render the
  ambiguity rather than a silently chosen winner.
* **The over-correction guard is real and passes on both byte sets**, and I verified independently that
  a genuinely linear lineage still reads `compared` with both sides' own guarantees.
* **The history sentence is now measured, scoped and true** in every state I could build, including the
  sharpest double-count case (4 revisions recorded by both snapshots → "4 before and 4 after … 3 other",
  not 6). Both falsification attempts failed.
* **The empty-but-recorded state is now first-class**: `recorded`, guarantee with provenance and seal,
  a measured complete empty roster, a truthful one-sided `not_recorded` on the other side, and both
  contrasts (invariant-in-no-family, family-neither-snapshot-records) preserved.
* **Rails are green and honest**: ruff/pyright clean on ten files with no suppressions, census unchanged
  with identical path sets, the catalog re-pinned from a fresh measurement at an unchanged 16/66 with
  genuinely derived rows (proven by perturbation), budgets measured under their ceilings, and the
  soft-rail crossing disclosed instead of hidden — which is precisely what F4 asked for.
* **The evidence discipline is good and got better**: the fingerprint recipe reproduces byte for byte on
  all three readings with no self-referential gap, the pre-fix failure proof is real (I reproduced it on
  my own frozen round-1 bytes), and the instrument artefact was disclosed rather than buried. E1 is the
  one place the record does not match the delivered bytes.
* **No regression**: the whole behavioural surface I verified in round 1 — real HTTP transport, the
  three bounded-collection cursor refusals, `payload.page` truthfulness, before-only membership
  retention, shared-member identity, source-inventory independence — is byte-for-byte the same
  behaviour on the post-fix bytes.

---

## 11. Verdict

**`pass-with-findings`.**

The three blocking findings are closed, and closed properly: I re-ran my own one-variable experiment and
the canonical memberless ambiguity is now `ambiguous` with no revision chosen, both head sets, every
head a candidate with its own guarantee, and no side presenting a guarantee as the family's own, while a
genuinely linear lineage still reads `compared` — so the fix repaired the population rather than
loosening the rule. The history sentence survived both of my falsification attempts (the sharpest being
four revisions recorded by both snapshots, correctly counted as three), and the recorded-but-empty family
is now `recorded` with its guarantee, provenance, seal and a measured complete empty roster, with both
contrasts intact and the family-neither-snapshot-records case still refused rather than rendered empty.
F4 is closed with the corrected figure (946 L) and an explicit soft-rail disclosure, and every other
stated number I sampled re-measured correctly. The new `recorded_revision_ids` guard is load-bearing
from both the validator and the production path. Rails are green, the census stays at 26 (mcp) with
identical path sets, the catalog re-pin is genuine and its rows provably derived, the budgets match
exactly, and the regression is clean at 374 passed. The worker's disclosed instrument artefact is
credible and hides nothing — I reproduced the same class of artefact twice myself and cleared it the same
way. The single finding, E1, is low and evidentiary: two mutations' recorded digests describe an earlier
revision of the roster module than the one delivered, and the report quotes that stale digest as a
restored post-fix digest; I verified both guards still bite on the frozen bytes, so the conclusion stands
and no correctness claim is undermined. Nothing here blocks the leaf.

---

**Artifacts** (all under `/home/firefox/projects/ar-coordination/temp/verify-l31/`):
`fingerprint.sh`, `fp-r2-start1.txt`, `fp-r2-start2.txt`, `fp-r2-end.txt`, `worker-recipe-r2.txt`,
`mine-r2-norm.txt`, `theirs-r2-norm.txt`, `frozen-r2/` (21 files + `frozen-hashes.txt`), `cand-r2/`
(mutable scratch), `probe_ambiguity_proof.py`, `amb-proof-r2/`, `probe_r2_f1.py`, `r2-f1/`,
`probe_r2_f2_f3.py`, `r2-f2f3/`, `probe_r2_f2_doublecount.py`, `r2-dc/`, `prefix-mine/` (my round-1
frozen bytes overlaid at base), `probe_l31_transport_states.py`, `r2-transport/`, `probe_l31_cursor.py`,
`r2-cursor/`, `probe_l31_stale.py`, `r2-stale/`, `probe_l31_crosscollection.py`, `r2-cross/`,
`probe_family_context.py`, `r2-fc/`, `rails-r2/rails.txt`, `rails-r2-static/static.txt`,
`r2-regression.txt`, `perturb-census.py`, `mutation-r2-guards.py`, `mutation-r2-m3.py`,
`mutation-r2-newguard.py`, `mutation-r2-n2.py`, `cand-r2-offenders.txt`, `off-4c000b11.txt`.

No production code was edited, nothing was committed, and the memory worktree was not touched.
