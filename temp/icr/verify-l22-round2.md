# Independent adversarial verification — leaf L22 / ICR-R22@v1 — ROUND 2 (fix round 1)

**Verifier.** Same independent adversarial verifier as round 1; no part in the implementation. Every
claim below was reproduced by me on the round-1 frozen bytes; the guard I call load-bearing was removed in
my own scratch copy and the attack re-run on the mutant (restored afterwards, byte-identical).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22`,
branch `ar/260921-icr-l22`, base `e605822eb3bf83bf63a45963c5f51d5fc28859ee`, plus the worker's
uncommitted fix-round delta. Packet `ICR-R22-v1-managed-git-recovery-rebinding.md`; report
`temp/icr/report-l22.md` §"Fix round 1" (claims checked, never treated as evidence). Harnesses reused from
round 1 under `/home/firefox/projects/ar-coordination/temp/verify-l22/`.

---

## Fingerprints

| Point | tracked `git diff` sha256 | untracked candidate code | status entries |
| --- | --- | --- | --- |
| gate reading (13:31:26, held 120 s to 13:33:36) | `4a1bb68c4f1c1cef0f2480f0c035965e8c5b2c7e319e213858c75dd1b62112e3` | `b7734041…`; 13 `mcp/` entries | 18 excluding report+verdict (20 raw) |
| end (13:44:22, after every probe, mutation and rails run) | `4a1bb68c…` **unchanged** | all five hashes unchanged | same |

## ⚠ `candidate-moved` — the worktree moved after the grading window closed

Everything below grades the **frozen round-1 bytes**. They were stable for the whole grading window
(13:31:36 → 13:44:22, gate readings A/B and the end reading all identical, all five untracked code hashes
unchanged). **While this verdict was being written the worktree moved**, and the new bytes are a fix round 2
aimed at this round's blocking finding G1 (evidence: a new
`temp/icr/fix-round-2-g1-bite-proof-l22.txt`, whose own text is titled *"the new case against the PRE-FIX
module (sha256 baa0282b… — the bytes round 2 graded)"*). Those new bytes are **not graded here** and need a
fresh verification round.

| | tracked `git diff` sha256 | code-fingerprint | `mcp/` entries |
| --- | --- | --- | --- |
| **graded** (stable 13:31:36 → 13:44:22) | `4a1bb68c4f1c1cef0f2480f0c035965e8c5b2c7e319e213858c75dd1b62112e3` | `b7734041cff85976d768a923f9bdc2c3bac8438df09c0874b998ed27473eadc6` | 13 |
| **worktree at 14:00:07** (still moving: a second sample 20 s later read `17f16083…`) | `a278a47c11721d9510984e2527de7a8a86702b5c19139dc090cb2530f72a6623` | `7e99ebb3…` → `17f16083…` | 12 |

Per-file movement (frozen → worktree): `application/review_sync_movement.py` `baa0282b…` → `92ab515f…`,
`application/review_sync_rebinding.py` `c3d3441c…` → `3a036d2c…`, `models/knowledge/review_staleness.py`
`7b6ebea7…` → `e73b58b5…`, `tests/test_review_sync_rebinding.py` `0ed32220…` → `ce16eb47…`;
`models/knowledge/review_sync_rebinding.py` `39ef0999…` unchanged, and `mcp/tests/test_worktree_sync.py`
is no longer modified at all (13 → 12 `mcp/` entries). Corroborating G1 independently: the worker's own
bite-proof shows its new case failing on the pre-fix module with exactly the defect I filed —
`ReviewSyncMovement(binding_state='current', moved_identities=(), …)` where the case requires
`not-measured`.

---

**Within that window the candidate did not move.** The worker's declared tracked diff reproduces exactly, and its five
declared untracked hashes reproduce exactly:

```
c3d3441cc472148dc075fee0a3dcfc515de599b6f764480afc01ae7fbadbe193  application/review_sync_rebinding.py
baa0282bc9faa0dff9416d5b835a009c21603430effa0ee2aed5dcf0d4d87b82  application/review_sync_movement.py
7b6ebea7c076bccc22229b8105a1b8333c9b77ead44f5404c4e19d2349011e0f  models/knowledge/review_staleness.py
39ef099955b1963c173bf59f6ec845f9a59e38b0f367f23ba85ed4fc282c68e2  models/knowledge/review_sync_rebinding.py
0ed322203c5359db49f25f0a1104c1ba59e1b21d89c406cef440411a18dff66e  tests/test_review_sync_rebinding.py
```
Its `18` status entries (report + my verdict excluded) also reproduce. I could not reproduce its *combined*
scalar (`9a6ff175…`) because its exact concatenation convention is not stated; I reconstructed a
plausible one and got `3ad836ab…`. Both content-bearing components match, so the identity is confirmed
either way — I record the discrepancy rather than pretend to a byte-exact match.

---

## 1. F1 — closed, and the MF1c disclosure is correct

All six states driven through the **real** `worktree_tools.worktree_sync_tool` (my `test_zz_probe5_l22.py`):

```
no-movement    | already-current                  | {'state': 'no-movement',    'detail': 'the recorded base pair and
                                                    participating work branches already contained the official line, so this
                                                    sync carried nothing and resolved no pair to measure the review against'}
preview        | would-sync (official line moved) | {'state': 'preview',        'detail': 'this is a preview: the sync read
                                                    its sources and moved no ref, no branch and no journal, so it resolved no
                                                    pair to measure the review against'}
not-resolved   | sync-resolution-required         | {'state': 'not-resolved',   ...'stopped with unmerged paths the knowledge
                                                    adapter would not settle'...}
cancelled      | sync-cancelled                   | {'state': 'cancelled',      ...'the explicit cancellation restored every
                                                    participating branch'...}
choice-required| memory-sync-choice-required      | {'state': 'choice-required',...'required a memory sync choice before any
                                                    mutation'...}
carrying       | sync-pass-completed-memory-skipped | {'state': 'moved', ...} — measured, with the resolved identities
```
Every sentence is **true about the store in the state that rendered it**: Git confirmed the already-current
leaf's recorded base equals the source tip; the preview's work branch was still at its pre-sync head and no
durable file was written in any of the five non-carrying states; the `choice-required` state is reached
(memory work branch carried its own commit, official memory line moved) and no mutation had happened. No
state claims a measurement it did not make, and none denies one it did.

**MF1c — the worker's disclosure is a correct reading of its own fix, verified by mutation.** I restored
`and "codeBaseCommit" in payload` in my scratch copy (`946fbbea…`) and ran both the delivery's cases and my
state probe:

* `pytest tests/test_review_sync_rebinding.py -q -m ''` on the mutant → **8 passed** — the conjunct is not
  load-bearing, exactly as reported.
* My state probe on the mutant prints the **same five true sentences** and the same carrying measurement.

So the state table alone keeps the mutated case honest: `already-current` is described by its own entry
rather than by payload-key inference, and the removed conjunct has no observable effect in either
direction. The round-0 prediction ("either half is sufficient") is confirmed, and the worker applied both
anyway and reported the redundant one honestly rather than claiming a bite it did not have.

## 2. F2 — closed

With the curator's untracked file parked and restored (`test_zz_probe5_l22.py::..._f2_clause_with_wip`):

```
wip restored         : True
head tree == capture : False                    (git rev-parse <head>^{tree} != resolved candidate tree)
old relation present : False                    ("carries candidate tree <T>" absent)
locating clause      : True                     ("captured from the leaf's worktree at work branch head <H>" present)
```
The claim is now true in both states (clean and dirty), which is what the round-1 finding asked for.

## 3. F3 — closed

```
no generation          : {'state': 'no-generation', 'detail': 'no comparison generation is published for this
                          leaf, so there is no reviewed comparison to measure against the pair this sync carried',
                          'selection_state': 'no-generation', 'selection_detail': 'no comparison generation is
                          published for 260921-icr-l1, so there is no reviewed generation to record a final output against'}
unreadable manifests   : {'state': 'generation-unreadable', 'detail': "this leaf's comparison-generation directories
                          exist and none holds a readable manifest, ...", 'selection_state': 'unreadable',
                          'selection_detail': '2 generation director(y/ies) exist ...'}
```
The block owns its own vocabulary; the selection owner's answer travels under `selection_state`/
`selection_detail`; the reader's `unreadable` now means one thing only (the artifact could not be read).
Verified on both paths (this block, and `read_review_sync_rebinding`).

## 4. F4 — closed

```
before: recorded | removed: 1
after state : not-recorded
after detail: nothing is recorded at <path> for comparison generation <id> of <leaf>; a record this leaf's own
              reclamation discarded and one that was never written read the same here
denies a measurement: False
```
The absence sentence is now an observation of the location, and the two readers that have no mounted caller
say so in their docstrings rather than implying one.

## 5. F5 — the split is real, verified with my own commands

```
test_worktree_sync.py         base 937   ->  945   (under 1200)          [claimed 945 ✓]
test_review_sync_rebinding.py       --   -> 1037   (new)
models/knowledge/review.py         1198  -> 1164   (extraction made the room)
whole-tree .py > 1200        base  26   ->   26    (back to the base count)   [claimed 26 ✓]
whole-tree .py >  900        base 108   ->  109    (the +1 is the new test module; disclosed)
catalog pin                  sha256sum mcp/tests/evidence-lifecycle.toml = 7c8c1646272ca4a17f87a16ecbe105a4aea558c0e0d0c4d62b1883d6d69e683c
                             LIFECYCLE_CATALOG_SHA256                   = 7c8c1646272ca4a17f87a16ecbe105a4aea558c0e0d0c4d62b1883d6d69e683c  ✓ match
lane row                     integration lane, beside test_worktree_sync.py (tomllib-verified)          ✓
population                   16 contracts / 66 artifacts  ("7 passed" on the census trio)                ✓
moved cases on base bytes    pytest <new module> against pristine base bytes ->
                             ModuleNotFoundError: No module named 'agents_remember.application.review_sync_rebinding'
                             1 error during collection
```
The eight cases are the four moved ones plus four new ones, the fixture and its helpers moved with them, and
`test_worktree_sync.py` no longer references `ManagedSyncReviewFixture`/`ReviewSyncFixture` at all. The
catalog rows were derived (the census reported the consumer changes; the rows were re-measured against a
fresh `sha256sum`, not guessed). The base-bytes reading is the existence half — the behavioural half is my
own base-defect witness, re-run on these bytes: base reports **no** `review_rebinding` key while the capture
moves `4bca9de2…` → `c1ae10f1…`; the candidate records `moved` with both identities.

## 6. F6 — the moved state is closed; **one state is not** (see G1)

Three states driven through the **shipped** `read_knowledge_review`:

```
(a) no sync yet            sync_movement: None,   staleness: current            (delivery case; my probe 5e covers the successor variant)
(b) carried, unchanged     sync_movement: current, staleness: current
(c) moved                  sync_movement: stale,   moved_identities: ('code-candidate-tree:4bca9de2…',),
                           previous_comparison_ref: present, submission: disabled_stale,
                           staleness: stale with the movement's sentence
```
**Falsification attempts that did NOT break it:**
* *A movement for another generation must not affect this one* — froze generation 1, synced (record for
  gen 1, movement `stale`), then published a successor naming gen 1 as parent: the live read's
  `sync_movement` is **None** (the successor has no record of its own) and nothing leaked from gen 1. ✓
* *Supersession must not fabricate a movement* — same run: no record → no movement, not a synthesised
  `current`. ✓
* *A record for another generation must not be read as this one's* — `rebinding_names_the_generation`
  cross-checks the generation id, index, binding digest and reviewed identities against the manifest before
  the projection (read-verified; the delivery's case 3 forges an identity and requires the refusal). ✓
* *Ambiguous selection* — `_measured` returns `None` whenever `selection.state != "selected"`; verified for
  the superseded (non-selected) case above. I could **not** build a sealed duplicate-index manifest without
  forging one, so the `ambiguous` branch specifically is **unreproduced**; it is guard-read, not re-driven.

**Seam extraction — clean.** `models/knowledge/review.py` 1198 → 1164 and re-exports `ReviewStaleness` /
`ReviewSubmission` / `ReviewSyncMovement` (import :61-64, `__all__` :116/:119/:121); the single
implementation lives in `models/knowledge/review_staleness.py`; `review_comparison_staleness.py` is
byte-untouched (97 L); `knowledge_review.py` is **+14/−1** and is one import, one
`review_sync_movement(resolved)` call, the fold call and the payload field — no feature logic.
`review_task_context.py` and `review_record_rendering.py` still import through the payload module, so the
re-export is doing its job. I walked and imported **1163 modules** under `mcp/src`: **0 import failures**.

---

## 7. Findings

### G1 — **blocking** — the live review read renders an **unmeasured** knowledge channel as a measured agreement

* **file:line** `mcp/src/agents_remember/application/review_sync_movement.py:111-132` (`_project`: a
  movement's `moved` tuple is derived only from `== "differs-from-reviewed-input"`, so `unmeasured` and
  `matches-reviewed-input` collapse into `binding_state: "current"`) and `:155-160` (the not-stale
  sentence), reached from `mcp/src/agents_remember/application/knowledge_review.py:470-476` into the
  `sync_movement` field of every `read_knowledge_review` payload.
* **What.** When the sync's own record says the knowledge channel was **never compared**
  (`state: unmeasured`, `knowledge_match: unmeasured`, `resolved_knowledge.state: not-recorded`), the
  shipped live read publishes `sync_movement.binding_state == "current"` and the sentence *"a managed sync
  completed and comparison generation … still describes the pair it resolved: the reviewed candidate tree
  … **and the reviewed dataset are the ones the leaf holds**"*, beside R17's `staleness.state == "current"`
  (*"the displayed comparison is the candidate's current comparison"*). Nothing measured that dataset: the
  declared publication location holds no dataset at all.
* **Evidence (my `test_zz_probe6_l22.py`, shipped `read_knowledge_review`, frozen bytes).**
  ```
  record state   : unmeasured
  knowledge_match: unmeasured
  location state : not-recorded            (declared location exists: False; the read route answers not-recorded)
  staleness      : current
  submission     : unavailable
  movement       : current
  movement stmt  : a managed sync completed and comparison generation 4737cb72-6748-58b0-a3b4-da7babe3ab0c
                   (index 1) still describes the pair it resolved: the reviewed candidate tree
                   224187e2cc1c3c2cbc9d51a0c36bc12199467887 and the reviewed dataset are the ones the leaf holds
  ```
  The state is reached by the ordinary shape my probe builds: the leaf already holds the content the
  official line adopts (so the code channel **matches** — a delivered case's own "measured agreement"
  scenario), the review retained a knowledge operand, and the declared publication location holds nothing.
  The delivery's F6 case always calls `publish_reviewed_candidate()`, so its three states are
  `matches`/`differs` only and it never reaches this one — the `unmeasured` verdict is exactly the state
  the round-0 report routed as reachable-but-uncased (§8.5).
* **Second rendering state (structural, not reproduced).** The same sentence is emitted whenever `moved` is
  empty, so it also renders for a generation whose after side is `not-recorded` or `not-selected` — a
  generation that recorded **no knowledge operand at all**. The round-0 *record* handles that state
  carefully (`_knowledge_clause`: "generation … records not-recorded for its knowledge operand, so no
  dataset was compared to a reviewed one"); the movement's sentence does not. Marked **unreproduced**: I
  drove no fixture whose manifest after side is `not-selected`, and say so rather than implying I did.
* **Why it matters.** This is the master's ruled-blocking class twice over. (i) The rule that the five
  states stay distinct: an **unmeasured** input now reads as a **measurement** on the surface the ruling
  named, and the live read is strictly *more* permissive than the record it reads — the record says
  "unmeasured", the read says "current". (ii) A false store-facing sentence. It is also the packet's own
  non-conforming example not actually closed for this state: the old review still reads as untouched, now
  with an explicit and untrue assertion of dataset identity attached.
* **Suggested fix.** Do not re-derive the state from the two match fields: read the record's own verdict
  (or refuse to project `unmeasured` as `current`). Concretely, either give `ReviewSyncMovement` a third
  binding state for "a retained operand was not compared" with its own sentence, or make the not-stale
  sentence claim only what was measured (*"the reviewed candidate tree T is the tree the leaf holds; the
  reviewed dataset was not compared"*). Then add the case: the F6 case's own "measured agreement" scenario
  **without** `publish_reviewed_candidate()`, asserting `binding_state != "current"` and the sentence not
  containing "the reviewed dataset".

### G2 — **low, latent** — `resolved_pair_completed` no longer checks `ok`

* **file:line** `application/review_sync_rebinding.py:206-210`.
* **What.** The predicate is now `operation == "worktree_sync"` and state ∈ the three carrying states; the
  `bool(payload.get("ok"))` conjunct was dropped. That is correct today — all three states are only ever
  produced with return code 0 (`finalize_sync` and `terminal_resolution_replay` → `completed_sync_result`)
  — but a future producer of a carrying state with a non-zero code would be measured and recorded as a
  resolution.
* **Evidence.** Read-verified over every producer of those three states (`worktrees/sync_transaction.py`,
  `sync_transaction_recovery.py`); **not reproduced** because no such producer exists on these bytes.
* **Suggested fix.** Restore the `ok` conjunct for symmetry with the state table, or state in the docstring
  that a carrying state implies success.

### G3 — **low** — the two named but unmounted readers

`read_review_sync_rebindings` and `discard_review_sync_rebindings` still have no production caller
(`grep -rn` over `mcp/src` returns only their own module). Round 1's F4 asked for this debt to be *named*,
and it now is (both docstrings state they are the acceptance/curation route); the F4 *sentence* defect is
closed. Recorded as accepted residue, not a new defect.

---

## 8. Regression and rails (my own runs, frozen bytes)

```
delivery cases + sync module          pytest tests/test_review_sync_rebinding.py tests/test_worktree_sync.py -q -m ''
                                      -> 16 passed, 9 subtests passed in 39.39s
affected modules (7)                  100 passed, 9 subtests passed in 131.52s
all tests/test_*review*.py            every review-family module -> 340 passed in 252.86s (0:04:12)
import smoke test                     1163 modules walked, 0 import failures
census trio (clean tree)              test_dependency_ownership_ast_helpers + test_evidence_lanes +
                                      test_file_size_detector -> 7 passed in 54.97s
pyright --pythonpath venv/python      0 errors, 0 warnings, 0 informations (10 touched files)
ruff check / ruff format --check      All checks passed! / 10 files already formatted
new # noqa in the four new modules    0, 0, 0, 0
populations                           unit 2701/4000, integration 444/1000   (both claimed values reproduce)
>=1200 census                         26 -> 26        soft band 108 -> 109 (disclosed)
```
Two census runs failed *before* this: both were my own probe modules sitting in the scratch `mcp/tests/`
(the census derives consumers from imports and correctly refuses an unregistered module). Removed and
re-run on the candidate's own tree — clean, as above.

---

## 9. What is genuinely strong in fix round 1

1. **F1 is fixed properly, not patched.** A state *table* with its own sentence per state, plus a
   case that drives all four non-carrying states through the production tool — including the genuine
   `would-sync` preview that round 0's case failed to build. The mutated case is honest without the removed
   conjunct.
2. **The MF1c disclosure is exactly right and I verified it**: the conjunct is not load-bearing once the
   table exists, and the worker said so instead of claiming a bite.
3. **F2, F3 and F4 are closed as asked**, each with the sentence made true rather than the state hidden.
4. **The F5 split is real and derivation-driven**: sizes, census count, lane row, catalog pin and
   population all reproduce, and the moved cases still fail on base bytes.
5. **The F6 seam work is exemplary**: one responsibility per module, a genuine extraction that made room at
   the rail (1198 → 1164, re-exported, `__all__` intact, no importer broken, 1163 modules importing
   cleanly), and the adapter at +14/−1 with no feature logic.
6. **The moved-state rendering works**: stale, the moved identity named, the previous input a real
   identity, submission `disabled_stale` — and the cross-generation isolation holds under my falsification.
7. **The probe-first discipline**: the forge gap, the prefix failures and the mutations are all retained as
   artifacts under `temp/icr/`, and the remaining gaps are routed rather than implied.

---

## Verdict

**Verdict: `candidate-moved`** for the worktree as it stands — and **`fail`** for the frozen round-1 bytes
this document actually grades, with **G1 blocking**.

The worktree is no longer the tree I verified: while this verdict was being written it moved to a fix
round 2 aimed at G1 (both readings and the per-file deltas are in the `candidate-moved` section above).
Those new bytes are ungraded and need a fresh round; a curator must not treat this document as a verdict on
them. What this document does establish, on the frozen bytes `4a1bb68c…` that were stable for the whole
grading window, is the result below.

Fix round 1 closes everything I filed in round 0 except one state, and closes it well: F1's table, F2's
clause, F3's vocabulary, F4's sentence, F5's split and F6's moved-state rendering all reproduce under my own
commands, MF1c's non-load-bearing disclosure is correct, and the seam extraction is clean with no broken
importer. But the F6 fix has a hole precisely where the packet's non-conforming example lives: a record whose
own verdict is `unmeasured` — a retained knowledge operand that was never compared because the declared
publication location holds nothing — is projected by `review_sync_movement` into `binding_state: current`,
and the shipped live read then tells the reader that "the reviewed dataset [is] the one the leaf holds",
beside R17's `current`. That is an unmeasured input rendered as a measurement and a false store-facing
sentence on the surface the ruling named, so by the master's standing rule it is blocking and those bytes
were not acceptable as they stood. The fix is local — derive the movement state from the record's own verdict
rather than from the two match fields, and give the unmeasured case its own sentence — and the case that
would pin it is the F6 case's own scenario with the publication step removed. Early evidence from the
worker's in-flight round 2 suggests exactly that fix; it must be verified on its own frozen bytes.

Artifacts: `temp/verify-l22/{probe_l22.py … probe6_l22.py, probe-base-defect.py, frozen/, base/,
code-fp.sh, fp2.sh, freeze2.sh, r2-delivery.log, r2-regression.log, r2-regression2.log, mf1c-backup}` under
`/home/firefox/projects/ar-coordination/temp/`.
