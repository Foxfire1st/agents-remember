# Leaf 260921-ICR-L21 — review-to-closeout identity continuity (ICR-R21@v1)

**Worker report.** Code worktree `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l21-ar/260921-icr-l21`,
branch `ar/260921-icr-l21`, base `972b44cc07b307929535fe7974d6a30d53c9c4f1` (read with `git log --oneline -1`,
never assumed). Packet: `requirements/ICR-R21-v1-review-to-closeout-identity-continuity.md`. Leaf doc:
`21_review-to-closeout-identity-continuity.md`. All work is uncommitted and left in the worktree.

## 1. What the leaf delivers, and where

The recorded final comparison now identifies the exact source and knowledge outputs normal
closeout/integration selected, and states truthfully whether the delivered output *is* the reviewed
input.

| # | File | Change |
| --- | --- | --- |
| 1 | `mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py` (**new, 268 lines**) | The typed record: `FinalOutputReceipt` (`:103`), its match vocabulary, its self-consistency validator (`:152`), and the one sentence it publishes, `statement()` (`:216`). |
| 2 | `mcp/src/agents_remember/application/review_final_output_receipt.py` (**new, 762 lines**) | The operation over the record: `select_review_generation` (`:178`, highest recorded generation index, with `no-generation` / `unreadable` / `ambiguous` as separate states), `record_final_output_receipt` (`:254`), `read_final_output_receipt` (`:429`), `discard_final_output_receipts` (`:521`, the named reclamation owner), the prepared-work projection `final_output_selection_block` (`:546`), the transaction-result entry point `final_output_result_block` (`:684`), and the three result attachments `attach_prepared_selection` (`:625`), `attach_closeout_receipt` (`:642`), `attach_integration_receipt` (`:662`). |
| 3 | `mcp/src/agents_remember/application/worktree_tools.py` (1020 → **1030 lines**) | The three production call sites: `worktree_closeout_preview` (`:992-994`), `worktree_closeout_apply` (`:995`), `worktree_integrate` (`:447-448`). +12 lines, all delegation. |
| 4 | `mcp/tests/test_review_final_output_receipt.py` (**new, 705 lines**) | Seven cases driving the real operation (section 3). |
| 5 | `mcp/tests/test-evidence-lanes.toml` (+1 line) | The new test module's lane row. |
| 6 | `mcp/tests/evidence-lifecycle.toml` (+3 lines) | The new module added to the three `consumer_scope = "exact"` consumer lists the source-derived ownership requires (`read_scope_test_support.py`, `diff_scope_test_support.py`, `fixtures/repository_profiles/node/package-lock.json`). |
| 7 | `mcp/tests/test_dependency_ownership_ast_helpers.py` (1 line changed) | `LIFECYCLE_CATALOG_SHA256` re-pinned to `81a518b567b654375bd1ea4e5af5395cefe3a3464563308c17f31278e276134c` = `sha256sum mcp/tests/evidence-lifecycle.toml`. |

`git diff --stat` (tracked files): `worktree_tools.py | 12 +-`, `evidence-lifecycle.toml | 3 +`,
`test-evidence-lanes.toml | 1 +`, `test_dependency_ownership_ast_helpers.py | 2 +-`; plus the three new
untracked files. `application/knowledge_review.py` was **not touched** (1041 lines before and after).

### The shape, in the packet's terms

* **Selection** — the leaf's highest recorded generation index, read from the generation store's own
  ordered refs (`read_generation_refs`, the same order the reopen owner publishes). No live branch tip,
  no current HEAD, no today's dataset. A tie between two bindings on one index is reported `ambiguous`
  and records nothing, rather than binding whichever directory sorts last.
* **Receipt** — one file per (leaf, generation, phase) under the shipped durable reports root
  (`<task_root>/notes/reports/final-output-<leaf>-<generation-id>-<phase>.json`), containing: the
  generation's id/index/seal/manifest digest, the reviewed baseline and candidate code trees, the
  delivered code commit and its tree, the delivered memory-content commit and its tree (or
  `memory_output_state: not-recorded`), the published knowledge identity read back through
  `declared_publication_location` + `resolve_published_intent` (the ordinary read route), and the two
  match verdicts plus the overall `bound`/`moved` verdict. Every verdict is re-derived from the
  identities the record carries and a record whose verdicts do not follow is refused at validation.
* **Read-back** — `read_final_output_receipt` returns `recorded` / `not-recorded` / `unreadable` and,
  beside the record, the later generations that supersede it; the receipt itself is never edited.

## 2. Packet clauses, and what implements each

| Packet clause | Where |
| --- | --- |
| "Carry the selected generation through the existing prepared-work … result owners" | `worktree_closeout_preview` result now carries `final_comparison_selection` (generation identity + `prepared_is_reviewed_candidate`). |
| "… and closeout/integration result owners" | `worktree_closeout_apply` result carries `final_output_receipt` (phase `closeout`); `worktree_integrate` result carries `final_output_receipt` (phase `integration`, measured after the refs moved). |
| "recording its actual code and memory commit/tree identities" | `_delivered_output` reads `rev-parse <commit>^{tree}` from the repositories that hold the commits the transaction created; the code tree is additionally the tree `accepted_code_commit` proved equals the admitted candidate. |
| "and published knowledge identity" | `_published_knowledge` reads the declared location through the read route's own owner; `published` / `not-recorded` / `unusable` are three states, with the owner's own sentence carried verbatim. |
| "If any selected input moves, publish a new generation or explicit supersession; do not relabel old review as covering changed output" | A moved input produces `state: moved` and a statement naming the exact mismatch plus the remedy (publish a successor naming this generation as its predecessor); `read_final_output_receipt` then reports the successor in `superseded_by` while the earlier receipt's bytes stay byte-identical. Nothing ever rewrites the receipt. |
| "No success receipt is emitted for mismatching candidate, publication or final commit" | `state == "bound"` requires every comparable channel to match; the validator refuses a record whose `state` does not follow from `code_match`/`knowledge_match`. A refused or blocked transaction attaches nothing (`attach_closeout_receipt`/`attach_integration_receipt` only fire on `ok` + a real commit). |
| "Closeout remains the approved Git transaction owner and adds no mandatory … gate" | The recording runs *after* the Git transaction and its contract write, inside the result builder; `final_output_result_block` never raises (it reports `not-recorded` with the concrete reason) and both attachments are no-ops on any non-`ok` result. Case 5 measures it: a leaf with no generation closes out normally and reports `not-recorded`. |
| "No silent fallback to current HEAD/current knowledge" | There is no fallback path: selection reads the generation store, the delivered identities are read from the commits the transaction *created*, and the published identity is read through the publication route. A missing generation is a reported state, never a substituted HEAD. |
| "A snapshot is retained evidence of an owner-produced input, never a new source of authored truth" | The record stores the owners' values and derives nothing; it never re-measures the comparison and never re-derives a manifest identity (the generation's `binding_digest`/`manifest_digest` are carried). |

## 3. Tests, and the exact commands with their results

New module: `mcp/tests/test_review_final_output_receipt.py` (7 cases, all driving the production path —
the real enclosure, the real `freeze_review_comparison`, the real publication owner
(`freeze_closed_snapshot` + `publish_prepared_snapshot`) at the declared location, and the real
`worktree_closeout_preview_tool` / `worktree_closeout_apply_tool` / `worktree_integrate_tool`):

1. `test_review_receipt_binds_the_delivered_pair_to_the_selected_generation` (`:438`) — the conforming
   example: the recorded comparison opens the pair the task landed.
2. `test_integration_receipt_records_the_refs_it_landed` (`:518`) — the integration result's own phase.
3. `test_review_receipt_reports_a_published_dataset_the_review_never_compared` (`:552`) — the
   non-conforming example.
4. `test_a_moved_candidate_is_recorded_as_moved_and_superseded_not_relabelled` (`:580`) — the boundary
   example.
5. `test_closeout_records_no_receipt_without_a_generation_and_still_closes` (`:625`) — no new gate.
6. `test_final_output_receipts_are_leaf_scoped_and_reclaimed` (`:648`) — boundedness + reclamation.
7. `test_review_receipt_reports_an_unreadable_published_location` (`:684`) — the `unusable` state: a
   location holding something that is not a dataset is reported as such, never as a mismatch, and the
   bytes there are left untouched.

Exact commands and observed results (all run in the leaf worktree with
`PYTHONPATH=mcp/src:mcp/test_support /home/firefox/projects/agents-remember/mcp/.venv/bin/python`):

```
python -m pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -p no:randomly -n0
  -> 6 passed in 34.83s   (before the seventh case was added)
  -> 7 passed in 51.86s   (final)

python -m pytest mcp/tests -q -p no:randomly
  -> 2626 passed, 62 skipped, 321 subtests passed in 326.68s   (whole unit population; the seventh
     case was added after that collection, so it is covered by the module run above, not by this count)

python -m pytest mcp/tests/test_transaction_only_worktree_delivery.py \
    mcp/tests/test_review_final_output_receipt.py \
    mcp/tests/test_knowledge_review_comparison_generation.py \
    mcp/tests/test_historical_committed_leaf_review.py \
    mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py \
    mcp/tests/test_file_size_detector.py mcp/tests/test_knowledge_review_surface.py \
    -q -m '' -p no:randomly -n4
  -> 74 passed in 104.87s

python -m pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''
  -> 5 passed in 54.45s      (catalog digest, population, lane rows)
# population re-measured through the owner itself:
#   contracts: 16 artifacts: 66      (unchanged; every new file is ordinary test/source, no catalog row)

# `npx --no-install pyright` cannot run in this worktree ("npx canceled due to missing packages"),
# so pyright was run from the pinned venv:
/home/firefox/projects/agents-remember/mcp/.venv/bin/pyright \
    --pythonpath /home/firefox/projects/agents-remember/mcp/.venv/bin/python \
    mcp/src/agents_remember/application/review_final_output_receipt.py \
    mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py \
    mcp/src/agents_remember/application/worktree_tools.py mcp/tests/test_review_final_output_receipt.py
  -> 0 errors, 0 warnings, 0 informations   (the first run reported two real test-module errors --
     a `Path | None` memory repository and an optional `identity` dereference -- both now fixed)

python -m ruff check <the four changed/added Python files>   -> All checks passed
python -m ruff format --check <the same files>               -> 5 files already formatted
# new `# noqa` added: 0 (grep -c noqa on all three new files = 0)

python -m agents_remember_test_support.code_quality.file_size --report --project-root . $(git ls-files 'mcp/**/*.py' 'dashboard/src/**/*.ts' 'dashboard/src/**/*.tsx')
  -> candidate: 26 file(s) at or above the 1200-line hard limit
  -> base 972b44cc (git archive scratch, same command): 26 file(s)
     no NEW hard-limit offender; the three new files measure 268 / 762 / 705 lines.
```

## 4. The base-defect reproduction

Scratch checkout of the base bytes with only the new tests overlaid
(`git archive 972b44cc | tar -x -C …/temp/icr-l21-base`, then the new test module and its lane row
copied in; the scratch was `git init`ed because the lane gate reads `git ls-files`). Full output saved at
`temp/icr/base-defect-l21.txt`.

```
$ cd /home/firefox/projects/ar-coordination/temp/icr-l21-base
$ PYTHONPATH=mcp/src:mcp/test_support …/python -m pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -p no:randomly -n0
E   ModuleNotFoundError: No module named 'agents_remember.application.review_final_output_receipt'
ERROR mcp/tests/test_review_final_output_receipt.py
1 error in 19.47s
```

That is the existence half. The behavioural half is the same operation driven at base with **no** new
module in the import path (`test_icr_l21_base_probe.py`, written into the same scratch and registered in
its lane row; the probe file is not part of the delivery):

```
$ … -m pytest mcp/tests/test_icr_l21_base_probe.py -q -m '' -p no:randomly -n0
>       assert "final_comparison_selection" in preview, sorted(preview)
E       AssertionError: ['approval_question', 'approved_for_commit', 'changed_code_paths',
E       'changed_code_paths_committed', 'cleanup', 'closeout_gate', ...]
E       assert 'final_comparison_selection' in {'state': 'would-closeout', 'task_id': 'MASTER',
E       'task_name': 'master', 'code_repository_name': 'repo', ...}
FAILED mcp/tests/test_icr_l21_base_probe.py::test_closeout_result_carries_the_final_output_receipt
1 failed in 17.82s
```

So at base a real closeout preview and a real closeout apply return a result that says **nothing** about
which comparison generation the transaction binds or what it delivered: a reader of the recorded review
cannot tell whether it covers the landed pair. The identical probe passes on the candidate
(`1 passed in 17.60s`, run with the probe temporarily copied into the worktree and removed again — the
candidate tree carries no probe file), and the delivered module asserts strictly more than the probe
(the seven cases above).

## 5. Identity and boundary assertions actually measured

From case 1 (conforming), all against the store rather than against the receipt's own words:

* `final_comparison_selection.generation_id/binding_digest/reviewed_candidate_code_tree_id` equal the
  frozen manifest's; `prepared_is_reviewed_candidate is True`.
* `final_output_receipt.delivered_code_commit == load_contract(...).code_commit` and
  `delivered_code_tree_id == git rev-parse <code_commit>^{tree}` **and** equals the reviewed candidate
  tree the manifest bound.
* `delivered_memory_content_commit == contract.memory_content_commit`, and
  `delivered_memory_tree_id == git rev-parse <memory_commit>^{tree}` in the memory repository.
* `published_knowledge_digest == dataset_identity(published copy).logical_digest == the generation's
  after-side logical digest`.
* `manifest_digest == read_generation_refs(...)[-1].manifest_digest`; the receipt file's own
  `sha256(bytes)` equals the reported `sha256` and the `read_back` is `matched`.
* `reopen_comparison_generation(...)` reports `available` for the same generation with the same
  candidate tree; `read_final_output_receipt(...)` returns `recorded`, the identical sha256, and
  `superseded_by == ()`.
* Integration: `final_output_receipt.delivered_code_commit == integrated_code_commit ==
  git rev-parse <source branch>`, phase `integration`, and its read-back is `recorded`.

Case 3 (non-conforming): the published dataset is the *baseline* half; `code_match` stays
`matches-reviewed-input` while `knowledge_match == differs-from-reviewed-input`,
`receipt_state == moved`, and the statement names both digests and says the generation "does not cover
the delivered output" — the assertion also checks that "is the reviewed candidate dataset" is absent.

Case 4 (boundary): after moving the candidate, `prepared_is_reviewed_candidate is False`,
`receipt_state == moved`, `code_match == differs-from-reviewed-input`, the delivered tree differs from
the reviewed tree, and `generation_id` is unchanged. Freezing a successor with
`ComparisonFreezeOptions(parent=…)` gives `generation_index + 1` and
`lineage.parent_generation_id == the first generation`; the earlier receipt's sha256 is **identical**
before and after and its read-back now names the successor in `superseded_by`.

Case 5 (no gate): no generation ⇒ `final_comparison_selection.state == "no-generation"`,
`prepared_is_reviewed_candidate is None`, `final_output_receipt.state == "not-recorded"` naming the
reason, and the closeout still reaches `state: closed` with a real `code_commit`.

Case 6 (bounded + reclaimed): closeout + integration produce exactly two files for the generation, a
file named for another leaf survives `discard_final_output_receipts`, and the read-back after removal is
`not-recorded` naming the reports root.

Case 7 (`unusable` location): a file that is not a dataset at the declared location yields
`published_knowledge_state == "unusable"`, no invented identity, `knowledge_match == "not-comparable"`,
a `bound` verdict on the code channel alone, and a statement that says the published dataset "was not
compared" — it never claims a mismatch it did not measure — while the file's bytes are left untouched.

## 6. Seam policy, rails, and honest limits

* **Seam policy.** The packet's Scope sentence applies to a *touched* responsibility; this leaf adds no
  behaviour to `application/knowledge_review.py`, so nothing was moved out of it — it is byte-identical
  (1041 lines) and remains a thin delegator. The new responsibility lives in its own purpose-named
  modules: `models/knowledge/review_final_output_receipt.py` for the record and
  `application/review_final_output_receipt.py` for the operation, with the record re-exported so there is
  one type and one import site. No second implementation of anything exists.
* **Rails.** New files: 268 / 762 / 705 lines (all below the 900 soft limit). `worktree_tools.py`
  1020 → 1030: it was already a soft-band (900-1200) file, and the +12 lines are an import plus three
  delegating call sites — I deliberately moved the payload-shaping helpers out of it into the receipt
  module rather than growing it (the first draft added 57 lines and was reduced). `C901`/`PLR0911`/
  `PLR0912`/`PLR0915` are green with **no** new `# noqa`; `ruff check` and `ruff format --check` are
  green on every file I touched; pyright is 0 errors. The ≥1200-line census is 26 at base and 26 on the
  candidate (no new offender).
* **Result-owner decision (for the verifier).** The receipt is attached at the MCP tool result
  (`application/worktree_tools.py`), which is the production result surface: `layers.toml` ranks
  `application` above `worktrees`, so putting the block into `worktrees/modules/closeout.py` /
  `integrate.py` would add a new layering violation, and the only other production caller of the domain
  functions (`worktrees/modules/cli.py`) is unreachable from any shipped console script — the shipped
  entry points are `agents_remember.cli.__main__:main` and the MCP server, and neither routes closeout
  or integration through that parser: `grep -rn "closeout\|integrate" mcp/src/agents_remember/cli/__main__.py`
  returns nothing, and the only importers of `worktrees.modules.cli` are
  `worktrees/git_worktree_manager.py` (a re-export) and one test. Direct
  in-process callers of `git_worktree_manager.closeout_result` (e.g. the recovery test) therefore see
  no receipt; no production route is in that set.
* **The receipt is a measurement, not history.** A phase measured twice replaces its own file (the
  result reports `replaced_existing_receipt`), because the record states what that phase delivered at
  the moment it was read; the *manifest* and the generation's retained bytes are never rewritten. The
  closeout and integration phases are separate files so neither has to be edited to describe the other
  moment. `discard_final_output_receipts` is the named, leaf-scoped reclamation owner.
* **Fixture provisioning that is not a product operation.** The new test module creates its own two
  review datasets through the store's own API (`open_knowledge_store` + `create_repository` +
  `create_invariant` + `create_revision`) instead of the shared read-scope fixture's pair, and it does
  so for a measured reason: that fixture invents a random namespace and records the module's fixed
  authority home ("agents-remember") beside it. The review route reads under the namespace and never
  consults the authority home, but the publication read-back does — a dataset bound to another
  repository's authority home is another repository's publication — and the store refuses to rebind a
  namespace (measured: `UPDATE repository …` → `apsw.ConstraintError: immutable_revision: a repository
  namespace cannot be rebound`). The datasets are therefore created bound to the repository the
  contract names. Nothing about the operation under test is replaced: the resolution, comparison,
  freeze, publication, closeout and integration are the shipped owners.
* **Not measured / not claimed.** (a) The browser half of the acceptance rows that touch this behaviour
  (A17/A20/A22 have Class-3 halves) is out of scope here and remains R25's, per
  `notes/05-acceptance-plan.md`. (b) No *generation* is present in the fast R21 fixture path for the
  recovery/partial-transaction closeout route (`test_closeout_recovery_…` drives the domain function
  directly, which carries no receipt by design): the "failed/partial transaction" clause is evidenced by
  the no-generation case, by the `ok`-gated attachments, and by the fact that recording happens only
  after `write_contract` — not by a crashed-closeout fixture with a frozen generation. (c) The receipt's
  published-knowledge channel has been measured in the `published` (matching and differing) and
  `not-recorded` states and, after case 7 was added, the `unusable` state (a location holding
  something that is not a dataset). (d) A `moved` receipt does **not**
  block the transaction — closing that gap is explicitly the packet's "new generation or explicit
  supersession" remedy, recorded in the statement, and adding a gate was forbidden.
* **Nothing needs a scope ruling.** No contract outside the packet changed: no schema, no MCP tool
  signature, no new authority, no new mandatory gate, no browser-selected dataset. The one catalog
  change (three consumer rows + the digest re-pin) is the documented procedure for adding a test module.

## 7. For the curator and for R25

Affected memory surface (for the curator, whose worktree is not mine): two new production modules and
one new test module. R25 can use this leaf's evidence directly: the receipt destination
(`<task_root>/notes/reports/final-output-…json`) plus `read_final_output_receipt(...)` and
`reopen_comparison_generation(...)` are exactly the "recorded identities vs the reopened final
generation" comparison A17/A20/A22 need, and `temp/icr/base-defect-l21.txt` holds the base witness.

---

# Fix round 1 (verifier `fail` on the frozen bytes `641e3d07…` / `85bc992d…`)

Read `temp/icr/verify-l21.md` in full first. The candidate was frozen at the verifier's fingerprints and my
pre-fix modules were preserved byte-exactly before any edit
(`/home/firefox/projects/ar-coordination/temp/icr-l21-fix/pre-fix/`):

| pre-fix module | sha256 (matches the verifier's table) |
| --- | --- |
| `application/review_final_output_receipt.py` | `32b864567b8e3fb9eedc883821489a89ed74514183f31d2cbbfec05c613582c7` |
| `models/knowledge/review_final_output_receipt.py` | `b71c27159fac4f6cd68daafebebe93d7e7e4ffe823bf5ea2e3fff8cb809f7153` |
| `tests/test_review_final_output_receipt.py` | `a80e58182e8538e5076df2e5d033ea223f8fff7ab5d6b3352f1c38ea05bc486e` |

## Findings, changes, and bite-proofs

### F1 (blocking) — the prepared-work sentence claimed a recording that does not exist

**Change.** `application/review_final_output_receipt.py:244` now states the relation the payload actually
has: "…so it is the generation this leaf's final output **is to be recorded against**". The whole sentence
is prospective, which is the only thing true in both reproduced states: before any closeout (no receipt
exists) and after a closeout recorded a generation the review has since superseded (the store's receipt
names the predecessor while the preview names the successor).

**New case.** `tests/test_review_final_output_receipt.py:701`
(`test_preview_selection_sentence_never_claims_a_recording_the_store_lacks`) drives the real
`worktree_closeout_preview_tool` twice on one enclosure and puts the store's own reader beside each state:
state (a) nothing recorded → `read_final_output_receipt` returns `not-recorded`; then a real publication,
a real closeout recording generation 1, a candidate move and a real successor generation; state (b) the
preview names index 2 while the stored receipt still names generation 1 with `superseded_by == [2]` and
generation 2 itself reads back `not-recorded`. Both states assert `"is to be recorded against" in detail`
and `"is recorded against" not in detail`.

**Bite-proof (mutation M-F1, one-line reversion of the sentence in a scratch copy;
`temp/icr-l21-fix/bite-M-F1.txt`).** With the pre-fix wording the new case fails, and the failure output
is the false sentence itself:

```
>       assert "is to be recorded against" in before["detail"], before["detail"]
E       AssertionError: generation 65d2e94e-… carries the highest recorded index (1) of the 1 readable
E       generation(s) this leaf published, so it is the one this leaf's final output is recorded against; …
FAILED …::test_preview_selection_sentence_never_claims_a_recording_the_store_lacks
1 failed, 10 deselected in 15.48s
```

### F4 (graded blocking by the master's reading of the success claim) — `bound` stood where no channel was compared

**Change.** A third verdict, `unmeasured`, and one rule for it, in the model:
`models/knowledge/review_final_output_receipt.py:80` (`FinalOutputVerdict =
Literal["bound", "unmeasured", "moved"]`) and `:114` (`final_output_verdict`). A compared channel that
differs is `moved`; otherwise a generation that **selected** a knowledge operand (`reviewed_knowledge_state
== "retained"`) with `knowledge_match == "not-comparable"` is `unmeasured`; `bound` requires a measured
match on every channel the generation actually selected (a generation that selected no knowledge operand
can still be `bound` on the code channel alone, which is all it selected). The application module now
writes that verdict (`application/review_final_output_receipt.py:416`) instead of its own two-way
expression, and the **model's validator** (`:247`) re-derives it and refuses any record whose verdict does
not follow — so a forged `bound` cannot survive a read-back. The carried fields
(`knowledge_match`, `published_knowledge_state`, `reviewed_knowledge_state`, digests) are unchanged.

**Sentence.** The `unmeasured` clause (`:273`) is a distinct non-coverage sentence: "the knowledge operand
this generation selected was not compared against any delivered dataset, so this generation covers the
delivered code and leaves the delivered knowledge unmeasured; publish the reviewed dataset at the declared
location, or publish a successor generation that compares what was delivered".

**Cases.**
* F4(a) `tests/…:755` — a selected knowledge operand with nothing published → `receipt_state ==
  "unmeasured"` (never `bound`), code matched, `knowledge_match == "not-comparable"`,
  `published_knowledge_state == "not-recorded"`, the clause present and the coverage string
  `"is the delivered output"` absent; the stored record reads back `unmeasured`, and the *integration*
  result of the same delivery is also `unmeasured`.
* F4(b) full match → `bound`: case 1 (`:451`) — code matched **and** `knowledge_match ==
  "matches-reviewed-input"` with the published digest equal to the reviewed one.
* F4(c) moved input → `moved`: case 4 (`:572`).
* F4 validator: `tests/…:790` rewrites the recorded file with **canonical** bytes and `state: "bound"`
  over its own `knowledge_match: "not-comparable"` → the read-back is `unreadable` with the verdict rule
  named in the detail, and the genuine bytes restore to `recorded`/`unmeasured`.

**Bite-proofs.** `temp/icr-l21-fix/bite-M-F4a.txt` (verdict rule reverted to two values):
`assert 'unmeasured' == 'bound'`-style failure (`- unmeasured / + bound`).
`temp/icr-l21-fix/bite-M-F4b.txt` (validator's verdict check deleted): the forged record reads back
`recorded … as bound`, `assert 'recorded' == 'unreadable'`.

### F2 (medium) — the truthfulness narrowing was unprotected

**Change.** The narrowing is no longer a branch that can be reverted into a false claim: the state itself
distinguishes the two facts, so the coverage clause is unreachable whenever a selected knowledge channel
was not compared (F4's rule), and the `unmeasured` state carries its own explicit clause.

**Case.** The same F4(a) case (`:755`) is the missing case F2 asked for: "generation present, nothing
published", asserting the unmeasured clause is present, the store's `not-recorded` state is carried, and
the coverage string is absent.

**Bite-proof (mutation M-F2, `temp/icr-l21-fix/bite-M-F2.txt`).** With the `unmeasured` clause replaced by
the coverage clause, the case fails and the assertion prints the false sentence:

```
>       assert "not compared against any delivered dataset" in statement
E       AssertionError: … the declared publication location … is not-recorded, so the published dataset was
E       not compared to the reviewed candidate …; every selected input this generation records is the
E       delivered output.
```

### F3 (low) — the reader and the reclamation owner had no production caller

**Change (reader: wired).** `application/review_comparison_reopen.py` now consumes the reader: the
`ComparisonReopen` record carries a fourth channel, `final_output` (`:187`, one entry per phase in phase
order, populated at `:336` from the new `read_final_output_receipts`, `:451` in the receipt module). The
reopen owner is reached in production through the closed-leaf review route
(`application/review_committed_leaf.py:208`), so the recorded comparison a reader opens now reports the
code, memory and published-knowledge outputs the task delivered as well as the inputs it bound — which is
ICR-R21's own sentence. `read_final_output_receipt`'s docstring (`:429`) names that consumer.

**Case + bite-proof.** `tests/…:825`
(`test_the_reopened_generation_reports_what_the_task_delivered`) asserts the phase list, the recorded
closeout entry's sha256/commit/verdict, and that the unmeasured integration phase is an entry too.
Mutation M-F3 (`temp/icr-l21-fix/bite-M-F3.txt`, the population dropped to `()`) fails the case:
`assert [] == ['closeout', 'integration']`.

**Change (reclamation owner: routed debt, documented).** No shipped route deletes a receipt, and that is
deliberate — the record is the retained evidence ICR-R21 exists to keep, and deleting it during ordinary
cleanup would destroy the artifact the requirement asks to survive; deleting it is also the *same* shape
the generation owner landed (`application/review_comparison_reclamation.py` is likewise a named owner no
automatic caller invokes). `discard_final_output_receipts` (`:553`) now states that plainly in its
docstring and names the consumers: an explicit retention/release pass and the acceptance corridor that
measures reclamation (**ICR-R25@v1**, owner: the R25 acceptance leaf; secondary: the R11
retention/release route). **Routed debt: L21 → R25 — call the retention owner when the acceptance
corridor measures reclamation, or state in R25's evidence that receipt deletion is not exercised.**

## Verification discipline (this round)

Combined fingerprint = tracked `git diff | sha256sum` plus each untracked code file's own sha256, with
`git status --porcelain` sampled before and after. The round-1 claim that a `git diff`-only fingerprint is
blind to the three new files is correct and is why the combined value is reported here.

**Combined fingerprint recipe (reproducible).** `sha256( tracked_diff_sha256 "\n"
application_sha256 "\n" models_sha256 "\n" tests_sha256 "\n" )`, i.e. the four digests below,
newline-separated, in that order:

```
tracked  git diff | sha256sum      b07381a01f70eedd73129da531507c2764fdd56b37697266d7866bb8c35691ae
app      application/review_final_output_receipt.py
                                   9cb82b1d47c375713ff2f27aa052df7a9a151d39eef4229375c61fa64a25a6bc
models   models/knowledge/review_final_output_receipt.py
                                   3478373f5173edfe95c6d1ee07f0769449e1face06c9012a795999d132cf3353
tests    tests/test_review_final_output_receipt.py
                                   b905626857dada3e2a834e18b0e83e156ef374184643fe7b6d02eb57cb7f9e3d
COMBINED                           61c039860a1a3b3bf8d52b483e5b1d176676666e952beebef2d2805b648379bc
```

| Point | tracked `git diff \| sha256sum` | untracked code shas | combined |
| --- | --- | --- | --- |
| round-1 candidate (the verifier's) | `641e3d0746a96d6f0613077cecf170bd8af9325a212302696a9a2bdebc209160` | `32b86456…` / `b71c2715…` / `a80e5818…` | `85bc992d…` |
| after this round | `b07381a0…` | `9cb82b1d…` / `3478373f…` / `b9056268…` | `61c03986…` |

`git status --porcelain` **before** this round: `M worktree_tools.py`, `M evidence-lifecycle.toml`, `M
test-evidence-lanes.toml`, `M test_dependency_ownership_ast_helpers.py`, `?? application/review_final_output_receipt.py`,
`?? models/knowledge/review_final_output_receipt.py`, `?? tests/test_review_final_output_receipt.py`,
`?? temp/icr/{base-defect-l21.txt,report-l21.md,verify-l21.md}`. **After**: the same list plus `M
review_comparison_reopen.py` (the F3 wiring) and the updated `?? temp/icr/report-l21.md` — no other path
appeared or disappeared, and no file was committed.

### Re-run results on the post-fix bytes

| Check | Command | Result |
| --- | --- | --- |
| module | `pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -p no:randomly -n0` | **11 passed** |
| full unit population | `pytest mcp/tests -q -p no:randomly` | **2631 passed, 62 skipped, 321 subtests passed** in 310.40s |
| catalog + lanes | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m '' -p no:randomly -n4` | **5 passed**; population **16 contracts / 66 artifacts** (unchanged — no new module, no catalog change beyond round 1; the pin `81a518b5…` still equals `sha256sum mcp/tests/evidence-lifecycle.toml`) |
| pyright | `<venv>/bin/pyright --pythonpath <venv>/bin/python` on the five changed/added files | **0 errors, 0 warnings, 0 informations** |
| ruff | `ruff check` / `ruff format --check` on the same five | `All checks passed!` / `5 files already formatted` |
| new `# noqa` | grep over the three new files | **0** |
| ≥1200-line census | `file_size --report` over the tracked measured tree | **26 at base, 26 now** — no new offender; sizes now 804 / 320 / 871 / 730 / 1030 (all under the 900 soft limit) |
| budgets | `-m "not integration" --collect-only` → **2693/2714** (21 deselected); `-m "integration"` → **435** | unit ceiling 4000 / integration 1000 — satisfied |

### Bite-proofs on the *frozen* bytes in one run

`temp/icr-l21-fix/bite-frozen-bytes-vs-new-cases.txt`: the scratch tree carrying the pre-fix
modules (`32b86456…`, `b71c2715…`, and the base reopen module) with the **new** test module runs
**`5 failed, 6 passed`** — the five failures are exactly the new/updated behaviour
(`unreadable`-location verdict, F1 sentence, F4(a) unmeasured, F4(b) forged verdict, F3 reopen channel),
and the six round-1 cases still pass. The fix is therefore new behaviour, not a restatement of the old
cases.

### Mutations performed this round (all in scratch copies, each restored byte-exactly)

| id | mutation | file | observed |
| --- | --- | --- | --- |
| M-F1 | the sentence reverted to "…it is the one this leaf's final output is recorded against" | `application/review_final_output_receipt.py` | `1 failed, 10 deselected` — the false sentence printed |
| M-F2 | the `unmeasured` clause replaced by the coverage clause | `models/…/review_final_output_receipt.py` | `1 failed, 10 deselected` — the coverage sentence printed |
| M-F4a | `final_output_verdict` reverted to two values | `models/…/review_final_output_receipt.py` | `1 failed, 10 deselected` — `- unmeasured / + bound` |
| M-F4b | the validator's verdict check deleted | `models/…/review_final_output_receipt.py` | `1 failed, 10 deselected` — the forged record reads back `recorded … as bound` |
| M-F3 | the reopen's `final_output` population dropped | `application/review_comparison_reopen.py` | `1 failed, 10 deselected` — `assert [] == ['closeout', 'integration']` |

Restoration was verified by sha256 after every mutation (`9cb82b1d…` / `3478373f…` / `1d2a56f7…` in the
scratch, identical to the leaf's files). No mutation touched the leaf worktree.

---

# Fix round 2 (verifier round-2 `pass-with-findings`, finding F5)

Read `temp/icr/verify-l21-round2.md` first; F1–F4 are closed and re-driven by the verifier, and this
round changes **one docstring paragraph in one file** — no other file, no test change, no formatting
sweep, no commit.

## F5 — the `ComparisonReopen.final_output` docstring was false about the delivered behaviour

**Both claims were wrong**, exactly as measured: the field carries one entry per *requested* phase
whenever a generation was measured (a generation with no receipts yields `['not-recorded',
'not-recorded']`), and the empty tuple means the reopen measured **no generation at all** — the `absent`,
`ambiguous` and `manifest-unreadable` states — not "neither phase recorded a receipt". The module's own
inline comment at `:331-335` already stated the correct entry rule, so the docstring contradicted the code
it documents. The inline comment was re-read and is **correct**; it is untouched.

**New text (`mcp/src/agents_remember/application/review_comparison_reopen.py:168-175`, verbatim):**

```
    ``final_output`` is the fourth kind of channel and the newest: what normal closeout and integration
    are asked about for this generation (ICR-R21@v1), one entry per phase in phase order whenever the
    reopen measured a generation. A phase that recorded nothing is still an entry carrying
    ``not-recorded``, because omitting it would make "nothing was recorded" and "no phase was asked
    about" the same answer. It is a tuple rather than an optional single value because closeout and
    integration are separate measurements taken at separate moments, and a reader that got only one of
    them would have to guess which. The tuple is empty exactly when the reopen measured no generation at
    all -- the ``absent``, ``ambiguous`` and ``manifest-unreadable`` states, which ask no phase anything.
```

Every clause is checkable in the code it documents: `_read_and_measure` (`:331-338`) always populates the
channel through `read_final_output_receipts`, whose requested phases default to `("closeout",
"integration")` and are never filtered; the three states that never reach `_read_and_measure` are
`ambiguous` (`:668`), `manifest-unreadable` (`:685`) and `absent` (`:708`), and they are exactly the ones
that leave the field at its `()` default.

**Line delta (for the curator's cited ranges).** The paragraph occupied **lines 168–175 before and after**
— eight lines replaced by eight lines — so **no other line in `review_comparison_reopen.py` shifted**:
730 lines before, 730 after, and the diff against the round-1 graded copy (`1d2a56f7…`) is exactly one
`@@ -166,13 +166,13 @@` hunk containing only this paragraph. Everything else in the file (the round-1 wiring
at `:64-67`, `:187`, `:331-338`) is byte-identical to the copy the verifier graded.

**Not touched (reported, not fixed, per this round's constraint).** The verifier's optional sibling
tightening in `application/review_final_output_receipt.py` ("the phases a generation recorded" → "the
phases asked about") is in another file, which this round's instruction forbids; it is a one-word class of
change available on request, and it is not false in the same way — it describes the small fixed phase list
the reader asks about rather than a filter on what was recorded.

## Re-runs (a docstring change can affect none of the rails; all four re-run anyway)

| Check | Command | Result |
| --- | --- | --- |
| delivery module | `pytest mcp/tests/test_review_final_output_receipt.py -q -m '' -p no:randomly -n0` | **11 passed** in 49.18s |
| ruff check | `ruff check mcp/src/agents_remember/application/review_comparison_reopen.py` | `All checks passed!` |
| ruff format | `ruff format --check` on the same file | `1 file already formatted` |
| pyright | `<venv>/bin/pyright --pythonpath <venv>/bin/python` on the same file | **0 errors, 0 warnings, 0 informations** |
| ≥1200-line size | `file_size --report mcp/src/agents_remember/application/review_comparison_reopen.py` | **0 files at or above the 1200-line hard limit**; the file is **730 lines** (unchanged), inside the 900 soft rail |

## Fingerprint after round 2

Same recipe as round 1: `sha256( tracked_diff_sha256 "\n" application_sha256 "\n" models_sha256 "\n"
tests_sha256 "\n" )`.

```
tracked  git diff | sha256sum      1eb66a0b287a84229e65eef3204f303088ba5813d27d1b65498af233e30bf86f
app      application/review_final_output_receipt.py
                                   9cb82b1d47c375713ff2f27aa052df7a9a151d39eef4229375c61fa64a25a6bc
models   models/knowledge/review_final_output_receipt.py
                                   3478373f5173edfe95c6d1ee07f0769449e1face06c9012a795999d132cf3353
tests    tests/test_review_final_output_receipt.py
                                   b905626857dada3e2a834e18b0e83e156ef374184643fe7b6d02eb57cb7f9e3d
COMBINED                           a907bc19d680f817e944fbe3cbf7863697f52917ab0447ef09235e71b9ff6663
```

Round-1 combined was `61c03986…`; the only input that moved is the tracked diff, because
`application/review_comparison_reopen.py` is a tracked file (its own sha256 against the round-1 graded
copy: `1d2a56f7…` → `7656b902f817c9f43d8910bca7598fcfd6fe44dbef9f989f9d2acabdb7bbde7c`).

`git status --porcelain` after round 2 (unchanged in path set from round 1, plus the report update):

```
 M mcp/src/agents_remember/application/review_comparison_reopen.py
 M mcp/src/agents_remember/application/worktree_tools.py
 M mcp/tests/evidence-lifecycle.toml
 M mcp/tests/test-evidence-lanes.toml
 M mcp/tests/test_dependency_ownership_ast_helpers.py
?? mcp/src/agents_remember/application/review_final_output_receipt.py
?? mcp/src/agents_remember/models/knowledge/review_final_output_receipt.py
?? mcp/tests/test_review_final_output_receipt.py
?? temp/icr/base-defect-l21.txt
?? temp/icr/report-l21.md
?? temp/icr/verify-l21.md
?? temp/icr/verify-l21-round2.md
```
