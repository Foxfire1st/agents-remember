# Independent adversarial verification — leaf L22 / ICR-R22@v1 (managed Git recovery rebinding)

**Verifier.** Independent adversarial verifier for `260921-ICR-L22`; no planning transcript, no part in
the implementation. Every claim below was reproduced by me on the frozen candidate; the one guard I call
load-bearing was removed in my own scratch copy and the attack re-run on the mutant (restored afterwards,
byte-identical to the frozen copy).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22`,
branch `ar/260921-icr-l22`, `HEAD = e605822eb3bf83bf63a45963c5f51d5fc28859ee` (`ICR-R15…`, the master
tip), plus the worker's uncommitted delta. Packet
`requirements/ICR-R22-v1-managed-git-recovery-rebinding.md`; leaf doc
`22_managed-git-recovery-rebinding.md`; worker report `temp/icr/report-l22.md` (claims checked, never
treated as evidence). Scratch space: `/home/firefox/projects/ar-coordination/temp/verify-l22/`
(`base/`, `frozen/`, probe files, logs).

---

## Fingerprints

Combined recipe (the L21 correction): tracked `git diff | sha256sum`, **plus** each untracked code file's
own sha256 over a sorted list, **plus** `git status --porcelain` (entry count and paths), sampled before
and after.

| Point | tracked `git diff` sha256 | untracked code files | status entries |
| --- | --- | --- | --- |
| gate reading A (12:42:19) | `5113aa73916cdd0c06e5bc1d53317f548daebde54d69816f55d8b321b3fdd4ac` | `23cf2c75…` (app `d2ec740c`, model `d7505636`, 3 `temp/icr` files) | 11 |
| gate reading B (12:43:54) | `5113aa73…` (unchanged) | `81c2e280…` — **delta is `temp/icr/report-l22.md` only** (`a7b64da8` → `8f302d18`) | 11 |
| end (12:53:43, after all probes, rails and mutation) | `5113aa73916cdd0c06e5bc1d53317f548daebde54d69816f55d8b321b3fdd4ac` | app `d2ec740c9f315b38f9a07065e113e4f540363d060648c59a6323b3d93bc6fbb7` · model `d7505636c44057bcdc65f9f7a5914bc8cc648659431179b1dfe86ab488a07f94` · tests `1f36583fa97c8aa5aa7d04385fabcfb166a953647bd93a3fcb0a768f6d349026` | 11 |

**The graded code did not move.** The tracked diff was stable for the whole grading window (11+ minutes)
and both new modules are byte-identical at gate and end; the only untracked change between readings A and
B is the worker's own report document, which is not graded code.

**The candidate did move earlier, and the first report described bytes that are not the delivered ones.**
`report-l22.md` first appeared at 12:14:23 declaring fingerprint `tracked d99f2709…`, app `d4404b38…`,
model `b93fd9b2…`. None of those three is the delivered candidate: the model module was then sitting in a
**mutation-applied** state (`b93fd9b2`; the worker's own pristine copy is `d7505636`), the app module was
the earlier `d4404b38` (now `d2ec740c`, 554 → 599 lines), and the tracked diff changed at ~12:14:35. The
tree kept moving until ~12:40 (rails, mutation matrix, post-report edits). I sampled the combined
fingerprint every 20 s throughout and did not grade anything before the code bytes held still; the
worker's **revised** report (`8f302d18`, 12:43) does match the delivered bytes exactly. Recorded because
the master may compare report fingerprints against the branch — the first one would not have matched.

---

## 1. What is delivered

| # | File | Change |
| --- | --- | --- |
| 1 | `mcp/src/agents_remember/application/review_sync_rebinding.py` (new, 599 L) | `resolved_pair_completed` (:163), `record_review_sync_rebinding` (:233), `rebinding_result_block` (:278), `read_review_sync_rebinding` (:338), `rebinding_names_the_generation` (:402), `read_review_sync_rebindings` (:440), `discard_review_sync_rebindings` (:459), `_nothing_to_bind_block` (:482), `_assemble` (:507), `_resolved_capture` (:547), `_resolved_knowledge` (:569) |
| 2 | `mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py` (new, 381 L) | the sealed record, the two channel-match vocabularies, `review_sync_verdict` (:142), the self-consistency validator (:243), `covers_resolved_pair` (:302), `statement` (:312) with `_code_clause` (:342) / `_knowledge_clause` (:356) |
| 3 | `application/worktree_tools.py` (1030 → 1035) | `worktree_sync_tool` (:353) hands its result to `rebinding_result_block` (:372-377) |
| 4 | `application/review_comparison_reopen.py` (730 → 778) | fifth channel `ComparisonReopen.sync_rebinding` (:201) + `_measured_rebinding` (:367), which cross-checks the record against the manifest |
| 5 | `mcp/tests/test_worktree_sync.py` (937 → 1678) | `ReviewSyncFixture` (:972) and four cases in `ManagedSyncReviewRebindingTests` (:1251) |
| 6 | `mcp/tests/evidence-lifecycle.toml`, `mcp/tests/test_dependency_ownership_ast_helpers.py` | three consumer rows + catalog re-pin (Sixteenth→Twenty-fourth), 16 contracts / 66 artifacts unchanged |

`application/knowledge_review.py` is untouched (1041 L at base and candidate) — the adapter seam is not
invoked, correctly: none of R22's owners routes through it.

---

## 2. Findings

### F1 — **blocking** — a completed sync is reported as "not a completed sync", in the ordinary already-current state

* **file:line** `mcp/src/agents_remember/application/review_sync_rebinding.py:181` (the
  `and "codeBaseCommit" in payload` conjunct of `resolved_pair_completed`) → `:489` (the detail string of
  `_nothing_to_bind_block`), surfaced through `rebinding_result_block` (:311) →
  `application/worktree_tools.py:372` (the `worktree_sync` tool result). The state is produced by
  `worktrees/sync_transaction.py:375-380` (`_already_current_result`).
* **What.** On a **live, non-preview** sync whose recorded base pair and participating work branches
  already contain the official line, the tool returns `state: "already-current"`, `ok: true`, summary
  *"The recorded base pair and participating work branches contain the official line."* — and, beside it,
  `review_rebinding: {state: "not-applicable", detail: "this result is not a completed sync, so no
  source/knowledge pair was resolved for a review to be measured against"}`. Both clauses are false about
  the store: the result *is* a completed sync, and the pair *is* resolved.
* **Evidence (my probe `test_probe_1_already_current_live_sync`, real tool, frozen bytes).**
  ```
  payload state        : already-current
  payload summary      : The recorded base pair and participating work branches contain the official line.
  payload ok           : True
  payload has codeBaseCommit: False
  block                : {'state': 'not-applicable', 'detail': 'this result is not a completed sync, so no
                          source/knowledge pair was resolved for a review to be measured against'}
  git: recorded base == code source tip: True
  ```
  The module's own `_COMPLETED_SYNC_STATES` (:153-160) **lists `already-current` as a completion** and its
  comment says "a state outside this set is a refusal, a retained conflict or a manual repair, and none of
  those resolved a pair" — so the block denies a fact the same module asserts three lines above.
* **Mutation (rule 4).** Removing exactly `and "codeBaseCommit" in payload` in my scratch copy changes the
  already-current block to a real measurement — `state: "current"`, `covers_resolved_pair: True`, sentence
  *"…was measured against it: the work branch head 3a8c0b21… carries candidate tree 4bca9de2…, the
  candidate tree the review captured; …the reviewed comparison still describes both sides of it."* — and
  leaves the **preview** block unchanged (`would-sync` → still `not-applicable`, no durable file written),
  because `would-sync` is not in the state allow-list. So the conjunct contributes nothing to keeping
  previews out (the allow-list does that) and its **only observable effect is this false sentence**.
* **Why it matters.** This is the master's ruled-blocking class: a user-visible sentence on a produced
  payload that contradicts the store in a state the operation itself produces — and not a narrow state:
  every `worktree_sync` call on an up-to-date leaf renders it. **The delivery's own test blesses it**: case 4
  (`mcp/tests/test_worktree_sync.py:1522`) calls `dry_run=True` *without moving the official line*, so its
  "preview" returns `already-current`; the case therefore pins the misclassification as the expected answer
  ("`assert preview["review_rebinding"]["state"] == "not-applicable"`"), and it is the only case that fails
  when the conjunct is removed (`1 failed, 3 passed`). A genuine `would-sync` preview was never exercised
  by any case here.
* **Suggested fix.** Two edits, either alone is enough; both is better. (a) Make the block honest about the
  completion it is looking at — give the already-current result its own state (e.g.
  `{"state": "no-movement", "detail": "the recorded pair already contained the official line, so this sync
  carried nothing to measure the review against"}`) instead of the blanket "not a completed sync"; the
  cleanest form is to branch on `payload["state"]` rather than collapse every non-measured result into one
  sentence. (b) Fix case 4 so its preview is a real one (move the official line before the `dry_run=True`
  call, or assert on a `would-sync` payload) — otherwise the case keeps pinning the defect whichever way the
  sentence is repaired.

### F2 — **blocking** — the rebinding sentence claims the work-branch head *carries* a tree it does not carry

* **file:line** `mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py:345-348`
  (`_code_clause`), rendered by `statement()` (:312) into the sync payload
  (`application/review_sync_rebinding.py:317`) and into every reopen read
  (`read_review_sync_rebinding`'s `detail`, :398, carried by `ComparisonReopen.sync_rebinding`).
* **What.** The clause reads *"the work branch head `<resolved_code_head>` carries candidate tree
  `<resolved_candidate_code_tree_id>`"*. `resolved_candidate_code_tree_id` is the **add-all** capture
  (`worktrees/modules/future_code_candidate.py:71` → `worktree_candidate_tree`, `read-tree HEAD` + `add -A`
  + `write-tree`), so it equals the head's own tree only while the worktree is clean. Whenever the leaf's
  candidate is uncommitted — precisely the state the packet requires the sync to preserve — the head does
  not carry that tree.
* **Evidence (my probe `test_probe_3_moved_sync_and_head_carries_tree`, real tool; restored WIP).**
  ```
  worktree porcelain        : '?? src/curator-wip.py'
  wip file restored         : True
  resolved_code_head        : 4f1346d1a234cbb46e86e3dab7cfc3840bca4842
  capture tree              : 9700e8fb0b132728a0b17f36e1de8bbabadd0fec
  git <head>^{tree}         : c1ae10f13283b2b053a7075b2c289e3319fe5de7
  head carries that tree    : False
  statement                 : "… the work branch head 4f1346d1… carries candidate tree 9700e8fb…, not the
                               review's captured candidate tree 4bca9de2…; …"
  ```
  `git rev-parse 4f1346d1^{tree}` is `c1ae10f1…`; the sentence names `9700e8fb…`. The identities
  themselves are all true (probe 3 in §3.2: head == `git HEAD`, tree == the capture owner's own value) —
  it is the *relation* the prose asserts that the store denies.
* **Why it matters.** Same class as L21-F1: the payload's own sentence tells a reader that a commit
  carries a tree it does not, and the state where it does so is the packet's WIP-preservation state, i.e.
  the ordinary post-sync state for a working leaf. It renders identically on the reopen channel, so a
  curator reading the recorded comparison months later is told the same untruth.
* **Suggested fix (wording only, no behaviour change).** Make the head a locator rather than the carrier:
  *"candidate tree T captured from the worktree at work branch head H"*, or *"the leaf's captured candidate
  tree T (work branch head H)"*. The `matches`/`differs` branches and the field set stay exactly as they
  are.

### F3 — **medium** — the block reports the *generation selection's* state under the rebinding key, and `unreadable` means two different things on the two surfaces

* **file:line** `mcp/src/agents_remember/application/review_sync_rebinding.py:504`
  (`return {"state": selection.state, "detail": selection.detail}`) versus `:366-375` (the reader's
  `unreadable`, which means *the rebinding artifact* cannot be read).
* **What.** `_nothing_to_bind_block` publishes `ReviewGenerationSelection.state` — `no-generation`,
  `ambiguous`, `unreadable` — as `review_rebinding.state`, and the selection's `detail`, which is R21's
  sentence about the **final-output** selection. So `review_rebinding.state == "unreadable"` can mean "the
  leaf's generation manifests are unreadable" (write path) or "the rebinding record is unreadable" (read
  path), and `review_rebinding.detail` can be a sentence about a different record entirely.
* **Evidence (my probe `test_probe_6` / `test_probe_7`, real tool, completed syncs).**
  ```
  PROBE 6 (no generation):     {'state': 'no-generation', 'detail': 'no comparison generation is published
                                for 260921-icr-l1, so there is no reviewed generation to record a final
                                output against'}
  PROBE 7 (unreadable manifests): {'state': 'unreadable', 'detail': '2 generation director(y/ies) exist for
                                260921-icr-l1 and none holds a readable manifest, so no generation could be
                                selected and none is named'}
  ```
  Both are true sentences about the store, but neither is about the rebinding, and the second reuses the
  reader's own word for a different fact.
* **Why it matters.** The master's rule that the five states stay distinct: "a generation manifest is
  unreadable" and "the rebinding record is unreadable" are different facts, and a consumer branching on
  `review_rebinding.state == "unreadable"` cannot tell them apart. The `detail` for these states is also
  R21's final-output sentence inside an R22 block.
* **Suggested fix.** Give the block its own vocabulary (`no-generation`, `generation-selection-ambiguous`,
  `generation-unreadable`) and its own sentences about the rebinding; keep the selection's own detail
  available under a distinctly named key if it is wanted at all.

### F4 — **low** — the reader's `not-recorded` sentence is false after the delivery's own reclamation, and both the reclamation owner and the leaf-wide reader have no production caller

* **file:line** `mcp/src/agents_remember/application/review_sync_rebinding.py:362` (the sentence),
  `:459-476` (`discard_review_sync_rebindings`), `:440-456` (`read_review_sync_rebindings`).
* **What.** A missing artifact reads *"no managed sync has measured comparison generation G of L: nothing
  is recorded at P"*. After `discard_review_sync_rebindings` — the module's own named reclamation owner —
  a managed sync **has** measured G; the record was removed. The second clause is true, the first is not.
* **Evidence (my probe `test_probe_4_reader_after_reclamation`).**
  ```
  before state: recorded
  removed: ['review-sync-rebinding-260921-icr-l1-bceab415-…json']
  after  state: not-recorded
  after  detail: no managed sync has measured comparison generation bceab415-… of 260921-icr-l1: nothing
                 is recorded at …/review-sync-rebinding-260921-icr-l1-bceab415-….json
  ```
  `grep -rn "discard_review_sync_rebindings\|read_review_sync_rebindings" mcp/src` returns no caller
  outside the defining module (only its docstring and `__all__`), so the sequence is not reachable from a
  shipped route today — which is what keeps this low rather than blocking, and is the same gap L21-F3
  recorded for R21's receipt reader.
* **Suggested fix.** Word the absence as an observation of the location rather than a claim about history
  ("nothing is recorded at P for generation G; a discarded record and one never written read the same
  here"), or have the reclamation owner leave a typed deletion record (the pattern
  `ComparisonHistoryDeletion` already establishes) so the reader can separate *deleted* from *never
  measured*. Route the reader and the reclamation owner from the surfaces that will consume them (R25's
  acceptance reader, or the review entry) or say in the docstring that they are the acceptance/curator
  route rather than a shipped one.

### F5 — **medium** — `mcp/tests/test_worktree_sync.py` crosses the 1200-line hard rail (937 → 1678)

* **file:line** `mcp/tests/test_worktree_sync.py` (S25, the packet's own anchor module).
* **Evidence (measured by me on both trees).**
  ```
  base:       937 lines   (soft band, over 900)
  candidate: 1678 lines   (over the 1200 hard rail)
  whole-tree .py over 1200 (mcp/src + mcp/tests): base 26 -> candidate 27
  whole-tree .py over  900 (mcp/src + mcp/tests): base 108 -> candidate 108
  ```
  The candidate adds exactly one hard-rail offender and makes no other offender worse. The new production
  modules are 599 and 381 lines; `review_comparison_reopen.py` 730 → 778 and `worktree_tools.py`
  1030 → 1035 are unchanged in band. No new `# noqa` (0 in both new modules).
* **Why it matters.** The template requires this reported, never hidden. The worker discloses it and
  requests a ruling (§4 of the report) with the R17 precedent for an over-rail *test* module. I neither
  grant nor refuse it: it is the master's call, and the report's alternative (move `ReviewSyncFixture` and
  the four cases to a new `test_review_sync_rebinding.py`, which costs a catalog registration) is a
  one-edit change. What I can state is the fact: **the leaf's own `test_file_size_detector.py` does not
  fail on it** — that case only exercises the detector's banding on synthetic files — so nothing in the
  delivery's rails set actually catches the crossing; only the census count does.

### F6 — **medium** — "invalidate moved review inputs" is delivered as a recorded measurement; no review read consults it

* **file:line** `mcp/src/agents_remember/application/review_sync_rebinding.py:314-334` (the payload block)
  and `application/review_comparison_reopen.py:201` (the reopen channel); `application/knowledge_review.py`
  untouched (1041 L).
* **What.** The packet's Required Behavior is *"invalidate moved review inputs and bind the resolved
  pair"*, and its non-conforming example is *"the old review stays current while its scratch datasets lag
  the merged memory line"*. What ships is a durable **measurement** (`moved` / `current` / `unmeasured`)
  plus a named successor action. It is carried on the sync result and on the reopen channel
  (`reopen_comparison_generation`, reached from `review_committed_leaf.py:208`), and it invalidates
  nothing: the live review read still composes its comparison and reports its own staleness state exactly
  as before, with no reference to the rebinding.
* **Evidence.** `grep -rn "review_sync_rebinding" mcp/src` → `worktree_tools.py` (the sync tool) and
  `review_comparison_reopen.py` (the reopen channel), and nowhere on the live review path;
  `knowledge_review.py` byte-identical to base (1041 L both). My probe 5 confirms the reopen channel
  carries it; the sync payload carries it (probes 1-3). The "old review stays current" state therefore
  survives on the surface a reviewing agent is actually looking at, and only a reader who reopens the
  *recorded* generation sees the movement.
* **Why I did not grade it blocking.** Nothing false is said: the record states only what it measured, its
  `successor_action` names the remedy, and it deliberately refuses to become a gate on the Git transaction
  or a parallel review authority — both of which the packet's Forbidden Overreach forbids. If the master
  reads "invalidate" as *enforced* (the moved review must stop reading as current on the live path), this
  is an unimplemented clause and blocking; if it reads "invalidate" as *record and route*, the delivery is
  a defensible partial and the gap is the one the worker himself records (§8.2, §8.5 of the report: the
  reconcile/cancel legs and the `unmeasured` verdict are unmeasured by any case). I record the fact and
  route the reading.
* **Suggested fix (if enforced invalidation is wanted).** Have the live review read consult the same
  record (the reopen owner already resolves it) and surface the sync rebinding beside its own staleness
  state, without inventing a verdict the comparison owner did not produce.

### F7 — **low, pre-existing (not this leaf's regression)** — the sync's own park refuses when the worktree holds an untracked `.gitignore`

* **file:line** `mcp/src/agents_remember/worktrees/sync_transaction_git.py:169-188` (`park_worktree_wip`,
  base code, unchanged by this leaf) with `git_status` (:135).
* **What.** `git stash push --include-untracked` removes an untracked `.gitignore` from the worktree; the
  directory it was ignoring is then no longer ignored, so `git status --porcelain` reports it and
  `park_worktree_wip` raises *"git stash push left the sync worktree dirty"*. A leaf whose worktree holds
  an untracked `.gitignore` (the fixture does; real leaves often do before their first commit) cannot be
  synced with WIP at all.
* **Evidence.** Standalone, no candidate code involved:
  ```
  $ git status --porcelain            ->  ?? .gitignore
  $ git stash push --include-untracked --message probe -q
  $ git status --porcelain            ->  ?? build/          (was ignored by the stashed .gitignore)
  $ git stash list                    ->  stash@{0}: On master: probe
  ```
  Hit in the leaf fixture as `state: sync-side-preflight-failed | summary: git stash push left the sync
  worktree dirty`, which is why a probe that parks code-side WIP must first commit the fixture's
  `.gitignore` (the delivery's `commit_leaf_candidate` does exactly that — its docstring says the clean
  worktree is what lets its cases isolate what they measure).
* **Why it matters.** It is not this leaf's defect and I do not charge it here — `sync_transaction_git.py`
  is untouched — but it bounds R22's WIP clause: on such a leaf the preserved-WIP path is unreachable, so
  A20's "preserve eligible untracked WIP" claim inherits this intake limitation. Worth a routing note to
  the leaf that owns the sync owner.
* **Suggested fix (out of this packet's scope).** Compare status with the same pathspec the stash used, or
  take the ignored-after-stash delta into account, before declaring the park dirty.

---

## 3. Obligations

### 3.1 Base-defect witness (my own script, both trees)

`probe-base-defect.py` (mine, in scratch) builds the real enclosure from the shipped endpoint fixture,
freezes a real generation through `freeze_review_comparison`, publishes the reviewed dataset, moves the
official code line, and drives the **real** `worktree_tools.worktree_sync_tool`. The identical file was run
against pristine base bytes (`base/`, `git status` empty) and against the frozen candidate:

```
base (e605822e)      sync state: sync-pass-completed-memory-skipped | ok: True
                     review_rebinding key    : False
                     reviewed candidate tree : 4bca9de2059271a1c9ba46e5196402631bf1d334
                     post-sync capture tree  : c1ae10f13283b2b053a7075b2c289e3319fe5de7
                     capture moved           : True
candidate            sync state: sync-pass-completed-memory-skipped | ok: True
                     review_rebinding key    : True
                     block state             : moved (code_match differs-from-reviewed-input)
                     post-sync capture tree  : c1ae10f13283b2b053a7075b2c289e3319fe5de7
                     capture moved           : True
```
Same sync, same moved capture, and only the candidate records it — the packet's failure is real on base
bytes and is caught on the candidate. The delivery's own four cases on base bytes cannot be collected
(`ModuleNotFoundError: No module named 'agents_remember.application.review_sync_rebinding'`), which is the
existence half only and is not counted as the behavioural witness.

### 3.2 The real production operation, identities against the store's own truth

Drive = the real tool (`worktree_tools.worktree_sync_tool`) over a real leaf enclosure, never a private
helper and never an injected payload. My probe 3:

```
payload resolved_code_head == git HEAD        : True
payload capture tree == capture owner's tree  : True
payload contract_path == live contract        : True
record reviewed candidate == manifest         : True
record reviewed baseline == manifest          : True
record generation id == manifest              : True
record manifest digest == ref digest          : True
record state == payload state                 : True
read_back                                     : matched
judged generation bytes unchanged             : True
```
Both delivered tests also pass on the frozen bytes: `pytest tests/test_worktree_sync.py -q -m '' -n0
-p no:randomly` → **12 passed, 9 subtests passed in 32.14s**.

### 3.3 Failure and recovery clauses

* **Preview** (`dry_run=True`, official line moved): `would-sync`, block `not-applicable`, durable location
  empty, work branch still at its pre-sync head — correct, and correctly re-checked on the mutant.
* **Retained source conflict**: `sync-resolution-required`, `ok: false`, block `not-applicable` with a true
  sentence; durable location empty.
* **Cancel** (supported action): `sync-cancelled`, `ok: true`, block `not-applicable` with a true sentence;
  branch restored.
* **Already-current completion**: **F1** — the one state where the sentence is false.
* **Reclamation/read-back**: **F4** — the reader's sentence after a discard.

### 3.4 Preservation boundaries

No fallback to current HEAD/current knowledge: the record's resolved identities come from the shipped
capture owner and from the declared publication location's own read route (`resolve_published_intent`), and
a refused capture publishes **no** record (`source-unmeasured`); I confirmed the identity fields match the
store exactly (§3.2). The judged generation's manifest bytes are unchanged after the sync (probe 3), and no
new gate was added to sync, closeout or integration (`rebinding_result_block` cannot refuse and the sync
tool returns the transaction's own payload). Nothing writes a successor generation: supersession is named
as `freeze_review_comparison`, not performed.

### 3.5 Truthfulness of every new sentence

| Sentence | Where | True? |
| --- | --- | --- |
| `statement()` `moved` / `current` / `unmeasured` clauses | sync payload, reopen detail | verdict and identities true; **the "head carries candidate tree" relation is false with restored WIP — F2** |
| `_nothing_to_bind_block` "not a completed sync…" | sync payload | true for a preview, a retained conflict and a cancel; **false for `already-current` — F1** |
| "no comparison generation is published… final output…" | sync payload | true, but R21's subject under an R22 key — F3 |
| "N generation director(y/ies) exist… none holds a readable manifest" | sync payload | true, `state: unreadable` collides with the reader's meaning — F3 |
| `source-unmeasured` detail | sync payload | true (no candidate tree was observed) |
| reader `not-recorded` "no managed sync has measured…" | reopen detail | true when nothing ran or the capture failed; **false after the owner's own discard — F4** |
| `_knowledge_clause` `unmeasured`/`not-selected` branches | payload/reopen | true as written |

### 3.6 Seam policy

`application/knowledge_review.py` untouched (1041 → 1041) — the seam note governs it and this leaf
correctly does not touch it. One implementation per responsibility: the two channel rules and the verdict
rule live once in the model and are *called* by both the writer and the validator; `rebinding_names_the_generation`
is the single cross-check and is wired into the reopen owner (`review_comparison_reopen.py:379`). Nothing
else imports a moved private name.

### 3.7 Rails (measured by me on the frozen bytes)

```
ruff check <5 touched files>          All checks passed!
ruff format --check <5 touched files> 5 files already formatted
pyright --pythonpath …/python <5>     0 errors, 0 warnings, 0 informations
pytest test_dependency_ownership_ast_helpers.py test_evidence_lanes.py test_file_size_detector.py -q -m ''
                                      7 passed in 55.21s   (16 contracts / 66 artifacts intact)
unit population                       2701 collected  (budget 4000)
integration population                440 collected   (budget 1000)
new # noqa in the two new modules     0
peer modules the report names         test_worktree_sync + test_review_final_output_receipt +
                                      test_historical_committed_leaf_review +
                                      test_knowledge_review_comparison_generation
                                      -> 46 passed, 9 subtests passed in 91.64s
file sizes                            one new hard-rail offender — F5
```
Note on the census: my first census run failed two cases *because my own probe modules were sitting in the
scratch `mcp/tests/`* (an unregistered test module is exactly what `test_production_proof_adds_no_governed_evidence_artifact`
refuses). Removed and re-run on the candidate's own tree: **7 passed**. The delivery's claim reproduces.

### 3.8 Unimplemented / partial clauses

* "**invalidate** moved review inputs" — recorded, not enforced on the live read — F6.
* "bind the resolved pair" — the resolved identities are carried and verified against the store (§3.2), but
  the binding is a durable *measurement*; no successor generation is created and no input is rebound.
* `resolution_action="reconcile"` and `"cancel"` legs produce no rebinding record (correct: neither is a
  completion), and the worker records that no case measures a rebinding *after* an authored reconciliation
  (report §8.2) — I confirm that gap; the reconcile leg does end in the same `finalize_sync` path, so the
  code path is shared, but it is not case-covered.
* `unmeasured` verdict (no publication at the declared location): model-guarded and probe-reachable, no
  case — worker's §8.5, confirmed by my own probe (probe 7's sibling state).
* `sync-pass-completed-source-moved-again` is in the allow-list by construction only (worker's §8.3).

---

## 4. The delivery's true strengths

1. **The base defect is real and is genuinely closed behaviourally.** Same probe, same fixture: base
   reports nothing while the capture moves; the candidate reports `moved` with both identities.
2. **The identities are the store's own.** Every recorded identity I compared against Git, the capture
   owner, the live contract and the generation manifest matched, and the durable artifact read back
   `matched`.
3. **The judged generation is preserved byte-for-byte**, and supersession is named rather than performed —
   the packet's "no parallel review authority" boundary holds.
4. **The record's validator is genuinely load-bearing** and the worker proved it with a mutation matrix
   over the *real published* record (six guards, each turning a case red); it also found and closed its own
   forgery gap (`rebinding_names_the_generation`, wired into the reopen owner) instead of claiming the
   validator covered it.
5. **The preview hole was caught by the worker's own case before I saw it**; the current allow-list keeps
   `would-sync`, retained conflicts and cancels out of the measurement, which I re-verified myself on both
   the frozen and the mutant bytes.
6. **An honest report**: the file-size crossing, the two unproven guards (M7/M9), the unmeasured legs and
   the unmeasured verdict are all disclosed and routed rather than hidden.

---

## 5. Verdict

**Verdict: `fail`** — with **F1 and F2 blocking**.

Two user-visible sentences the delivery emits are false about the store, and neither is protected by any
case. **F1**: on a live, non-preview sync whose pair is already current — the commonest `worktree_sync`
result there is — the payload says "this result is not a completed sync, so no source/knowledge pair was
resolved", contradicting the same payload's own summary, Git's own answer, and the module's own
`_COMPLETED_SYNC_STATES`; my mutation shows the `codeBaseCommit` conjunct that causes it contributes
nothing to keeping previews out, and the delivery's own case 4 pins the misclassification because its
"preview" is an `already-current` result. **F2**: the rebinding sentence asserts that the work-branch head
*carries* the add-all candidate tree, which Git denies whenever the leaf's WIP is restored — the packet's
own preservation state. Everything else the delivery claims largely reproduces: the identities are true,
the generation is preserved, the seam is respected, the rails are green, and the four cases pass. Because
the master's standing rule is that a false user-visible sentence is blocking and is not softened by the
narrowness of the state or by the delivery's own test blessing it, this candidate is not acceptable as it
stands; both fixes are wording-level or a single conjunct plus a case correction, and neither touches the
record's design. The remaining findings (F3-F7) are non-blocking: F5 needs the master's ruling on the test
module's hard-rail crossing, and F6 needs the master's reading of "invalidate".

Artifacts I keep: `temp/verify-l22/{probe_l22.py, probe2_l22.py, probe-base-defect.py, frozen/, base/,
delivery-tests.log, rails.log, fingerprint-history.txt, mut-backup-py}` under
`/home/firefox/projects/ar-coordination/temp/`.
