# Independent adversarial verification — leaf L23 / ICR-R23@v1 (explicit raw-Git support boundary)

**Verifier.** Independent adversarial verifier; no part in the implementation. Every claim below was
reproduced by me on the frozen candidate bytes with my own fixtures, my own probes and my own commands.
Every new guard I call load-bearing was disabled one at a time in my own scratch copy, the attack re-run,
and each file restored and re-hashed (`restored=OK` on all eleven mutations).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l23-ar/260921-icr-l23`,
branch `ar/260921-icr-l23`, base `473ad8242bb4c22bdabed5d5253767350381eb3e` (`git log --oneline -1` =
`473ad824 ICR-R22: managed Git recovery rebinding (260921-ICR-L22)`), plus the worker's uncommitted delta.
Packet `ICR-R23-v1-explicit-raw-git-support-boundary.md`; leaf doc `23_explicit-raw-git-support-boundary.md`;
report `temp/icr/report-l23.md` (claims checked, never treated as evidence).

---

## 0. Fingerprints — the stated recipe reproduces byte for byte, and the candidate never moved

**Self-gate.** The report appeared and my combined fingerprint was sampled five times at 20 s intervals
before grading opened; it held from 15:59:16. Every reading below matches.

| component | declared (report §10) | my recomputation | match |
| --- | --- | --- | --- |
| `git status --porcelain` entry list | 8 entries, listed in order | identical 8 entries, same order | yes |
| `git diff \| sha256sum` | `d9d287db223b4cf32ea2ced328bc635b596339ed6b96bfce1b17a08163f75fa2` | `d9d287db…` | yes |
| untracked `mcp/…/review_external_git_movement.py` | `d536a86915921585490e495f8284ac0c83f739c873bc847471ceb920eed21a0d` | `d536a869…` | yes |
| untracked `models/knowledge/review_external_movement.py` | `2c4abb00cefdcab708ef43a150149c3355e73aab40c4e34bf2fd9ebcbb6c64cc` | `2c4abb00…` | yes |
| **combined** (diff ∥ filtered status ∥ untracked sha256 records, newline-joined, sha256) | not stated | **`854309a055894b49bb79bbdb42411f2428b90a477229bddad158dac53dc148f4`** | — |

**Start reading** 15:59:16 → `854309a0…`, component 1 `d9d287db…`, 8 entries.
**End reading** 16:19:14 → `854309a0…`, component 1 `d9d287db…`, 8 entries.
**The candidate did not move** across the whole verification (gate + all probes, mutations, rails and
regressions). My frozen copy
(`/home/firefox/projects/ar-coordination/temp/verify-l23/frozen`, commit `a1553de7…`) is byte-identical to
the leaf on **all eight** candidate paths — verified path by path with `sha256sum`.

*One recipe gap (L6):* report §10 item 3 declares **two** untracked code hashes, but its own one-line
"Recompute" command (`sha256sum $(git status --porcelain | awk '$1=="??"{print $2}')`) also hashes
`temp/icr/report-l23.md`, so that command prints a third hash the declared component does not include. The
declared components themselves reproduce exactly.

---

## 1. Base-defect witness — the failure really is on base bytes, and really is fixed on the candidate

**Their recipe, reproduced** (`/home/firefox/projects/ar-coordination/temp/verify-l23/base`, a pristine
`473ad824` checkout with only the delivery's test module and two new production modules copied in; the four
wiring files left at base bytes: `grep -c external_git_movement` on both = `0`):

```
cd base/mcp && PYTHONPATH=<base>/mcp/src:<base>/mcp/test_support:<base>/mcp/tests \
  <venv>/bin/python -m pytest tests/test_review_sync_movement_read.py -q -o addopts="" -p no:cacheprovider
-> 9 failed, 6 passed in 66.62s
   bare failure: AttributeError: 'KnowledgeReviewPayload' object has no attribute 'external_git_movement'
   (their §4.1 records 8 failed, 6 passed — the same substance; their recorded output predates the
    sixth new case, and their FAILED list has 6 + 3 SUBFAILED = 9 items)
```

**My own independent witness** (no test edits at all: my probe drives the real `git rebase` then the
shipped read on base bytes):

```
probe_l23.py rebase  @ base 473ad824
  [rebase] work head 49e8ab16… -> 4a7029fd…   recorded observedCodeHead 49e8ab16…
  staleness.state : current
  staleness.stmt  : the displayed comparison is the candidate's current comparison
  external_movement: <field not on the payload>
```

Same scenario on the candidate (`probe_l23.py rebase` @ frozen):

```
  staleness.state : stale
  staleness.moved : ('code-work-branch:37c44bca…',)
  staleness.stmt  : a raw Git operation replaced a declared identity of the reviewed generation
                    without a managed sync: the code work branch ar/icr-r01-l1 is at 3f9dd3d2… and the
                    head the review captured (37c44bca…) is no longer in its history. …
  submission.state: disabled_stale
```

**Base defect confirmed on base bytes, fixed on the candidate.** The delivery's claim reproduces.

---

## 2. The real operation, driven by me (never a private helper)

Every probe builds a real leaf enclosure through the shipped endpoint fixture, freezes a real comparison
generation (`freeze_review_comparison`), publishes the reviewed candidate through the publication owner,
performs **real Git** in the isolated fixture, and then reads through the shipped production read
(`application.knowledge_review.read_knowledge_review`) — the same entry the dashboard's
`serving_collaborators` calls (`cli/dashboard.py:102`). Nothing injects a payload or a resolution.

Artifacts: `temp/verify-l23/probes-frozen-final.txt`, `base-defect-witness.txt`,
`base-defect-candidate-tests.txt`, `debug_unreadable.py`, `bite_proofs_l23.py`,
`mutations_l23.py`, `regression-l23.txt`, `census.py`, `census_rev.py`.

| my probe (frozen bytes) | real operation | external state | evidence per channel | staleness | submission |
| --- | --- | --- | --- | --- | --- |
| control (untouched) | none | `current` | current / current / current | current | unavailable |
| append | real commit on the work branch | `current` | advanced / current / current | current | unavailable |
| **rebase** | real `git rebase <source>` in the worktree | **`stale`** | **replaced** / advanced / current | **stale** | **disabled_stale** |
| cherry-pick | real `git cherry-pick <official commit>` | `current` | advanced / current / current | current | unavailable |
| revert | real `git revert HEAD` | `current` | advanced / current / current | current | unavailable |
| **branch-switch** | real `git checkout <source>` in the worktree | **`not-measured`** | **unavailable** / current / current | current | unavailable |
| **source-rewrite** | real `commit --amend` rewriting the declared source base | **`stale`** | current / **replaced** / current | **stale** | **disabled_stale** |
| **memory-rewrite** | real amend rewriting the memory work branch | **`stale`** | current / current / **replaced** | **stale** | **disabled_stale** |
| **unreadable generation** | real junk bytes in the generation manifest | **`None`** | — | current | unavailable |
| **missing recorded object** | rebase + delete the loose object | `not-measured` | unavailable / advanced / current | current | unavailable |

The switch case's reason is truthful and actionable: *"code-work-branch was not compared: the code worktree
is on super, not on the declared work branch ar/icr-r01-l1, so the recorded work-branch head was not compared
against it"*, and its recovery is the matrix's own (*"return the worktree to its declared work branch, then
read the boundary again; this system never checks a branch out on a reader's behalf and never mutates a
checkout"*). `unsupported_transitions()` rides on every published value I produced, including the `stale`
ones — I verified that per state, so a green read still names the four unhandled routes.

**Preservation boundary — verified.** After a raw rebase the reviewed generation keeps every byte
(`manifest bytes same: True`), re-reads to the same `generation_id`, and the reopen owner still resolves it
(`reopen state: available`, source `available`, both knowledge halves `available`). *"An exact historic
generation stays inspectable while live recovery is pending"* holds.

---

## 3. Findings

### B1 — **blocking** — the observed-transition vocabulary names a Git event that did not happen, in the control state and in a second reachable state

* **file:line** `mcp/src/agents_remember/application/review_external_git_movement.py:622-634`
  (`_transition`), rendered through `_recovery_actions` (`:102-115`) into the published statement
  (`:682-698`) and into the payload field `external_git_movement.transitions`
  (`models/knowledge/review.py:1037`).
* **What.** Two distinct stores render a *named transition that did not occur*:
  1. **The control state — every ordinary review of an untouched leaf.** All three channels are exactly at
     their recorded identities, yet the value publishes `transitions = ('ordinary-append',)` and the
     sentence asserts *"a hash-level advance of the branch is the ordinary shape of work continuing under
     an already-frozen generation"*. Nothing advanced; the branch stands **at** the recorded head.
  2. **A missing recorded object.** After a rebase plus removal of the recorded commit's loose object (a
     reachable state — the repository's own reclamation owner exists), the worktree *is* on its declared
     branch, yet the value publishes `transitions = ('branch-switch', 'ordinary-append')`, claiming a
     branch switch that never happened. The reason beside it is truthful ("not a readable commit object"),
     the label is not.
* **Evidence (my reproduction, exact output).**
  ```
  probe control  @ frozen:  transitions ('ordinary-append',)
                            evidence (('code-work-branch','current'),('declared-source-branch','current'),
                                      ('memory-work-branch','current'))
                            statement …"ar/icr-r01-l1 is still at the recorded identity <H>; super is still at
                            the recorded identity <H2>; ar/icr-r01-l1 is still at the recorded identity <H3>.
                            ordinary-append: none required for the recorded identities; a hash-level advance
                            of the branch is the ordinary shape of work continuing under an already-frozen
                            generation"
  probe append   @ frozen:  same transition label, evidence code-work-branch 'advanced'
  bite_proofs_l23.py object @ frozen + MT4 removed:
                            transitions ('rebase','ordinary-append') for a missing object
  bite_proofs_l23.py object @ shipped: transitions ('branch-switch','ordinary-append') — no switch occurred
  ```
* **Why it matters.** The value is returned to every client (and persisted in the response) and the field's
  own docstring (`models/knowledge/review_external_movement.py:108`) says `transitions` *"names every shape
  observed"*: the shape observed in the control state is **no movement**, and the delivered matrix row
  rendered into the user-visible documentation says *"the branch moved forward without replacing anything"*
  — false for the state the read produces most often. The delivery already **holds** the distinguishing
  measurement (`GitMovementEvidence.current` vs `advanced`, `:95`) and shows it in `transition_evidence`;
  the transition label discards it and keeps the value that asserts an event. Under the master's standing
  rule (a persisted or user-visible sentence that is false about the store is blocking), this is blocking
  in the widest possible state, not a narrow one. I record the counter-reading explicitly: the model's
  definition comment (`:72-78`) calls `ordinary-append` *"the shape in which nothing the generation recorded
  was replaced"*, i.e. a bucket name rather than an event claim — but the name, the row's prose and the
  rendered recovery clause all assert an append/advance, `transitions` claims to name observed shapes, and
  the per-channel evidence proves the delivery knows the difference.
* **Suggested fix.** Give the vocabulary the state it actually observes (a `no-transition` /
  `unchanged` value, or `transitions = ()` when every channel is `current`), and derive `branch-switch`
  from the *branch* sub-fact (`_work_branch_finding`'s declared-branch check) rather than from
  "the code channel is unavailable", so a missing object can never render as a switch. Then align the
  `ordinary-append` row's `measured_signature`/`recovery_action` with the shape it covers.

### B2 — **blocking** — the `cherry-pick` row's documented recovery points the reader at measurements that do not exist for that transition

* **file:line** `mcp/src/agents_remember/application/review_external_git_movement.py:189-193` (the row),
  rendered verbatim into `docs/reference/worktrees-c09.md:134` (user-visible, and asserted by the
  delivery's own `test_the_documented_matrix_and_the_published_matrix_are_one_table`).
* **What.** The row's `recovery_action` says: *"read the existing measurements that do take it — the
  managed-sync rebinding on the sync route and the reopen channel on the read route — or publish a
  successor generation"*. For a real raw pick with no managed transaction behind it, **neither named route
  holds any measurement**: I drove all three.
* **Evidence (my reproduction, exact output).**
  ```
  probe pick-measurement-claim @ frozen (real git cherry-pick, no sync):
    payload.sync_movement                    : None
    reopen_comparison_generation(...)        : state available
    reopen … .sync_rebinding.state           : not-recorded
      detail "nothing is recorded at …/review-sync-rebinding-…json for comparison generation … of
              260921-icr-l1; a record this leaf's own reclamation discarded and one that was never
              written read the same here"
    external binding_state: current, transitions ('ordinary-append',)
  probe pick-then-sync @ frozen (same pick, then the REAL worktree_sync_tool):
    sync state             : already-current
    review_rebinding block : {'state': 'no-movement', 'detail': 'the recorded base pair and participating
                              work branches already contained the official line, so this sync carried
                              nothing and resolved no pair to measure the review against'}
    rebinding reader state : not-recorded
    sync_movement on read  : None
  ```
* **Why it matters.** This is the delivery's central deliverable — the support matrix — and it tells a
  reader who just cherry-picked that existing measurements take it. Measured, there are none on either
  named route, and the read renders `current` instead. The row's other clause (*"the pick is never
  identified from an ancestry check alone"*) is true and is the honest part; the recovery sentence is not.
* **Suggested fix.** State the recovery that measurably exists (publish a successor generation from the
  advanced tip, or run a managed sync first and then read *its* rebinding — the sync is the measurement,
  not an existing record), and drop the claim that the reopen channel takes a pick: the reopen channel
  reports the recorded generation's availability, which a pick does not change.

### H1 — **high** — an unreadable or ambiguous generation collapses into "no boundary measured", so the model's `unavailable` state is unreachable and two of the five states are merged

* **file:line** `mcp/src/agents_remember/application/review_external_git_movement.py:343`
  (`if selection.state != "selected" or selection.ref is None: return None`), against the promise at
  `models/knowledge/review_external_movement.py:23-28` (*"``unavailable`` says the generation itself could
  not be read"*) and the shipped `_unavailable` (`:355-376`).
* **What.** `select_review_generation` distinguishes four states (`review_final_output_receipt.py:145`:
  `selected`, `no-generation`, `unreadable`, `ambiguous`), each with its own `detail`. The boundary maps
  **all three non-selected states to `None`**. A generation whose manifest bytes are corrupt, and a tie of
  two generations claiming one index (the tie the reopen owner refuses), therefore read exactly like a leaf
  that never published a generation.
* **Evidence (my reproduction).**
  ```
  debug_unreadable.py @ frozen (manifest overwritten with b"{not json at all":
    selection: unreadable None
    read_manifest raised: KnowledgeStorageError the comparison manifest … could not be read
    movement: None
  probe unreadable-generation @ frozen (full shipped read):
    external binding_state: None      (field is None)
    staleness.state       : current
  ```
  `_unavailable`'s own sentence and its recovery (*"restore the readable comparison generation this leaf
  published …, then read the boundary again"*) are therefore never rendered for the case they name; the
  only way to reach them is a read failure between the selection's own manifest read and the boundary's
  second read.
* **Why it matters.** Leaf S3 requires *"Preserve known absence versus unavailable input"*, and the master's
  second standing rule keeps five states distinct. Today "never reviewed" and "review record corrupt" are
  one value, and the recovery the delivery documents for the corrupt case is unreachable in practice.
* **Suggested fix.** Map `selection.state == "unreadable"` (and `ambiguous`) to
  `_unavailable(enclosure, selection.detail)`; the vocabulary, the sentence and the recovery already exist.

### H2 — **high** — the closeout boundary carries no statement of the measured movement (incomplete clause of the packet's named boundary set)

* **file:line** packet Required Behavior; natural owners
  `mcp/src/agents_remember/application/worktree_tools.py:999-1000` (`attach_prepared_selection`,
  `attach_closeout_receipt`), `:453` (`attach_integration_receipt`), `:375` (`rebinding_result_block`).
  Delivered entry points only: `knowledge_review.py:490-496`, `review_task_context.py:165`.
* **What.** The clause says *"At the next review/**authoring**/**closeout** boundary compare declared
  source, knowledge ancestry and publication identities."* The delivery measures at the two **review**
  entries and attaches nothing at closeout/integration. `worktree_tools.py` already carries the exact
  attachment shape for R21 (`payload["final_output_receipt"]`) and R22 (`payload["review_rebinding"]`), so
  the owner is not missing — only the statement is.
* **Evidence.** `grep -rn "external_git_movement" mcp/src` resolves to the two review entries, the payload
  model and the tests only; the closeout result and the integration result attach their R21 receipts and
  nothing from this leaf. The closeout door's own checks
  (`worktrees/integration/closeout/door_evidence.py:47-77`) validate the **source** lineage and tips
  (`closeout-door-source-lineage-stale`, `closeout-door-code-source-moved`, and the memory twin) — not the
  leaf's work-branch head/tip this boundary measures, so a rewritten work branch leaves no statement at
  closeout. (The door comparing the *candidate tree* is a content fact; a same-content rewrite does not
  move it. Code reading, not a driven closeout — recorded as `unreproduced` below.)
* **Why it matters.** The packet names three boundaries; the master's scope reading covers all three; and
  the packet forbids adding a gate, so the fix is an attached statement (the R21/R22 shape), not a refusal.
* **Suggested fix.** Add an attachment in the new module
  (e.g. `external_git_movement_result_block(contract, payload)`) beside `attach_closeout_receipt` /
  `attach_integration_receipt` / `attach_prepared_selection`, publishing the measured value or its typed
  absence — never refusing.

### H3 — **medium** — the branch-switch state leaves the review surface's own sentence reading `current`

* **file:line** `application/review_sync_movement.py:311-340` (the fold promotes only `stale`);
  surface sentence at `models/knowledge/review_staleness.py:69-82`.
* **What.** With the code worktree switched away from its declared branch, `external_git_movement` is
  `not-measured` with a truthful reason, but `payload.staleness.statement` still reads *"the displayed
  comparison is the candidate's current comparison"* for a comparison composed from whatever branch is
  checked out. The fold's docstring calls this deliberate ("promoting an absence into `stale` would report a
  movement nobody observed"), and the sentence is not itself false (it is about the reader's carried
  binding) — but on the one surface that renders it (the client does not render the new field, report §8.4)
  a switched checkout reads as an ordinary current review.
* **Evidence.** `probe branch-switch @ frozen`: `staleness.state current`, `staleness.stmt "the displayed
  comparison is the candidate's current comparison"`, `external.state not-measured`,
  `external.reason "the code worktree is on super, not on the declared work branch …"`.
* **Why it matters.** The packet's Failure And Recovery clause is *"An unrecognized transition cannot reuse
  stale assessment as current"*; the review *is* the assessment surface, and here the only mounted sentence
  is the current one.
* **Suggested fix.** Render the boundary's own sentence on the surface for the `not-measured` case as well
  (e.g. carry the movement's statement into `staleness` without claiming `stale`, or expose a display state
  the client can render), and let R24/R25 mount the field as reported.

### L1 — **low** — the new documentation section miscounts its own table

`docs/reference/worktrees-c09.md:127`: *"the transitions are the ones a repository's own history can show,
and **the two** that this system does not reconcile say so rather than being implied by omission"* — the
table directly below has **four** `**unsupported**` rows (`rebase`, `cherry-pick`, `revert`,
`branch-switch`) and one `**supported**`. Reproduction: `doc rows: 5`, `unsupported_transitions()` returns
four. Suggested fix: say "the ones this system does not reconcile".

### L2 — **low** — the `generation_readable` validator clause is pinned by no case (undisclosed)

`models/knowledge/review_external_movement.py:182-186`. Disabling exactly that clause leaves the delivery's
module green (`12 passed, 3 subtests passed`, my run MT11); a forged value
(`binding_state="unavailable"`, a non-empty `reason`, `generation_readable=True`) is **refused** by the
shipped validator with its own message and **accepted** once the clause is disabled — so the clause is
load-bearing and unpinned. The report's §6 says the five forgeries cover the validator clauses; this one is
over-determined by the absence-needs-a-reason clause. Suggested fix: a sixth forgery
(`unavailable` + reason + `generation_readable=True`) required to raise, exactly as my bite-proof does.

### L3 — **low** — the `_object_readable` guard is unpinned (disclosed), and I built the fixture it asks for

`review_external_git_movement.py:586` (used at `:527`). Removing it leaves the module green (my MT4). With
it removed, my fixture renders `stale` and the sentence *"the head the review captured (<sha>) is no longer
in its history"* — **false** (the object is absent, not unreachable); with the guard shipped the same
fixture renders `not-measured` and the truthful reason *"the recorded identity … is not a readable commit
object …, so whether it still leads to the branch tip cannot be measured"*. The worker's §8.1 says the pin
would need "a fixture that removes the recorded commit's object bytes"; the fixture is: rebase the work
branch first (so the recorded head is no longer the tip and the capture still works), then delete
`<git-common-dir>/objects/<xx>/<rest>` — reproduced in `bite_proofs_l23.py object`.

### L4 — **low** — three dead module-level constants in the new module, one with a comment describing a guard that does not exist

`review_external_git_movement.py:87` (`_SUCCESSOR_GENERATION`), `:124-126` (`_MEASURED_STATES`, whose
comment says *"``reconciliation`` is read from this set, so a row's support verdict and its rendered state
cannot drift apart"* — nothing reads it), `:230` (`_UNMEASURED_RECOVERY`). Each name occurs exactly once in
the file (its own definition). Suggested fix: delete them, or wire `_MEASURED_STATES` into the check its
comment claims.

### L5 — **low** — the agreement sentence's "channels not compared" clause is unreachable

`review_external_git_movement.py:693-694`: `coverage` can never be non-empty because `_state` (`:412-427`)
returns `current` only when **no** channel is `unavailable`. Emptying the clause in my scratch copy leaves
the delivery green (MT8). The docstring above it (`:685-689`) claims a channel that could not be read "is
named here … rather than being left out"; the naming happens in `not-measured`'s `reason`, not here.

### L6 — **low** — the report's one-line fingerprint recompute does not match its declared component 3

Report §10 item 3 declares two untracked code hashes; the command
`sha256sum $(git status --porcelain | awk '$1=="??"{print $2}')` also prints
`55d4fdb1… temp/icr/report-l23.md`. The declared components reproduce exactly; only the one-liner is wider
than the declaration. (This is the L21/L22 evidence-reproducibility discipline, so it is worth stating.)

**`unreproduced` (said plainly, not implied as a pass):** I did not drive a real closeout/integration to
observe the absence of a boundary statement end-to-end (H2's door reading is a code reading plus a grep);
and I did not exercise the `ambiguous` selection tie (H1) — I drove `unreadable`, which is the same mapping
in the same expression.

---

## 4. Mutations — every new guard, disabled and re-attacked (rule 4)

All applied to my frozen copy, attack re-run, file restored and re-hashed (`restored=OK` on every row).

| mutation | guard | attack outcome |
| --- | --- | --- |
| MT1 | `_state` replaced ⇒ `stale` (`:423-424`) | **NOTICED** — 2 failed (raw rebase + source rewrite stops reading `stale`) |
| MT2 | the staleness fold (`review_sync_movement.py:331`) | **NOTICED** — 2 failed (the packet's failure clause returns) |
| MT3 | `_transition` replaced ⇒ `rebase` (`:632-633`) | **NOTICED** — 1 failed |
| MT4 | `_object_readable` guard (`:527`) | **silent** — 0 failed (unpinned; see L3) |
| MT5 | validator `stale` needs a replacement | **NOTICED** — forged shape accepted |
| MT6 | validator moved ⇒ `stale` | **NOTICED** |
| MT7 | validator absence needs a reason | **NOTICED** |
| MT8 | agreement coverage clause emptied (`:694`) | **silent** — the clause is unreachable (L5) |
| MT9 | `_state` "every declared channel was compared" conjunct (`:425`) | **NOTICED** — 1 failed (switch renders `current`) |
| MT10 | declared-branch check (`:456`, `_work_branch_finding`) | **NOTICED** — 1 failed |
| MT11 | validator `generation_readable` (`:182`) | **silent** — unpinned (L2); my direct bite-proof shows the clause refuses the forged value and stops refusing when disabled |

The three silent rows are each filed above; the two the worker disclosed (`_object_readable`) my runs
confirm, and `generation_readable` is an additional one.

---

## 5. Rails (my own commands, frozen bytes)

```
file sizes (1200 hard / 900 soft)
  application/knowledge_review.py            1054 -> 1073   (over soft at base, +19, delegation only)
  models/knowledge/review.py                 1164 -> 1175   (over soft at base, +11, field only)
  application/review_sync_movement.py         334 ->  368
  application/review_task_context.py          405 ->  413
  tests/test_review_sync_movement_read.py     356 ->  836
  NEW application/review_external_git_movement.py    729
  NEW models/knowledge/review_external_movement.py   187
  docs/reference/worktrees-c09.md             155 ->  185
>=1200 whole-tree census (my detector + the repository's own scope; base 473ad824 measured the same way)
  base : 2116 files, 27 offenders, 100 in the 900-1199 band
  cand : 2118 files, 27 offenders, 100 in the 900-1199 band      -> no new offender
catalog          sha256sum mcp/tests/evidence-lifecycle.toml = d07c2f9d456b0f658228c91aecb2a1f3da8d13e2b6575b6b40b0b0d2ca165f6b
                 (unchanged; 16 contracts / 66 artifacts; LIFECYCLE_CATALOG_SHA256 in the census module matches)
no new lane row needed: no new test module; tests/test_review_sync_movement_read.py is already in the
                 integration lane of mcp/tests/test-evidence-lanes.toml
rails trio+  pytest test_dependency_ownership_ast_helpers test_evidence_catalog_gate_boundaries
             test_file_size_detector test_evidence_lanes test_layering test_suite_budget -q -m ''  -> 18 passed
pyright --pythonpath <main>/mcp/.venv/bin/python (7 touched files)   -> 0 errors, 0 warnings, 0 informations
ruff check (7 files)          -> All checks passed!        ruff format --check -> 7 files already formatted
new # noqa                    -> none in the two new modules
pytest mcp/tests --collect-only:  unit 2701/2722 (base 2701/2722), integration 455 (base 448)   both far under 4000/1000
regression: 29 review-facing modules incl. the L22 siblings and test_worktree_sync.py, -m ''  -> 359 passed, 12 subtests, 165.9 s
delivery module alone, -m ''  -> 12 passed, 3 subtests passed
```

**Seam policy.** The adapter is a delegator: one import (`:103`), one call and fold (`:490-496`), one
payload field (`:556`), plus the docstring record — no feature logic; `review_task_context.py` adds one
import plus one keyword. The one implementation lives in the purpose-named adjacent module; no private name
moved or was imported across modules (`grep` of both importers shows only `external_git_movement`); the
matrix, the transition vocabulary, the state composition and the sentence builders exist once. No existing
owner is duplicated: `select_review_generation`/`read_manifest` resolve the generation, R22's rebinding is
read and never re-derived, and the capture owner is not called. Both folds are exported with one caller.
Nothing else in `mcp/src` imports an old private name.

---

## 6. What is genuinely strong here

1. **The core obligation is closed on the shipped read.** A real `git rebase` — on the work branch, on the
   declared source branch, or on the memory work branch — now renders `stale`, names the exact replaced
   identity as `channel:identity`, disables submission, and leaves the reviewed generation byte-identical
   and reopenable. My base witness shows the packet's non-conforming state on base bytes and its absence on
   the candidate, driven by me, not by their test.
2. **The four-state vocabulary is honest where it matters**: `not-measured` for a switched checkout and for
   a missing recorded object carries a precise, actionable reason and names the concrete recovery, and no
   `unavailable`/`not-measured` state is ever promoted to agreement. `unsupported_transitions()` rides on
   every value I produced.
3. **`unsupported` is a field, not a tone**, `supported_recovery(name)` refuses an unknown transition
   instead of defaulting, and the documented table is asserted equal to the production table (I re-rendered
   it myself: `rendered table in doc: True`, 5 rows each).
4. **The seam and the rails are clean**: adapter delegation only, no duplicated owner, no new test module,
   census/population/pin unchanged, pyright/ruff/format green, no new suppression, budgets well inside.
5. **The report is unusually honest about its own limits** (§8 enumerates the unpinned guard, the
   unexercised unreadable path, the non-distinguishability of pick/revert, the unmounted client field and
   the two-entry-point reading) — and my measurements confirm its disclosures rather than contradicting
   them, except that the unreadable path is *unreachable*, not merely unexercised (H1).

---

## Verdict

**Verdict: `fail`** — two blocking findings.

The packet's central deliverable is the support matrix and the boundary measurement that reports against it,
and both blocking findings are false statements that the delivery itself publishes. **B1**: the observable
transition value names an event that did not happen in the control state — every ordinary review of an
untouched leaf publishes `transitions=('ordinary-append',)` with a sentence asserting "a hash-level advance
of the branch" — and in a second reachable state (a missing recorded object) it names a `branch-switch` that
never occurred; the delivery holds the distinguishing measurement (`current` vs `advanced`) and discards it
in the label, and the same value's row is rendered into the user-visible documentation as "the branch moved
forward". **B2**: the `cherry-pick` row's documented recovery tells a reader to read "the existing
measurements that do take it — the managed-sync rebinding on the sync route and the reopen channel on the
read route", and I measured all three: the R22 read is `None`, the reopen channel is `available` with its
rebinding `not-recorded`, and the real managed sync returns `already-current` with `no-movement` ("resolved
no pair to measure the review against"). Both are exactly the class the master's first standing rule makes
blocking, and both are cheap to fix (a vocabulary value plus an honest recovery sentence). Beyond them I
filed two highs — an unreadable/ambiguous generation collapsing into `None`, which makes the model's own
`unavailable` state and its recovery unreachable (H1), and the absent closeout boundary statement the parent
asked me to grade explicitly (H2) — plus H3 (a switched checkout still reads `current` on the one mounted
sentence) and six lows, three of which are unpinned/unreachable code the delivery did not disclose. The
verification itself is reproducible: the worker's stated fingerprint components recompute byte for byte, my
combined fingerprint `854309a0…` held from the gate through the end reading, every probe drove the real
operation on frozen bytes, and every load-bearing guard was mutation-proved and restored.

Artifacts under `/home/firefox/projects/ar-coordination/temp/verify-l23/`: `probes-frozen-final.txt`,
`probes-frozen.txt`, `base-defect-witness.txt`, `base-defect-candidate-tests.txt`, `debug_unreadable.py`,
`bite_proofs_l23.py`, `bite_proofs_mutated_l23.py`, `mutations_l23.py`, `mutation_harness.py`,
`regression-l23.txt`, `census.py`, `census_rev.py`, `fingerprint-l23.sh`, `gate-l23.sh`, `frozen/`, `base/`.
