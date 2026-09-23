# Independent adversarial verification — leaf L15 (ICR-R15@v1, measured assessment currentness)

**Verdict: `pass-with-findings`** — no blocking finding. Four reproduced medium findings (three false
source docstrings and one incomplete model guard), one low latent same-class defect in an owner the
worker itself disclosed and routed for a scope ruling, and two low partial/rail observations. The
packet's Required Behavior, Preservation Boundaries, Failure And Recovery Behavior, Examples and
Forbidden Overreach clauses are all implemented and I reproduced each through the real operation.

| Field | Value |
| --- | --- |
| Verifier | independent adversarial verifier (separate session from worker and curator) |
| Code worktree | `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l15-ar/260921-icr-l15` |
| Branch / HEAD | `ar/260921-icr-l15`, HEAD `3103e1142a3ded8a843c3e5bbefca14861ba4a58` (unchanged start → end) |
| Scratch (all artifacts) | `/home/firefox/projects/ar-coordination/temp/icr-l15-verify/` (never `/tmp`) |
| Production code edited / committed | **none** — no edit to either worktree, no commit, memory worktree untouched |

---

## 1. Self-gate and fingerprints

The worker's report `temp/icr/report-l15.md` appeared at `2026-09-23T08:53:04Z`. Sampled with the
**combined** recipe (tracked diff **plus** per-untracked-file hashes **plus** the full
`git status --porcelain` entry list), three times:

| Sample | UTC | `git diff \| sha256sum` | untracked manifest | combined | status entries |
| --- | --- | --- | --- | --- | --- |
| T0 | 08:53:08 | `b43b793f30ad8a90eb3a7436847c820408dbf18aa6b9b59479eca88e2143e9af` | `bbe931ee9b94c47b7c0633e569295305aaadca6119c282f7ddd3ea1162e05704` | `f3b3d78b41b4d299b5ba0bdaa85b4f6b0703bc47c1051f19b01a727ecc5b8049` | 16 |
| T1 | 08:53:17 | identical | identical | identical | 16 |
| T2 | 08:54:55 | identical | identical | identical | 16 |

T0 → T2 is a **107-second unchanged window**, so the gate is satisfied on `T2`'s bytes (report present
*and* stable ≥90 s). Start fingerprint of the graded bytes:
`b43b793f…`, 16 status entries: 14 `M` (see §2) + 2 `??`
(`mcp/src/agents_remember/application/review_assessment_currentness.py`,
`temp/icr/report-l15.md`), no staged diff.

**End fingerprint (09:18:33Z).** The tracked-diff hash and the new module's hash are **byte-identical**
to the gate:

- `git diff | sha256sum` = `b43b793f30ad8a90eb3a7436847c820408dbf18aa6b9b59479eca88e2143e9af` (same)
- `sha256sum mcp/src/agents_remember/application/review_assessment_currentness.py` =
  `31d6dbf889cbaec9c9820c1d6d96b530eed31570e80d8c1f49e3076075c9ccd6` (same as at the gate)
- status entries still 16, still no staged diff, HEAD still `3103e114`

Only **`temp/icr/report-l15.md`'s own sha256 changed** (`77b25b34…` → `4d3ce93d…`): the worker re-saved
its report after my gate (it edited the fingerprint row at `report-l15.md:9` to record exactly this
artifact). Its length is unchanged (281 lines) and its content is a documentation clarification. **The
candidate did not move**: every byte of the code under verification is identical at start and end, and
no source/test byte changed after my gate reading. This is *not* `candidate-moved`.

The combined-recipe correction from the brief was necessary and is confirmed here: at T0 the
`git diff`-only hash was already `b43b793f…` while the 234-line module sat untracked; only the
untracked manifest exposed it.

---

## 2. Subject of verification

Frozen candidate = base `3103e114` + the worker's uncommitted delta: 14 modified tracked files +
1 new module (`application/review_assessment_currentness.py`, 234 L).

```
M mcp/src/agents_remember/application/memory_quality/controller.py
M mcp/src/agents_remember/application/review_comparison_freeze.py
M mcp/src/agents_remember/application/review_evidence_records.py
M mcp/src/agents_remember/application/review_record_rendering.py
M mcp/src/agents_remember/memory_quality/family_review.py
M mcp/src/agents_remember/memory_quality/knowledge_review.py
M mcp/src/agents_remember/models/knowledge/review.py
M mcp/src/agents_remember/models/lifecycles/review_assessment.py
M mcp/src/agents_remember/models/lifecycles/review_assessment_binding.py
M mcp/src/agents_remember/worktrees/integration/closeout/curator_coherence.py
M mcp/tests/test_curator_review_assessment_publication.py
M mcp/tests/test_knowledge_review_evidence_channels.py
M mcp/tests/test_knowledge_review_surface.py
M mcp/tests/test_review_assessments.py
?? mcp/src/agents_remember/application/review_assessment_currentness.py
```

`application/knowledge_review.py` (the over-rail review adapter) is **not** in the delta — 1041 lines
before and after.

---

## 3. Obligation 1 — base-defect witness (my own fixture, my own command)

I wrote ONE **API-adaptive** probe module so the same file grades both revisions' public APIs
(`ReviewRecordInputs.current` on base, `ReviewRecordInputs.currentness` on the candidate; the probe
introspects `dataclasses.fields` and says which it adapted to). It drives the real production
composition `compose_review(...)` over the real two-snapshot fixture built by
`diff_scope_test_support.build_diff_fixture`, and asserts the packet's rule for absent / empty /
mismatched / matching / unavailable measurement.

Base tree = `git archive 3103e114 | tar -x` into scratch (never a worktree of the leaf), `git init` +
`git add -A` so the lane loader can enumerate; only my probe module and its lane row are overlaid.

```
cd <scratch>/base   # and <scratch>/cand
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support \
  /home/firefox/projects/agents-remember/mcp/.venv/bin/python -m pytest \
  mcp/tests/test_verifier_l15_adaptive.py -q -m '' -p no:randomly
```

| Revision | Observed |
| --- | --- |
| BASE `3103e114` | **`2 failed, 3 passed, 1 skipped in 22.21s`** |
| CANDIDATE | **`6 passed in 23.37s`** |

The two base failures, verbatim (`temp/icr-l15-verify/EVIDENCE-adaptive-base.txt`):

```
E  AssertionError: an EMPTY measurement mapping marked the stored assessment current: ['current']
   mcp/tests/test_verifier_l15_adaptive.py:171
E  AssertionError: a measured movement rendered as ['current']
   mcp/tests/test_verifier_l15_adaptive.py:189
```

So the packet's named non-conforming example ("an empty measurement mapping automatically marks all
stored assessments current") is **reproduced on base and fixed on the candidate** — and the base
defect is *wider* than the packet's example: on base, a supplied-but-**mismatching** measurement also
renders `current`, because `review_record_rendering.subject_states` decided from
`records.current is not None` and never consulted the shipped comparison at all:

```python
# application/review_record_rendering.py:268-272 at base
stale_ids = () if records.current is not None else tuple(a.assessmentId for a in stored)
```

The base also fails the packet's *other* direction (absent → `stale`, i.e. a measured movement nobody
measured); the candidate renders it `not-measured`.

**A third base defect I found and reproduced, which I grade blocking-class on base:** the PERSISTED
memory-quality checklist (`memory_quality/knowledge_review.py:knowledge_review_section`, written into
`reports/curator-memory-quality.md`) rendered

```
| `stale` | 1 | The recorded inputs moved; the finding stays readable and is not reused |
```

for an assessment whose dependencies **nobody measured** (`curator_coherence_subject_assessment_state`
was called with no measurement and answered `stale`). My probe over a real published authority:

```
BASE      : E AssertionError: the checklist counts an unmeasured binding as a measured movement: staleCount=1
            E AssertionError: assert 'The recorded inputs moved' not in '## knowledg... subject |\n'
            -> 2 failed in 20.51s
CANDIDATE : 2 passed in 19.85s
```

That is a persisted sentence false about the store (master rule 1) and it is **fixed** on the
candidate (§6, mutation M6/M7).

---

## 4. Obligation 2 — the real production operation, against the store's own reopened truth

Probe: `temp/icr-l15-verify/probe/test_verifier_l15_production.py` (**6 passed**). It drives, over a
real external-memory leaf enclosure holding a real assessment published through
`curator_coherence_action`:

1. `review_records_for(config, request)` — the application composition the dashboard port calls;
2. `serving_collaborators(config).knowledge_review` — the composition root's port;
3. `GET /api/review/intent` over a real `TestClient` — the transport a browser reaches.

and compares the composition's answer against the **store's persisted record** reopened from the
canonical authority with `load_curator_coherence_authority` (the same reader the composition itself
uses in `_assessments`), re-deriving its digests independently rather than reading the fixture's
claims.

```
cd <scratch>/cand
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support \
  <venv>/bin/python -m pytest mcp/tests/test_verifier_l15_production.py -q -m '' -p no:randomly
-> 6 passed in 25.72s
```

| # | Case | Observed |
| --- | --- | --- |
| 1 | measurement agrees with the store on every covered identity (no false movement) | `measurement.values[("code-tree","candidate")] == ("git-object", <store's recorded tree>)`; `("evidence-bytes","scope-manifest") == ("sha256", canonical_sha256(contract.leaf_id))`; `("evidence-bytes","comparison") == ("sha256", canonical_sha256(contract_path))` — **all equal the record's own `examinedInputs.identities`**, `covered` non-empty |
| 2 | a matching canonical binding is **`not-measured`**, not `current` and not `stale` | `['not-measured']`; channel `recorded`, `record_count == 1`, `unreadable` names `memory-tree:candidate` and `task-intent:requirement-identities` |
| 3 | a real source movement is `stale` and the record is **not rewritten** | after a real `_commit` of a new file: row `stale`; store's declaration, `provenance.authorRef` and `disposition` byte-identical before/after |
| 4 | transport agrees with the port | route JSON `binding_state` list `==` port's `['not-measured']` |
| 5 | **packet Boundary**: recorded generation, not today's branch | freeze via `freeze_review_comparison` → real branch advance → live measurement's `code-tree:candidate` `==` today's tree `!=` frozen tree; `history="recorded"` measurement's `==` frozen tree |
| 6 | corrupt authority → measurement `unavailable` | channel `assessment_currentness` `state == "unavailable"`, `record_count is None`, `next_action` present, `assessments == ()` |

No private helper replaced the operation, and no prebuilt payload was injected: every case goes
through `review_records_for` / the real port / the real route.

---

## 5. Obligation 3 — every new guard proved load-bearing by mutation

Harness: `temp/icr-l15-verify/mutate.py` — copies the candidate to a scratch tree, applies **one**
exact-text mutation, runs the designated probe, restores the file and re-hashes it
(`sha=b3b2ed9d13ff422c` for the binding module, `55fbdc191475cc9a` for the rendering module,
`83ba4a0695604f55` for the checklist, `6584e2b619040dce` for the curator-coherence module — all
restored exactly).

| # | Mutation (removed conjunct / branch) | Probe | Attack that fired |
| --- | --- | --- | --- |
| M1 | `measured_binding_status`: dropped `if unmeasured_identities(...): return "not-measured"` | attack | `AssertionError: an empty measurement produced 'current'` + sibling-inheritance case |
| M2 | `measured_binding_status`: dropped the measured-disagreement conjunct | attack | `AssertionError: a fully covered measured movement produced 'current'` |
| M3 | `subject_states`: `statuses = measured_binding_statuses(...)` → `{}` | adaptive | `KeyError: 'AS-VERIFIER-1'` (projection consumes the statuses) |
| M3b | forced every status to `"current"` | adaptive | **4 failed**: absent, empty, mismatched and unavailable all render `['current']` |
| M4 | checklist: dropped the `not-measured` markdown row | checklist | `AssertionError: ## knowledgeReview` (the row is not rendered) |
| M4b | checklist: dropped the `not-measured` limitation code | checklist | `AssertionError: ()` (`limitations` loses `not-measured`) |
| M5 | `supplied_measurement_statuses`: per-record lookup → one global value | attack | `one record's measurement decided its sibling's state: {'AS-MEASURED': 'current', 'AS-UNMEASURED': 'current'}` |
| M6 | `curator_coherence._measured_state`: reverted to presence-based staleness | checklist | `staleCount=1` **and** the rendered sentence `| \`stale\` | 1 | A measurement of the current inputs found this record's binding moved …` |
| M7 | checklist: reverted only the stale sentence | checklist | `assert 'The recorded inputs moved' not in …` fails |

Every deliverable guard in the new rule is therefore shown to matter. (My first M4 instrument was not
sharp enough — it passed while the row was missing — so I tightened the probe to assert the row *renders*
and re-ran; the corrected instrument fires, and I report the initial silence rather than hiding it.)

---

## 6. Obligation 4 — the four/five states kept distinct; attacks that failed

I tried hard to make one state render as another. Results:

| Attack | Result |
| --- | --- |
| empty `{}` measurement → `current` | **blocked** (`not-measured`); M1/M3b prove the guard |
| absent measurement (`None`) → `current` or `stale` | **blocked** (`not-measured`) |
| partially covered measurement that agrees → `current` | **blocked** (`not-measured`) |
| one record's measurement deciding its sibling's state | **blocked**; M5 proves the guard |
| mismatching measurement → `current` | **blocked** (`stale`); M2 proves the guard |
| a *different* assessment's id in the mapping → sibling promoted | **blocked** (`not-measured` for the uncovered id) |
| unassessed (`none-recorded`) vs measured-zero | **distinct**: `assessment_state_for([])` → `none-recorded`, count 0; a measured world covering nothing → `not-measured`; the composition's `none_recorded` channel carries a real `record_count == 0` |
| unreadable authority → stale/current | **blocked**: collection and currentness channels both `unavailable`, no count, no assessment rows |
| historical read measured against today's branch | **blocked** (§4 case 5) |
| model-level: a status its own entries contradict | **NOT blocked — F3** |
| unreachable `not-measured` branch of `comparison_currentness_measurement` | **NOT blocked — F2** |

---

## 7. Obligation 5 — truthfulness of every new emitted/persisted sentence

Read every new sentence the delta emits or persists, and drove the ones that could be false:

- **Fixed and true:** the persisted checklist now renders
  `` | `stale` | N | A measurement of the current inputs found this record's binding moved; … | ``
  plus a separate `` | `not-measured` | M | No measurement covered what these records examined; they
  are reported neither current nor stale | ``. I drove the real summariser over a real published
  authority: `staleCount == 0`, `notMeasuredCount == 1`, `limitations == ('not-measured',)`.
- **True:** the `assessment_currentness` channel detail ("… N stored binding(s) were compared against
  it (X measured current, Y measured stale, Z not measured), and a binding counted not measured is one
  whose declared identities this comparison publishes no value for") — verified against a real store
  (0/0/1 for an untouched canonical binding, 0/1/0 after a real movement).
- **True:** `none_recorded` detail ("this candidate records no assessment, so no binding existed to
  measure") and the `unavailable` detail (the authority's own reason).
- **True:** the dashboard sentence `binding: {binding_state}` (`ReviewSurface.tsx:180`) — the two new
  spellings reach the DOM truthfully and TS types them as `string` (`data/review.ts:165`), so no TS
  change was needed. I verified no dashboard file changed (`git diff --name-only | grep ^dashboard/`
  → empty) rather than accepting the report's claim.
- **False, unreachable — F2/F7.**

---

## 8. Obligation 6 — seam policy (`notes/03-adapter-seam.md`)

| Rule | Observed |
| --- | --- |
| Keep the adapter a delegator | `application/knowledge_review.py` **untouched**, 1041 lines before and after; no feature logic added, no growth toward the rail |
| One responsibility per purpose-named module beside its owners | new `application/review_assessment_currentness.py` (234 L) owns "measure the bindings against the viewed comparison"; named for the responsibility, not the leaf |
| Re-export, never fork; existing importers keep working | `knowledge_review.py` still re-exports `subject_states`/`assessment_displays` (it imports them unchanged); no importer needed an edit for the seam |
| Never duplicate an existing owner | the equality decision stays `disputed_dependencies` (shipped); the new module performs no second comparison. `assessment_currentness_for_record` is no longer reached by the review or coherence read, and its API is left intact |
| Report seam moves | report §2.5 gives module, delegation points and line counts; I verified the numbers |

---

## 9. Obligation 7 — rails

| Rail | Command | Observed |
| --- | --- | --- |
| pyright (source delta, 10 files) | `<venv>/bin/python -m pyright --project . --pythonpath <venv> <10 src files>` | **0 errors, 0 warnings, 0 informations** |
| pyright (test delta, 4 files) | same, test modules | **0 errors, 0 warnings, 0 informations** |
| ruff check | `<venv>/bin/python -m ruff check <15 delta .py>` | `All checks passed!` |
| ruff format | `… ruff format --check <15 delta .py>` | `15 files already formatted` |
| ≥1200 census | `code_quality.file_size --report` over the **identical** 2317-file scope in both trees | base **33** offenders / candidate **33**; offender set identical except `test_knowledge_review_surface.py` 1395 → 1475 (same file, still an offender). **No new offender.** `models/knowledge/review.py` is **1198 in both** (2 lines under the hard rail) |
| new `# noqa` | grep over the 15 delta files | **0** |
| ownership/lane census | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` | **5 passed** — catalog population 16 contracts / 66 artifacts, lane rows consistent, `evidence-lifecycle.toml` digest matches (no test module was added, so no re-pin) |
| case budgets | `pytest mcp/tests -m "not integration" --collect-only -q -n0` → **2699/4000**; `-m "integration"` → **436/1000** (`pyproject.toml:278-279`) | satisfied with headroom |

Environment claims checked rather than believed (master rule 6): the report's "no dashboard file was
touched, so no dashboard suite was run" is **true and verified** (no `dashboard/` path in the delta,
tracked or untracked), and the dashboard's `binding_state` is an untyped `string` so the new spellings
cannot break the TS build. I also re-ran the delivery's own focused counts in an independent scratch
copy and reproduced **every one of them exactly**: `test_review_assessments.py` **50 passed**;
`test_knowledge_review_surface.py` **31 passed**; `test_knowledge_review_evidence_channels.py`
**13 passed**; `test_curator_review_assessment_publication.py -m integration` **23 passed**; the four
consumer modules **45 passed**.

**Full-suite reconciliation (my finding, not the delivery's):** my first full combined run reported
`4 failed, 3057 passed, 77 skipped, 392 subtests passed`, against the worker's "0 failed". I did **not**
accept either number. All four failures were
`test_memory_quality_runs.py::MeasuringBuildStampTests` (`AssertionError: None is not true` on
`stamp.commit`, and `KeyError: 'commit'`) — the serving-build stamp resolves a commit, and **my scratch
tree had no commit**. Decisive control: the identical 4 failures reproduce on the **base** bytes in the
same scratch conditions (`4 failed, 15 passed`); after committing both scratch snapshots the module is
**`16 passed, 3 subtests passed` on base and on the candidate**. So the 4 failures are an artifact of
my copy, not a delta regression, and the candidate introduces no suite failure.

---

## 10. Obligation 8 — packet clause by clause against the actual bytes

| Clause | Applied to the bytes |
| --- | --- |
| Required Behavior — distinguish unassessed / not measured / measured current / measured stale / unavailable | **Implemented.** `AssessmentBindingStatus = Literal["not-measured","current","stale","unavailable"]` and `none-recorded` at subject level; produced by `measured_binding_status`, not by mapping presence. Verified end-to-end for four of the five and by unit probe for all five |
| Required Behavior — use the shipped dependency comparison rather than presence of a mapping | **Implemented.** `measured_binding_status` calls `disputed_dependencies`; the presence test is gone from `subject_states`, `_measured_state` and `assessment_state_for` |
| Required Behavior — historical assessments measured relative to their recorded generation; current-branch comparison a separate explicitly selected operation | **Implemented and driven** (§4 case 5): the live read measures today's tree, `history="recorded"` measures the frozen manifest's tree |
| Failure And Recovery — missing measurement reports not measured | **Implemented** (absent, empty and partially covered all `not-measured`) |
| Failure And Recovery — failed/corrupt measurement reports unavailable | **Implemented at the level the failure actually occurs** (§4 case 6). The per-record/per-subject `unavailable` member has no production producer — see F7 |
| Failure And Recovery — changed dependencies identify stale axes and require an authored new assessment | **Implemented, unchanged**: `require_current_*` refusal helpers untouched; a stale row carries its disposition/author/role/finding verbatim (verified, §4 case 3) |
| Preservation — disposition and author unchanged; observation success never grants approval | **Held.** Verified: after a real movement the store's declaration and provenance are byte-identical and the row keeps `concern_found` / `curator-3` |
| Preservation — canonical identity, authored meaning, mechanical detection, execution observations and human judgment remain separate | **Held.** No new authored/approval authority; the change is a read projection + one measurement |
| Preservation — existing Git/publication authorities remain the owners | **Held.** No Git/publication code touched |
| Examples — conforming (exact match current; source-only change stale) | **Implemented.** Exact match → `current`; a real committed source change → `stale` (§4 cases 2–3). Note the qualification in §11 below |
| Examples — non-conforming (empty mapping marks everything current) | **Fixed and mutation-proved** (§3, M1/M3b) |
| Examples — boundary (advancing today's branch does not rewrite a historical assessment's recorded comparison) | **Implemented** (§4 case 5) |
| Forbidden Overreach — no silent fallback to current HEAD/current knowledge | **Held.** `comparison_currentness_measurement` reads only `ReviewCandidateResolution` fields and performs **no I/O**; the recorded read's endpoints come from the frozen manifest. There is no HEAD read to fall back to |
| Forbidden Overreach — no browser-selected dataset / generated semantic verdict / parallel store, merge or review authority | **Held.** No new store, no verdict field, no merge or review authority; the measurement is derived from the resolution the request already selected |
| Forbidden Overreach — a snapshot is retained evidence of an owner-produced input, never a new source of authored truth | **Held** |
| Scope — move a touched responsibility out of the over-limit review adapter before adding behaviour; do not duplicate | **Honoured** (§8): the adapter is untouched because no behaviour was added to it; the touched projection already lived in R02's extraction |
| Verification Evidence — exercise absent, empty, mismatched and matching measurements through the production record adapter, plus historical reopen | **Done independently** (§3 base witness, §4) |

### Unimplemented / partial / test-only clauses

1. **`unavailable` per record is test-only — F7 (low).** The packet's failed/corrupt clause is
   satisfied at the collection level, which I drove. But `unavailable_currentness_measurement`
   (`review_assessment_binding.py:265`) has **zero callers in `mcp/src`** (only its `__all__` export),
   so `AssessmentEntry.currentness == "unavailable"`, `SubjectAssessmentStatus == "unavailable"` and
   `unavailableCount` are reachable only by a caller injecting the status — which is exactly what the
   delivery's own `test_review_assessments.py:654` does.
2. **`not_measured` on the currentness channel is dead — part of F2.** See §11.
3. **`compose_currentness` still presence-based — F5 (low), disclosed by the worker and correctly
   routed for a ruling.**

---

## 11. Findings

### F1 — medium — false self-reference in the new module's docstring

- **id / severity:** F1 / medium
- **file:line:** `mcp/src/agents_remember/application/review_assessment_currentness.py:15`
- **what:** the module docstring says
  "*`:func:`measure_comparison_currentness` reads the identities the resolution binds …*", but no such
  symbol exists; the delivered function is `comparison_currentness_measurement`. `__all__` and the
  report also use the real name, so only the docstring is wrong.
- **evidence:** `hasattr(module, "measure_comparison_currentness")` → `False`;
  `hasattr(module, "comparison_currentness_measurement")` → `True`
  (`temp/icr-l15-verify/docstring_probe.py`).
- **why it matters:** master rule 2 — a false source docstring on a public function is a finding. A
  reader following the docstring reaches nothing.
- **suggested fix:** rename the reference in the docstring to
  `:func:`comparison_currentness_measurement``.

### F2 — medium — an unreachable branch whose docstring claims it is reachable (and it hides a whole dead channel path)

- **id / severity:** F2 / medium
- **file:line:** `mcp/src/agents_remember/application/review_assessment_currentness.py:96-99` (claim)
  and `:117-124` (dead branch)
- **what:** `comparison_currentness_measurement`'s docstring claims "*a resolution that publishes no
  identity at all (a pair a caller assembled from two named files, with no contract and no bound
  endpoints) reports `not-measured`: there was nothing to measure*". In fact the two validator
  versions are appended **unconditionally** (`:117-118`), so `values` is never empty and the
  `if not values: return no_currentness_measurement(...)` at `:119-124` is unreachable.
- **evidence:** driving the function with a resolution that binds **nothing** (no endpoints, no leaf,
  no contract):
  `state = "measured"`, `values = {("validator","curator-evidence-resolver/v1"): …, ("validator","review-assessment-policy/v1"): …}`
  (`temp/icr-l15-verify/deadbranch_probe.py`). Consequence: `currentness_channel`'s
  `measurement.state == "not-measured"` branch and its `MEASURE_ACTION` string are also unreachable
  from the composition, so the `not_measured` channel state — the base's own state for this collection
  — can no longer be produced.
- **why it matters:** the docstring is false about its own function, and a dead branch plus a dead
  channel state (`not_measured`) is an unimplemented half of the availability vocabulary the packet's
  "not measured" distinction relies on. Not user-visible or persisted (nothing reaches it), so medium,
  not blocking.
- **suggested fix:** either remove the unconditional validator entries / make the empty case genuinely
  reachable, or delete the dead branch and correct the docstring to state which identities are always
  measured and that `not_measured` for this collection is unreachable. Also correct `MEASURE_ACTION`'s
  "measures every stored assessment's recorded dependencies", which overstates what the composition
  measures (it measures only the identities the viewed comparison publishes and reports the rest
  unmeasured).

### F3 — medium — the new `SubjectAssessmentState` guard does not check counts against the records, contrary to its docstring

- **id / severity:** F3 / medium
- **file:line:** `mcp/src/agents_remember/models/lifecycles/review_assessment.py:460-466` (claim),
  `:484-497` (validator), `:499-508` (`_counted_status`)
- **what:** the docstring says "*The four currentness counts partition the stored records*" and "*the
  model refuses a count that exceeds the records or that together with its siblings over-counts them*",
  and the report §3 claims the model "refuses a subject `status` its own records contradict". The
  validator only compares `status` against the **counts** (`_counted_status()`), never the counts
  against `assessments`, so a state may carry `status="current"` with `notMeasuredCount=0` while its own
  single entry declares `currentness="not-measured"`.
- **evidence:** `temp/icr-l15-verify/validator_probe.py`
  (`SubjectAssessmentState(status="current", assessmentCount=1, staleCount=0, notMeasuredCount=0,
  unavailableCount=0, assessments=(AssessmentEntry(assessmentId="AS-1", disposition="concern_found",
  currentness="not-measured"),))`) → **ACCEPTED** (`status="current"`, `notMeasuredCount=0`,
  `entry.currentness="not-measured"`). The same construction with `currentness="unavailable"` is also
  **ACCEPTED**. Control: a sixth `currentness` value **is** refused, and `status="stale"` with
  `staleCount=0` **is** refused — so the guard exists but is incomplete in exactly the direction the
  packet cares about (an unmeasured record inside a `current` summary).
- **why it matters:** the model's whole justification is that a summary "able to disagree with the
  records it summarises" is unrepresentable; today it is representable. Not reachable through any
  production path (`assessment_state_for` always derives counts from entries; the model is not a
  payload field and is not persisted), so medium, latent.
- **suggested fix:** extend the validator to derive status and the three currentness counts from
  `self.assessments` and refuse any mismatch (the same way `assessment_state_for` does), or narrow the
  docstring to what is actually enforced.

### F4 — medium — a public function's docstring still enumerates the old four-state vocabulary

- **id / severity:** F4 / medium
- **file:line:** `mcp/src/agents_remember/memory_quality/family_review.py:445` (docstring of
  `reported_subject_status`, `:437`)
- **what:** "*The states are `none-recorded`, `unresolved`, `stale` and `current`*" — the vocabulary now
  has six members; `not-measured` and `unavailable` are missing and the function returns the former.
- **evidence:** `temp/icr-l15-verify/docstring_probe.py` — `SUBJECT_ASSESSMENT_STATUSES ==
  ('none-recorded','unresolved','stale','not-measured','unavailable','current')`, missing from the
  claim: `['not-measured','unavailable']`. Driving the real function
  (`temp/icr-l15-verify/status_probe.py`): `reported_subject_status([record], {}, subject_id)` →
  **`not-measured`**; `current={record.assessmentId: {}}` → **`not-measured`**.
- **why it matters:** master rule 2. This is the leaf's own vocabulary change left half-documented in a
  neighbouring module that consumes it, and a reader of that docstring would conclude `not-measured`
  cannot occur.
- **suggested fix:** enumerate the full `SUBJECT_ASSESSMENT_STATUSES` (and say the two new members are
  what a measurement that covered nothing / failed produces).

### F5 — low — the same defect class survives in an unwired owner (disclosed by the worker, unreachable today)

- **id / severity:** F5 / low
- **file:line:** `mcp/src/agents_remember/memory_quality/family_review.py:309-345`
  (`compose_currentness`, `curator_currentness_status:349`)
- **what:** `compose_currentness` still decides from `assessment_currentness(assessment,
  current.get(id, {}))` and `FamilyIntegrityRequest.current` still defaults to `{}`, so with the
  **default** pipeline input the persisted sentence claims movement that was never measured.
- **evidence:** `temp/icr-l15-verify/family_sentence_probe.py` →
  `binding_state = stale`, `moved_identities = 8 identities 'moved'`,
  `PERSISTED SENTENCE = "stale: 1 recorded review(s) no longer match their examined inputs (invariant-revision:bb7c80a8-…)"`.
  **Not blocking: verified unreachable.** A repo-wide sweep (`mcp/src`, `dashboard/src`, `scripts`,
  `skills`, `*.py/*.md/*.json/*.toml/*.ts/*.tsx`) finds `FamilyIntegrityRequest` and
  `family_integrity_report` referenced **only inside their own module and in tests**;
  `COMPOSE_REPORT_OPERATION` is used only at `knowledge_family_integrity.py:258`; and the publication's
  `currentnessStatus` comes from an exception's refusal status
  (`curator_coherence_publication.py:141,821`), not from `compose_currentness`.
- **why it matters:** it is the packet's own defect class, one owner away, and it becomes
  blocking-class the moment anything wires that pipeline to a surface. The worker disclosed it in
  report §8.2 and asked for a scope ruling rather than changing a contract outside the packet — which
  is the behaviour the packet's Forbidden Overreach requires.
- **suggested fix:** take the scope ruling the worker asked for (either extend the leaf to route
  `compose_currentness` through the new measurement, or record a follow-up leaf that owns it).

### F6 — low — a test module crossed into the 900-line soft band (reported, not hidden)

- **id / severity:** F6 / low
- **file:line:** `mcp/tests/test_knowledge_review_evidence_channels.py` (796 → **914** lines)
- **what:** the delta moved this module from under the soft rail into the 900–1200 "refactor pressure"
  band (+118). No hard-rail violation.
- **evidence:** `wc -l` over the delta; the ≥1200 census is unchanged at **33/33** over an identical
  2317-file scope, and `models/knowledge/review.py` stayed at **1198** (no growth). The other growth,
  `test_knowledge_review_surface.py` 1395 → 1475, is a pre-existing hard-rail offender covered by
  master ruling 2 ("no NEW offender may appear"), which holds.
- **why it matters:** honest rail reporting; the master should see the pressure rather than a clean
  "27 → 27".
- **suggested fix:** none required; fold into the dedicated extraction leaf master ruling 2 already
  names.

### F7 — low — the per-record `unavailable` state has no production producer (test-only half)

- **id / severity:** F7 / low
- **file:line:** `mcp/src/agents_remember/models/lifecycles/review_assessment_binding.py:265`
- **what:** `unavailable_currentness_measurement` has zero callers in `mcp/src`; the composition's
  measurement is always `measured` (or, per F2, unreachably `not-measured`), and
  `supplied_measurement_statuses` always builds a `measured` measurement. So the per-record /
  per-subject `unavailable` members are reachable only by injecting a status.
- **evidence:** `grep -rn 'unavailable_currentness_measurement' mcp/src mcp/tests` → only the
  definition, its `__all__` entry, and (in tests) none; the delivery reaches the state by injecting it
  (`mcp/tests/test_review_assessments.py:654`,
  `assessment_state_for([record], statuses={record.assessmentId: "unavailable"})`).
- **why it matters:** the packet's failed/corrupt clause **is** satisfied where the failure actually
  occurs (a corrupt authority → both channels `unavailable`, driven in §4 case 6). This finding only
  records that the extra vocabulary member is not reachable from production, so "the five states are
  produced through the real composition" should read "four per-record states plus the collection-level
  `unavailable`".
- **suggested fix:** none required; state the reachability honestly in the report/acceptance row, or
  give the measurement a producer that can fail (it performs no I/O by design, so this is a
  documentation fix).

---

## 12. Unreproduced

None. Every item in this verdict is backed by my own reproduction, command and observed output, all
retained under `/home/firefox/projects/ar-coordination/temp/icr-l15-verify/`. Two attempts failed and
are reported rather than hidden: (a) my first `M4` instrument did not fire because it asserted only the
limitation code and not the markdown row — the tightened instrument fires; (b) my first full-suite
result showed 4 failures which I traced to my own scratch tree having no commit (the same 4 fail on
base; both trees go green once committed).

---

## 13. The delivery's true strengths

1. **The packet's named defect and a wider one are both fixed at the right layer.** The rule moved off
   "is a mapping present" and onto "what did the measurement cover": `measured_binding_status` returns
   `current` only for a **complete** measurement that disagrees nowhere, `stale` for a **measured**
   disagreement, `not-measured` for anything less, and `unavailable` for a failed measurement.
2. **The defect-blessing tests were corrected rather than preserved.** `test_a_measured_matching_binding_reports_the_assessment_current`
   (a name claiming a measuring caller over an input of `current={}` at base `:727-734`, one of three
   such call sites) is replaced by
   `test_only_a_complete_matching_measurement_reports_the_assessment_current`, which asserts all four
   inputs; the KS-era `test_an_unmeasured_assessment_is_reported_stale_not_current` becomes
   `…_not_measured_not_current`. Nothing in the delta still asserts that an empty measurement is
   current.
3. **A blocking-class defect I found on base is fixed with mutation proof.** The **persisted**
   memory-quality checklist used to write "The recorded inputs moved" and `staleCount=1` for an
   unmeasured record; it now writes `stale | 0` plus a new `not-measured` limitation and row, and M6/M7
   show that reverting either half makes the false sentence reappear.
4. **The composition produces a real measurement that agrees with the store.** The measured values for
   every identity it covers are byte-equal to the record's own reopened `examinedInputs.identities`,
   so there is no false movement; a real reported movement leaves the record byte-identical.
5. **The historical boundary is genuinely implemented, not asserted.** The recorded read measures the
   frozen manifest's tree after a real branch advance, and the live read measures today's — one
   fixture, two explicitly selected operations, reproduced end-to-end.
6. **Seam policy is honoured unusually well.** The over-rail adapter is untouched, the new
   responsibility is in a 234-line purpose-named module, the equality authority is still the single
   shipped comparison, and `models/knowledge/review.py` was documented without gaining a line (1198 in
   both trees, 2 lines under the hard rail).
7. **Honest self-reporting.** The report discloses the two limits that matter (the review read can
   claim `current` only for bindings the comparison covers; `compose_currentness` is untouched and
   needs a ruling) instead of overclaiming, and every focused count it states reproduced exactly in my
   independent copy.

---

## 14. Verdict

**`pass-with-findings`.**

The candidate implements ICR-R15@v1's Required Behavior, Preservation Boundaries, Failure And Recovery
Behavior, Examples and Forbidden Overreach clauses, and I reproduced each through the real operation
rather than a private helper: one API-adaptive probe fails exactly the packet's non-conforming example
on base (`2 failed, 3 passed, 1 skipped`) and passes six-for-six on the candidate; six
production-composition probes (real `review_records_for`, real dashboard port, real HTTP route, real
published assessment, real source movement, real frozen generation, real corrupt authority) pass; eight
mutations each make a specific delivered guard fire and every file was restored and re-hashed; and all
rails are green (pyright 0/0/0 on both the source and test deltas, ruff clean and formatted, **no new
≥1200 offender** at 33/33 over an identical file scope, 0 new `# noqa`, census 5 passed with the
catalog at 16 contracts / 66 artifacts, budgets 2699/4000 unit and 436/1000 integration). No
user-visible or persisted sentence false about the store survives: the one I found and reproduced on
base — the checklist persisting "The recorded inputs moved" for an unmeasured record — is fixed and
mutation-proved, and the surviving same-class defect is in `compose_currentness`, which I verified is
unreachable from any production surface and which the worker itself disclosed and routed for a scope
ruling. The four medium findings are three false source docstrings (a wrong self-reference, an
unreachable branch described as reachable, an old four-state enumeration on a public function) and one
incomplete model guard that accepts a `current` summary holding a `not-measured` entry — none of them
emitted or persisted, and none of them touching the packet's behavioural contract. The verdict is
`pass-with-findings` rather than `pass` because those five findings should reach the master's ruling
queue (F1/F3/F4 are one-line corrections; F2 needs a decision about the dead `not_measured` channel
path; F5 needs the scope ruling the worker requested), and rather than `fail` because no blocking
finding exists: the defect the packet names is fixed, proven, and cannot be reintroduced without
breaking a test that now asserts the correct rule.

---

### Artifact index (all under `/home/firefox/projects/ar-coordination/temp/icr-l15-verify/`)

| Artifact | Contents |
| --- | --- |
| `fingerprint.sh`, `fp-t0.txt`, `fp-t1.txt`, `fp-t2.txt`, `fp-end.txt` | the combined fingerprint recipe and all four readings |
| `probe/test_verifier_l15_adaptive.py`, `EVIDENCE-adaptive-base.txt`, `EVIDENCE-adaptive-cand.txt` | the base witness (one probe, two revisions) |
| `probe/test_verifier_l15_production.py`, `production-cand.txt`, `production-cand2.txt` | the six real-composition probes |
| `probe/test_verifier_l15_attack.py`, `probe/test_verifier_l15_checklist.py`, `attack-cand-clean.txt`, `checklist-{base,cand}.txt` | the guard attacks and the persisted-sentence probe |
| `mutate.py`, `mutations-unit.txt`, `mutations-projection.txt`, `mutations-extra.txt`, `mutations-m4.txt` | the mutation harness and all eight runs |
| `deadbranch_probe.py`, `validator_probe.py`, `docstring_probe.py`, `status_probe.py`, `family_sentence_probe.py` | the finding reproductions |
| `base-size-census2.txt`, `cand-size-census2.txt`, `base-offenders.txt`, `cand-offenders.txt` | the apples-to-apples size census |
| `rails-census.txt`, `rails-budgets.txt`, `rails-focused.txt`, `rails-full-suite.txt`, `base-mq-runs.txt`, `mq-runs-{base,rails}.txt` | every rail run and the environment control |
