# Independent adversarial verification — leaf L21 / ICR-R21@v1 (review-to-closeout identity continuity)

**Verifier.** Independent adversarial verifier for `260921-ICR-L21`; no planning transcript, no part in
the implementation. Every claim below was reproduced by me on the frozen candidate; every guard I call
load-bearing was removed in my own scratch copy and the attack re-run on the mutant (mutants restored
afterwards, byte-identical to the frozen copy).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l21-ar/260921-icr-l21`,
branch `ar/260921-icr-l21`, `HEAD = 972b44cc07b307929535fe7974d6a30d53c9c4f1` (`ICR-R17…`), plus the
worker's uncommitted delta. Packet
`requirements/ICR-R21-v1-review-to-closeout-identity-continuity.md`; leaf doc
`21_review-to-closeout-identity-continuity.md`; worker report `temp/icr/report-l21.md` (claims checked,
never treated as evidence).

---

## Fingerprints (self-gate held)

`git diff | sha256sum` (tracked) — sampled every 90 s from 07:51; report present from 08:30.

| Point | tracked `git diff \| sha256sum` | combined fingerprint¹ |
| --- | --- | --- |
| start (08:32:48) | `641e3d0746a96d6f0613077cecf170bd8af9325a212302696a9a2bdebc209160` | `85bc992d12a2687a626cc8c74f333b45d9e60f0482eee8f842ae6e15ba551c16` |
| gate open (08:31:41 / 08:33:04) | `641e3d07…` (90 s stable) | `85bc992d…` (95 s stable) |
| end (08:39, after all probes, mutations and rails) | `641e3d0746a96d6f0613077cecf170bd8af9325a212302696a9a2bdebc209160` | `85bc992d12a2687a626cc8c74f333b45d9e60f0482eee8f842ae6e15ba551c16` |

¹ combined = sha256 over the tracked diff **plus every untracked code file's own sha256** (the template's
requirement; a `git diff`-only gate cannot see the three new files). Untracked code at both ends:

| file | sha256 |
| --- | --- |
| `mcp/src/agents_remember/application/review_final_output_receipt.py` | `32b864567b8e3fb9eedc883821489a89ed74514183f31d2cbbfec05c613582c7` |
| `mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py` | `b71c27159fac4f6cd68daafebebe93d7e7e4ffe823bf5ea2e3fff8cb809f7153` |
| `mcp/tests/test_review_final_output_receipt.py` | `a80e58182e8538e5076df2e5d033ea223f8fff7ab5d6b3352f1c38ea05bc486e` |

**The candidate did not move.** The tree *did* move during the wait (four distinct fingerprints,
07:51→08:30, including a change to the receipt sentence itself); nothing before 08:33 is graded here.
Scratch space: `/home/firefox/projects/ar-coordination/temp/verify-l21/` (`base-tree`, `base-rail`,
`frozen`, `mut`, `probe_*.py`, `final-run.log`).

---

## 1. What is delivered

| # | File | Change |
| --- | --- | --- |
| 1 | `mcp/src/agents_remember/application/review_final_output_receipt.py` (new, 762 L) | `select_review_generation` (:178, highest recorded index; `no-generation`/`unreadable`/`ambiguous` are separate states), `record_final_output_receipt` (:254), `read_final_output_receipt` (:429), `discard_final_output_receipts` (:521), `final_output_selection_block` (:546), `final_output_result_block` (:684), and the three result attachments `attach_prepared_selection` (:625), `attach_closeout_receipt` (:642), `attach_integration_receipt` (:662). |
| 2 | `mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py` (new, 268 L) | The sealed record, the two match vocabularies (:72, :80), the self-consistency validator (:152) and `statement()` (:216). |
| 3 | `mcp/src/agents_remember/application/worktree_tools.py` (1020 → 1030 L) | Three delegating call sites: preview (:994), closeout apply (:995), integrate (:448). |
| 4 | `mcp/tests/test_review_final_output_receipt.py` (new, 705 L) | Seven cases driving the real preview/apply/integrate tools on a real enclosure. |
| 5 | `mcp/tests/test-evidence-lanes.toml`, `mcp/tests/evidence-lifecycle.toml`, `mcp/tests/test_dependency_ownership_ast_helpers.py` | Lane row, three consumer rows, catalog digest re-pin. |

`application/knowledge_review.py` is untouched (1041 L before and after).

---

## 2. Findings

### F1 — **blocking** — a user-visible sentence that is false about the store (closeout preview)

* **file:line** `mcp/src/agents_remember/application/review_final_output_receipt.py:240`
  (`select_review_generation`'s selected detail), surfaced through
  `final_output_selection_block` (:546) → `attach_prepared_selection` (:625) →
  `mcp/src/agents_remember/application/worktree_tools.py:994` (the `worktree_closeout_preview` result),
  i.e. the `final_comparison_selection.detail` a caller reads on every closeout preview.
* **What.** The sentence ends "…so it is the one this leaf's final output is recorded against". It is
  asserted for the *highest recorded generation index*, in the present tense, whether or not any
  final-output receipt exists and whichever generation an existing receipt names.
* **Evidence (my probes on the frozen bytes; logs in `final-run.log`).**
  1. *Nothing recorded yet* — real preview of a leaf with a frozen generation:
     `selection_detail: … generation c2302d38-… (index 1) … so it is the one this leaf's final output is
     recorded against`, while the store's own reader says otherwise:
     `store_receipt_for_the_selected_generation: not-recorded` / `no closeout final-output receipt is
     present at …/final-output-260921-icr-l1-c2302d38-…-closeout.json, so no final output for that phase
     is recorded at that location`. (`probe_preview.py`.)
  2. *A receipt exists and names a different generation* — real closeout recorded generation
     `74483c8e…` (bound), the review then moved and a successor `6bf9a745…` (index 2) was frozen with
     `ComparisonFreezeOptions(parent=…)`, then a fresh preview was read:
     `selection_detail: generation 6bf9a745-… carries the highest recorded index (2) … so it is the one
     this leaf's final output is recorded against`, while
     `stored_receipt_state: recorded` / `stored_receipt_generation: 74483c8e-10b2-5ddf-b3fc-c82802d51d67`
     / "records generation 74483c8e… as bound, and 1 later generation(s) of this leaf supersede it".
     The sentence names index 2 as what the final output *is* recorded against; the persisted record
     names index 1. (`probe_recloseout.py`.)
* **Why it matters.** This is the same defect class the master has ruled blocking twice: a sentence on a
  produced payload that a reader cannot distinguish from a recorded fact, and that the store contradicts
  in a state the operation itself produces. It is not narrow — case 1 is *every* first preview of a
  leaf with a generation, and case 2 is the ordinary re-preview after a supersession, which is exactly
  the state ICR-R21 exists to make legible ("a source move before closeout supersedes the prior
  generation without deleting its history"). A reader concluding "the final output is recorded against
  the generation shown" is told the opposite of what the store holds.
* **Suggested fix (one word, in the detail string).** State the prospective relation the payload
  actually has: "…so it is the generation this closeout would record against", or keep the present tense
  only on the apply path where a receipt exists. The same block's following clause is already
  prospective ("this closeout would deliver code …"), so the fix is consistent with its own sentence.

### F2 — **medium** — the truthfulness fix on this leaf's own output is unprotected by any case

* **file:line** `mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py:225`
  (the narrowed `bound` clause) and `.../application/review_final_output_receipt.py:240` (F1's sentence).
* **What.** On the intermediate bytes the receipt ended its sentence with "every selected input this
  generation **records** is the delivered output" in the `knowledge_match: not-comparable` state, where
  the generation's retained knowledge dataset has no delivered counterpart at all (the declared
  publication location does not exist). The frozen bytes correctly narrow it to "…this generation
  **compared** …". No delivered case pins that narrowing.
* **Evidence.** My mutation `M-F2` restored the un-narrowed single clause in the frozen copy and ran the
  delivery's own module:
  `python -m pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -n0` → **`7 passed in
  33.14s`** — while my probe on the same mutant printed the false sentence again:
  `…the declared publication location … is not-recorded, so the published dataset was not compared to the
  reviewed candidate 9c8e2bce…; every selected input this generation records is the delivered output.`
  Reverting the one branch that makes the sentence true is invisible to the delivery's suite.
* **Why it matters.** The master's standing rule is that a false user-visible sentence is blocking; the
  branch that prevents one here can be deleted with the suite still green, so the protection is
  reverted-by-accident away. (The `unusable` sibling state *is* covered — the new case at
  `test_review_final_output_receipt.py:684` — so this is specifically the `not-recorded`/no-publication
  state, which no case drives.)
* **Suggested fix.** One case: a generation whose after side is `retained`, no dataset at the declared
  location, real closeout → assert `knowledge_match == "not-comparable"`, `published_knowledge_state ==
  "not-recorded"`, the narrowed clause present **and** the string "records is the delivered output"
  absent. (Same for F1's sentence: assert the selection detail does not claim a recording that does not
  exist.)

### F3 — **low** — the receipt's reader and reclamation owner have no production caller

* **file:line** `mcp/src/agents_remember/application/review_final_output_receipt.py:429`
  (`read_final_output_receipt`) and `:521` (`discard_final_output_receipts`).
* **What.** `grep -rn "read_final_output_receipt\|discard_final_output_receipts" mcp/src` returns no
  caller outside the defining module. The durable record is written and read back once at publication
  (`read_back_evidence`), but no shipped route, tool or payload reads a phase's receipt afterwards, and
  no lifecycle operation removes it.
* **Evidence.** Frozen-tree grep (0 hits outside the module); the only consumers today are the delivery's
  tests. The report's own §7 offers `read_final_output_receipt(...)` to R25, which is consistent — this
  is a gap, not a false claim.
* **Why it matters.** ICR-R21's point is that the *recorded final comparison* identifies what closeout
  delivered; today that record is reachable only by reading `<task_root>/notes/reports/final-output-*.json`
  directly, and the named reclamation owner is never called by cleanup. Low because the record is
  durable, correctly located and complete.
* **Suggested fix.** Either wire the read into the surface that will consume it (the R25 acceptance
  reader, or the review entry's payload) or state in the module docstring that the reader is the
  acceptance/curator route rather than a shipped surface. Route the reclamation call from the existing
  terminal cleanup owner if the receipts are meant to be disposable.

### F4 — **medium** (residual; the master may read this as blocking) — `bound` is the verdict in a state no channel was compared

* **file:line** `mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py:216`
  (`statement`) and the `state` field it derives from; the delivery pins the behaviour at
  `mcp/tests/test_review_final_output_receipt.py:684`.
* **What.** With a `retained` reviewed dataset and a declared location that is `not-recorded` or
  `unusable`, the receipt carries `knowledge_match: "not-comparable"` but `receipt_state: "bound"`, and
  the `statement` publishes "every selected input this generation compared is the delivered output".
  The sentence is now true (it ranges only over compared inputs); the *verdict* is the field a consumer
  is likeliest to key on, and it reads as coverage of a delivery in which the knowledge channel was
  never compared.
* **Evidence.** Frozen probes: `receipt_state: bound`, `knowledge_match: not-comparable`,
  `published_knowledge_state: not-recorded` through the real `worktree_closeout_apply_tool` and
  `worktree_integrate_tool` (`probe_closeout.py` S2), and `… unusable …` in `probe_receipt.py` P3 /
  `probe_published.py`; delivery case 7 asserts `bound` + `unusable` explicitly.
* **Why I did not grade it blocking.** The master's rule is about sentences that are false about the
  store; this one is true, and the unmeasured channel travels beside it as its own typed field
  (`knowledge_match`, `published_knowledge_state`, `reviewed_knowledge_state`). The packet's failure
  clause forbids a success receipt for a *mismatching* publication; `not-recorded` is the absence of a
  publication rather than a mismatch, and `unusable` is reported as unreadable rather than as a match.
  **If the master reads the `bound` verdict itself as the receipt's success claim, this is blocking by
  the same rule that makes F1 blocking** — the fix is one more verdict value (e.g. `unmeasured`) so the
  top-level field cannot be read as coverage, with the validator extended to keep it consistent.
* **Suggested fix.** Add a third verdict for "a selected channel was not compared" and give it its own
  clause; then `bound` means every selected channel was measured and matched.

---

## 3. Obligations

### 3.1 Base-defect witness (own fixture, base bytes)
* Real operation at base `972b44cc` (`probe_base.py`, my fixture, base tree with only the delivery's
  tests overlaid): the leaf has a frozen generation, the closeout and the integration both succeed
  (`closeout_ok: True`, `state: closed`; `integrate_ok: True`, `state: integrated`) and **neither result
  carries any final-output record**: `closeout_has_final_output_receipt: False`, `integrate_block: None`.
  The identical probe on the candidate returns `receipt_state: bound|moved` with the identities below.
* The delivery's own module overlaid on base bytes fails at collection:
  `E ModuleNotFoundError: No module named 'agents_remember.application.review_final_output_receipt'` —
  the existence half only, so I am not counting it as the behavioural witness. The behavioural half is
  mine above; the worker's `temp/icr/base-defect-l21.txt` reports the same two halves.

### 3.2 The real production operation, and identities against the store's own truth
Drive (never a private helper, never a prebuilt payload): `freeze_review_comparison` (production freeze
entry) then the public `worktree_closeout_apply_tool`, then the public `worktree_integrate_tool`, on a
real leaf enclosure (`probe_closeout.py`). Observed on the frozen bytes:

* closeout: `ok: True`, `state: closed`; receipt `delivered_code_commit == load_contract().code_commit ==
  git rev-parse HEAD`, `delivered_memory_content_commit == contract.memory_content_commit == rev-parse
  HEAD` in the memory worktree, `delivered_code_tree_id == git rev-parse <commit>^{tree}`; the receipt
  file's own published bytes re-read `read_back: matched` and `sha256` equals `sha256sum` of the file.
* integration: `ok: True`, `state: integrated`; the integration receipt's `delivered_code_commit` equals
  the live source-branch tip the tool reported (`integrate_source_tip == the commit in the statement`).
* **Rule 4 attack — make the recorded identity disagree with what closeout committed.** Source moved
  after the freeze (S1): receipt `state: moved`, `code_match: differs-from-reviewed-input`, the
  delivered tree `9d74e72c…` printed beside the reviewed `4bca9de2…`, and the statement routes to the
  supersession remedy. Nothing relabels the old review. In the unchanged case (S2) the receipt is
  `bound` with tree `9d74e72c…` on both sides — measured, not copied: the tree is read with
  `rev-parse <commit>^{tree}`, not taken from the manifest.
* Supersession/selection: two generations → `select_review_generation` picks index 2; the first
  receipt's read-back is `recorded` with `superseded_by: [2]` and its bytes are unchanged.

### 3.3 Failure and recovery clauses
* No generation: real closeout still `closed`; block `state: "not-recorded"` naming the reason; nothing
  refused (`probe_states.py` E). The recording runs after the Git transaction and the contract write,
  and both attachments are `ok`-gated (`worktree_tools.py:448, :994-995`) — so a refusal or a partial
  transaction cannot be reported with a receipt.
* Unreadable store vs absent store vs ambiguity: a generation directory whose manifest is edited in
  place → selection `unreadable` → block `not-recorded`; no directories → `no-generation`; two bindings
  on one index → `ambiguous` (code path and state vocabulary); a leaf with no generation still closes
  (`probe_manifest.py`, `probe_states.py` D).
* Forged success: a stored receipt edited to `bound`/`matches` against its own differing identities
  reads back `unreadable` — the validator refuses it (`probe_tamper.py`).
* Reclamation: `discard_final_output_receipts` removes exactly the leaf's files, and a later read is
  `not-recorded` naming the path (`probe_states.py` F).

### 3.4 Preservation boundaries
No fallback to current HEAD or current knowledge: selection reads the generation store only; the
delivered trees come from the commits the transaction created; the published identity comes from
`declared_publication_location` + `resolve_published_intent`. No new gate on closeout (3.3). No schema
rewrite, no new authority, no second store or merge/review authority: the record is derived data in the
shipped durable reports root. Mixed generations: a superseded receipt keeps its bytes and names its
successor rather than being rewritten.

### 3.5 Truthfulness of every new sentence
Read every emitted string in the frozen bytes and drove its states (above). Clean after the fix **except
F1**: the preview selection sentence claims a recording that does not exist (case 1) or names the wrong
generation (case 2). The receipt's other sentences held up in every state I could reach: the code clause
names both trees and is right in both directions; the knowledge clause reports `not-recorded` /
`unusable` / "is the reviewed candidate dataset" / "is not the reviewed candidate dataset" only where
measured; the `not-recorded` block says it claims no binding; the read-back distinguishes
`recorded`/`not-recorded`/`unreadable` and labels supersession.

### 3.6 Seam policy
`application/knowledge_review.py` untouched (1041 L); the new responsibility has one implementation in a
purpose-named adjacent module plus a vocabulary module; `worktree_tools.py` gained an import and three
delegating calls; the payload-shaping helpers live with the responsibility they serve (they were moved
out of `worktree_tools.py` during the leaf). No duplicated logic, no second resolution path, no importer
left on a moved private name. `route_review.code_candidate_tree` is the existing prepared-candidate
owner, reused rather than re-implemented, and the preview leaves no residue in the enclosure
(`files_created_by_preview: []`).

### 3.7 Rails (frozen bytes)
| Rail | Command | Result |
| --- | --- | --- |
| pyright | `<venv>/bin/python -m pyright --project . --pythonpath <venv>` on the four changed/added files | **0 errors, 0 warnings, 0 informations** |
| ruff check | `<venv>/bin/python -m ruff check <delta .py>` | `All checks passed!` |
| ruff format | `… ruff format --check <delta .py>` | `5 files already formatted` |
| sizes | `python -m agents_remember_test_support.code_quality.file_size --report …` over `mcp/src mcp/tests mcp/test_support dashboard/src scripts .dagger` | **27 at base, 27 on the candidate** — no new offender; new files 762 / 268 / 705 L (all under the 900 soft rail). `worktree_tools.py` 1020 → 1030 (already in the soft band; the +10 lines are the import plus three delegations). |
| census + lanes | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` | **5 passed** — catalog population 16 contracts / 66 artifacts, lane rows consistent, digest re-pin matches `sha256sum mcp/tests/evidence-lifecycle.toml` |
| new `# noqa` | grep over the delta | **0** |
| budgets | `pytest mcp/tests -m "not integration" --collect-only -q -o filterwarnings=ignore -n0` → **2689/2710 collected (21 deselected)**; `-m "integration"` → **435 collected** against the declared ceilings unit 4000 / integration 1000 | satisfied, with headroom (the delivery adds one unit module, 7 cases) |

**Environment check (master rule 5).** The brief's `npx --no-install pyright …` **cannot run here**:
`npm error npx canceled due to missing packages and no YES option: ["pyright@1.1.414"]` — there is no
local or cached install. The working rail is the venv's pyright 1.1.411 (`mcp/.venv/bin/pyright`, or
`python -m pyright`), which is what I used and what the worker's report states. Chromium **is** installed
(`~/.cache/ms-playwright/chromium-1223`); L21 has no browser obligation, so that claim does not arise
here.

### 3.8 Clause by clause
| Clause | Applied to the frozen bytes |
| --- | --- |
| Required Behavior — carry the selected generation through prepared-work and closeout/integration owners, recording actual code/memory commit+tree and published knowledge identity | **Implemented and measured** on the real tools (3.2); the prepared-work projection is the preview's `final_comparison_selection` with `prepared_is_reviewed_candidate`. |
| If any selected input moves: new generation or explicit supersession, no relabelling | **Implemented** (`moved` + remedy; successor naming in `superseded_by`; old bytes untouched). |
| Failure: a failed/partial transaction retains evidence; **no success receipt for mismatching candidate, publication or final commit** | Mismatching *code* → `moved` (measured, M-G1 proves the guard). Mismatching *publication* → `moved` in the delivery's own case 3. An **uncompared/absent** publication still yields `bound` (F4) and, until the in-flight fix, a false clause (F2); the *candidate* clause that remains is F1. |
| Preservation — closeout stays the Git owner, no new mandatory gate | **Implemented** (no generation ⇒ still closed, `not-recorded`). |
| Preservation — separate invariant identity / authored meaning / mechanical detection / judgment | No new assessment or certification authority; the record carries only identities. |
| Forbidden Overreach — no silent fallback, no browser-selected dataset, no generated semantic verdict, no parallel store | **Implemented**; no fallback path exists and the store is the only source. |
| Deliverable evidence — changed production owner with exact source and candidate identities | Present (`worktree_tools.py` + two new modules; identities recorded and compared). |
| Verification evidence — exercise prepared candidate, publication, closeout/integration and input movement; compare to the reopened generation | The delivery's seven cases do this on purpose-built datasets; I did it independently on my own fixture for the no-publication, moved, superseded, tampered and integration states. |
| Unimplemented / partial | (a) no production reader for the receipt and no reclamation caller (F3); (b) the `not-recorded`/`unusable` verdict semantics (F4); (c) the truthfulness branches unprotected (F2); (d) recovery with a *frozen generation* (a crashed closeout) is not exercised — the worker discloses this in §6, and I agree it is disclosed rather than claimed. |

---

## 4. Strengths (each verified, not taken from the report)

1. **The core identity chain is genuinely measured.** Real closeout/integration on a real enclosure;
   recorded commits/trees equal `git rev-parse` of the live refs; the published identity comes from the
   read route; `read_back: matched` over the receipt's own bytes.
2. **The code guard is load-bearing.** Mutation M-G1 (delivered-vs-reviewed comparison weakened) makes
   the packet's forbidden case fire: a moved source is reported `bound`, "the reviewed candidate tree" —
   proving the delivered comparison is what keeps "an old review never covers changed output" true.
3. **The selection rule is load-bearing.** Mutation M-G4 (`refs[0]` instead of the highest index) makes
   the leaf bind a superseded generation (`selection_index: 1` while index 2 exists).
4. **The receipt is tamper-evident.** A forged `bound` receipt reads back `unreadable`; mutation M5
   (validator disabled) makes the same forged file read back `recorded … as bound`, proving the validator
   is what stops an invented success.
5. **A sealed manifest edited in place is refused**, so no receipt can name a reviewed identity the
   store did not publish (`probe_manifest.py`).
6. **Selection is honest about the store's shape**: `no-generation` ≠ `unreadable` ≠ `ambiguous`, and the
   block states each without substituting a HEAD or a directory.
7. **The preview is a genuinely useful pre-commit signal**: `prepared_is_reviewed_candidate: False` with
   "this closeout would deliver code the selected generation did not review" — before anything is
   committed.
8. **Recording is not a gate**, exactly as the packet requires.
9. **Rails green** with no new suppressions and no size regression; the new modules are well inside the
   soft rail.
10. **The worker's report is honest about its own limits** (§6: browser half deferred, recovery with a
    frozen generation not exercised, `unusable` untested at report time — the last of which they then
    closed in case 7).

---

## 5. Verdict

**Verdict: `fail`.**

The delivery is a serious, well-built implementation of ICR-R21@v1: the recorded comparison really does
carry the owner-produced code, memory and published-knowledge identities, the identities agree with the
store's reopened truth under the real closeout and integration tools, and I could not break the guards
that protect them (three mutations prove they bite). It fails on truthfulness, on the standard this
master has applied consistently: the closeout preview publishes the sentence "…so it is the one this
leaf's final output is recorded against" for the highest-index generation, and the store contradicts it
in two reproduced states — before any closeout, where the store's own reader reports
`not-recorded` for that generation, and after a real recorded closeout, where the store's receipt names
the superseded generation while the preview names its successor (F1, blocking). Alongside it: the branch
that makes the receipt's own verdict sentence true is unprotected — reverting it leaves all seven of the
delivery's cases green while the false coverage sentence returns (F2, medium) — and the `bound` verdict
still stands in a state where the published-knowledge channel was never compared (F4, medium; blocking
if the master reads the verdict itself as the success claim). F3 (no production reader or reclamation
caller) is low. I never edited production code or the memory worktree and never committed; the candidate
did not move (start == end fingerprints above).
