# L15 (ICR-R15@v1) — Measured assessment currentness: worker report

| Field | Value |
| --- | --- |
| Leaf | `260921-ICR-L15` |
| Requirement | `ICR-R15@v1` (sole primary; R11/R14/R26 are dependencies, not claimed here) |
| Code worktree | `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l15-ar/260921-icr-l15` |
| Branch / base | `ar/260921-icr-l15`, base = `3103e1142a3ded8a843c3e5bbefca14861ba4a58` (master tip, L21) |
| Candidate fingerprint (code, frozen before this report was written) | `git diff \| sha256sum` = `b43b793f30ad8a90eb3a7436847c820408dbf18aa6b9b59479eca88e2143e9af`; `git status --porcelain` = 15 code entries (14 `M` + 1 `??`: the new module). After this report exists the status list is 16 entries — the extra is `temp/icr/report-l15.md` — and the `git diff` hash is unchanged, because that hash is blind to untracked files. No code byte changed after the frozen-bytes run in §6. |
| Git operations | none (no commit, no push, no memory worktree, no task-document write) |

## 1. The defect, exactly

`ICR-R15@v1` Problem: *"Absent measurement becomes stale, while merely supplying `current={}` marks an
assessment current."* Both halves were live in one line,
`application/review_record_rendering.py:268-272` at base:

```python
stale_ids = (
    ()
    if records.current is not None          # <- {} is "not None": every record becomes current
    else tuple(assessment.assessmentId for assessment in stored)  # <- no measurement: every record is stale
)
states[subject_id] = assessment_state_for(stored, stale_ids=stale_ids)
```

so the *presence of a mapping* decided a fact about the store. Three consequences, all reproduced
(§4): an unmeasured assessment rendered `stale`; an empty measurement rendered `current`; and a
*measured movement* also rendered `current` (a partly-covered mapping short-circuits the comparison
entirely). The `assessment_currentness` channel meanwhile said `not_measured` — "so every stored
assessment keeps the unmeasured state the shipped projection reports for it" — a sentence that was
false about the record set it described, because the projection below it was reporting `stale`.

The same collapse existed in the curator-coherence projection
(`worktrees/integration/closeout/curator_coherence.py:609-655` at base):
`curator_coherence_subject_assessment_state(validated, subject)` with no measurement answered `stale`,
and a KS-era case pinned that (`test_curator_review_assessment_publication.py:551-560`), as did
`test_review_assessments.py:606-615` and `test_knowledge_review_surface.py:670-734` (the packet's S26
anchor).

## 2. What shipped

Five states, distinguished by a **measurement** rather than by the presence of a mapping, and the
production record adapter now produces that measurement for the comparison the review is viewing.

### 2.1 The vocabulary and the projection — `models/lifecycles/review_assessment.py`

| Change | Where |
| --- | --- |
| `AssessmentBindingStatus = Literal["not-measured","current","stale","unavailable"]` + `ASSESSMENT_BINDING_STATUSES` + `CURRENTNESS_STATES` | `:81`, `:104-115` |
| `SubjectAssessmentStatus` gains `not-measured` and `unavailable` (`none-recorded` is the packet's *unassessed*) | `:117-135` |
| `AssessmentEntry.currentness` is the four-member status; `SubjectAssessmentState` gains `notMeasuredCount` and `unavailableCount`, and the model refuses counts that over-count the records or a `status` its own records contradict (`_counted_status`) | `:439-511` |
| `assessment_state_for(assessments, *, statuses=None)` — an assessment nobody measured reports **`not-measured`**; there is no argument that means "assume current" and none that means "assume stale" | `:513-568` |

Status precedence over a subject's records: `stale` (a measured movement) → `unavailable` (a failed
measurement) → `unresolved` (an authored open question) → `not-measured` → `current`.

### 2.2 The measurement value and the equality decision — `models/lifecycles/review_assessment_binding.py`

* `AssessmentCurrentnessMeasurement` (`:224-247`): `state ∈ {measured, not-measured, unavailable}`,
  `values` (identity → value), `detail`, `unmeasured` + `unmeasured_detail`. **Its keys are its
  coverage**, which is what makes a partial measurement answerable without inventing an answer.
* `measured_binding_status` (`:317-350`) — the whole rule:
  * a failed measurement → `unavailable`;
  * **a measured disagreement** (a declared identity the measurement holds a *different* value for,
    via the shipped `disputed_dependencies`) → `stale`;
  * a completed measurement that covered every declared identity and disagreed nowhere → `current`;
  * everything else → `not-measured` (including an empty measurement, which covers nothing).
* `unmeasured_identities` (`:299-314`), `measured_binding_statuses` (one world for a whole bundle,
  `:352-369`), `supplied_measurement_statuses` (a caller's per-record mapping, `:372-398`), and
  `subject_state(assessments, current=None)` (`:401-417`).
* `assessment_currentness` / `disputed_dependencies` / `require_current_*` are **unchanged**: the
  shipped comparison stays the one equality authority; `AssessmentCurrentness` keeps its two-member
  literal so `compose_currentness` (KS-R16) and the refusal helpers are untouched.

### 2.3 The production measurement — `application/review_assessment_currentness.py` (new; 234 L at first delivery, 226 L after fix round 1)

* `comparison_currentness_measurement(resolved)` (`:89-131`) measures the identities **the viewed
  comparison itself publishes**, from the resolution:
  `code-tree:baseline` / `code-tree:candidate` (algorithm `git-object`, the trees the resolution
  bound), `evidence-bytes:scope-manifest` = `canonical_sha256(leaf_id)`,
  `evidence-bytes:comparison` = `canonical_sha256(contract_path)` and
  `validator:{curator-evidence-resolver/v1, review-assessment-policy/v1}` — each digested by the same
  `canonical_sha256` the declaration builder used, and each a name an assessment declares. It performs
  no I/O, so it cannot fail halfway and its state is always `measured`: the two validator identities
  are published by the shipped assessment publication unconditionally, so a resolution that binds no
  endpoint still publishes those two (`fix round 1, F2` — the original text claimed a `not-measured`
  return that the code could never produce).
* **The recorded generation is the one the resolution bound** (`:43-51`): `history == "recorded"`
  resolves the manifest's own code objects, the live read resolves the candidate captured now, and
  neither falls back to the other.
* `currentness_channel(collection, measurement, assessments)` (`:125-181`) states the measurement's
  availability: `unavailable` (the assessment authority could not be read, carrying its own reason —
  nothing was measurable) → `none_recorded` (a real zero: no binding existed) → `recorded` with the
  count of bindings compared and the **declared identities the comparison publishes no value for**
  named in `unreadable`. Those three are the only states a resolved candidate can earn: the composing
  read always measures, so `not_measured` is unreachable here and the function asserts that contract
  rather than rendering an unperformed measurement as a comparison that happened
  (`fix round 1, F2`; the original text listed a fourth `not_measured` branch that no input could
  reach).

### 2.4 The adapter, the projection and the two consumers

| Owner | Change |
| --- | --- |
| `application/review_evidence_records.py` | produces the measurement in `_resolved_records` (`:214`, `:217`, `:226`); `_COLLECTION_OWNERS["assessment_currentness"]` now names the real owner (`:143`); the unresolved-candidate path reports the currentness collection `unavailable` like its siblings (`:708-715`); docstring records the delegation (`:30-34`) |
| `application/review_record_rendering.py` | `ReviewRecordInputs.current` (a bare mapping) → `currentness: AssessmentCurrentnessMeasurement \| None` (`:132`); `subject_states` delegates to the shipped projection and no longer tests anything for presence (`:267-291`) |
| `application/review_comparison_freeze.py` | `current_measured` is now "a measurement was *performed*" (`currentness.state == "measured"`), not "a value was present" (`:522-528`) |
| `worktrees/integration/closeout/curator_coherence.py` | `curator_coherence_assessments` / `curator_coherence_subject_assessment_state` classify per record through `supplied_measurement_statuses`; `_stale_assessment_ids` (the stale-ids helper) is gone; omitting `current` now means *nothing measured anything* and answers `not-measured` (`:609-680`) |
| `memory_quality/knowledge_review.py` | the persisted checklist gains a `not-measured` limitation row and count, so `| stale | 0 |` can no longer be read as "nothing moved" when nothing was measured (`:33-46`, `:118-126`, `:153-186`); "stale" now says *a measurement found the binding moved* |
| `application/memory_quality/controller.py`, `memory_quality/family_review.py` | pass `notMeasuredCount` through (`:813`, `:428`) |
| `models/knowledge/review.py` | `ReviewAssessmentDisplay.binding_state` documented as the measured status (net 0 lines — the file stays at 1198, so the ≥1200 census is unchanged) |

### 2.5 Seam report (policy `notes/03-adapter-seam.md`)

* `application/knowledge_review.py` is **untouched** (1041 lines before and after): this leaf adds no
  behavior to the over-rail adapter, so there was no responsibility to move out of it. The defect and
  the projection live in R02's extraction `application/review_record_rendering.py`, the composition in
  R14's `application/review_evidence_records.py`, and the rule in the currentness model.
* The new responsibility — *measuring the stored bindings against the comparison being viewed* — is a
  purpose-named adjacent module, `application/review_assessment_currentness.py`: **one implementation**,
  called by the record adapter and by nothing else; `review_evidence_records` and
  `review_record_rendering` delegate to it and to the model rule. No second seam pattern was invented.
* Adapter line counts: `review_evidence_records.py` 869 → 879 (under the 900 soft rail),
  `review_record_rendering.py` 473 → 488, `knowledge_review.py` 1041 → 1041, new module 234 → 226 after fix round 1.

## 3. Boundary and identity assertions

* **Unassessed / not measured / current / stale / unavailable** are five vocabulary members
  (`test_review_assessments.py::test_the_five_binding_states_are_the_closed_vocabulary`), the model
  refuses a sixth value and refuses a subject `status` its own records contradict.
* **An empty measurement is not a measurement of anything**: it marks no record current and no record
  stale (`test_review_assessments.py::test_an_absent_measurement_is_not_measured_and_never_stale`;
  `test_knowledge_review_surface.py::test_only_a_complete_matching_measurement_reports_the_assessment_current`).
* **A measured movement is named, not inferred**: `test_a_moved_candidate_marks_the_stored_assessment_stale_on_the_measured_axis`
  drives the real publication, lands one real change and reads the movement through the production
  port while the disposition, author and examined inputs stay the author's.
* **Historical generation identity**: in
  `test_a_recorded_review_measures_its_recorded_generation_not_todays_branch` the same fixture is read
  twice after a branch advance — live → `stale` on the measured axis; `history="recorded"` → the
  recorded generation's own tree id is what the binding is measured against, verdict `not-measured`,
  comparison identity `== recorded_tree`. I measured that both reads are served by the same assessment
  record, so the difference is the *measurement*, not two collections.
* **No favourable default anywhere**: the channel model still refuses a count on an unanswered state,
  and the new channel never carries one.
* **Nothing about the record moved**: `assessmentCount`, `unresolvedCount`, disposition, author, role
  and `examinedInputs` are untouched; a stale record stays readable and reusable only through a new
  authored assessment (the refusal helpers are unchanged).

## 4. Base-defect reproduction

Scratch tree at the base commit (not `/tmp`):

```
git -C <leaf> worktree add --detach /home/firefox/projects/ar-coordination/temp/l15-base 3103e114
cp <leaf>/mcp/tests/{test_review_assessments,test_knowledge_review_surface,test_knowledge_review_evidence_channels,test_curator_review_assessment_publication}.py \
   /home/firefox/projects/ar-coordination/temp/l15-base/mcp/tests/
cd /home/firefox/projects/ar-coordination/temp/l15-base
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support \
  /home/firefox/projects/agents-remember/mcp/.venv/bin/python -m pytest \
  mcp/tests/test_review_assessments.py mcp/tests/test_knowledge_review_surface.py \
  mcp/tests/test_knowledge_review_evidence_channels.py mcp/tests/test_curator_review_assessment_publication.py \
  -q -p no:randomly -m ''
```

Observed on base bytes: **`6 failed, 30 passed, 2 errors in 36.58s`**

```
FAILED mcp/tests/test_curator_review_assessment_publication.py::TestReadStatesOverAStoredCollection::test_an_unmeasured_assessment_is_reported_not_measured_not_current
FAILED mcp/tests/test_curator_review_assessment_publication.py::TestReadStatesOverAStoredCollection::test_only_a_complete_measured_match_reports_the_stored_assessment_current
FAILED mcp/tests/test_knowledge_review_evidence_channels.py::test_the_composition_measures_the_bindings_it_reads
FAILED mcp/tests/test_knowledge_review_evidence_channels.py::test_a_moved_candidate_marks_the_stored_assessment_stale_on_the_measured_axis
FAILED mcp/tests/test_knowledge_review_evidence_channels.py::test_a_recorded_review_measures_its_recorded_generation_not_todays_branch
FAILED mcp/tests/test_knowledge_review_evidence_channels.py::test_an_unreadable_authority_reports_the_measurement_unavailable
ERROR  mcp/tests/test_review_assessments.py - ImportError while importing test module
ERROR  mcp/tests/test_knowledge_review_surface.py - ImportError while importing test module
```

Two of the six failures are *behavioural* on base, quoted verbatim:

```
E       AssertionError: assert 'not_measured' == 'unavailable'
mcp/tests/test_knowledge_review_evidence_channels.py:811: AssertionError

E       AttributeError: 'ReviewRecordInputs' object has no attribute 'currentness'
mcp/tests/test_knowledge_review_evidence_channels.py:789: AttributeError
```

The two collection errors are the honest shape of a vocabulary change: those modules import the new
names, so on base they do not even collect. To make the two *core* defects behavioural on both
revisions I ran one probe with one code path (`temp/l15-scratch/base_probe.py`, which picks the
measurement field by inspecting `dataclasses.fields(ReviewRecordInputs)`), driving the real
`compose_review` with a real stored assessment:

```
BASE 3103e114   ReviewRecordInputs: ['assessments','channels','claims','current','observations','signals']
(a) no measurement    : ['stale']       <- "absent measurement becomes stale"
(b) empty measurement : ['current']     <- the packet's non-conforming example
(c) moved measurement : ['current']     <- a measured movement rendered as currency
(d) matching measure  : ['current']

CANDIDATE       ReviewRecordInputs: ['assessments','channels','claims','currentness','observations','signals']
(a) no measurement    : ['not-measured']
(b) empty measurement : ['not-measured']
(c) moved measurement : ['stale']
(d) matching measure  : ['current']
```

## 5. Guards, and the mutation that removes each

| Guard | Mutation run | Result |
| --- | --- | --- |
| Coverage decides `current` (`measured_binding_status`) | deleted the `if unmeasured_identities(...): return "not-measured"` branch | 2 failed: `TestReadStates::test_an_absent_measurement_is_not_measured_and_never_stale`, and R14's `test_the_production_composition_supplies_every_owner_produced_record_class` (`assert 'current' != 'current'`) |
| The projection actually uses the measurement (`subject_states`) | replaced `statuses = measured_binding_statuses(...)` with `statuses = {}` | 2 failed: `test_the_production_composition_supplies_every_owner_produced_record_class`, `test_two_disagreeing_assessments_are_both_displayed_with_their_authors_and_no_resolution` |
| A subject `status` may not contradict its records (`_counted_status`) | disabled the validator branch | 1 failed: `test_a_status_that_contradicts_its_records_is_refused` |

Every mutation was reverted and the fingerprint re-measured (`b43b793f30ad8a90eb3a7436847c820408dbf18aa6b9b59479eca88e2143e9af`, 15 status entries) before continuing.

## 6. Commands and results (candidate)

All with `PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support /home/firefox/projects/agents-remember/mcp/.venv/bin/python -m pytest`.

| Command | Result |
| --- | --- |
| `mcp/tests/test_review_assessments.py -q -p no:randomly` | **50 passed** |
| `mcp/tests/test_knowledge_review_surface.py -q -p no:randomly` | **31 passed** |
| `mcp/tests/test_knowledge_review_evidence_channels.py -q -p no:randomly` | **13 passed** |
| `mcp/tests/test_curator_review_assessment_publication.py -q -p no:randomly -m integration` | **23 passed** |
| `test_knowledge_family_review.py test_knowledge_family_integrity_pipeline.py test_knowledge_review_subject_isolation.py test_knowledge_review_comparison_generation.py -q -p no:randomly` | **45 passed** |
| `mcp/tests -q -p no:randomly` (default lanes) | **2637 passed, 62 skipped, 321 subtests passed**, 0 failed (297.6 s) |
| `mcp/tests -q -p no:randomly -m ''` (unit + integration, re-run on the frozen bytes after the last code edit and after every mutation was reverted) | **3058 passed, 77 skipped, 395 subtests passed**, 0 failed (476.1 s) |
| `test_dependency_ownership_ast_helpers.py test_evidence_lanes.py -q -m ''` | **5 passed**; `sha256sum mcp/tests/evidence-lifecycle.toml` = `81a518b567b654375bd1ea4e5af5395cefe3a3464563308c17f31278e276134c` = the pinned `LIFECYCLE_CATALOG_SHA256` (no test module was added, so no re-pin; population still 16 contracts / 66 artifacts, asserted by that run) |
| `ruff check` + `ruff format --check` on the 15 touched files | **All checks passed! / 15 files already formatted** |
| `pyright --pythonpath <main>/mcp/.venv/bin/python <7 changed source files>` and `<4 changed test modules>` | **0 errors, 0 warnings, 0 informations** (both runs) |
| ≥1200-line census (repo-wide, `code_quality.file_size.measure`) | base **27** offenders / candidate **27**; `models/knowledge/review.py` is 1198 lines in both (not an offender). `mcp/tests/test_knowledge_review_surface.py` 1395 → 1475 is a pre-existing offender growing under master ruling 2; no NEW offender appeared |

## 7. R25 usability (the evidence plan, `notes/05-acceptance-plan.md`)

* **A14** ("all five states", Class 1): the five states now exist as a closed vocabulary, and four of
  them are produced through the real composition in `test_knowledge_review_evidence_channels.py`
  (measured-stale, not-measured, unavailable, plus `current` through a complete measurement in the
  curator-coherence owner). The artifacts a curator keeps: the pytest node ids above and the probe
  output.
* **A17** ("freeze → closeout → restart → advance → reopen the exact comparison", Class 2): the
  recorded-generation case freezes a real generation through R11's owner, advances the branch with a
  real commit, and reads both the live and the recorded review; the recorded answer is measured
  against `manifest.source.candidate_code_tree_id`. L25 can extend this to a full enclosure cleanup +
  fresh process using R12's existing helpers.
* **A20**: the "candidate moved after publication" case is the state A20's source movement produces,
  and the measured axis is named.
* Class 3 (browser) rows are untouched by this leaf: no dashboard file changed.

## 8. Open limits and things needing a ruling

1. **The review read measures only the identities the viewed comparison publishes** — the code
   endpoints, the scope/comparison references and the shipped validator versions (5 identities for a
   canonical declaration). The curator-coherence observation's own inputs (`candidate-state:
   knowledge-candidate-pair`, `memory-tree:candidate`, `semantic-topology:registered-scope`,
   `task-intent:requirement-identities`, and each cited published byte) are **not** re-derived here:
   doing so would be a second implementation of that observation, which the packet's Scope and
   Forbidden Overreach both exclude. They are reported `not-measured` **and named** on the channel
   (`unreadable`) and per binding. Consequence, stated plainly: for an assessment published by
   `_assessment_inputs`, the review read reports `not-measured` when the source matches and `stale`
   when it moved — `current` in the review read is reachable only for a binding whose declaration the
   comparison covers. A complete matching measurement *does* yield `current` through the
   curator-coherence owner, proven with a real published assessment
   (`test_curator_review_assessment_publication.py::TestReadStatesOverAStoredCollection::test_only_a_complete_measured_match_reports_the_stored_assessment_current`).
   If the master wants the review read to be able to claim `current` for canonical assessments it
   needs a ruling on reusing the coherence observation from a read path (out of this packet's Scope).
2. **`compose_currentness` / `FindingCurrentness` (KS-R16's family-review pipeline) is unchanged** and
   still treats a caller-supplied mapping's absent value as a mismatch, with
   `FamilyIntegrityRequest.current` defaulting to `{}`. That is the same *class* of defect the packet
   names, but it is a different owner and a different surface than the two the packet's Scope names
   (`ReviewRecordInputs` currentness projection and the curator assessment dependency checks), and
   changing its two-member literal is a contract change outside this packet. Reported, not changed —
   it needs a scope ruling if the master wants it here.
3. `review_assessment_store.assessment_currentness_for_record` and the `require_current_*` refusal
   helpers keep the shipped two-member semantics with a caller-supplied measurement; nothing in the
   review or coherence read reaches them any more (the store's API is left intact for later leaves
   rather than edited outside Scope).
4. `mcp/tests/test_knowledge_review_surface.py` is 1475 lines (base 1395) — a pre-existing hard-rail
   offender that grew; the dedicated extraction leaf the master ruling names remains the follow-up.
   `mcp/src/agents_remember/application/review_evidence_records.py` is 879 lines (soft band, +10).
5. The dashboard renders `binding: {binding_state}` from a `string`, so the two new spellings reach
   the DOM truthfully with no TS change; this leaf ran no dashboard suite because it touched no
   dashboard file.

---

# Fix round 1 (after `temp/icr/verify-l15.md`, verdict `pass-with-findings`, no blocking finding)

Verifier's graded fingerprint: tracked diff `b43b793f30ad8a90eb3a7436847c820408dbf18aa6b9b59479eca88e2143e9af`,
new module `31d6dbf889cbaec9c9820c1d6d96b530eed31570e80d8c1f49e3076075c9ccd6`. Four medium findings fixed, no
blocking one existed, and two low observations recorded without behaviour change.

## F1 (medium) — false self-reference in the new module's docstring — FIXED

`application/review_assessment_currentness.py:15` named `:func:`measure_comparison_currentness``, which does not
exist. The docstring now names `:func:`comparison_currentness_measurement`` — the delivered function, and the same
name `__all__` and the report use. (The rewritten module docstring in the same edit also states the channel's three
product states instead of the four the old text claimed; see F2.)

## F2 (medium) — unreachable branch described as reachable — FIXED, option (b)

**Choice: (b).** Option (a) would have needed the two validator identities to become conditional, which would
either shrink what is honestly measurable or make the measurement depend on the stored record set; neither is a
property of the viewed comparison, so the empty case cannot be made real without contriving it.

* The dead `if not values: return no_currentness_measurement(...)` branch is **deleted**
  (`application/review_assessment_currentness.py:82-123`). The docstring now states exactly what is measured and
  why the state is always `measured`: both bound code endpoints when the resolution binds them, the leaf's
  scope-manifest and comparison references when it names them, **and always the two shipped validator identities**
  (`curator-evidence-resolver/v1`, `review-assessment-policy/v1`), which the assessment publication declares as
  constants — so a resolution binding nothing still publishes two identities. The `no_currentness_measurement`
  import is gone from this module.
* The measurement's own `detail` no longer overstates it: it is now
  `"measured the identities this comparison publishes: <list>; every identity outside that list is one this
  comparison publishes no value for, and is reported unmeasured rather than as agreement"`.
* `currentness_channel`'s `not_measured` branch and the `MEASURE_ACTION` string are **deleted**: the composing read
  always measures a resolved candidate, so this collection has exactly three product states — `recorded`,
  `none_recorded`, `unavailable` — and `not_measured` (a quantity nobody measured) is not one of them. A bundle
  nobody measured never reaches this function at all (it carries no channels). Handing it a not-measured
  measurement is now refused loudly by an assertion carrying that contract, in the shipped style of an operation's
  own precondition, instead of being rendered as a comparison that never happened — the assertion is load-bearing
  (`:170`) and the new case below trips it.
* New case: `test_knowledge_review_evidence_channels.py::test_the_currentness_collection_has_exactly_three_product_states`
  drives `comparison_currentness_measurement` with a resolution that binds **nothing** (no endpoints, no leaf, no
  contract) and measures `state == "measured"` with `{kind} == {"validator"}`; it then drives
  `currentness_channel` to `recorded`, and asserts the assertion fires for a not-measured measurement.

## F3 (medium) — the counts did not partition the records — FIXED

`models/lifecycles/review_assessment.py:478-537`: `_require_the_counts_to_partition_the_records` (called from the
validator at `:485`, defined at `:495-525`) now derives each currentness count from the entries and compares it back to the field, and
requires the three counts plus the measured-current remainder to be exactly the record set; `_status_of_records`
(`:527-539`) derives the subject status from the **entries** (plus `unresolvedCount`) rather than from the counts.
A `current` summary holding a `not-measured` entry, or an `unavailable` summary whose `unavailableCount` is 0, is
now unconstructible.

**Bite-proof (new case: `test_review_assessments.py::TestReadStates::test_a_count_that_does_not_describe_its_records_is_refused`).**
The case was run against the **pre-fix validator rule** (partition call removed *and* the status rule restored to
the counts-derived one, in a scratch copy that was then restored byte-identically):

```
$ pytest mcp/tests/test_review_assessments.py -q -p no:randomly -k count_that_does_not_describe
E       Failed: DID NOT RAISE ValidationError
mcp/tests/test_review_assessments.py:762: Failed
FAILED mcp/tests/test_review_assessments.py::TestReadStates::test_a_count_that_does_not_describe_its_records_is_refused
```

so the contradictory state — the verifier's exact construction — was **accepted** before the fix and is refused
after it. A second reading, with only the partition call removed (the entries-derived status rule still in place),
shows the partition guard is a distinct guard rather than a duplicate of the status check:

```
E       AssertionError: Regex pattern did not match.
E         Expected regex: 'not-measured count does not describe the records'
E         Actual message: "1 validation error for SubjectAssessmentState
                            Value error, status 'current' does not follow from the records: their states are
                            not-measured"
```

and the `unavailable` direction (an `unavailable` entry with `unavailableCount=0`, status `unavailable`) is refused
**only** by the partition guard — the entries-derived status rule agrees with that status. The file was restored
after each mutation and re-hashed (`f84436106a762ccca41c874f6bd675826e51662fe58ef196d0ba63f081de20ed`).

## F4 (medium) — a public docstring still enumerated four states — FIXED

`memory_quality/family_review.py:438-452` (`reported_subject_status`, docstring at `:443-452`) now enumerates the whole vocabulary in the
projection's own precedence order — `none-recorded`, `stale`, `unavailable`, `unresolved`, `not-measured`,
`current` — and says which two members a measurement that covered nothing / failed produces, and that neither is a
clearance.

## F6 / F7 (low) — recorded, deliberately not fixed

* **F6** `mcp/tests/test_knowledge_review_evidence_channels.py` 796 → **966** lines (the verifier measured 914
  before this round; the two fix-round cases and their imports add 52). It sits in the 900–1200 soft band, no hard
  rail, and the ≥1200 census is unchanged at **27 offenders in base and candidate** over the same scope (the
  verifier's own wider scope read 33/33); `models/knowledge/review.py` is **1198 in both**. No new offender.
* **F7** per-record `unavailable` (`unavailable_currentness_measurement`, `review_assessment_binding.py`) still has
  no production producer: the composition's measurement is always `measured`, and the failure the packet names is
  stated where it actually occurs — a corrupt authority makes the *collection* and the *channel* `unavailable`
  (driven in `test_an_unreadable_authority_reports_the_measurement_unavailable`). A producer for the per-record
  member would be new behaviour outside this leaf, so it was **not** added. The acceptance wording therefore reads
  "four per-record states plus a collection-level `unavailable`", not "five states produced per record".
* F5 (the worker-disclosed `compose_currentness` reachability question) still stands as routed for a scope ruling —
  unchanged by this round.

## Re-runs on the fixed bytes (exact commands and results)

| Command | Result |
| --- | --- |
| `pytest mcp/tests/test_review_assessments.py mcp/tests/test_knowledge_review_surface.py mcp/tests/test_knowledge_review_evidence_channels.py -q -p no:randomly` | **96 passed** (was 94: +2 fix-round cases) |
| `pytest mcp/tests/test_knowledge_review_evidence_channels.py -q -p no:randomly` | **14 passed** (was 13) |
| `pytest mcp/tests/test_review_assessments.py -q -p no:randomly` | **51 passed** (was 50) |
| `pytest mcp/tests/test_curator_review_assessment_publication.py -q -p no:randomly -m integration` | **23 passed** |
| `pytest mcp/tests -q -p no:randomly -m ''` (unit + integration, frozen bytes) | **3060 passed, 77 skipped, 395 subtests passed**, 0 failed (474.2 s) — +2 cases, no regression |
| `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` | **5 passed**; `sha256sum mcp/tests/evidence-lifecycle.toml` = `81a518b567b654375bd1ea4e5af5395cefe3a3464563308c17f31278e276134c` (pin unchanged, 16 contracts / 66 artifacts) |
| `pyright --pythonpath <venv> <8 source files + 4 test modules>` | **0 errors, 0 warnings, 0 informations** |
| `ruff check <15 delta files>` / `ruff format --check <15 delta files>` | **All checks passed! / 15 files already formatted**; `grep '# noqa'` over the delta = **0** |
| ≥1200 census (`code_quality.file_size.measure`, repo-wide) | base **27** / candidate **27**; `models/knowledge/review.py` 1198 in both; the new module 226 L (not an offender) |

Seam posture unchanged: `application/knowledge_review.py` is still 1041 lines and untouched, the new module is
still the single implementation of the measurement, and the equality decision is still the shipped comparison.

## New fingerprint (code, after fix round 1)

```
git diff | sha256sum                       = 863d618731600e4510a15808386e7c6e412d95b5518cde56ad0b820f9717316d
sha256sum mcp/src/.../review_assessment_currentness.py = d67e375d6c229613e7130ee9262e83fef8a30f044cc2a787b84a72ae602afde2
git status --porcelain                     = 17 entries (14 M + 3 ??)
  ?? mcp/src/agents_remember/application/review_assessment_currentness.py
  ?? temp/icr/report-l15.md        (4031dd879011bc57485a1b5c33e225a9b72fcef27ad8791d586d4e9540421271
                                    before this section was appended; the only writes after the frozen-bytes run
                                    are documentation edits to this report — this section and two corrected
                                    line references in §2.3 — so no code or test byte moved. The report's own
                                    hash necessarily changes with each such edit, which is why it is read here
                                    as the pre-append value)
  ?? temp/icr/verify-l15.md        (ea99f2728cc105520acf212b229ef3fe1db2a42295b9c606b47447016c2faf00,
                                    the verifier's own artifact)
```

The per-file hashes of all 14 modified tracked files are recoverable from `git diff` at the hash above; the two
files whose untracked hashes matter are listed here. Two mutation rounds were run against the candidate during
this fix (the F3 bite-proofs) and each was restored byte-identically before the frozen-bytes suite run
(`review_assessment.py` = `f84436106a762ccca41c874f6bd675826e51662fe58ef196d0ba63f081de20ed`).
