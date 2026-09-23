# Independent adversarial verification — ROUND 2 (fix round 1), leaf L21 / ICR-R21@v1

**Verifier.** Same independent adversarial verifier as round 1; my round-1 harnesses in
`/home/firefox/projects/ar-coordination/temp/verify-l21/` were reused, not rebuilt. Targeted re-check of
the four round-1 findings, a full regression on the frozen bytes, and the round-1 standard applied
throughout (I report a false user-visible sentence as blocking; every guard I call load-bearing was
removed in my own scratch copy and the attack re-run, mutants restored byte-exactly).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l21-ar/260921-icr-l21`,
HEAD `972b44cc…`, plus the fix-round-1 delta (now including `M application/review_comparison_reopen.py`).
Round-1 report section: `temp/icr/report-l21.md` → "Fix round 1". Round-1 verdict: `temp/icr/verify-l21.md`.

---

## Fingerprints — the candidate did not move

Fixer's combined recipe, recomputed by me (`fp2.sh`): sha256 over
`tracked-diff-sha`, `app-sha`, `models-sha`, `tests-sha`, newline-separated.

| Point | tracked `git diff` | app | models | tests | combined |
| --- | --- | --- | --- | --- | --- |
| given | `b07381a0…` | `9cb82b1d…` | `3478373f…` | `b9056268…` | `61c03986…` |
| start (mine) | `b07381a01f70eedd73129da531507c2764fdd56b37697266d7866bb8c35691ae` | `9cb82b1d47c375713ff2f27aa052df7a9a151d39eef4229375c61fa64a25a6bc` | `3478373f5173edfe95c6d1ee07f0769449e1face06c9012a795999d132cf3353` | `b905626857dada3e2a834e18b0e83e156ef374184643fe7b6d02eb57cb7f9e3d` | `61c039860a1a3b3bf8d52b483e5b1d176676666e952beebef2d2805b648379bc` |
| end (after every probe, mutation and rail) | unchanged | unchanged | unchanged | unchanged | `61c039860a1a3b3bf8d52b483e5b1d176676666e952beebef2d2805b648379bc` |

`git status --porcelain` at both ends: the four round-1 tracked modifications, `M
application/review_comparison_reopen.py`, the three new modules, and `temp/icr/*` only. I wrote only
`temp/icr/verify-l21-round2.md`; no production file, no memory worktree, no commit.

**Fixer's preservation claim verified.** `temp/icr-l21-fix/pre-fix/` holds exactly my round-1 hashes:
app `32b864567b8e3fb9…`, models `b71c27159fac4f6c…`, tests `a80e58182e8538e5…` — so the pre-fix bytes I
grade against are the ones I graded in round 1.

---

## Round-1 findings — all four closed, each re-driven by me

### F1 (was blocking) — the preview sentence is prospective, and true in both states — **closed**

Frozen `application/review_final_output_receipt.py:244`: "…so it is the generation this leaf's final
output **is to be recorded against**". My own checks (`probe_round2.py`, 45/45 OK on the frozen bytes):

| state | my check | observed |
| --- | --- | --- |
| (a) nothing recorded yet — real preview, real enclosure, frozen generation | `preview.a-prospective`, `preview.a-no-present-tense-claim` | detail contains "is to be recorded against" and not "final output is recorded against" |
| (b) successor after a real recorded closeout — gen 1 recorded (`unmeasured`), candidate moved, successor index 2 frozen, fresh real preview | `preview.b-prospective`, `preview.b-no-present-tense-claim`, `preview.b-names-successor`, `preview.b-store-names-the-predecessor` | preview names the successor (`0db1bae7…`) and the store's receipt still names the predecessor (`92b2c79d…`, `superseded_by == [2]`) |

**Mutation M-R2-F1** (sentence reverted to the round-1 wording, scratch copy): my `preview.a-prospective`
fails and prints the false sentence
("…so it is **the one** this leaf's final output **is recorded against**"), and independently the
delivery's own `test_preview_selection_sentence_never_claims_a_recording_the_store_lacks` fails
(`1 failed, 10 passed`). The guard is real and now protected.

### F4 (was medium, master-read blocking) — three verdicts, one shared rule — **closed, verified as asked**

The rule is exactly the one I asked for, at `models/knowledge/review_final_output_receipt.py:80`
(`FinalOutputVerdict = Literal["bound", "unmeasured", "moved"]`) and `:114` (`final_output_verdict`),
re-derived by the validator at `:250`, written by the application at `:416`. My own truth table:

| code_match | knowledge_match | reviewed_knowledge_state | verdict (mine) |
| --- | --- | --- | --- |
| matches | matches | retained | `bound` ✓ |
| matches | not-comparable | not-selected | `bound` ✓ (nothing selected to leave unmeasured) |
| matches | not-comparable | retained | `unmeasured` ✓ |
| differs | matches | retained | `moved` ✓ |
| matches | differs | retained | `moved` ✓ |
| differs | not-comparable | retained | `moved` ✓ (mismatch beats unmeasured) |

End-to-end on my own fixture (own seeded datasets in the enclosure's namespace):

* **bound, measured**: seeded datasets, freeze, publish the reviewed candidate dataset through the
  shipped publication owner, real closeout → `receipt_state: bound`, `knowledge_match:
  matches-reviewed-input`, `published_knowledge_digest == dataset_identity(published)` == the
  generation's after-side digest, coverage clause present, unmeasured clause absent.
* **unmeasured**: same but nothing published → `unmeasured`, `not-comparable`, `not-recorded`, the
  unmeasured clause present, `"is the delivered output"` absent; read-back `recorded`/`unmeasured`.
  (`unusable` location → same verdict, `probe_receipt.py` P3.)
* **moved**: candidate moved after the freeze → `moved` with both trees named.

**Mutations.** M-R2-F4a (rule returns `bound` where `unmeasured` is due) → my
`rule.unmeasured-selected-but-uncompared` fails, and the delivery's own cases fail
(`3 failed, 8 passed`, including its forged-verdict case). M-R2-F4b (validator's `if self.state !=
verdict` deleted) → my `forgery.refused-on-read-back` fails: a **canonical-bytes** rewrite of the stored
record claiming `bound` over its own `not-comparable` reads back `recorded … as bound`. On the frozen
bytes the same forgery reads back `unreadable` naming the verdict rule — the validator is what refuses
it, and it is load-bearing.

### F2 (was medium) — the narrowing is now the state, and a case pins it — **closed**

The coverage clause is unreachable for an uncompared selected knowledge operand by construction (the
`unmeasured` verdict), and `test_a_selected_knowledge_operand_with_nothing_published_is_never_bound`
(`tests/…:755`) is the missing case. **Mutation M-R2-F2** (the `unmeasured` clause replaced by the
coverage clause): my `unmeasured.clause` fails and prints
"…every selected input this generation records is the delivered output", and the delivery's own module
fails **two** cases (`test_review_receipt_reports_an_unreadable_published_location`,
`test_a_selected_knowledge_operand_with_nothing_published_is_never_bound`). The branch is pinned.

### F3 (was low) — the reader is wired into a production route; the reclamation debt is honest — **closed with a routed condition**

* **Wiring verified.** `application/review_comparison_reopen.py:336` populates
  `ComparisonReopen.final_output` from `read_final_output_receipts`; `review_committed_leaf.py:208` calls
  `reopen_comparison_generation` — the closed-leaf review route — and carries the reopen on
  `ClosedLeafReview.reopened`. Driving the real reopen owner on my own worlds:
  * no generation → `state='absent'`, `final_output == ()`;
  * generation, no receipts → `state='available'`, **two** entries in phase order, both `not-recorded`;
  * freeze + real closeout + real integrate → both phases `recorded`, the closeout entry's
    `delivered_code_commit` equals the contract's `code_commit`, and its `sha256` equals the file's.
  **Mutation M-R2-F3** (population dropped to `()`): my reopen checks fail (`[]`), and the delivery's
  `test_the_reopened_generation_reports_what_the_task_delivered` fails (`1 failed, 10 passed`).
* **Reclamation debt judged.** `discard_final_output_receipts` still has no shipped caller, and the
  docstring now says so plainly, names its consumers (an explicit retention/release pass and the R25
  acceptance corridor) and routes the debt L21 → R25. I checked the analogy it draws: the generation
  owner's reclamation module is genuinely in the same shape — `release_comparison_code_object` and
  `discard_comparison_snapshots` have **no** callers under `mcp/src`. Retaining the receipt during
  ordinary cleanup is the right default for a requirement whose point is that the record survives, and
  the record is bounded by construction (one file per leaf/generation/phase). **Honest and sufficient as
  a routed debt**, on one condition the orchestrator owns: R25's evidence must either call the retention
  owner or state that receipt deletion is not exercised. No new code finding.

---

## New finding this round

### F5 — **medium** — the new `final_output` field's docstring is false about the behaviour it documents

* **file:line** `mcp/src/agents_remember/application/review_comparison_reopen.py:168-175` (the
  `ComparisonReopen.final_output` field docstring), against the implementation at `:336-338` and the
  module's own inline comment at `:334`.
* **What is false, with my measurements.**
  1. "…one entry per phase **that recorded anything**, in phase order." Measured: the tuple has one entry
     per *requested* phase whenever a generation was measured — a generation with no receipts at all
     yields `['not-recorded', 'not-recorded']` (`reopen.noreceipt.two-not-recorded-entries`). Entries are
     not filtered to phases that recorded something.
  2. "An empty tuple means neither phase recorded a receipt for this generation." Measured: "neither
     phase recorded a receipt" produces two `not-recorded` entries, **not** an empty tuple; the empty
     tuple arises when the reopen did not measure a generation at all — my `reopen-nogen` world returns
     `state='absent'`, `final_output == []` (and the same holds on the absent/unreadable paths, where
     `_read_and_measure` never runs).
  The module's own inline comment states the correct rule — "A phase that recorded nothing is still an
  entry: 'not-recorded' is a fact, and omitting it would make 'nothing was recorded' and 'no phase was
  asked about' the same answer" — so the docstring contradicts the code it documents.
* **Why it matters.** This is the only specification of a brand-new public field, and it is wrong in the
  direction a consumer will branch on: a reader following it treats `()` as "no receipts recorded" when
  it actually means "no generation was measured" — and will not find the per-phase `not-recorded`
  entries it promises for exactly that case. The field was added this round to be consumed (R25 and the
  closed-leaf review route), so the contract text is load-bearing.
* **Why it is not blocking.** It is a source docstring, not a user-visible payload sentence or a
  persisted record about the store; my round-1 blocking grade was for a sentence the operation emits.
  If the master reads public-field docstrings under the same rule, this becomes blocking and the fix is
  the same one sentence.
* **Suggested fix (one sentence).** "one entry per phase in phase order whenever this generation was
  measured; the tuple is empty when the reopen measured no generation at all (absent, unavailable,
  unreadable or ambiguous), which is a fact about the reopen rather than about the receipts" — and
  likewise tighten `read_final_output_receipts`' "the phases a generation recorded" to "the phases
  asked about".

---

## Regression and rails on the frozen bytes

| Check | Command | Result |
| --- | --- | --- |
| delivery module | `pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -n0` | **11 passed** in 74.49s |
| full unit population | `pytest mcp/tests -q -p no:randomly` | **2631 passed, 62 skipped, 321 subtests passed** in 340.78s |
| my own probe suite | `probe_receipt.py`, `probe_states.py`, `probe_preview.py`, `probe_recloseout.py`, `probe_closeout.py`, `probe_published.py`, `probe_tamper.py`, `probe_manifest.py`, `probe_round2.py` | all pass; `probe_round2.py` **45/45 OK**, no failures |
| census + lanes | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` | **5 passed**; population **16 contracts / 66 artifacts**; pin `LIFECYCLE_CATALOG_SHA256 = 81a518b5…` == `sha256sum mcp/tests/evidence-lifecycle.toml` |
| pyright | `<venv>/bin/python -m pyright --project . --pythonpath <venv>` on the five changed/added python files | **0 errors, 0 warnings, 0 informations** |
| ruff | `ruff check` / `ruff format --check` on the delta | `All checks passed!` / `6 files already formatted` |
| new suppressions | grep over the delta | **0** new `# noqa` |
| ≥1200-line census | `file_size --report` over `mcp/src mcp/tests mcp/test_support dashboard/src scripts .dagger` | **27 at base, 27 now** — no new offender; sizes 804 / 320 / 871 / 730 / 1030 L (all under the 900 soft rail) |
| budgets | `-m "not integration" --collect-only` → **2693/2714** (21 deselected); `-m "integration"` → **435** | ceilings unit 4000 / integration 1000 — satisfied |

**Independent reproduction of the fixer's two strongest claims.**
* *"the fix is new behaviour, not a restatement"* — pre-fix app+models + **base** reopen + the new test
  module: **`5 failed, 6 passed`**, the five failures being exactly the new/updated behaviours (F1
  sentence, F4 unmeasured, F4 forged verdict, `unusable` verdict, F3 reopen channel) and the six round-1
  cases still passing. Reproduced exactly.
* *Bite-proof table* — each mutation run against the delivery's own module: M-F1 → 1 failed
  (`…never_claims_a_recording_the_store_lacks`); M-F2 → 2 failed; M-F4a → 3 failed (incl. the
  forged-verdict case); M-F4b → 1 failed (the forged-verdict case); M-F3 → 1 failed
  (`…reports_what_the_task_delivered`). Every row matches the report.

**Fail-closed note (strength, no legacy risk).** A stored record in the old two-verdict shape (or any
record whose verdict does not follow from its channels) is refused `unreadable` on read-back rather than
silently re-read — correct here because the format has no production history yet; nothing shipped before
this leaf.

---

## Verdict

**Verdict: `pass-with-findings`.**

All four round-1 findings are genuinely closed, and I reproduced each closure myself rather than
accepting the report: the preview sentence is prospective and true in both states I had indicted (and
reverting it fires my attack and their case); the verdict rule is exactly `bound`-only-on-a-measured-
match-of-every-selected-channel, `unmeasured` for a selected knowledge operand never compared, `moved`
for a measured difference — verified as a truth table, end to end (bound with a real published matching
dataset, unmeasured with nothing published or an unusable location, moved with a moved candidate), and
mutating either the rule or the validator is caught by my own cases and theirs, with a canonical-bytes
forgery refused on read-back; the narrowing is pinned by a case that fails when the clause is reverted
while printing the false sentence; and the receipt reader now runs on a production route
(`review_committed_leaf` → `reopen_comparison_generation` → `read_final_output_receipts`, carried as
`ComparisonReopen.final_output`), with the population dropped my check and theirs both fail. The
reclamation half is a documented routed debt whose analogy to the existing generation-reclamation owner
I verified, and it is sufficient provided R25 records it. Regression is clean: 11/11 delivery cases,
2631 passed on the full unit population, census/lanes green with the 16/66 population and the re-pinned
digest, pyright and ruff green, no new suppressions, no new size offender, budgets satisfied, and the
fingerprint did not move. The single new finding, F5, is a **medium** documentation defect on the new
public field: its docstring states the opposite of the delivered behaviour for both the entry rule and
the meaning of the empty tuple, contradicting the module's own inline comment — a one-sentence fix, and
the reason this verdict is `pass-with-findings` rather than a clean `pass`. No blocking finding remains.

---

## Round 3 confirmation (F5) — confirmed closed

**Verdict unchanged: `pass-with-findings`, with no blocking finding.** This section is the tail; the
round-2 verdict above stands as written.

**Fingerprint check.** Recomputed with my own recipe at the start and the end:
`a907bc19d680f817e944fbe3cbf7863697f52917ab0447ef09235e71b9ff6663`, from tracked
`1eb66a0b287a84229e65eef3204f303088ba5813d27d1b65498af233e30bf86f` plus components app
`9cb82b1d…`, models `3478373f…`, tests `b9056268…` — i.e. **app/models/tests are byte-identical to the
bytes I graded in round 2**, and only the tracked diff moved. Start == end.

**1. The two claims are now true, checked against the code, not the prose.**

* *"one entry per phase in phase order whenever the reopen measured a generation. A phase that recorded
  nothing is still an entry carrying `not-recorded`"* — `read_final_output_receipts`
  (`application/review_final_output_receipt.py:451`) loops `for phase in phases` over the default
  `("closeout", "integration")` and appends one `FinalOutputReceiptRead` per phase unconditionally, and
  `_read_destination` (`:498`) returns `state="not-recorded"` for an absent file. My empirical checks
  re-ran green on these bytes: `reopen.noreceipt.two-not-recorded-entries` and
  `reopen.noreceipt.phases-in-order` (generation with no receipts → `['not-recorded','not-recorded']`,
  phases in order), `reopen.two-phases` / `reopen.closeout-recorded` / `reopen.integration-recorded`
  (real closeout + real integration → both phases `recorded`). **TRUE.**
* *"The tuple is empty exactly when the reopen measured no generation at all — the `absent`, `ambiguous`
  and `manifest-unreadable` states, which ask no phase anything"* — `final_output` is passed in exactly
  one constructor: `_read_and_measure` (`review_comparison_reopen.py:315`), whose state is
  `available`/`unavailable`. The other three constructors leave the field's default `()`:
  `_ambiguous` (`:667`, `state="ambiguous"`), `_unreadable` (`:684`, `state="manifest-unreadable"`),
  `_absent` (`:707`, `state="absent"`). The one-directory-unreadable path in `_reopen_latest` does call
  `_read_and_measure`, but that function returns `_unreadable(...)` **before** the populated constructor
  (measured manifest read at `:302`-`305` vs the constructor at `:315`), so it too yields the empty tuple
  with `manifest-unreadable`. Conversely `unavailable` — correctly **not** named in the new text — comes
  from the populated constructor and always has both entries. **Empty ⟺ one of the three named states.
  TRUE**, and the exclusion of `unavailable` is right.
* Residual prose nit, **not a finding**: the clause "what normal closeout and integration *are asked
  about* for this generation" describes the phases rather than the payload (the field carries each
  phase's answer, `recorded`/`not-recorded`/`unreadable`). The next sentence states the entry semantics
  correctly, so no reader is misled about the store; leaving it as-is is fine.

**2. Nothing else moved.** `diff -u` against the copy I graded (`frozen2/`) is **one hunk**, on that
docstring paragraph only (`:166-178`); every other delta file is byte-identical
(`review_final_output_receipt.py` app and models, `worktree_tools.py`,
`test_review_final_output_receipt.py`, both lane/catalog tomls, the ownership helper); the file is still
**730 lines**; `_read_and_measure` and the inline comment at `:334` ("A phase that recorded nothing is
still an entry…") are untouched.

**3. No regression on the checks I had run.** Re-run on these bytes: my `probe_round2.py` — **45/45 OK,
exit 0**, no assertion failed (the same 45-assertion set as round 2, covering the F1 preview states,
the F4 rule + end-to-end bound/unmeasured/moved, the canonical-bytes forgery refusal, and the F3 reopen
channel); `probe_receipt.py` — `unmeasured` / `moved` / `unmeasured` as before; `probe_preview.py` and
`probe_recloseout.py` — prospective sentence in both F1 states, the store still naming the predecessor
in the successor state; `pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -n0` —
**11 passed** in 44.55s.

**F5 is closed.** One medium documentation finding, fixed exactly as a one-paragraph docstring change,
verified against the behaviour by my own reading of the code paths and re-verified by my own checks;
no residual false clause; no blocking finding anywhere in this leaf's verification.
