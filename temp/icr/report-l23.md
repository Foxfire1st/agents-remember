# L23 (ICR-R23@v1) — explicit raw-Git support boundary: worker report

**Leaf:** `260921-ICR-L23` · **requirement:** ICR-R23@v1 (sole primary) · **worktree:**
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l23-ar/260921-icr-l23`
(branch `ar/260921-icr-l23`, base `473ad8242bb4c22bdabed5d5253767350381eb3e`, `git log --oneline -1`
= `473ad824 ICR-R22: managed Git recovery rebinding (260921-ICR-L22)`).

**Status:** implementation complete, all rails green, base-defect reproduction recorded. No commit, no
closeout, no memory worktree touched. Nothing was written into either worktree after the fingerprint
below was taken.

---

## 1. What the packet asked for, and what this leaf measured

Required Behavior: *"At the next review/authoring/closeout boundary compare declared source, knowledge
ancestry and publication identities. Document and exercise rebase, cherry-pick, revert and
branch/worktree switch routes that already exist; where reconciliation is unsupported, preserve the
old generation and expose the concrete supported recovery. Inspection of an exact old generation
remains valid."*

Measured answer, in one paragraph: **`worktree_sync` is the only route that moves a leaf's declared
identities under measurement**; every other way they can move is raw Git, which leaves no record. The
leaf therefore adds one boundary measurement at the two existing review entry points. It resolves the
generation the leaf published through the owner that already selects it
(`select_review_generation` → `read_manifest`) and asks the repository three ancestry questions about
the identities the sealed manifest declared: the code work branch's recorded head, the code-base
commit the capture was taken from, and the memory work branch's recorded base. A **replaced** identity
marks the generation `stale`, disables submission against it, names the exact replaced identity as
`channel:identity`, and names the recovery the matrix publishes. A channel that could not be read
makes the whole report `not-measured` (never `current`) and carries its reason. The four named
transitions are exercised with real Git in isolated enclosures and are documented in a support matrix
that marks three of them **unsupported** in words.

---

## 2. Changes, with file:line

### 2.1 New: the measurement owner — `mcp/src/agents_remember/application/review_external_git_movement.py` (729 L)

| What | Where |
| --- | --- |
| Module docstring: what is measured, what is *not* claimed, why the four transitions are reported the way they are | `:1`–`:44` |
| `_GENERATION_UNREADABLE_RECOVERY`, `_recovery_actions(transitions)` — the recovery is **derived from the matrix rows** so a report can never restate a verdict the matrix contradicts | `:95`, `:102` |
| `GitTransitionSupport` — one matrix row: transition, measured Git signature, boundary state, reconciliation, recovery action | `:130` |
| **`GIT_TRANSITION_SUPPORT`** — the support matrix (5 rows: `ordinary-append`, `rebase`, `cherry-pick`, `revert`, `branch-switch`) | `:152`–`:230` |
| `supported_recovery(transition)` — named lookup that **refuses an unknown transition** instead of defaulting | `:236` |
| `unsupported_transitions()` — derived from the matrix, not written out | `:255` |
| `render_git_transition_support()` — the Markdown table the docs are asserted against | `:269` |
| `external_git_movement(resolved, contract=None)` — the entry point the adapter calls; `None` for "no boundary to measure" (no enclosure, a closed leaf's record, no declared work branch, no published generation, any read error) | `:319` |
| `_unavailable(...)` — the generation-unreadable state, with no invented generation identity | `:355` |
| `_report(...)`, `_state(...)` — the state composition (`stale` > `current` > `not-measured`), with `current` requiring **every** declared channel to have been compared | `:379`, `:412` |
| `_findings`, `_work_branch_finding`, `_source_finding`, `_memory_finding` — the three declared channels, in fixed order | `:430`–`:502` |
| `_ancestry(...)` — the one ancestry decision (`current` / `advanced` / `replaced` / `unavailable`) | `:505` |
| `_tip`, `_object_readable`, `_related`, `_text` — every read that can fail, with the failure kept as a *state* | `:577`–`:610` |
| `_transition(...)` — the shape measured, not the command guessed | `:622` |
| `_statement`, `_agreement_statement`, `_movement_statement`, `_replacement_clause`, `_reason` — one sentence per state, each derived from measured fields | `:643`–`:726` |

### 2.2 New: the typed value — `mcp/src/agents_remember/models/knowledge/review_external_movement.py` (187 L)

`ExternalGitTransition` (`:72`), `GitTransitionReconciliation` (`:83`), `GitMovementChannel` (`:89`),
`GitMovementEvidence` (`:95`), `ExternalGitMovement` (`:98`) and its validator
`_the_state_follows_from_what_was_measured` (`:147`). The key field is `reconciliation`
(`supported` / `unsupported`) — **what is supported is a field, not a tone of voice** — and
`unsupported` repeats the matrix's unsupported verdicts where the reader is.

It reuses R22's four-state vocabulary (`ReviewSyncMovementState`: `current`/`stale`/`not-measured`/
`unavailable`) rather than minting a fifth, and adds `GitMovementEvidence` (`advanced` is a
*measurement*: the recorded identity is still an ancestor) so the new state cannot be confused with
R22's.

### 2.3 Modified: the payload — `mcp/src/agents_remember/models/knowledge/review.py` (1164 → 1175 L)

`external_git_movement: ExternalGitMovement | None = None` at `:1037` (import `:48`, `__all__` entry
in sorted position). **+11 lines. No path changes here by design** — this file was at 1164 L against
the 1164/1200 hard rail, so the new value's whole vocabulary went into the new module and only the
field was added here.

### 2.4 Modified: the movement fold — `mcp/src/agents_remember/application/review_sync_movement.py` (334 → 368 L)

`review_staleness_with_external_movement(staleness, movement, sync_movement)` at `:311`: an
external `stale` **outranks** the reader's carried comparison identity and a recorded managed-sync
rebinding, because R17's `current` ("the displayed comparison is the candidate's current comparison")
stops being true the moment the branch is rewritten under it. `not-measured` and `unavailable` change
nothing — promoting an absence into `stale` would report a movement nobody observed.

### 2.5 Modified: the adapter and the task-context entry (delegation only)

- `application/knowledge_review.py` (1054 → 1073 L): capability import at `:103`, fold import at
  `:152`, the call and fold at `:491`–`:496`, the payload field at `:556`. Its module docstring now records this
  seventh delegated responsibility beside R22's. **The adapter selects nothing and still grows no
  feature logic.**
- `application/review_task_context.py` (405 → 413 L): import at `:55`, `external_git_movement(resolved)`
  at `:168`. The entry that names no selector is a review boundary too, so the same measurement is
  taken there; nothing refuses on it (this entry carries no comparison a stale state could disable).

### 2.6 Modified: the documentation — `docs/reference/worktrees-c09.md` (155 → 185 L)

New section **`## Raw Git Identity Boundary`** between `## Closeout And Recovery` and
`## Integration And Landing Serialization` (which already states "Queue rows, raw Git, … are not
recovery paths"). It renders the matrix as five table rows plus the framing paragraph, and an
assertion in the tests requires the *rendered* table to be a substring of the file — so the document
is generated from the production table rather than transcribed.

### 2.7 Modified: the cases — `mcp/tests/test_review_sync_movement_read.py` (356 → 836 L)

**No new test module was added**, so the census population, `evidence-lifecycle.toml` and
`LIFECYCLE_CATALOG_SHA256` are all unchanged; the module is already a member of the `integration` lane
in `mcp/tests/test-evidence-lanes.toml:301`. The four existing `LiveReviewMovementTests` cases are
untouched; the new class is `RawGitIdentityBoundaryTests`:

| Case | Line | What it drives |
| --- | --- | --- |
| `test_a_raw_rebase_is_measured_and_the_review_stops_reading_as_current` | `:386` | Real `git rebase --onto` inside the enclosure's work branch, then the shipped `read_knowledge_review` |
| `test_a_managed_sync_alone_is_not_reported_as_a_raw_transition` | `:468` | The production `worktree_sync_tool`; the control that keeps R22's fact and this leaf's fact apart |
| `test_a_worktree_that_left_its_declared_branch_reports_the_switch` | `:528` | Real `git checkout --detach`, then the read |
| `test_each_forward_moving_transition_is_exercised_and_reported_as_what_it_is` | `:572` | Real `git cherry-pick`, real `git revert`, plus an ordinary commit, each in its own fixture |
| `test_a_rewritten_official_line_replaces_the_recorded_source_base` | `:620` | The declared **source** branch's recorded base rewritten (`commit --amend`) while the leaf's own branch is untouched |
| `test_the_documented_matrix_and_the_published_matrix_are_one_table` | `:670` | The docs table equals the rendered production table; every named transition has a row; unknown names refuse |
| `test_the_movement_validator_refuses_each_false_shape` | `:711` | Five forgeries, each departing from exactly one validator clause, with the real published value as the accepted control |

Helpers `:770`–`:833` (`_rebase_work_branch_onto_a_new_official_commit`,
`_cherry_pick_an_official_commit_into_the_work_branch`, `_revert_the_leaves_own_commit`,
`_commit_another_leaf_change`, `_on_branch`, `_git_ok`). Every fixture is built through the sibling's
`ReviewSyncFixture` / `build_endpoint_fixture` — **no prebuilt payload, no injected resolution, no
private helper in place of the operation.**

---

## 3. Commands and exact results

Environment for all leaf commands:
`PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support[:<leaf>/mcp/tests]` and
`/home/firefox/projects/agents-remember/mcp/.venv/bin/python`.

| # | Command | Result |
| --- | --- | --- |
| 1 | `pytest mcp/tests/test_review_sync_movement_read.py -q -m integration` | `12 passed, 3 subtests passed` (the module is deliberately an `integration`-marked module; the same file with `-o addopts=""` gives `12 passed, 3 subtests passed`) |
| 2 | `pytest mcp/tests/test_review_sync_movement_read.py mcp/tests/test_review_sync_rebinding.py mcp/tests/test_worktree_sync.py -q -m ''` | `17 passed, 9 subtests passed` |
| 3 | `pytest mcp/tests/test_historical_committed_leaf_review.py test_knowledge_family_review.py test_knowledge_ingest_publication_route.py test_knowledge_review_*.py test_review_assessments.py test_review_bounded_pagination.py test_review_final_output_receipt.py test_review_route_refusals.py test_review_subject_catalogue.py -q -m ''` | **`281 passed in 126.64s`** (the full review-facing regression set) |
| 4 | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` | `5 passed` — population **16 contracts / 66 artifacts** unchanged, `LIFECYCLE_CATALOG_SHA256` unchanged |
| 5 | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py test_evidence_lanes.py test_layering.py test_file_size_detector.py test_suite_budget.py -q -m ''` | `17 passed` |
| 6 | `pyright --pythonpath <main>/mcp/.venv/bin/python` on the 6 production files + the test module | `0 errors, 0 warnings, 0 informations` |
| 7 | `ruff check` on the 7 touched Python files | `All checks passed!` |
| 8 | `ruff format --check` on the same 7 files | `7 files already formatted` |
| 9 | `python -c "render_git_transition_support() in docs/reference/worktrees-c09.md"` | `True` |

**Rails, stated per file** (base → candidate, 1200 hard / 900 soft):

| File | Base | Candidate | Note |
| --- | ---: | ---: | --- |
| `application/knowledge_review.py` | 1054 | **1073** | already over soft at base, well under hard; +19 lines, all import/call/docstring |
| `models/knowledge/review.py` | 1164 | **1175** | already over soft at base; +11 lines and **no path logic**, deliberately |
| `application/review_sync_movement.py` | 334 | 368 | under soft |
| `application/review_task_context.py` | 405 | 413 | under soft |
| `tests/test_review_sync_movement_read.py` | 356 | 836 | under soft; **no new offender in the ≥1200 census** |
| `docs/reference/worktrees-c09.md` | 155 | 185 | not a Python module |
| new `review_external_git_movement.py` | — | 729 | under soft |
| new `models/knowledge/review_external_movement.py` | — | 187 | under soft |

No `# noqa` was added anywhere; `C901`/`PLR0911`/`PLR0912`/`PLR0915` are green with no exemption.

---

## 4. Base-defect reproduction (exact command, exact observed output)

A scratch checkout at the leaf's own base was built and then **removed** after the readings were
taken (`git worktree remove --force`), so this section records the complete recipe. Rebuild it with:

```bash
BASE=473ad8242bb4c22bdabed5d5253767350381eb3e
LEAF=/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l23-ar/260921-icr-l23
SCRATCH=/home/firefox/projects/ar-coordination/temp/l23-base-repro
git -C /home/firefox/projects/agents-remember worktree add --detach "$SCRATCH" "$BASE"
cp "$LEAF/mcp/tests/test_review_sync_movement_read.py" "$SCRATCH/mcp/tests/"
cp "$LEAF/mcp/src/agents_remember/application/review_external_git_movement.py" "$SCRATCH/mcp/src/agents_remember/application/"
cp "$LEAF/mcp/src/agents_remember/models/knowledge/review_external_movement.py" "$SCRATCH/mcp/src/agents_remember/models/knowledge/"
# the four wiring files stay at BASE bytes: the boundary is measured nowhere
git -C "$SCRATCH" status --porcelain
#   M mcp/tests/test_review_sync_movement_read.py
#   ?? mcp/src/agents_remember/application/review_external_git_movement.py
#   ?? mcp/src/agents_remember/models/knowledge/review_external_movement.py
grep -c external_git_movement "$SCRATCH/mcp/src/agents_remember/application/knowledge_review.py"   # 0
grep -c external_git_movement "$SCRATCH/mcp/src/agents_remember/models/knowledge/review.py"        # 0
```

### 4.1 The failure (base bytes)

```bash
cd "$SCRATCH" && PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support:$PWD/mcp/tests \
  /home/firefox/projects/agents-remember/mcp/.venv/bin/python -m pytest \
  mcp/tests/test_review_sync_movement_read.py -q -o addopts="" -p no:cacheprovider
```

Observed: **`8 failed, 6 passed in 34.63s`** — the three pre-existing `LiveReviewMovementTests` cases
and their siblings pass (they are L22's, untouched), and every new case fails:

```
FAILED ...::RawGitIdentityBoundaryTests::test_a_managed_sync_alone_is_not_reported_as_a_raw_transition
FAILED ...::RawGitIdentityBoundaryTests::test_a_raw_rebase_is_measured_and_the_review_stops_reading_as_current
FAILED ...::RawGitIdentityBoundaryTests::test_a_worktree_that_left_its_declared_branch_reports_the_switch
FAILED ...::RawGitIdentityBoundaryTests::test_a_rewritten_official_line_replaces_the_recorded_source_base
FAILED ...::RawGitIdentityBoundaryTests::test_the_documented_matrix_and_the_published_matrix_are_one_table
FAILED ...::RawGitIdentityBoundaryTests::test_the_movement_validator_refuses_each_false_shape
SUBFAILED(transition='cherry-pick') ...::test_each_forward_moving_transition_is_exercised_and_reported_as_what_it_is
SUBFAILED(transition='revert')      ...::test_each_forward_moving_transition_is_exercised_and_reported_as_what_it_is
SUBFAILED(transition='ordinary-append') ...::test_each_forward_moving_transition_is_exercised_and_reported_as_what_it_is
```

The bare failure is `AttributeError: 'KnowledgeReviewPayload' object has no attribute
'external_git_movement'` (base payload has no such field). Patching **one** assertion in the scratch
copy so the *defect* is what fails, rather than the missing field, gives the packet's own
non-conforming state in the adapter's own words:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("mcp/tests/test_review_sync_movement_read.py"); t = p.read_text()
old = '''            movement = payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "stale", movement'''
new = '''            movement = getattr(payload, "external_git_movement", None)
            assert payload.staleness.state == "stale", payload.staleness
            assert movement is not None, read
            assert movement.binding_state == "stale", movement'''
assert old in t; p.write_text(t.replace(old, new, 1))
PY
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support:$PWD/mcp/tests \
  /home/firefox/projects/agents-remember/mcp/.venv/bin/python -m pytest \
  "mcp/tests/test_review_sync_movement_read.py::RawGitIdentityBoundaryTests::test_a_raw_rebase_is_measured_and_the_review_stops_reading_as_current" \
  -q -o addopts="" -p no:cacheprovider
```

Observed, verbatim:

```
>           assert payload.staleness.state == "stale", payload.staleness
E           AssertionError: ReviewStaleness(state='current', statement="the displayed comparison is the candidate's current comparison", previous_comparison_ref=None, moved=())
E           assert 'current' == 'stale'
E             - stale
E             + current
1 failed in 14.67s
```

That is the recorded measurement: **a real `git rebase` rewrote the work branch, the review's own
declared head was replaced, and the shipped read still said "the displayed comparison is the
candidate's current comparison."** (The packet's non-conforming example in its second form: old
attribution shown as current because its record still exists. `packet:46`.)

### 4.2 The same bytes, with the candidate's production wiring

```bash
for f in application/knowledge_review application/review_sync_movement application/review_task_context; do
  cp "$LEAF/mcp/src/agents_remember/$f.py" "$SCRATCH/mcp/src/agents_remember/$f.py"; done
cp "$LEAF/mcp/src/agents_remember/models/knowledge/review.py" "$SCRATCH/mcp/src/agents_remember/models/knowledge/review.py"
cp "$LEAF/docs/reference/worktrees-c09.md" "$SCRATCH/docs/reference/"
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support:$PWD/mcp/tests \
  /home/firefox/projects/agents-remember/mcp/.venv/bin/python -m pytest \
  mcp/tests/test_review_sync_movement_read.py -q -o addopts="" -p no:cacheprovider
```

Observed: **`11 passed, 3 subtests passed in 30.52s`** (12/3 after the sixth new case was added and the
candidate re-verified). Same fixture, same tests, same interpreter; the only difference is whether
the boundary measurement is wired in.

---

## 5. Boundary and identity assertions (what the cases pin)

- The report names **the exact identity the manifest recorded** as replaced:
  `moved_identities == ("code-work-branch:<observedCodeHead>",)` and
  `("declared-source-branch:<code_base_commit>",)`, each compared against the value the sealed
  manifest holds and against `git rev-parse` read back from the branch.
- The reviewed generation is **byte-identical** after the movement:
  `read_manifest(...).model_dump(mode="json") == reviewed.model_dump(mode="json")`, and the recorded
  commit and tree objects still resolve (`cat-file -t` = `commit` / `tree`). *"Inspection of an exact
  historic generation remains valid."*
- The movement **disables submission**: `payload.submission.state == "disabled_stale"` and
  `payload.staleness.statement == movement.statement`, with `previous_comparison_ref` naming a real
  identity nobody invented.
- The transition a report names is a **shape**, and `transition_evidence` carries the per-channel
  measurement beside it (`("code-work-branch", "current")` in the source-rewrite case), so a channel
  that did not move is never blamed.
- **A channel that could not be read is not a channel that agreed**: `binding_state == "not-measured"`,
  `observed_code_work_branch_head is None`, and the reason names the detached HEAD and the declared
  branch.
- The **managed route is not double-reported**: after a real `worktree_sync`, the boundary reports
  `current` / `ordinary-append` while R22's `sync_movement` still carries the sync's own measurement —
  two fields because they are two measurements.
- The docs table **is** the production table (`render_git_transition_support() in docs/...`), every one
  of the four named transitions has its own row, and `supported_recovery("reset --hard")` raises rather
  than defaulting.

---

## 6. Every guard, the mutation that removes it, and what the test then does

Each mutation was applied to the candidate, the module was run, and the file was restored and verified
against a backup (`diff -q` identical).

| Guard | Mutation | Result |
| --- | --- | --- |
| `_state`'s "every declared channel was compared" conjunct (`:412`) | `if all(finding.state != "unavailable" …)` → `if True:` | **1 failed**: `test_a_worktree_that_left_its_declared_branch_reports_the_switch` — the switch case renders as `current` again |
| The staleness fold (`review_sync_movement.py:311`) | `if movement is not None and movement.binding_state == "stale":` → `if False and …` | **2 failed**: the raw-rebase case and the rewritten-source-base case — a replaced identity leaves the review reading as current, which is the packet's failure clause |
| The declared-branch check (`:451`) | `if checked_out != branch:` → `if False and …` | **1 failed**: the switch case — the work branch is compared against whatever is checked out |
| The five validator clauses (`review_external_movement.py:147`) | not mutated: the case forges each false shape and requires refusal, then requires that the **real** published value is accepted — so the control proves the forgeries are refused by these clauses and not by an unrelated one | all five forgeries refused; the real value accepted |

**One guard is deliberately reported as NOT pinned** (see §8, routed debt): the
`_object_readable(repository, recorded)` guard at `:586`. Removing it costs **0 failures**
(`12 passed, 3 subtests passed`) because no fixture produces a recorded head that is absent from the
object store. Its purpose is to keep a *state that was not measured* distinct from a *measured
replacement*: with the guard removed, a missing object is reported as "the branch was rewritten"
(`replaced`) instead of `unavailable`.

---

## 7. Support matrix, as documented and as published

| Transition | Measured signature | Boundary state | Reconciliation | Recovery |
| --- | --- | --- | --- | --- |
| `ordinary-append` | the recorded work-branch head is an ancestor of the branch tip | `current` | **supported** | none required for the recorded identities |
| `rebase` | the recorded head is a readable commit object that is **not** an ancestor of the branch tip | `stale` | **unsupported** | successor generation from the rebased tip (`freeze_review_comparison`, reviewed generation as `parent`); no replay or reversal |
| `cherry-pick` | the tip differs while the recorded head is still an ancestor, or the tree/dataset differ while both commits resolve | `current` | **unsupported** | read R22's rebinding or R11's reopen channel, or publish a successor generation; **never identified from an ancestry check alone** |
| `revert` | the branch advances by exactly the commits that undo earlier ones | `current` | **unsupported** | compare tree and dataset through their existing owners, or publish a successor generation; **never inferred from an ancestry check** |
| `branch-switch` | the worktree is not on the declared work branch — detached or another branch — so the comparison cannot be taken | `not-measured` | **unsupported** | return the worktree to its declared branch, then read the boundary again; **never checks a branch out on a reader's behalf** |

`unsupported_transitions()` returns `('rebase:unsupported', 'cherry-pick:unsupported',
'revert:unsupported', 'branch-switch:unsupported')` and rides on every published value, so a reader
of a green `current` report is still told which transitions this system does not reconcile. There is
**no** claim of universal raw-Git automation: no hook, no replacement Git layer, no speculative
compatibility layer, no automatic semantic reconciliation, and nothing here writes to a repository.

---

## 8. Limitations, and what needs a ruling

1. **The `_object_readable` guard is unpinned** (`review_external_git_movement.py:586`) — §6. No
   fixture produces a recorded head that is absent from the object store, and `is_ancestor` cannot
   distinguish "missing" from "unreadable" because `git merge-base --is-ancestor` reports both as a
   non-zero exit. **Routed debt, not applied**: the guard stays (it is the difference between a state
   that was not measured and a measured replacement), and the gap is recorded here honestly rather
   than closed with a weaker test. A future case can pin it by removing the recorded commit's object
   bytes (loose objects can be deleted when neither packed nor referenced) and requiring
   `unavailable` — that is a fixture change, not a production change.
2. **The generation-readable `unavailable` path is constructible but not exercised end-to-end.** The
   value is validated by the payload field's own construction, but no case drives a *real* freeze with
   unreadable manifest bytes. `test_a_record_that_cannot_be_used_is_reported_unavailable` (L22's,
   untouched) covers the analogous R22 state; the R23 counterpart is recorded as not-run.
3. **`cherry-pick` and `revert` are not distinguishable from an ordinary commit by this measurement,
   by construction.** The matrix says exactly that, and each still has its own executed case. If the
   master wants those two *identified* rather than reported as `ordinary-append`, that is a contract
   change outside this packet (it would need a patch-id/`git log` reading and a new claim about what
   Git will tell us) and must return for a scope ruling.
4. **The dashboard/client does not render the new field.** ICR-R23's Scope is identity validation at
   existing review and lifecycle entry points plus the support matrix; the review payload field is
   host-side and the client lane belongs to R24/R25. Recorded, not claimed.
5. **`docs/reference/worktrees-c09.md` is asserted, not generated.** The test requires the rendered
   table to be a substring of the file, so drift is caught by a red test rather than by construction.
   Editing the matrix without re-running the case is a red rail, not a silent change.
6. **The closeout/integration boundary is not a new gate.** The packet's Required Behavior names
   "the next review/authoring/closeout boundary"; R21 already carries the final-output receipt and
   closeout gains no new gate (owner map `:79`). I implemented the measurement at the two existing
   **review** entry points, where the generation is actually selected and read. Adding a refusal to
   closeout would be a new authority and is explicitly not in this packet — **if the master reads
   "closeout boundary" as requiring a closeout-side statement, that is a scope ruling, and this leaf
   does not have it.**

---

## 9. Seam policy compliance (`notes/03-adapter-seam.md`)

- Responsibility moved **out**: declared-identity boundary detection, its vocabulary and the support
  matrix. New module `application/review_external_git_movement.py` — purpose-named, adjacent.
- The adapter is a **delegator**: one import, one call, one payload field, plus a docstring record of
  the delegated responsibility in the established style. Nothing else moved into it, and it grew
  1054 → 1073.
- **One implementation**: the only ancestry decision, the only matrix, and the only sentence builders
  live in the new module; no importer was forced to learn a new home (no name moved, so no re-export
  was needed, and `review_sync_movement.py` keeps both fold functions with one re-exported caller).
- **No existing owner duplicated**: R22's rebinding is read, never re-derived; R11's `select_review_generation`
  and `read_manifest` resolve the generation; the capture owner is not called at all (the recorded
  identity is compared against the branch the contract names).

## 10. Fingerprint recipe (explicit — which hashes, order, separators, exclusions)

Taken at the end, in the leaf worktree, with nothing written afterwards:

1. `git status --porcelain` — one line per entry, `XY<space>path`; 8 entries in this order:
   `M docs/reference/worktrees-c09.md`, `M mcp/src/agents_remember/application/knowledge_review.py`,
   `M mcp/src/agents_remember/application/review_sync_movement.py`,
   `M mcp/src/agents_remember/application/review_task_context.py`,
   `M mcp/src/agents_remember/models/knowledge/review.py`,
   `M mcp/tests/test_review_sync_movement_read.py`,
   `?? mcp/src/agents_remember/application/review_external_git_movement.py`,
   `?? mcp/src/agents_remember/models/knowledge/review_external_movement.py`.
2. `git diff | sha256sum` — the plain `diff` of tracked changed files **against the index**, no
   `--stat`, no context trimming, no pathspec, nothing staged or excluded:
   **`d9d287db223b4cf32ea2ced328bc635b596339ed6b96bfce1b17a08163f75fa2`**.
   (This is blind to untracked files — hence item 3, the L21 correction.)
3. `sha256sum <untracked file>` for each entry: `review_external_git_movement.py`
   `d536a86915921585490e495f8284ac0c83f739c873bc847471ceb920eed21a0d`;
   `models/knowledge/review_external_movement.py`
   `2c4abb00cefdcab708ef43a150149c3355e73aab40c4e34bf2fd9ebcbb6c64cc`.
4. `temp/icr/report-l23.md` (this file) is itself untracked and is **not** part of the candidate the
   verifier grades beyond being the claim set.

**Recompute:** `cd <leaf> && git status --porcelain > /tmp/s && git diff | sha256sum && sha256sum
$(git status --porcelain | awk '$1=="??"{print $2}')`. A changed status entry list means the
candidate moved.

---

# Fix round 1 — applied in ONE round against `temp/icr/verify-l23.md` (verdict `fail`)

The verification was read in full first. Everything it filed was applied: B1, B2, H1, H2 (under the
master's ruling), H3, L1–L6, and one additional false sentence I found while fixing B1 and disclose in
§F8 below. Every fix is proven to bite; every guard is proven load-bearing by mutation, restored and
re-hashed. A **sixth round-1 disclosure** (`_object_readable` unpinned, §8.1) is now pinned as L3 and
the agreement clause the verifier called unreachable (L5) is removed rather than left dead.

## F1 — B1: the observed shape vocabulary no longer names an event that did not happen

* **New value.** `ExternalGitTransition` gains **`unchanged`**, published by `_shapes`
  (`review_external_git_movement.py:506`) — and nothing else — exactly when every declared identity was
  compared and not one of them moved. `_transition` (`:745`) now returns **`None`** for a channel that
  still stands at its record, so "no shape observed" is representable; an empty tuple is the other
  honest answer and belongs to the boundary that observed no shape *because something could not be
  compared*.
* **`branch-switch` is derived from the branch sub-fact**, not from "the code channel is unavailable":
  `_Finding` gains `branch_left` (`:330`), set only by `_work_branch_finding` (`:582`) when the
  worktree is not on the declared branch. A missing recorded object is therefore never a switch.
* **The control state's sentence** (`_agreement_statement`, `:827`) now reads *"…and this boundary
  observed no transition: every declared identity still stands exactly where the generation recorded
  it"*, with its own recovery row. The advance sentence is reachable only from a measured `advanced`,
  and it names *"a declared branch advanced from its recorded identity"* rather than "the branch" —
  an advance on the memory line is not an advance of the code work branch, and the sentence says which
  channel the detail beside it names.
* **The `ordinary-append` row is aligned with the shape it covers** (`:159`): "the recorded
  work-branch head is still an ancestor of the branch tip **and the tip has moved past it**". The
  `unchanged` row (`:152`) is the measured absence.
* **Reproduction.** Control state under the shipped read: `transitions == ("unchanged",)`,
  `transition_evidence == (("code-work-branch","current"),("declared-source-branch","current"),
  ("memory-work-branch","current"))`, statement contains "observed no transition" and neither
  "advanced" nor "hash-level". Missing-object state: `binding_state == "not-measured"`,
  `"branch-switch" not in transitions`, `observed_code_work_branch_head is None`, reason names the
  unreadable commit object, sentence claims no switch. Both cases are new
  (`test_an_untouched_leaf_reports_no_transition_at_all`,
  `test_a_missing_recorded_object_is_not_reported_as_a_branch_switch`).
* **Bite-proof.** MT1 replaces `("unchanged",)` with `("ordinary-append",)` → **NOTICED** (1 failed);
  MT2 restores the round-1 rule (`code channel unavailable ⇒ branch-switch`) → **NOTICED** (1 failed).
  The validator also gains the matching clause (`"unchanged"` cannot travel beside a replacement or a
  non-`current` state, `models/…/review_external_movement.py:196`) with two new forgeries; MT8 removes
  it → **NOTICED** (1 failed).

## F2 — B2: the cherry-pick row names only measurements that exist for a pick

Measured by the verifier and re-stated honestly in the row (`:200`): the managed-sync rebinding exists
only once a sync has **carried the official line and resolved a pair** — and the verifier's own probe
showed a sync run *after* a pick returns `already-current` / `no-movement` ("resolved no pair to measure
the review against"), so it records no rebinding to read; the reopen channel reports the recorded
generation's *availability* rather than the pick; and this boundary's own state stays `current`. The
recovery now says: publish a successor generation from the advanced tip, and it states both sync
outcomes rather than implying that running one always yields a measurement. The phrase "read the
existing measurements that do take it" is gone. (An earlier draft of this fix said "running a managed
sync first makes its rebinding the measurement"; that is false in exactly the case the verifier
measured, so it was rewritten before this report was finalised — the assertion in the case pins the
corrected wording, `A sync that carries nothing resolves no pair and records no rebinding`.) The docs table was re-rendered from the production
table, so the document carries the corrected row byte for byte. MT10 restores the old sentence →
**NOTICED** (1 failed).

## F3 — H1: an unreadable or ambiguous generation is `unavailable`, not "no boundary"

`external_git_movement_for_contract` (`:368`) now maps `selection.state in {"unreadable","ambiguous"}`
to `_unavailable(contract, selection.detail)`, so the model's `unavailable` state, its sentence and its
recovery are reachable and "never reviewed" (`no-generation` → `None`) stays a different fact. The
case corrupts the published manifest's bytes and asserts `binding_state == "unavailable"`,
`generation_readable is False`, `transitions == ()`, no observed identities, the **selection's own
detail** in `reason`, the restore-generation recovery in the statement, and
`staleness.state == "not-measured"`; a second half of the same case asserts a leaf that published
nothing still yields `external_git_movement is None`. MT3 reverts the mapping → **NOTICED** (1 failed).
**Disclosed:** only `unreadable` is driven; the `ambiguous` tie needs two generations claiming one
index with different bindings, which the freeze owner never produces (it increments), and no operation
was found that does — the same two-line expression covers both, and the verifier did not drive it
either.

## F4 — H2: the closeout and integration results carry the statement (ruling applied)

* **New entry point** `external_git_movement_result_block(contract, payload)`
  (`review_external_git_movement.py:395`) beside `rebinding_result_block` / `attach_*`: it publishes
  the measured value, or a **typed absence** (`not-measured` / `unavailable`) with the detail, and it
  repeats `unsupported_transitions()` in every state. It **refuses nothing**.
* **Attached at the three existing owners** (`application/worktree_tools.py:1012` closeout preview
  beside `attach_prepared_selection`, `:1015` closeout apply beside `attach_closeout_receipt`, and
  `:461` integration beside `attach_integration_receipt`). A non-`ok` payload is returned unchanged: there is
  no finalised work for the statement to be about, and the failure's own refusal is the answer.
* **Proof (real tools, not a helper):** `test_the_closeout_and_integration_results_carry_the_boundary_statement`
  freezes a real generation, commits the candidate, **amends the reviewed head** (same tree, new
  identity), then runs the real `worktree_closeout_preview_tool`, `worktree_closeout_apply_tool` and
  `worktree_integrate_tool`. All three carry `binding_state == "stale"`,
  `moved_identities == ["code-work-branch:<recorded head>"]`, `"rebase" in transitions`, the frozen
  `generation_id`, the four unsupported routes, and the movement sentence — **while the closeout still
  reaches `state == "closed"` with its R21 receipt recorded and the integration still lands**. The
  second case (a leaf with no generation) requires the typed absence on both closeout results and the
  closeout still closing. MT6 removes the closeout attachment → **NOTICED** (1 failed).
* **The authoring boundary has no natural owner — recorded as an absence, not invented.** Searched and
  named: `application/worktree_tools.py` carries the receipt/rebinding attachments for closeout and
  integration only; the ingest/publication route (`cli/knowledge_ingest.py` →
  `application/knowledge_ingest.py` → `application/published_intent.py`) carries no per-leaf block and
  resolves no published generation at all; the freeze owner
  (`application/review_comparison_freeze.py`) **creates** a generation rather than validating one, so
  attaching a validation statement there would be a second authority over the record it is writing.
  Nothing else in `mcp/src` resolves both the leaf's published generation and a leaf-boundary result.
  The packet's clause is therefore met at review and closeout, and the authoring half is reported as an
  unwired boundary with that evidence rather than given a location the code does not have.

## F5 — H3: the one mounted sentence can no longer claim currency

* **The fold** (`application/review_sync_movement.py:350`) now folds an **absence** as well as a
  movement: `not-measured` / `unavailable` becomes `ReviewStaleness(state="not-measured", statement=
  movement.statement)` — the boundary's own sentence — and deliberately **not** `stale`, which would
  assert a movement nobody observed and additionally disable submission. A recorded *measurement* of
  movement still wins over an absence (R22's rebinding measured a moved input).
* **The vocabulary** gains that state (`models/knowledge/review_staleness.py:63`, `:79`) with the reason
  it is not a softer `current`.
* **The client renders it** (`dashboard/src/panels/review/ReviewSurface.tsx:562`,
  `dashboard/src/data/review.ts:363`): the `not-measured` state mounts the boundary's sentence, with no
  "previous input" clause because nothing moved. One mounted case added to the module that owns the
  mounted staleness rendering (`ReviewSurface.outcomes.test.tsx`, 19 tests in that file now).
* **Reproduction.** Branch-switch state: `staleness.state == "not-measured"`,
  `staleness.statement == movement.statement`, `"current comparison" not in statement`,
  `submission.state != "disabled_stale"`. MT5 removes the absence fold → **NOTICED** (1 failed); MT11
  disables the client branch → **NOTICED** (the new mounted case fails).

## F6 — L1, L2, L3, L4, L5, L6

| Low | Fix | Bite-proof |
| --- | --- | --- |
| L1 the doc miscounted its own table | prose now reads "the four this system does not reconcile"; the case asserts the count from `GIT_TRANSITION_SUPPORT` **and** that the old wording is gone | MT9 reverts the wording → **NOTICED** |
| L2 `generation_readable` unpinned | the `unreadable_but_read` forgery is rebuilt to satisfy **every other clause** (nonempty reason, no replacement, no other absence), so only that clause can refuse it | MT7 disables the clause → **NOTICED** (was SILENT before this fix) |
| L3 `_object_readable` unpinned | the verifier's fixture is built: rebase first, then delete the recorded commit's **loose object** (`_delete_loose_object`, with `git cat-file -e` proving it is gone); the case requires `not-measured` and the truthful unreadable-object reason | MT4 disables the guard → **NOTICED** (was SILENT in round 1; the round-1 disclosure is closed) |
| L4 three dead constants | `_SUCCESSOR_GENERATION`, `_MEASURED_STATES` **and the comment claiming a guard nothing read**, `_UNMEASURED_RECOVERY` — all deleted; the empty-transitions recovery is now `_UNCOMPARED_RECOVERY`, which `_recovery_actions` genuinely reads | `grep` shows one occurrence each removed; the empty-transitions path is exercised by the missing-object case |
| L5 the unreachable coverage clause | removed from `_agreement_statement`; the docstring now says the naming happens in `not-measured`'s reason. `current` still requires **every** channel compared, which is why the clause could not be made reachable honestly | the control case asserts `"not compared" not in statement` |
| L6 the report's one-line recompute | §10's recipe is replaced by a script (`fingerprint-l23-fix1.sh`) that excludes `temp/icr/` from **both** status and hash components, so the declared components and the command agree | the script prints the combined digest below |

## F7 — the round-1 `not-measured` sentence was itself false, and is fixed (undisclosed until now)

The round-1 absence sentence began *"this boundary compared no declared identity against the
repository"*, but `_state` returns `not-measured` when **some** channel could not be compared, not when
none could — so in the branch-switch state (code unreadable, source and memory compared) the sentence
was false about the store. Found while fixing B1; the verifier did not file it. The sentence is now
*"this boundary did not compare every declared identity of the reviewed generation: … "*, followed by
`_compared_clause` (`:813`): the identities that **were** compared when any were, and an explicit "no
declared identity was compared" only when that is true. The missing-object case asserts both clauses
are present and names the compared channel.

## F8 — rails in this round

| File | base | round 1 | now | note |
| --- | ---: | ---: | ---: | --- |
| `application/review_external_git_movement.py` | — | 729 | **881** | under the 900 soft rail (count corrected in fix round 2: it was stated as 880) |
| `models/knowledge/review_external_movement.py` | — | 187 | 205 | |
| `application/worktree_tools.py` | 1035 | 1035 | 1052 | already in the band at base |
| `application/review_sync_movement.py` | 334 | 368 | 382 | |
| `models/knowledge/review_staleness.py` | 204 | 204 | 214 | |
| `tests/test_review_sync_movement_read.py` | 356 | 836 | **358** | split back to R22's own obligation (count corrected in fix round 2: it was stated as 356; the +2 are the docstring lines pointing at the new sibling) |
| `tests/test_review_external_git_movement_read.py` | — | — | **820** | NEW, in the `integration` lane |
| `docs/reference/worktrees-c09.md` | 155 | 185 | 191 | |
| `dashboard/…/ReviewSurface.tsx` | 986 | 986 | 995 | already in the band at base |
| `dashboard/…/ReviewSurface.outcomes.test.tsx` | 884 | 884 | **915** | **the one soft-band crossing this round**; disclosed below |

**Test-catalog gate.** A new module was added, so per the master's rule 7: its `consumer_scope="exact"`
rows were **derived from the census's own findings** (never guessed) —
`mcp/tests/fixtures/repository_profiles/node/package-lock.json`,
`mcp/tests/merge_case_test_support.py`, `mcp/tests/diff_scope_test_support.py`,
`mcp/tests/read_scope_test_support.py` — its lane row was added to `test-evidence-lanes.toml`, and
`LIFECYCLE_CATALOG_SHA256` was re-pinned from a fresh `sha256sum mcp/tests/evidence-lifecycle.toml`
= `caf1b9ee2b0a0b82356ee65339e77328e6f632d1848a9ba05fc4a0fc88831f78`. Population is unchanged at
**16 contracts / 66 artifacts** (no contract and no artifact was added: only consumer rows).

**Census (explicit scope).** `derive_scope(...).size_paths` plus the candidate's still-untracked source
files under the same roots, `temp/` excluded (this master's scratch is not delivered, and letting a
leaf's own evidence files decide a rail population would be self-serving):
**base `473ad824`: files=2111, offenders ≥1200 = 27, band 900–1199 = 100.**
**candidate: files=2114, offenders ≥1200 = 27, band 900–1199 = 101.**
No new offender. The one band crossing is `ReviewSurface.outcomes.test.tsx` 884 → 915, from the mounted
H3 case; every other file I touched was already inside the band at base. **Disclosed, not hidden.**

## F9 — every finding, and its bite-proof

| Finding | Fix | Bite-proof (mutation → attack) |
| --- | --- | --- |
| B1 control state names an advance | `unchanged` + `_shapes` + control sentence | MT1 → 1 failed |
| B1 missing object names a switch | `branch_left` sub-fact | MT2 → 1 failed |
| B2 cherry-pick recovery points at nothing | row rewritten, doc re-rendered | MT10 → 1 failed |
| H1 unreadable/ambiguous collapsed to None | mapped to `_unavailable` with the selection's detail | MT3 → 1 failed |
| H2 no closeout boundary statement | attached at 3 owners + typed absence | MT6 → 1 failed |
| H3 mounted sentence claims currency | staleness `not-measured` fold + client render | MT5 → 1 failed; MT11 (client) → 1 failed |
| L1 doc miscount | prose + assertion | MT9 → 1 failed |
| L2 `generation_readable` unpinned | forged refusal that only that clause refuses | MT7 → 1 failed |
| L3 `_object_readable` unpinned | real missing-object fixture | MT4 → 1 failed |
| L4 dead constants | deleted; the empty-transitions recovery is live | grep: no name occurs twice |
| L5 unreachable clause | removed honestly | control case asserts its absence |
| L6 recipe mismatch | script with the exclusion stated | script prints the digest |

**Bite-proof, before and after (the whole round in one reading).** The new cases were run against the
verifier's own frozen round-1 copy (their `temp/verify-l23/frozen`, commit `a1553de7`) with only the
new test modules and the lane registry copied in — the production bytes untouched:
**8 failed, 5 passed, 3 subtests passed**; the 5 that pass are the round-1 behaviours that were already
right (raw rebase, managed-sync control, the three forward-moving transitions, the rewritten source
base, the round-1 forgery set). On the candidate: **18 passed, 3 subtests passed**.

## F10 — verification re-run in this round (exact results)

| Command | Result |
| --- | --- |
| `pytest mcp/tests/test_review_external_git_movement_read.py mcp/tests/test_review_sync_movement_read.py -q -m integration` | **18 passed, 3 subtests passed** |
| the 32-module review/sync/closeout family, `-q -m ''` | **370 passed, 12 subtests passed** |
| the 22-module worktree/closeout family (the `worktree_tools.py` blast radius), `-q -m ''` | **118 passed, 12 subtests passed** |
| `pytest test_dependency_ownership_ast_helpers test_evidence_lanes test_layering test_file_size_detector test_suite_budget -q -m ''` | **17 passed** |
| `pytest test_dependency_ownership_ast_helpers test_evidence_lanes -q -m ''` | **5 passed** — population 16/66, pin re-measured |
| `pyright --pythonpath <venv>/bin/python` (10 touched files) | **0 errors, 0 warnings** |
| `ruff check` (11 files) / `ruff format --check` (7 files) | `All checks passed!` / `7 files already formatted` |
| `npx tsc --noEmit -p dashboard/tsconfig.json` | clean |
| `vitest run src/panels/review/` (after `temp/icr/prepare-dashboard-worktree.sh`) | **57 passed (7 files)** |
| censuses (scope above) | base 2111/27/100 → candidate 2114/27/101 |

No new `# noqa` anywhere; `C901`/`PLR0911`/`PLR0912`/`PLR0915` green with no exemption.

## F11 — combined fingerprint for this round (recipe and value)

Recipe, reproducible verbatim (`<coordination>/temp/l23-fix1/evidence/fingerprint-l23-fix1.sh`):

```bash
cd <leaf>
{ git diff | sha256sum                                  # tracked changes only, no pathspec
  git status --porcelain | grep -v '^?? temp/icr/'      # entries, scratch excluded
  git status --porcelain | awk '$1=="??"{print $2}' | grep -v '^temp/icr/' | sort | xargs sha256sum
} | sha256sum                                           # the three components, newline-joined
```

* component 1 `git diff | sha256sum` = **`217be3a52753321a146d099075b4db02cd978992fab4ab3e4311d4bbf07f188f`**
* component 2 = **17 entries** (14 `M`, 3 `??`), scratch excluded; the 4th and 5th `??` entries in the
  raw status (`temp/icr/report-l23.md`, `temp/icr/verify-l23.md`) are scratch and are excluded by the
  recipe above — this is L6's correction, stated rather than implied.
* component 3 `sha256sum` of the three untracked code files:
  `mcp/src/agents_remember/application/review_external_git_movement.py`
  `af680ef9a82cfecbddc43b6633b24962ca232c0e81acbd1ba010408f66235df3`;
  `mcp/src/agents_remember/models/knowledge/review_external_movement.py`
  `78766cec80902721101204fdd46808f220607f772091fd5091770f946fb63ad7`;
  `mcp/tests/test_review_external_git_movement_read.py`
  `a66605d9eb2ca080f27b95d2d08b59849cea0641e3924d8c693084899a14bc05`.
* **combined = `a58a3a73101e90bee167b304afc0263545c3bfac6ed3e006b82f5c6518e86fc7`**

A changed entry list, a changed component digest or a changed untracked hash means the candidate moved.
No file was written after this reading except this section.

## F12 — what is still open after this round

1. **The `ambiguous` selection tie is mapped but not driven** (F3). No operation produces two
   generations claiming one index with different bindings, so the fixture would have to hand-write a
   store state; the mapping is the same expression as the driven `unreadable` case.
2. **The authoring boundary is unwired** (F4) with the search that found no owner recorded in its place.
3. **One soft-band crossing** (F8): `ReviewSurface.outcomes.test.tsx` 884 → 915. The ≥1200 offender
   census is unchanged at 27; no touched file approaches 1200.
4. **`cherry-pick` and `revert` remain indistinguishable from an ordinary commit by ancestry** — by
   construction, and the matrix now says so in the row rather than in a neighbour.
5. **The client lane beyond this one sentence is R24/R25's**: the field itself is still not rendered,
   and the mounted case covers only the state this leaf added.

## F13 — a second truthfulness pass over this round's own new sentences

Every sentence this round added was re-read against the store before the report was finalised, and two
were tightened because a case the verifier measured made them false as first written:

* the **cherry-pick** row's recovery claimed that running a managed sync makes its rebinding *the*
  measurement. The verifier's probe shows the opposite in the reachable case: a sync run after a pick
  returns `already-current` / `no-movement` because it "resolved no pair to measure the review
  against", and records no rebinding at all. The row now states both outcomes (F2).
* the **advance** sentence said "the branch advanced", which a reader takes as the code work branch
  while the advance may be on the memory line. It now says *"a declared branch advanced from its
  recorded identity"*, and the per-channel detail beside it names which one (F1).

Re-verified after both edits: delivery modules `18 passed, 3 subtests passed`; census + lanes
`5 passed`; ruff check/format green; the fingerprint below is the post-edit reading.

---

# Fix round 2 — one production string and two report counts (`verify-l23-round2.md`, `pass-with-findings`)

The round-2 verdict was read in full first. It carries **no blocking finding**; B1, B2, H1–H3 and L1–L6
are confirmed closed (including the two guards that were silent in round 1), so nothing it confirmed was
touched except where the one low required it. Two findings were filed and both are applied below.

## G1 — R2-F1 (the only code change): each absence cause now gets its own sentence

**What was wrong.** `external_git_movement_result_block` published one sentence for every cause:
*"no boundary was measured for this result: this contract declares no work branch, **or** the leaf has
published no comparison generation whose declared identities could be compared."* Explicitly
disjunctive, so not false — but the function already knew which cause applied, and in the verifier's own
probe the contract *did* declare a work branch, so the sentence offered a cause that could not apply. A
reader of a closeout result could not tell "never reviewed" from "not a leaf enclosure".

**What changed** (`application/review_external_git_movement.py`):

* `_BoundaryAbsence` (`:352`) carries the cause, and `_measure_contract` (`:363`) returns either the
  measurement or that cause. `_measure_contract` names the **declaration** causes itself — a contract
  that is not a leaf enclosure, and a leaf that names no work branch — and delegates to
  `_measure_generation` (`:384`) for what the store **holds**, which returns the selection's own detail
  for `no-generation` and `_unavailable(contract, selection.detail)` for `unreadable`/`ambiguous`.
* `external_git_movement_for_contract` (`:407`) is now the thin review-path wrapper over the same
  function — one implementation of the decision, so the review read and the closeout block cannot
  disagree about whether a boundary exists.
* `external_git_movement_result_block` (`:421`) publishes `f"no boundary was measured for this result:
  {measured.detail}"`, so the sentence a reader gets is the one the arm that established the cause
  produced. The typed-absence behaviour is unchanged: `state: "not-measured"`, the same
  `unsupported_transitions()` list, `ok: false` payloads still returned untouched, and **nothing
  refuses** — the closeout still closes and the integration still lands.

**The three causes, each driven** (the two below by a new case; the third through the real tools by the
existing case, which now asserts it):

| cause | how it is driven | detail published |
| --- | --- | --- |
| not a leaf enclosure | the real **series** contract the closeout fixture writes (loaded, `kind == "series"`) | "this contract records kind 'series' rather than a leaf enclosure, so no leaf's published comparison generation resolves under it" |
| no declared work branch | the real leaf contract with its `code_work_branch` cell emptied | "this contract declares no work branch, so there is no declared work-branch head to compare against the repository" |
| never reviewed | the real `worktree_closeout_preview_tool` + `worktree_closeout_apply_tool` over a closeout-ready leaf that froze no generation | the store's own "no comparison generation is published for <leaf>, so there is no reviewed generation…" |

**New case:** `test_each_cause_of_a_missing_boundary_gets_its_own_sentence`
(`test_review_external_git_movement_read.py`) drives causes 1 and 2 through
`external_git_movement_result_block` — the production entry point the tools call — and requires the
three details to be **distinct**, each naming itself and **not** its neighbours (`"declares no work
branch" not in` the kind cause, `"records kind" not in` the branch cause, and neither carrying the
generation sentence). The existing `test_a_result_whose_leaf_published_nothing_states_the_absence` was
strengthened to assert the store's own sentence in both closeout results and to assert that the two
inapplicable causes are **absent** from them. A failed payload (`ok: false`) still carries nothing at
all, asserted in the same case.

**Bite-proofs** (mutation → attack → restore + re-hash, all `restored=True`):

| mutation | result |
| --- | --- |
| MT13 the per-cause detail replaced by the old disjunction | **NOTICED** — `test_each_cause_…` fails |
| MT13b the same mutation, attacked through the real closeout tools | **NOTICED** — `test_a_result_whose_leaf_published_nothing_…` fails |
| MT6 the closeout/integration attachment removed (re-checked after the refactor) | **NOTICED** |
| MT3 the unreadable/ambiguous mapping replaced by an absence (re-checked after the refactor) | **NOTICED** |

**One more code consequence, disclosed.** The new machinery pushed the module from the verifier's 881
lines to 937 — **over the 900 soft rail** — so this round compressed its own prose and inlined the
declaration arms back into `_measure_contract`, landing at **895**, under the rail. The compression
touched only text this leaf wrote, and it corrected one stale sentence found while doing it: the module
docstring still said a pick's measurement "belongs to the owners that already take it (the managed-sync
rebinding on the sync route, the reopen channel on the read route)" — the exact round-1 B2 claim the
verifier disproved. It now says no ancestry check identifies a pick or a revert *and no record already
on disk measures it*. Behaviour is unchanged; the fingerprint below is the post-compression reading.

## G2 — R2-F2 (report only): the two line counts in §F8

Corrected in place: `application/review_external_git_movement.py` **881** (was stated 880) and
`tests/test_review_sync_movement_read.py` **358** (was stated 356; the leaf's base is 356 and the +2 are
the docstring lines pointing at the new sibling). Both rows now carry the correction and its reason, so
the table states what `wc -l` prints. Sizes **after** this round, measured in the same pass as the
fingerprint:

| File | base | round 1 | now |
| --- | ---: | ---: | ---: |
| `application/review_external_git_movement.py` | — | 881 | **895** |
| `models/knowledge/review_external_movement.py` | — | 187 | 205 |
| `tests/test_review_external_git_movement_read.py` | — | — | **879** |
| `tests/test_review_sync_movement_read.py` | 356 | 836 | **358** |
| `application/worktree_tools.py` | 1035 | 1035 | 1052 |
| `application/review_sync_movement.py` | 334 | 368 | 382 |
| `models/knowledge/review_staleness.py` | 204 | 204 | 214 |
| `docs/reference/worktrees-c09.md` | 155 | 185 | 191 |
| `dashboard/…/ReviewSurface.tsx` | 986 | 986 | 995 |
| `dashboard/…/ReviewSurface.outcomes.test.tsx` | 884 | 884 | 915 |

**Census, same scope as fix round 1** (detector scope + still-untracked sources, `temp/` excluded):
base `473ad824` **2111 files / 27 offenders ≥1200 / band 100** → candidate **2114 / 27 / 101** —
unchanged from fix round 1, so this round adds no offender *and no band member* (the module is now 895).

## G3 — verification re-run in this round (exact results)

| Command | Result |
| --- | --- |
| `pytest mcp/tests/test_review_external_git_movement_read.py mcp/tests/test_review_sync_movement_read.py -q -m integration` | **19 passed, 3 subtests passed** |
| `pytest test_dependency_ownership_ast_helpers test_evidence_lanes test_layering test_file_size_detector test_suite_budget -q -m ''` | **17 passed** |
| `pyright --pythonpath <venv>/bin/python` (the changed module, its test module, `worktree_tools.py`) | **0 errors, 0 warnings** |
| `ruff check` / `ruff format --check` (same three) | `All checks passed!` / `3 files already formatted` |
| censuses (scope above) | base 2111/27/100 → candidate 2114/27/101 |

No new `# noqa`; `C901`/`PLR0911`/`PLR0912`/`PLR0915` green with no exemption (the refactor was driven
by `PLR0911` itself: the declaration arms were inlined and the generation step extracted so no function
exceeds six returns).

## G4 — combined fingerprint after fix round 2

Recipe unchanged (`<coordination>/temp/l23-fix1/evidence/fingerprint-l23-fix1.sh`): `git diff |
sha256sum` ∥ `git status --porcelain` with `^?? temp/icr/` excluded ∥ `sha256sum` of each untracked
non-scratch path, sorted, the three concatenated with newlines and hashed.

* component 1 = **`217be3a52753321a146d099075b4db02cd978992fab4ab3e4311d4bbf07f188f`** (unchanged: the
  two files this round edited are untracked, so `git diff` covers neither)
* component 2 = the same **17 entries** as fix round 1
* component 3 =
  `mcp/src/agents_remember/application/review_external_git_movement.py`
  **`8b9016dcd0bdae186e37b3685ef4794c6b5c425f7d44235fff0f06ef9f631047`**;
  `mcp/src/agents_remember/models/knowledge/review_external_movement.py`
  `78766cec80902721101204fdd46808f220607f772091fd5091770f946fb63ad7` (unchanged);
  `mcp/tests/test_review_external_git_movement_read.py`
  **`1f736f9c96a46b7a4b0e8551e657f231b1d66b6503167ea61dbdee937ff6459b`**
* **combined = `13d0791b959c74b018fea9b05f210dbf8bb5277d0402cb67b61ec1f6ce796d`**

As in fix round 1, editing this report does not move the digest: the recipe excludes `temp/icr/` from
both components. Nothing outside `temp/icr/` was written after the reading.

## G5 — what is still open after this round

1. **The `ambiguous` selection tie is mapped but not driven** — no operation produces two generations
   claiming one index with different bindings (round 2 accepted this disclosure).
2. **The authoring boundary is unwired**, with the search that found no natural owner recorded (§F4).
3. **One soft-band crossing remains, disclosed**: `ReviewSurface.outcomes.test.tsx` 884 → 915 from the
   mounted H3 case. The ≥1200 offender census is unchanged at 27 and this round added no band member.
4. **`cherry-pick` and `revert` remain indistinguishable from an ordinary commit by ancestry**, by
   construction, and the matrix says so in the row.
5. **The client lane beyond the one mounted sentence is R24/R25's**; the field itself is still not
   rendered.
