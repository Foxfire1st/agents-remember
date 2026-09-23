# L22 / ICR-R22@v1 — Managed Git recovery rebinding — worker report

| Field | Value |
| --- | --- |
| Leaf | `260921-ICR-L22` |
| Requirement | ICR-R22@v1 (`requirements/ICR-R22-v1-managed-git-recovery-rebinding.md`, SHA256 `680a7ccd…bd8155`) |
| Code worktree | `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22` |
| Branch / base | `ar/260921-icr-l22` @ `e605822eb3bf83bf63a45963c5f51d5fc28859ee` (`e605822e`, master tip = ICR-R15) |
| Memory worktree | **not touched, not read, not committed** |
| Commits | **none** — the orchestrator commits |

## Combined fingerprint

Measured **after** the last write to any candidate file and **before** this report was (re)written, so
the number below describes the candidate the verifier will read. This report file is excluded from its
own fingerprint — including it would make the number unsatisfiable — and the exclusion is stated here
rather than hidden:

```
tracked diff  sha256  5113aa73916cdd0c06e5bc1d53317f548daebde54d69816f55d8b321b3fdd4ac
git status --porcelain entries (excluding temp/icr/report-l22.md)   10
combined      sha256  a0c100d59b452e7d0229b6dd9da998696b6f5cfdfdeffe59d7dbec2b70cc016a
```

Combined = `sha256( git diff | sha256sum ; git status --porcelain (sorted, report excluded) ; sha256sum
of every untracked file (sorted, report excluded) )`. The `git diff`-only digest is **blind** to the two
new modules, so the status list and the per-file hashes are part of the identity.

```
 M mcp/src/agents_remember/application/review_comparison_reopen.py
 M mcp/src/agents_remember/application/worktree_tools.py
 M mcp/tests/evidence-lifecycle.toml
 M mcp/tests/test_dependency_ownership_ast_helpers.py
 M mcp/tests/test_worktree_sync.py
?? mcp/src/agents_remember/application/review_sync_rebinding.py      d2ec740c9f315b38f9a07065e113e4f540363d060648c59a6323b3d93bc6fbb7
?? mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py d7505636c44057bcdc65f9f7a5914bc8cc648659431179b1dfe86ab488a07f94
?? temp/icr/base-defect-l22-base.txt                                 da9bda48cd26aa9704930ef4864e6a442fa085abb45ecf1eecb14c18d3a6f2c8
?? temp/icr/base-defect-l22-candidate.txt                            486db4766950d4515756ee7588585a7d22ffced09f254ad6d9d3629115a64b83
?? temp/icr/probe-l22-base-defect.py                                 c5ec98ebe7c12257fe5706fcd8fea1959444944cbe088fda44df8f95d1f00b18
?? temp/icr/report-l22.md                                            (this file; excluded above)
```

`temp/icr/probe-l22-base-defect.py` is the witness **script**, byte-identical between the base and
candidate runs of §6a; their outputs are the two `.txt` files. **Nothing was written to this worktree
after this report.**

## 1. What the requirement asks, and the base defect

Required Behavior: *after fast-forward, clean merge, source resolution or authored knowledge resolution,
invalidate moved review inputs and bind the resolved pair; preserve journaled partial outcomes, original
generations and eligible untracked WIP through supported resume/cancel.*

The managed sync (`worktrees/sync_transaction*.py`) moves **both** things a comparison generation binds:
the leaf worktree's captured candidate tree (the merge advances `HEAD`) and the dataset at the
repository's declared publication location (the binary-stage merge rewrites `knowledge.sqlite`). Nothing
in the transaction, in `worktrees/knowledge_conflict.py` or in `application/knowledge_merge.py` said
anything about the leaf's review. `application/review_candidate_resolution.require_current_candidate_identity`
does refuse a capture that moved — but only when someone later reopens the review, and it binds nothing.

**Base witness is behavioural, not an import error** (§6a): on the base commit a real managed sync
completes over a moved official line (`sync-pass-completed-memory-skipped`, `ok: true`) while the review's
captured candidate tree moves `4bca9de2… → c1ae10f1…`, and the production tool reports **nothing** about
the review. That is the packet's non-conforming example measured.

## 2. Changes (file:line)

### 2.1 NEW `mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py` (379 lines)

Record vocabulary, mirroring the shape R21 established and **calling R21's comparison rules** rather than
restating them.

- `code_channel_match` (:107), `knowledge_channel_match` (:120), `review_sync_verdict` (:142) — one
  comparison rule per channel and one verdict rule, so a writer and the validator cannot disagree.
- `SyncKnowledgeObservation` (:164) + `_an_identity_is_carried_exactly_when_a_dataset_was_read` (:183):
  a `published` state carries an identity; `not-recorded`/`unusable` carry none.
- `ReviewSyncRebinding` (:194): judged generation id/index/seal/manifest-digest, reviewed and resolved code
  identities, reviewed knowledge state + digest, resolved knowledge observation, channel matches, verdict,
  successor action.
- `_the_rebinding_agrees_with_itself` (:244): re-derives every verdict from the record's own fields; refuses
  `unmeasured` beside a location reported as holding a readable dataset (:282).
- `covers_resolved_pair` (:301), `statement` (:311) with `_code_clause`/`_knowledge_clause` (:341/:355).

### 2.2 NEW `mcp/src/agents_remember/application/review_sync_rebinding.py` (550 lines)

- `_COMPLETED_SYNC_STATES` (:154) and `resolved_pair_completed` (:164): the operation, a non-failing
  result, one of the four completion states, **and** the finalized base pair (`codeBaseCommit`) that only
  the transaction's own completion writes — the conjunct that keeps a `dry_run=True` preview out.
- `record_review_sync_rebinding` (:234): selects the generation through R21's `select_review_generation`,
  reads its sealed manifest, re-derives the candidate through `capture_future_code_candidate` after the
  sync, reads the declared publication location through `declared_publication_location` +
  `resolve_published_intent`, and publishes one durable record under `<task_root>/notes/reports/`.
- `rebinding_result_block` (:279): the entry point the sync tool calls. **Refuses nothing** — an
  unmeasurable capture (`source-unmeasured`), an unreadable generation record and a filesystem error are
  reported as states; a capture refusal publishes **no** record rather than inventing a tree.
- `read_review_sync_rebinding` (:335) / `read_review_sync_rebindings` (:437): read back as
  `recorded`/`not-recorded`/`unreadable`, never raising.
- `rebinding_names_the_generation` (:399): the cross-check against the generation the record names.
- `discard_review_sync_rebindings` (:456): the named reclamation owner.

### 2.3 `mcp/src/agents_remember/application/worktree_tools.py` (+5)

Import at :15; `worktree_sync_tool` (:353) now returns `rebinding_result_block(configured.contract,
payload)` (:372-377). Same wiring shape and same reason as R21's closeout/integration attachment one
function above: the Git transaction has already finished.

### 2.4 `mcp/src/agents_remember/application/review_comparison_reopen.py` (+29)

`ComparisonReopen.sync_rebinding` (fifth channel) and `_measured_rebinding`, which reads the location and
then checks the record against the generation **itself** — a record whose identity fields were forged is
internally consistent, so it reads back `not-recorded` with the reason instead of being taken as a
measurement of this generation.

### 2.5 Tests — `mcp/tests/test_worktree_sync.py` (S25, the packet's own anchor; 937 → 1678 lines)

`ReviewSyncFixture` (:972) builds one live leaf enclosure: the shipped endpoint fixture's external-memory
enclosure, an MCP configuration bound to it, a published lifecycle-operation location, and two review
datasets created through the store's own API **in the enclosure's own namespace** (the endpoint fixture's
datasets are invented namespaces beside a fixed authority home, which the publication route rightly
refuses). `ManagedSyncReviewRebindingTests` (:1251), four cases:

1. :1261 — reviewed dataset published, official code line moved, production sync tool carries it in; the
   record says `moved`, names both trees, the real merge head and the freeze entry point; the judged
   generation's bytes are identical afterwards; the durable record reads back through its own reader.
2. :1332 — one authored knowledge line as both sides' ancestor (`stage_knowledge_divergence`), disjoint
   changes per side, a real binary-stage union, a curator's untracked file parked and returned with an
   empty stash, and the record measuring the dataset now at the declared location against the one the
   review compared.
3. :1380 — nine mutations of the **real published** record, each required to be refused (§5), plus the
   identity-forgery case the record's own validator cannot refuse and the generation cross-check does.
4. :1461 — a dry-run preview and a sync stopped on a retained source conflict each report
   `not-applicable`, and the durable location stays empty.

### 2.6 Catalog rows

No test module was added, so no artifact, contract or lane row is registered. The census derived **new
consumers** for three existing `consumer_scope="exact"` rows because the case module now imports the shared
endpoint fixture (`mcp/tests/fixtures/repository_profiles/node/package-lock.json`,
`mcp/tests/diff_scope_test_support.py`, `mcp/tests/read_scope_test_support.py`), each reporting
`missing=['mcp/tests/test_sync_parked_candidate.py', 'mcp/tests/test_worktree_sync.py']` with
`unsupported=[]`. All three rows gained those two paths. `LIFECYCLE_CATALOG_SHA256` re-pinned
`81a518b5…` → `8ce61c57b8660011b1cb81f0c2f21bd493ce3e2295359228d8804f0b8192b9d1` (`sha256sum
mcp/tests/evidence-lifecycle.toml`), with the proof's own Twenty-fourth re-pin note recording exactly this.

## 3. Seam policy — deliberately not invoked

`notes/03-adapter-seam.md` governs `application/knowledge_review.py`. This leaf does **not touch it**: it
is unmodified in `git status` and 1041 lines at both base and candidate. R22's owners are the sync
transaction, the knowledge-conflict adapter and the review-generation integration, none of which routes
through the review adapter — the same finding L21 recorded. The new modules sit beside their owners, one
responsibility each, one implementation each, and the vocabulary **calls** R21's rules rather than
duplicating them.

## 4. Rails

| Rail | Result |
| --- | --- |
| `ruff check` (7 changed files) | **All checks passed** |
| `ruff format --check` (6 formatted files) | **all formatted** |
| New `# noqa` | **none** on either new module or the test module |
| pyright (`mcp/.venv/bin/pyright --pythonpath …/mcp/.venv/bin/python`, 5 files) | **0 errors, 0 warnings, 0 informations** |
| `C901`/`PLR0911`/`PLR0912`/`PLR0915` | green; one `PLR0915` (test method over 50 statements) was hit and **cleared by extraction** (`stage_knowledge_divergence`), not by a suppression |
| Catalog population | **16 contracts / 66 artifacts** — `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` → **5 passed**; with `test_file_size_detector.py` → **7 passed** |
| Case budgets | `pyproject.toml` unit 4000 / integration 1000; this leaf adds **4 unit cases** |

### File size against the 1200 hard / 900 soft rail

| File | Base | Candidate | Note |
| --- | ---: | ---: | --- |
| `mcp/tests/test_worktree_sync.py` (S25) | 937 (soft band) | **1678 (hard rail)** | ALREADY in the soft band at base; this leaf crosses the hard rail |
| `application/review_comparison_reopen.py` | 730 | 759 | under 900 |
| `application/worktree_tools.py` | 1030 | 1035 | already over soft at base; +5 (an import, four comment/payload lines) |
| new application / model module | — | 550 / 379 | under 900 |
| `application/knowledge_review.py` | 1041 | **1041** | untouched (§3) |

**Ruling requested on `test_worktree_sync.py`.** Two resolutions:

1. fragment the managed-sync rebinding evidence into a new test module (costs a new catalog registration
   and separates it from the module the packet names as its S25 anchor);
2. extend the module that already owns the managed merge/conflict production-operation evidence.

I chose **(2)** on the master's standing precedent: R17's landed report records
`mcp/tests/test_knowledge_review_surface.py` growing `1313 → 1395` under *"the master's ruling that this
over-rail test module's growth is accepted for the series because it sits in 17 leaves' test scope, with
the whole-tree census unchanged at 22 offenders"*. `test_worktree_sync.py` is S25 — the module R22, R23
**and** R25 share — so it is the same category. If the master prefers (1), the four cases, the fixture and
the four module-level helpers move to `mcp/tests/test_review_sync_rebinding.py` in one edit plus the
registration; that decision is the master's.

## 5. Every guard, the mutation that removes it, and the observed result

The mutation driver is `/home/firefox/projects/ar-coordination/temp/l22/mutations.py`: it rewrites one
guard, runs the named test, and restores pristine bytes (final `sha256sum -c` verified — the candidate
files still hash to the values in the fingerprint above).

### 5a. The record's own validator (case 3)

Nine mutations of the **real published** record; the test requires every one to be refused:

| Mutation of the published record | Guard it targets (model line) | Mutating that guard |
| --- | --- | --- |
| `state: current` | verdict derivation (:150/:303) | **test FAILS** — M4 |
| `code_match: matches` | code derivation (:253) | **test FAILS** — M1 |
| `knowledge_match: unmeasured` | knowledge derivation (:276) | **test FAILS** — M2 |
| `code_match`+`knowledge_match`+`state` all forged to "matches/current" | code derivation | **test FAILS** — M1 (nothing else can catch a fully coherent forgery) |
| `reviewed_knowledge_state: not-selected` | retained ⟺ digest (:261) | **test FAILS** — M5 (this is the case that isolates it) |
| both knowledge fields dropped | knowledge derivation | **test FAILS** — M2 |
| knowledge dependency dropped **and** the verdict forged to follow it | unmeasured-beside-published (:282) | **test FAILS** — M3 (isolated by bisection, §5c) |
| a dataset identity beside `resolved_knowledge.state: not-recorded`, verdict forged | observation identity (:183) | **test FAILS** — M6 (isolated by bisection, §5c) |
| `reviewed_candidate_code_tree_id: "0"*40` | *none* — accepted by the validator | see §5d |

### 5b. The operation's gates (cases 1, 2, 4)

| Mutation | Guard (application line) | Observed |
| --- | --- | --- |
| `return True` in the completion predicate | allow-list + finalized pair (:164) | **test FAILS** — M7 (case 4) |
| drop `and "codeBaseCommit" in payload` (the finalized-pair conjunct) | same | **test FAILS** — M10 (case 4) |
| `reviewed_digest = None` | reviewed knowledge read (:234) | **test FAILS** — M8 (case 1) |
| `successor_action = "review it again"` | the remedy names the freeze (:126) | **test FAILS** — M9 (case 1) |

The conjunct's load-bearing role was also proven at implementation time: before it existed the dry-run
preview reported `unmeasured` **and wrote a record**, which case 4 caught; `codeBaseCommit` is the fix.

### 5c. Guard bisection (which guard refuses what)

For the two compound forgeries that no single-field mutation reaches, each guard was disabled in turn and
the record re-validated (`/home/firefox/projects/ar-coordination/temp/l22/guard-bisect.py`):

```
M3 unmeasured-beside-published: dependency_dropped: ACCEPTED -> moved   <-- the only guard that holds it
M5 retained/digest:             dependency_dropped: REFUSED
M4 verdict:                     dependency_dropped: REFUSED
M2 knowledge derivation:        dependency_dropped: REFUSED
M6 observation identity:        identity_beside_unread: REFUSED
```

`dependency_dropped` is therefore a **witness for M3**, and it is in the test (case 3). `identity_beside_unread`
is refused by more than one guard and is therefore **not** a witness for M6; M6's own §5b row records the
bisection result rather than a red run.

### 5d. The identity-forgery gap — found by the probe, closed, and asserted

`reviewed_candidate_code_tree_id: "0"*40` is **ACCEPTED** by the record's own validator: re-deriving a
comparison from two forged identities still yields a consistent verdict. That is a real gap against the
master's *"carry identities; never fabricate them"* rule, and it is closed by
`rebinding_names_the_generation` (application :399) — a comparison against the **manifest the record
names** — reached in production from the reopen channel through `_measured_rebinding`, and asserted in
case 3: the cross-check returns the record for the real one and `None` for the forged one.

## 6. Evidence — exact commands, exact results

Leaf Python for every command:
`PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support /home/firefox/projects/agents-remember/mcp/.venv/bin/python`.

### 6a. Base-defect reproduction

```
$ cd /home/firefox/projects/agents-remember
$ git worktree add --detach /…/temp/l22/base e605822eb3bf83bf63a45963c5f51d5fc28859ee
HEAD is now at e605822e ICR-R15: measured assessment currentness (260921-ICR-L15)
$ cd /…/temp/l22/base && git status --porcelain      # empty: pristine base bytes
```

The witness script is byte-identical in both runs. **Base:**

```
$ PYTHONPATH=mcp/src:mcp/test_support …/python mcp/tests/base_defect_probe_l22.py /…/temp/l22/bd4
sync state              : sync-pass-completed-memory-skipped | ok: True
review_rebinding key    : False
review_rebinding block  : null
reviewed candidate tree : 4bca9de2059271a1c9ba46e5196402631bf1d334
post-sync capture tree  : c1ae10f13283b2b053a7075b2c289e3319fe5de7
capture moved           : True
```

**Candidate** (same file, copied to `mcp/tests/` for one run and deleted immediately after):

```
$ PYTHONPATH=mcp/src:mcp/test_support …/python mcp/tests/probe-l22-rerun.py /…/temp/l22/bd5
sync state              : sync-pass-completed-memory-skipped | ok: True
review_rebinding key    : True
review_rebinding block  : {"state": "moved", "covers_resolved_pair": false, "statement": "the managed sync
  resolved this leaf's pair and comparison generation f963bac8-… (index 1) was measured against it: the work
  branch head 65d18060… carries candidate tree c1ae10f1…, not the review's captured candidate tree
  4bca9de2…; the dataset 34ca5e14… at <memory worktree>/knowledge.sqlite is the candidate dataset the review
  compared; the reviewed comparison no longer describes it; publish a successor generation naming that one
  as its predecessor, …", … "code_match": "differs-from-reviewed-input", "read_back": "matched"}
```

Full outputs: `temp/icr/base-defect-l22-base.txt`, `temp/icr/base-defect-l22-candidate.txt`.
The new cases cannot even be collected on base bytes:

```
$ cd /…/temp/l22/base && cp <leaf>/mcp/tests/test_worktree_sync.py mcp/tests/
$ PYTHONPATH=mcp/src:mcp/test_support …/python -m pytest mcp/tests/test_worktree_sync.py -q -m '' -k ManagedSyncReviewRebindingTests
E   ModuleNotFoundError: No module named 'agents_remember.application.review_sync_rebinding'
1 error in 17.90s
$ git checkout -- mcp/tests/test_worktree_sync.py        # scratch restored to pristine base bytes
```

### 6b. Candidate runs

```
$ pytest mcp/tests/test_worktree_sync.py -q -m '' -p no:randomly
12 passed, 9 subtests passed in 36.04s
$ pytest mcp/tests/test_worktree_sync.py mcp/tests/test_review_final_output_receipt.py \
    mcp/tests/test_historical_committed_leaf_review.py mcp/tests/test_knowledge_review_comparison_generation.py \
    mcp/tests/test_knowledge_review_source_endpoints.py -q -m '' -p no:randomly
53 passed in 59.02s
$ pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py \
    mcp/tests/test_file_size_detector.py -q -m ''
7 passed in 51.66s
```

### 6c. Mutation matrix (final run, against the frozen candidate)

```
$ /home/firefox/projects/agents-remember/mcp/.venv/bin/python /…/temp/l22/mutations.py
M1 code-channel derivation: rc=1 :: 1 failed in 17.28s :: test FAILS (guard load-bearing)
M2 knowledge-channel derivation: rc=1 :: 1 failed in 16.83s :: test FAILS (guard load-bearing)
M3 unmeasured-beside-published guard: rc=1 :: 1 failed in 17.12s :: test FAILS (guard load-bearing)
M4 verdict derivation: rc=1 :: 1 failed in 16.89s :: test FAILS (guard load-bearing)
M5 retained/digest consistency: rc=0 :: 1 passed in 16.60s :: test PASSES (guard NOT load-bearing)
M6 observation identity: rc=0 :: 1 passed in 16.30s :: test PASSES (guard NOT load-bearing)
M7 completed-sync predicate: rc=1 :: 1 failed in 16.78s :: test FAILS (guard load-bearing)
M10 finalized-pair conjunct: rc=1 :: 1 failed in 16.46s :: test FAILS (guard load-bearing)
M8 reviewed knowledge state read: rc=1 :: 1 failed in 17.09s :: test FAILS (guard load-bearing)
M9 successor action names the freeze: rc=1 :: 1 failed in 16.94s :: test FAILS (guard load-bearing)
restored
```

M5/M6 show `PASSES` here because the *single*-field mutation that targets them is refused first by an
earlier guard; §5c's bisection is what isolates them, and their witness rows are in §5a. Nothing in this
report claims a red run it does not have.

## 7. Boundary and identity assertions

- **No silent fallback.** The resolved source side is the capture owner's own value and the resolved
  knowledge side is the ordinary read route's own answer; `HEAD`, another branch, today's dataset and a
  working tree are never substituted. A capture the owner refuses publishes **no** record.
- **Five states stay five.** `no-generation`, `not-applicable`, `source-unmeasured`, `not-recorded` and
  `unreadable` are distinct; case 4 asserts the durable location stays empty for a preview and a retained
  conflict, so no success receipt exists for a state that was never established.
- **The judged generation is preserved.** Cases 1 and 2 compare manifest bytes before/after; the record
  never rewrites history.
- **Supersession is named, not performed.** `successor_action` names `freeze_review_comparison` with the
  judged generation as `parent` (asserted in case 1); nothing here writes a generation.
- **No new authority and no new gate.** `rebinding_result_block` cannot refuse, and no closeout,
  integration or sync admission changed.
- **The parked candidate is untouched.** Case 2 exercises the real park/restore path: the untracked file
  returns byte-identical with an empty stash list.

## 8. Limitations and what needs a ruling

1. **`mcp/tests/test_worktree_sync.py` crosses the hard rail** (937 → 1678) — **ruling requested**, §4.
2. **Not measured here: a rebinding produced on the `resolution_action="reconcile"` leg.** The authored
   reconciliation, parked-candidate restore and cancel paths are covered by the existing module's cases
   (`test_memory_merge_settles_content_and_knowledge_conflicts_in_the_transaction`, `test_sync_parked_candidate.py`),
   which run in the same population and passed in §6b — but no case in *this* leaf authors a decision and
   then reads the rebinding. The reconcile leg ends in the same completion path the recorder is attached
   to; that is a structural argument, not a measurement, and it is recorded as such.
3. **`sync-pass-completed-source-moved-again` is in the allow-list by construction, not by test.** No case
   produces the "source moved again between the pass and the read" state; `codeBaseCommit` is what
   separates completions from previews.
4. **The `resolved_knowledge` `not-recorded`/`unusable` → `unmeasured` verdict path has no case in this
   leaf.** It is exercised by the base-defect probe (`unusable` at base, §2 earlier draft) and by the
   model's own guards; the four cases always publish. A case that syncs a leaf with no publication would
   pin it directly and is the first thing to add if the verifier wants it.
5. **`rebinding_names_the_generation` needs the caller to hold the manifest.** The reopen channel does;
   `read_review_sync_rebinding` alone answers only what is *recorded* at a location, which is exactly why
   the cross-check is a separate function and is documented as that difference.
6. **No dashboard, memory, Git or AR state operation was performed.** No commit, push, closeout, memory
   worktree access or master task-document edit.
7. **Routed to L25 (R25@v1) as before:** the browser/Class-3 halves and the assembled A19/A20 rows. This
   leaf produces the Class-1/Class-2 evidence the acceptance plan names for A19/A20, at the exact candidate
   identity in the fingerprint above.

---

# Fix round 1 (verdict `fail`; F1, F2 blocking — all findings addressed in ONE round)

Verifier verdict read in full first: `temp/icr/verify-l22.md` (452 lines, 33 729 bytes, sha256
`1ce45277e76e7aeed890a8a7f53cdba4e3d87b34c18668293871738485b3d383`). It graded the round-0 candidate
(`tracked diff 5113aa73…`) and its self-gate recorded that the graded code did not move during the
grading window. **This fix round changes those bytes**, so the round-0 fingerprint in §Fingerprint above
describes the *graded* candidate, and the fingerprint at the end of this section describes the fixed one.

## F5 (rail ruling) — SPLIT, applied

`mcp/tests/test_worktree_sync.py` 937 → 1678 was a **new** ≥1200 offender (26 → 27), which the master's
precedent does not accept. The four R22 cases, `ReviewSyncFixture` and the four module-level helpers
(`admitted_destination_identity`, `anchor_paths_of`, `assert_rebinding_measures_the_location`,
`stage_knowledge_divergence`) moved into a new `mcp/tests/test_review_sync_rebinding.py`, which also
took the R22 constants and its own copies of `git`/`commit_file` so it does not depend on the sibling.

| Module | Base | Round-0 candidate | Fix round 1 |
| --- | ---: | ---: | ---: |
| `mcp/tests/test_worktree_sync.py` | 937 (soft band) | **1678 (over the hard rail)** | **945** (soft band, under 1200) |
| `mcp/tests/test_review_sync_rebinding.py` | — | — | **1037** (new) |
| whole-tree `.py` > 1200 (`mcp/src` + `mcp/tests`) | **26** | 27 | **26** ✓ — the census is back to its base count |
| whole-tree `.py` > 900 (soft) | 108 | 109 | **109** — the +1 is the new module itself (1037 lines for 8 cases and a fixture); reported, not hidden |

Registration, derived from the census rather than guessed (`pytest mcp/tests/test_dependency_ownership_ast_helpers.py`
→ the four findings it printed were applied verbatim):

* lane row added in `mcp/tests/test-evidence-lanes.toml:300`, in the **`integration`** lane beside its
  sibling (the R22 cases need the lane's `worktree_services` composition, and this keeps the population
  accounting identical). Verified by `pytest mcp/tests/test_evidence_lanes.py -q -m ''` — before the row
  existed the run refused with *"test files without an explicit lane: ['mcp/tests/test_review_sync_rebinding.py']"*;
* `mcp/tests/evidence-lifecycle.toml`: three rows lost `mcp/tests/test_sync_parked_candidate.py` and
  `mcp/tests/test_worktree_sync.py` (the census reported them `unsupported` — their only route to those
  artifacts was the case module's own import) and gained `mcp/tests/test_review_sync_rebinding.py`;
  `mcp/tests/merge_case_test_support.py` kept both and gained the new module;
* `LIFECYCLE_CATALOG_SHA256` re-pinned `8ce61c57…` → **`7c8c1646272ca4a17f87a16ecbe105a4aea558c0e0d0c4d62b1883d6d69e683c`**,
  measured with `sha256sum mcp/tests/evidence-lifecycle.toml`, with the Twenty-fifth re-pin note in
  `mcp/tests/test_dependency_ownership_ast_helpers.py`;
* population unchanged: **16 contracts / 66 artifacts** — `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py mcp/tests/test_file_size_detector.py -q -m ''` → **7 passed in 55.30s**.

## F1 (blocking) — a carried-nothing completion is described as what it is

**Change.** `mcp/src/agents_remember/application/review_sync_rebinding.py`:
`_CARRYING_SYNC_STATES` (:155) now names only the three states in which the transaction **carried the
official line** (`synced`, `sync-pass-completed-memory-skipped`,
`sync-pass-completed-source-moved-again`); `resolved_pair_completed` (:199) is those two facts and no
longer infers anything from which keys the payload holds; and a new table `_CARRIED_NOTHING` (:167) gives
every state that resolved no pair its own state name and its own true sentence — `preview`,
`no-movement`, `not-resolved`, `cancelled`, `choice-required`, with `not-measured` for an unknown state
(which claims only that *this tool* records nothing for it). `_nothing_to_bind_block` (:482) dispatches on
that table.

**Case.** `mcp/tests/test_review_sync_rebinding.py::test_every_state_that_carried_nothing_says_which_one_it_is`
drives all four through the production tool: a live up-to-date sync (`already-current` → `no-movement`), a
**genuine `would-sync` preview** (the official line is moved *before* the dry run, which is what the round-0
case failed to do — it pinned the defect), a retained source conflict (`not-resolved`) and the supported
cancellation (`sync-resolved`→`cancelled`), asserting the durable location stays empty in all four.

**Bite-proof.** Pre-fix, with the round-0 code and this case in place:
```
E  AssertionError: {'state': 'not-applicable', 'detail': 'this result is not a completed sync, so no
   source/knowledge pair was resolved for a review to be measured against'}
E  assert 'not-applicable' == 'no-movement'
```
(`temp/icr/fix-round-1-prefix-failures-l22.txt`, first failure). Post-fix: **8 passed**.

**Mutations** (`temp/icr/mutations-fix-round-1-l22.py`, driver kept in `temp/icr/`):

| # | Mutation | Result |
| --- | --- | --- |
| MF1a | the blanket "not a completed sync" sentence restored for every non-carrying state | **test FAILS** |
| MF1b | `already-current` put back into the carrying set (measured as a movement) | **test FAILS** |
| MF1c | the removed `codeBaseCommit` conjunct restored | test passes — **not** load-bearing |

MF1c is reported as it measured: with the state table in place the conjunct changes nothing observable,
because the table already describes `already-current` truthfully. The two halves of F1's fix are therefore
**independently sufficient**, which is what the verifier predicted ("either alone is enough"); both were
applied, and the conjunct was removed as the hygiene half rather than as the load-bearing one.

## F2 (blocking) — the source clause locates the capture at the head

**Change.** `mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py::_code_clause` (:341):
*"candidate tree T **captured from the leaf's worktree at** work branch head H"*, in both the `matches` and
the `differs` branch. No behaviour change; the field set is untouched, and the docstring records why the
old relation was false (the capture is the add-all tree, equal to the head's own tree only while the
worktree is clean — i.e. false in the packet's own WIP-preservation state).

**Case.** `test_the_source_clause_names_a_locator_not_a_carrier`: with the curator's untracked file parked
and restored, it asserts `git rev-parse <resolved head>^{tree} != resolved candidate tree` (the store
denies the old relation), that the statement does not contain `carries candidate tree <T>`, and that it
does contain the locating clause.

**Bite-proof.** Pre-fix: `E AssertionError: assert 'carries can...' not in 'the managed…'` (same log).
**Mutation MF2** (old wording restored) → **test FAILS**. Post-fix: passes.

## F3 (medium) — the block owns its generation vocabulary

**Change.** `_no_generation_block` (new, `review_sync_rebinding.py`): `no-generation`,
`generation-selection-ambiguous`, `generation-unreadable`, each with R22's own sentence; the selection
owner's own answer travels under `selection_state` / `selection_detail`, labelled as its owner's. The
reader's `unreadable` therefore means one thing only — *the rebinding artifact could not be read*.

**Case.** `test_a_carrying_sync_without_a_measurable_generation_says_which`: (1) no generation at all;
(2) a stray generation directory with no readable manifest → `generation-unreadable`, with the selection's
own sentence kept under its own key and absent from `detail`.

**Bite-proof.** Pre-fix: `E AssertionError: {... 'detail': 'no comparison generation is published for
260921-icr-l1, so there is no reviewed generation to record a final output against'}` — R21's sentence
under an R22 key (same log). **Mutation MF3** (selection state/detail published as the block's) → **test FAILS**.

## F4 (low) — the reader observes the location

**Change.** `read_review_sync_rebinding`'s absence sentence now says *"nothing is recorded at &lt;path&gt; for
comparison generation &lt;id&gt;; a record this leaf's own reclamation discarded and one that was never written
read the same here"*, and both `read_review_sync_rebindings` and `discard_review_sync_rebindings` state in
their docstrings that they are the **acceptance/curation route** with no mounted caller today — the debt,
named with its consumer, rather than an implied one.

**Case.** `test_the_reader_observes_the_location_after_its_own_reclamation`: record → discard → read, and
assert the detail names the generation and the location, contains `discarded`, and does **not** contain
`no managed sync has measured`.

**Bite-proof.** Pre-fix: `E AssertionError: no managed sync has measured comparison generation …` (same
log). **Mutation MF4** (old sentence restored) → **test FAILS**.

## F6 (medium, ruled the packet's clause) — the live review read renders the movement

**Change — three parts, seam policy followed.**

1. **The measurement is read and rendered on the live read.** New
   `mcp/src/agents_remember/application/review_sync_movement.py` (206 L) owns it: it selects the leaf's
   published generation, reads its durable rebinding, accepts it only when
   `rebinding_names_the_generation` proves it describes *that* generation, and projects it into the
   review's own measured-currentness value. Every way of not measuring — no contract, no generation, no
   record, a record describing another generation, any read error — answers `None`, because the live read
   must never fail on a measurement, and "nothing is recorded" is not "a sync measured agreement".
2. **The vocabulary is R15's and the identities are R21's/R22's.** New
   `mcp/src/agents_remember/models/knowledge/review_staleness.py` (166 L) owns `ReviewStaleness` (R17),
   `ReviewSubmission` and the new `ReviewSyncMovement`: `binding_state: current|stale` **following from**
   `moved_identities` (both directions refused by the validator), plus `record_readable`,
   `reuse_permitted`, `reinterpreted_for_new_inputs` with R15's meanings, and the rebinding's own
   identities — generation id/index, the reviewed binding digest, the reviewed candidate tree and
   knowledge digest, and the resolved head/tree/dataset, each named exactly on the channel that moved.
   The failure path the payload can render (`stale` with no resolved identity) is refused by construction.
3. **The fold, and the thin adapter.** `review_staleness_with_sync_movement` (same new module) folds a
   measured movement into the published staleness: a stale movement **outranks** the reader's carried
   identity and yields `stale` with the movement's sentence, its moved identities, and a real previous
   input — so the payload's own constructor disables submission. `application/knowledge_review.py` gained
   **+14/-1 lines and no feature logic**: one import, `sync_movement = review_sync_movement(resolved)`,
   the fold call, and the `sync_movement=` payload field.

**Model-module extraction (required before the payload could be extended).**
`mcp/src/agents_remember/models/knowledge/review.py` was **1198 lines against the 1200 rail**, so the three
fields could not be added there. `ReviewStaleness` + `ReviewSubmission` were extracted whole into
`models/knowledge/review_staleness.py` with the payload module re-exporting every name (`__all__` intact,
importers unchanged — `review_comparison_staleness.py` needed no edit at all): **1198 → 1164**, and the new
`sync_movement` field fits. One implementation per rule; the new module's docstring records the move.

**Case.** `test_the_live_review_read_renders_what_the_sync_moved` drives the **shipped**
`read_knowledge_review` three times: (1) before any sync → `sync_movement is None`, staleness untouched;
(2) after a sync that carried the official line *without* changing the capture → `binding_state: current`,
no moved identities; (3) after a sync that moved the reviewed capture → the read renders
`binding_state: stale`, the moved identity names the reviewed candidate tree, the resolved head and tree
are the store's own, the comparison the read composed is provably **not** the recorded generation, the
staleness is `stale` with the movement's identities and the generation's binding digest as the previous
input, the statement says the recorded generation does not describe it, and submission is
`disabled_stale`.

**Bite-proof.** Pre-fix (round-0 adapter, no measurement on the read path) the case cannot even be
expressed — the round-0 payload has no `sync_movement`, which is F6's finding. Post-fix it passes.
**Mutations:** MF6a (the adapter stops folding the movement) → **test FAILS**; MF6b (the read stops
measuring) → **test FAILS**; MF6c (a stale movement with no resolved head) → **test FAILS**.

**Clause-by-clause on the rest of F6:** *"invalidate moved review inputs"* — the moved inputs are now
rendered as moved on the live read and submission against them is refused; the recorded generation is
never rewritten, and the successor-generation remedy stays the ownership of the freeze owner (naming it,
never performing it), which is what the packet's "no parallel review authority" requires. *"bind the
resolved pair"* — the record already carries and verifies the resolved identities against the store
(verifier §3.2), and the live read now publishes them.

## F7 (low, pre-existing, not this leaf) — recorded, not fixed

`mcp/src/agents_remember/worktrees/sync_transaction_git.py::park_worktree_wip` (:169-188) refuses when the
worktree holds an untracked `.gitignore`: `git stash push --include-untracked` removes it, the directory it
ignored becomes visible, and `git_status` then reports the worktree dirty. Untouched by this leaf (base
code, byte-identical), reproduced independently by the verifier, and it bounds the WIP clause on such a
leaf. **Routed debt**, with the verifier's own evidence and the suggested fix (compare status under the
same pathspec the stash used) — the leaf that owns the sync owner should take it; L23 (raw-Git boundary)
and R25 are the candidates named in the master's map.

## Fix-round mutation matrix (final, against the frozen bytes)

```
MF1a blanket 'not a completed sync' for every non-carrying state: rc=1 :: 1 failed :: test FAILS (load-bearing)
MF1b already-current measured as a carrying state:                rc=1 :: 1 failed :: test FAILS (load-bearing)
MF1c the removed codeBaseCommit conjunct restored:                rc=0 :: 1 passed :: NOT load-bearing (reported)
MF2  the old 'head carries candidate tree' wording:               rc=1 :: 1 failed :: test FAILS (load-bearing)
MF3  the selection's own state and sentence published:            rc=1 :: 1 failed :: test FAILS (load-bearing)
MF4  the old reader absence sentence:                            rc=1 :: 1 failed :: test FAILS (load-bearing)
MF6a the adapter not folding the measured movement:               rc=1 :: 1 failed :: test FAILS (load-bearing)
MF6b the live read not measuring at all:                          rc=1 :: 1 failed :: test FAILS (load-bearing)
MF6c a movement that claims movement without the resolved head:   rc=1 :: 1 failed :: test FAILS (load-bearing)
restored
```

Every mutation was reverted and the candidate re-verified byte-identical to its pre-mutation copy for all
seven touched production/test files (`cmp` per file: SAME).

## Fix-round re-runs (exact results)

```
pytest mcp/tests/test_review_sync_rebinding.py -q -m '' -p no:randomly                  →  8 passed
pytest mcp/tests/test_review_sync_rebinding.py mcp/tests/test_worktree_sync.py …       → 16 passed, 9 subtests
pytest <the 16 affected review modules> -q -m '' -p no:randomly                        → 246 passed, 9 subtests in 130.80s
pytest mcp/tests/test_dependency_ownership_ast_helpers.py test_evidence_lanes.py test_file_size_detector.py -q -m ''
                                                                                       →  7 passed in 55.30s  (16 contracts / 66 artifacts)
ruff check <10 touched files>                                                          → All checks passed!
ruff format --check <10 touched files>                                                 → 10 files already formatted
pyright --pythonpath …/mcp/.venv/bin/python <10 touched files>                         → 0 errors, 0 warnings, 0 informations
new # noqa in the new modules / new test module                                        → 0
unit population     2701 collected (budget 4000)      — unchanged by this round
integration population 444 collected (budget 1000)    — 440 + the 4 net-new cases
whole-tree .py > 1200: 26 (base) → 26 (candidate)     — no new offender
```

## Fix-round file sizes (every touched file)

| File | Base | Round 0 | Fix round 1 |
| --- | ---: | ---: | ---: |
| `application/review_sync_rebinding.py` (new) | — | 599 | **716** |
| `application/review_sync_movement.py` (new) | — | — | **206** |
| `models/knowledge/review_sync_rebinding.py` (new) | — | 381 | **390** |
| `models/knowledge/review_staleness.py` (new, extracted) | — | — | **166** |
| `models/knowledge/review.py` | 1198 (at the rail) | 1198 | **1164** (extraction made room) |
| `application/knowledge_review.py` (adapter) | 1041 | 1041 | **1054** (+14/-1, delegation only) |
| `application/review_comparison_reopen.py` | 730 | 778 | **778** |
| `application/review_comparison_staleness.py` | 97 | 97 | **97** (untouched — the fold lives in the new module) |
| `application/worktree_tools.py` | 1030 | 1035 | **1035** |
| `mcp/tests/test_review_sync_rebinding.py` (new) | — | — | **1037** |
| `mcp/tests/test_worktree_sync.py` | 937 | 1678 | **945** |

## Fix-round fingerprint — computed from the frozen bytes only

As the master noted, the round-0 report first declared hashes that were **not** the delivered candidate
(the model module was momentarily in a mutation-applied state while the mutation matrix ran). This
fingerprint was computed **after** the last write to any candidate file, from a tree in which every
mutation had been reverted and verified (`cmp`), and it excludes only two files that are not candidate
bytes: this report and the verifier's own verdict document.

```
tracked diff  sha256   4a1bb68c4f1c1cef0f2480f0c035965e8c5b2c7e319e213858c75dd1b62112e3
git status --porcelain entries (report + verify-l22.md excluded)   18
combined      sha256   9a6ff17573a7f19be3136670ea382513298f85d6bcce7941f092c1a3d5f3b196
```

Combined = `sha256( git diff | sha256sum ; git status --porcelain (sorted, the two excluded) ; sha256sum of
every untracked file (sorted, the two excluded) )`, per the L21 fingerprint correction.

```
 M mcp/src/agents_remember/application/knowledge_review.py
 M mcp/src/agents_remember/application/review_comparison_reopen.py
 M mcp/src/agents_remember/application/worktree_tools.py
 M mcp/src/agents_remember/models/knowledge/review.py
 M mcp/tests/evidence-lifecycle.toml
 M mcp/tests/test-evidence-lanes.toml
 M mcp/tests/test_dependency_ownership_ast_helpers.py
 M mcp/tests/test_worktree_sync.py
?? mcp/src/agents_remember/application/review_sync_movement.py       baa0282bc9faa0dff9416d5b835a009c21603430effa0ee2aed5dcf0d4d87b82
?? mcp/src/agents_remember/application/review_sync_rebinding.py      c3d3441cc472148dc075fee0a3dcfc515de599b6f764480afc01ae7fbadbe193
?? mcp/src/agents_remember/models/knowledge/review_staleness.py      7b6ebea7c076bccc22229b8105a1b8333c9b77ead44f5404c4e19d2349011e0f
?? mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py 39ef099955b1963c173bf59f6ec845f9a59e38b0f367f23ba85ed4fc282c68e2
?? mcp/tests/test_review_sync_rebinding.py                           0ed322203c5359db49f25f0a1104c1ba59e1b21d89c406cef440411a18dff66e
?? temp/icr/base-defect-l22-base.txt                                 da9bda48cd26aa9704930ef4864e6a442fa085abb45ecf1eecb14c18d3a6f2c8
?? temp/icr/base-defect-l22-candidate.txt                            486db4766950d4515756ee7588585a7d22ffced09f254ad6d9d3629115a64b83
?? temp/icr/fix-round-1-prefix-failures-l22.txt                      5135535df7cf1e2dd974afecd6ad6e9db4a09f877ac587b00e39d7be5a6b37a2
?? temp/icr/mutations-fix-round-1-l22.py                             ab77cbf0b0769114ee3dc36a4dc158ab09ab7476b6496ef82175285e229fbf11
?? temp/icr/probe-l22-base-defect.py                                 c5ec98ebe7c12257fe5706fcd8fea1959444944cbe088fda44df8f95d1f00b18
   (temp/icr/report-l22.md — this file — and temp/icr/verify-l22.md — the verifier's — are excluded)
```

## What is still open after fix round 1

1. **No case authors a `resolution_action="reconcile"` decision and then reads a rebinding** (round-0 §8.2,
   confirmed by the verifier). The reconcile leg ends in the same completion path, but that is structural.
2. **`sync-pass-completed-source-moved-again` is in the carrying set by construction, not by test.**
3. **The `unmeasured` verdict has no case** (round-0 §8.5) — the fixtures always publish; the model guards
   and the base-defect probe reach it.
4. **F7 is routed, not fixed** (above).
5. **The soft-band census is 109 rather than 108**: the new test module (1037 lines) is itself in the
   900–1200 band. No gate counts it, the ruling's condition was the ≥1200 count (back to 26), and the
   alternative was three modules for one leaf's evidence.
6. **Class-3/browser and the assembled A19/A20 rows remain L25's**, unchanged from round 0.

---

# Fix round 2 (verdict `fail`; G1 blocking — all three findings addressed in ONE round)

Round-2 verdict read in full first: `temp/icr/verify-l22-round2.md` (313 lines, sha256
`b3f5878ad201a541d17b6d7b71ba17bd19b06613ed70d50944467503382abe19`). It confirmed F1–F5 and the seam
extraction as closed, reproduced the round-1 tracked diff and all five untracked module hashes exactly, and
filed **G1 (blocking)**, G2 and G3. It also recorded (correctly) that it could not reproduce the round-1
*combined* scalar `9a6ff175…` because the recipe was never written down — the recipe is given literally at
the end of this section, with every component printed.

## G1 (blocking) — the movement state now follows the record's own verdict

**The defect.** `_project` derived the moved set only from `== "differs-from-reviewed-input"`, so a record
whose own verdict was `unmeasured` collapsed into `binding_state: "current"` and the live read rendered the
agreement sentence *"…and the reviewed dataset are the ones the leaf holds"* beside R17's `current` — an
unmeasured channel rendered as a measurement, on the surface the F6 ruling named.

**Change — `mcp/src/agents_remember/application/review_sync_movement.py`:**

* `_MOVEMENT_STATES` (:75) maps the record's **three** verdicts to the movement's states —
  `current→current`, `moved→stale`, `unmeasured→not-measured` — and the projection (`_project`, :165) takes
  its state from that table, never from the channel matches. There is no two-way reading left to collapse.
* `_unmeasured_reason` (:216) builds the absence's reason from the record's own values: the channel's match,
  the declared location's state and that location's own sentence (and the `not-recorded`/`not-selected`
  generation case says only that no dataset was compared).
* `_statement` (:237) now has one sentence per state, and `_measured_clause` (:284) — the agreement clause —
  is reachable **only** from `current`, naming only the channels that were compared and matched (a
  generation that retained no knowledge operand says exactly that instead).
* `_measured` (:108) distinguishes "nothing has measured this generation" (`None`) from "a record is there
  and cannot be used" → `_unavailable` (:138): unreadable bytes, and a valid record that does not describe
  this generation, are now two facts inside one named state instead of a silent absence.

**Change — `mcp/src/agents_remember/models/knowledge/review_staleness.py`:** `ReviewSyncMovementState`
(:48) is R15's vocabulary at its own spellings — `current` / `stale` / `not-measured` / `unavailable`
(`FindingCurrentness.binding_state` plus the collection-level `not-measured`/`unavailable` that
`family_review.py` already publishes). `ReviewSyncMovement` (:85) gains `reason` (:115) and a four-state
validator (:132) that refuses, in every direction: a named moved input that is not `stale`; a `stale` with
nothing moved; an absence with no reason; a measurement carrying a reason; `record_readable` disagreeing
with `unavailable`; and any resolved identity on a non-`stale` state.

**Cases — `mcp/tests/test_review_sync_rebinding.py` (8 → 11 cases):**

| Case | State | What it pins |
| --- | --- | --- |
| `test_an_uncompared_knowledge_channel_is_rendered_unmeasured` (:887) | `not-measured` | the exact G1 reproduction, driven through the **shipped** `read_knowledge_review`: no publication at the declared location, a retained knowledge operand, `binding_state == "not-measured"`, `moved_identities == ()`, the record's own reason (`not-recorded`) named, **no** agreement clause and specifically no "the reviewed dataset"; plus three forged movements the validator must refuse |
| `test_a_record_that_cannot_be_used_is_reported_unavailable` (:952) | `unavailable` | (a) junk bytes → `record_readable is False`, reason names the unreadable record; (b) a valid record naming another comparison → reason says it does not describe this generation; neither renders an agreement or a movement |
| `test_a_carrying_state_reported_as_a_failure_is_not_measured` (:1002) | G2 | the predicate and the block on a real carrying payload with `ok: false` |

The R17 interaction the ruling asked about is asserted in the G1 case: the movement carries the absence
(the payload's `sync_movement` is present and `not-measured`), no movement is fabricated
(`moved_identities == ()`), no agreement clause exists anywhere in the movement, and R17's `staleness`
stays its own fact about the comparison this read composed — the movement is a *separate* field, so the
absence cannot be read as an upgrade of the review to current.

**Bite-proof (the case fails on the pre-fix bytes).** The round-1 graded module was preserved as
`/home/firefox/projects/ar-coordination/temp/l22/pristine/app/review_sync_movement.py`, sha256
`baa0282bc9faa0dff9416d5b835a009c21603430effa0ee2aed5dcf0d4d87b82` — the hash round 2 graded. Restored in
place and run (`temp/icr/fix-round-2-g1-bite-proof-l22.txt`):

```
$ cp <pre-fix module> mcp/src/agents_remember/application/review_sync_movement.py
$ pytest mcp/tests/test_review_sync_rebinding.py -q -m '' -p no:randomly -k uncompared_knowledge_channel
>           assert movement.binding_state == "not-measured", movement
E           AssertionError: ReviewSyncMovement(binding_state='current', moved_identities=(), reason=None,
            statement='a managed sync completed and …', record_readable=True, …)
E           assert 'current' == 'not-measured'
1 failed
```

Post-fix the module is restored (`92ab515fe6da27a992cab15d0e4d19d9ef521ffc9509abcf0b94b38ae86ebf8f`) and the
case passes.

**Mutations — `temp/icr/mutations-fix-round-2-l22.py` (every one load-bearing):**

```
MG1a the two-way reading: 'unmeasured' mapped to current      rc=1 :: 1 failed :: test FAILS
MG1b the unmeasured state rendered with the agreement sentence rc=1 :: 1 failed :: test FAILS
MG1c an unreadable record collapsed into 'nothing recorded'    rc=1 :: 1 failed :: test FAILS
MG1d the cross-check bypassed for a record naming another generation rc=1 :: 1 failed :: test FAILS
MG1e the absence-without-a-reason rule removed                 rc=1 :: 1 failed :: test FAILS
MG1f the moved-input-implies-stale rule removed                rc=1 :: 1 failed :: test FAILS
MG2  the success conjunct removed again                        rc=1 :: 1 failed :: test FAILS
restored
```

The second rendering state the verifier flagged as unreproduced — a generation recording
`not-recorded`/`not-selected` for its knowledge operand — is now handled **by construction and by
sentence**: `_unmeasured_reason` names that state explicitly, and `_measured_clause` refuses to claim a
dataset agreement for it. It is covered by the code path, not by a case (see the limits below).

## G2 (low) — the success conjunct restored

`resolved_pair_completed` (`application/review_sync_rebinding.py:196`) is three facts again: the operation,
`bool(payload.get("ok"))`, and the carrying state — with the docstring stating why the conjunct is carried
even though every producer of those three states returns zero today. A carrying state beside a failed
result now gets its own honest sentence in `_nothing_to_bind_block` (:565): *"the sync reported … with a
failed result, so its pair is not measured and no review rebinding is recorded for it"*, rather than being
described as a state this tool never measures. Case: `test_a_carrying_state_reported_as_a_failure_is_not_measured`
(:1002) — the predicate is `True` for the real payload and `False` for the same payload with `ok: false`,
the block reports `not-measured`, and no durable file is written. **MG2 → test FAILS.**

## G3 (low) — recorded, not fixed

`read_review_sync_rebindings` and `discard_review_sync_rebindings` still have no mounted caller. Both
docstrings say so plainly and name the route that will consume them (**ICR-R25's recorded-comparison
acceptance journey, or the curator draining a leaf's history**), and this report records it as a **routed
debt with its named consumer** — the same disposition L21 recorded for R21's receipt reader. The F4
sentence defect itself stays closed.

## Housekeeping — the duplicated comment block

The curator was right: the F5 split left two copies of a four-line `# ICR-R22@v1:` comment in
`mcp/tests/test_worktree_sync.py` (lines 68-75). Both copies described cases that now live in the sibling
module, so **both** were removed rather than one — and the result is stronger than the ask: with the R22
cases, their fixture, their helpers, their constants and now their stale comment gone,
`mcp/tests/test_worktree_sync.py` is **byte-identical to its base bytes** (`git diff --quiet` exits 0,
937 → 937 lines). This leaf no longer touches that module at all, and the census derives no consumer change
from it.

## Fix-round-2 re-runs (exact results)

```
pytest mcp/tests/test_review_sync_rebinding.py -q -m '' -p no:randomly           → 11 passed
pytest mcp/tests/test_review_sync_rebinding.py mcp/tests/test_worktree_sync.py \
       mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py \
       mcp/tests/test_file_size_detector.py -q -m '' -p no:randomly              → 26 passed, 9 subtests in 64.52s
pytest <18 affected review/transport modules> -q -m '' -p no:randomly            → 266 passed, 22 subtests in 176.06s
pytest mcp/tests/test_dependency_ownership_ast_helpers.py -q -m ''               →  2 passed  (no findings: the sibling is base-identical)
ruff check / ruff format --check <9 touched files>                               → All checks passed! / 9 files already formatted
pyright --pythonpath …/mcp/.venv/bin/python <9 touched files>                    → 0 errors, 0 warnings, 0 informations
new # noqa                                                                       → 0
unit population        2701 collected (budget 4000)     — unchanged
integration population  447 collected (budget 1000)     — 444 + the 3 new cases
whole-tree .py > 1200  26 (base) → 26 (candidate)       — no new offender
whole-tree .py >  900 108 (base) → 109 (candidate)      — the +1 is the new test module (1186 lines, under 1200)
```

## Fix-round-2 file sizes

| File | Base | Round 1 | Round 2 |
| --- | ---: | ---: | ---: |
| `application/review_sync_movement.py` (new) | — | 206 | **334** |
| `models/knowledge/review_staleness.py` (new, extracted) | — | 166 | **204** |
| `application/review_sync_rebinding.py` (new) | — | 716 | **733** |
| `models/knowledge/review_sync_rebinding.py` (new) | — | 390 | 390 |
| `models/knowledge/review.py` | 1198 | 1164 | 1164 |
| `application/knowledge_review.py` (adapter) | 1041 | 1054 | **1054** (unchanged: still +14/-1 delegation) |
| `application/review_comparison_staleness.py` | 97 | 97 | **97** (byte-untouched throughout) |
| `mcp/tests/test_review_sync_rebinding.py` (new) | — | 1037 | **1186** |
| `mcp/tests/test_worktree_sync.py` | 937 | 945 | **937 — byte-identical to base** |

## Fix-round-2 fingerprint — the recipe, literally, and every component

**Recipe** (`temp/icr/fingerprint-l22.sh`, run from the code worktree root; deterministic — run twice,
identical):

```sh
EXCLUDE='temp/icr/report-l22.md|temp/icr/verify-l22.*\.md'
{
  git diff | sha256sum
  git status --porcelain | grep -Ev "$EXCLUDE" | LC_ALL=C sort
  git status --porcelain | awk '$1=="??"{print $2}' | grep -Ev "$EXCLUDE" | LC_ALL=C sort | xargs -r sha256sum
} | sha256sum
```

Read literally: **component 1** is the sha256 of `git diff` as `sha256sum` prints it (`<hex>␣␣-`).
**component 2** is `git status --porcelain`, filtered to drop the two excluded inputs, sorted with
`LC_ALL=C`, one `XY␣<path>` line each. **component 3** takes the untracked (`??`) paths from that same
filtered list, in that same sorted order, and emits each file's own sha256 as `sha256sum` prints it
(`<hex>␣␣<path>`). The three components are concatenated with nothing between them but their own trailing
newlines — **no labels, no blank lines, no added separator** — and the sha256 of that byte stream is the
combined scalar. Excluded, and named explicitly: `temp/icr/report-l22.md` (this report) and
`temp/icr/verify-l22*.md` (the verifier's verdicts) — documents *about* the candidate, not candidate bytes.

**Component 1 — tracked diff:**
`a278a47c11721d9510984e2527de7a8a86702b5c19139dc090cb2530f72a6623`

**Component 2 — the filtered status list (20 entries):**

```
 M mcp/src/agents_remember/application/knowledge_review.py
 M mcp/src/agents_remember/application/review_comparison_reopen.py
 M mcp/src/agents_remember/application/worktree_tools.py
 M mcp/src/agents_remember/models/knowledge/review.py
 M mcp/tests/evidence-lifecycle.toml
 M mcp/tests/test-evidence-lanes.toml
 M mcp/tests/test_dependency_ownership_ast_helpers.py
?? mcp/src/agents_remember/application/review_sync_movement.py
?? mcp/src/agents_remember/application/review_sync_rebinding.py
?? mcp/src/agents_remember/models/knowledge/review_staleness.py
?? mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py
?? mcp/tests/test_review_sync_rebinding.py
?? temp/icr/base-defect-l22-base.txt
?? temp/icr/base-defect-l22-candidate.txt
?? temp/icr/fingerprint-l22.sh
?? temp/icr/fix-round-1-prefix-failures-l22.txt
?? temp/icr/fix-round-2-g1-bite-proof-l22.txt
?? temp/icr/mutations-fix-round-1-l22.py
?? temp/icr/mutations-fix-round-2-l22.py
?? temp/icr/probe-l22-base-defect.py
```

**Component 3 — each untracked file's own sha256, in that order:**

```
92ab515fe6da27a992cab15d0e4d19d9ef521ffc9509abcf0b94b38ae86ebf8f  mcp/src/agents_remember/application/review_sync_movement.py
3a036d2c98c178f700826ccb2435002bab6ca972312297dad81fac71c359c8ac  mcp/src/agents_remember/application/review_sync_rebinding.py
e73b58b51c4d1eaa577f761ba0e3995c72452732a47c0925780c1ca20e9fc494  mcp/src/agents_remember/models/knowledge/review_staleness.py
39ef099955b1963c173bf59f6ec845f9a59e38b0f367f23ba85ed4fc282c68e2  mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py
e8abefe6973dc19f62540302ca314036f26520a71abdc9a6056559f83add4c27  mcp/tests/test_review_sync_rebinding.py
da9bda48cd26aa9704930ef4864e6a442fa085abb45ecf1eecb14c18d3a6f2c8  temp/icr/base-defect-l22-base.txt
486db4766950d4515756ee7588585a7d22ffced09f254ad6d9d3629115a64b83  temp/icr/base-defect-l22-candidate.txt
87c5d24c68fa8b6d5bbe2deabb6c451a9a151948107d70cdfee90390b10bc998  temp/icr/fingerprint-l22.sh
5135535df7cf1e2dd974afecd6ad6e9db4a09f877ac587b00e39d7be5a6b37a2  temp/icr/fix-round-1-prefix-failures-l22.txt
2f99c22028aed1470f1be86036a706ae000552c435f4555f8f1b1e6ed73e8ffa  temp/icr/fix-round-2-g1-bite-proof-l22.txt
ab77cbf0b0769114ee3dc36a4dc158ab09ab7476b6496ef82175285e229fbf11  temp/icr/mutations-fix-round-1-l22.py
c5fb1daa189949d29e62c569b277d4e89616fdb0550aaa3cd7c932a3638c4b27  temp/icr/mutations-fix-round-2-l22.py
c5ec98ebe7c12257fe5706fcd8fea1959444944cbe088fda44df8f95d1f00b18  temp/icr/probe-l22-base-defect.py
```

**Combined (the three components' concatenation hashed, computed twice, identical):**
`3be9eb2c0b4fa20ff86b9b4b0fce4606f31424bbc89364cabbc3213d8feec45f`

This supersedes the round-1 scalar `9a6ff175…`, which was computed with the older exclusion pattern
(`temp/icr/verify-l22.md` only, so `verify-l22-round2.md` had since entered the list) — a further reason the
recipe is now written down and the script kept in the tree.

## What is still open after fix round 2

1. **No case authors a `resolution_action="reconcile"` decision and then reads a rebinding.**
2. **`sync-pass-completed-source-moved-again` is in the carrying set by construction, not by test.**
3. **A generation whose knowledge operand is `not-recorded`/`not-selected`** is handled by
   `_unmeasured_reason` and `_measured_clause` but has no case (the verifier flagged it as unreproduced in
   round 2 and it is now handled by construction, not pinned by a fixture).
4. **G3's two unmounted readers** — routed debt with the named consumer (R25's acceptance journey / curator
   history drain).
5. **F7 (pre-existing, not this leaf)** — `park_worktree_wip` refusing on an untracked `.gitignore`;
   routed to the sync owner, unchanged.
6. **Soft-band census 109 vs base 108** — the new test module itself; the hard-rail count the ruling
   required is back at 26.
7. **Class-3/browser and the assembled A19/A20 rows remain L25's.**

---

# Fix round 3 (verdict `pass-with-findings`, no blocking — H1 closed, H2 escalated, one rail forced)

Round-3 verdict read in full first: `temp/icr/verify-l22-round2b.md` (282 lines). It reproduced the
fingerprint **two ways** (the script, and an independent implementation of the stated recipe) with an exact
match on every component, held `3be9eb2c…` from before grading opened to the end reading, closed G1/G2/G3,
and filed **H1** and **H2**, both low. The H1 ruling was test-only. **H2 cannot be done without changing a
production byte, so per the round's hard constraint it is escalated rather than improvised — see below.**

## H1 (low) — the four previously-unpinned validator clauses are pinned

**Change — `mcp/tests/test_review_sync_movement_read.py::test_the_movement_validator_refuses_each_false_shape`.**
It takes the **real published** movement as the accepted control and builds four forgeries, each departing
from exactly **one** clause (so the refusal is that clause's and not a neighbour's):

| forgery | clause it isolates |
| --- | --- |
| `stale` naming an identity but with nothing moved | "a stale movement names the input that moved" |
| `unavailable` with `record_readable: True` | "`record_readable` follows from whether the record could be used" |
| `current` naming a resolved identity | "no resolved identity unless the state is `stale`" |
| `stale` with a moved identity but no resolved identity | "a stale movement names the identities the sync resolved" |

**Bite-proof — `temp/icr/mutations-fix-round-3-l22.py` disables each clause in turn:**

```
H1a stale requires something moved:      rc=1 :: 1 failed :: test FAILS (clause pinned)
H1b record_readable agrees with unavailable: rc=1 :: 1 failed :: test FAILS (clause pinned)
H1c no resolved identity unless stale:   rc=1 :: 1 failed :: test FAILS (clause pinned)
H1d stale names a resolved identity:     rc=1 :: 1 failed :: test FAILS (clause pinned)
restored
```

The verifier's own sweep recorded **11 passed** for every one of those four mutants; with this case in
place each turns red, and the model file was restored and re-hashed to `e73b58b5…` afterwards. The two
clauses round 3 found already covered (moved⇒stale, absence-needs-a-reason) stay covered by the G1 case,
which also forges a measurement carrying a reason.

## H2 (low) — ESCALATED, not applied: it needs a production byte

**The conflict.** H2's two options are to rename `record_readable` → `record_usable` or to narrow its
documented meaning. The field is `mcp/src/agents_remember/models/knowledge/review_staleness.py:127`, its
meaning is stated in that module's class docstring (:100-105, "carry the same three facts `ICR-R15@v1`
carries") and in the validator message at :168-171 ("only an unusable record is unreadable, and every other
state was read"), and the sentences that consume it are in
`application/review_sync_movement.py` (`_unavailable`, :138-152). **Both options are edits to production
files**, and this round's hard constraint says production bytes must not change — with the explicit
instruction: *"If any production byte has to change, STOP and tell me instead of changing it."* So I
stopped. (The round's own framing — "test-plus-docstring changes" — and that constraint cannot both hold,
because the docstring in question is production.)

**What I did instead, inside the constraint.** The `unavailable` case
(`test_a_record_that_cannot_be_used_is_reported_unavailable`) now pins the finding where it can be pinned
from the test side: both sub-cases — junk bytes, and a valid record naming another generation — are
asserted to report `record_readable is False`, with a comment recording that the sub-fact which separates
them is carried by `reason` and that the field-level fix awaits the ruling. So a consumer that branches on
the field alone is now visibly wrong in a test rather than silently wrong.

**The exact minimal patch, ready to apply on one word** (comment-only, no behaviour, no validator change;
it narrows the *claim* rather than the name, which is the smaller of H2's two options):

```python
# review_staleness.py, at the field (currently line 127)
    # False when the record could not be used for *this* generation: an unreadable artifact and a
    # valid record that describes another generation read the same here, and `reason` carries which
    # one it was.
    record_readable: bool = True
```
Applied, it would change `models/knowledge/review_staleness.py` from `e73b58b5…` and invalidate round 3's
per-file hash for that module (its behaviour is unchanged, so the *verdict* would still hold; only the
byte-level identity would move). The rename variant is larger: it also touches the validator prose, the
`_unavailable` construction and any consumer of the field.

## The rail forced one more extraction (H1 grew the test module past 1200)

Adding H1's case took `mcp/tests/test_review_sync_rebinding.py` to **1263 lines — a new ≥1200 offender
(26 → 27)**, which is exactly the condition the master's F5 ruling refuses. Per the rail's own doctrine the
remedy is extraction, not compression, so the five **read-side** cases and their module-level helper moved
into a new `mcp/tests/test_review_sync_movement_read.py`, which imports the enclosure fixture from its
sibling rather than duplicating it (the sibling-fixture pattern other case modules in this repository
already use):

| Module | Before | After |
| --- | ---: | ---: |
| `mcp/tests/test_review_sync_rebinding.py` | 1263 (**over**) | **942** |
| `mcp/tests/test_review_sync_movement_read.py` | — | **356** |
| whole-tree `.py` > 1200 | 27 | **26** ✓ (base value) |
| whole-tree `.py` > 900 | 109 | **109** (unchanged: the sibling is still in the band; the new module is not) |

Registration, derived from the census's own findings rather than guessed: the new module's lane row was
added to `mcp/tests/test-evidence-lanes.toml` beside its sibling in the integration lane, and the four
`consumer_scope="exact"` rows the census named (`node/package-lock.json`, `merge_case_test_support.py`,
`diff_scope_test_support.py`, `read_scope_test_support.py`) each reported
`missing=['mcp/tests/test_review_sync_movement_read.py'], unsupported=[]` and each gained that one path.
`LIFECYCLE_CATALOG_SHA256` re-pinned from `7c8c1646…` to
**`d07c2f9d456b0f658228c91aecb2a1f3da8d13e2b6575b6b40b0b0d2ca165f6b`** (fresh `sha256sum`), with the
Twenty-sixth re-pin note; population still **16 contracts / 66 artifacts**.

**I record the alternative I did not take**: trimming ~64 lines of prose from the module would have kept the
file under the rail *without* touching any tracked registration file. I chose extraction because the rail's
own rule is "the default action is extraction, not extension", because the new seam is the one production
already has (what the sync measured, beside what the read says), and because compressing the reasoning the
verifier twice called out would have been the worse trade. The cost is that three **test-registration**
tracked files moved (see the hash proof below) — no production file did.

## Production bytes: unchanged, proven file by file

Round 3 graded: untracked production modules `3a036d2c…` / `92ab515f…` / `39ef0999…` / `e73b58b5…`, aggregate
tracked diff `a278a47c…`. After fix round 3 (`temp/icr/production-hashes-l22.txt`):

```
## untracked production modules: working-tree sha256            (round-3 value in brackets)
3a036d2c98c178f700826ccb2435002bab6ca972312297dad81fac71c359c8ac  application/review_sync_rebinding.py   [3a036d2c ✓]
92ab515fe6da27a992cab15d0e4d19d9ef521ffc9509abcf0b94b38ae86ebf8f  application/review_sync_movement.py    [92ab515f ✓]
39ef099955b1963c173bf59f6ec845f9a59e38b0f367f23ba85ed4fc282c68e2  models/knowledge/review_sync_rebinding.py [39ef0999 ✓]
e73b58b51c4d1eaa577f761ba0e3995c72452732a47c0925780c1ca20e9fc494  models/knowledge/review_staleness.py   [e73b58b5 ✓]

## tracked production files: per-file diff hash (e3b0c442… = no diff at all)
9c6bd5376047f26e…  application/knowledge_review.py            [9c6bd537 ✓ unchanged]
94b3d4f33f49c0e0…  application/worktree_tools.py              [94b3d4f3 ✓ unchanged]
4b0d37a3859b89f0…  application/review_comparison_reopen.py    [4b0d37a3 ✓ unchanged]
3123cf0f20e05246…  models/knowledge/review.py                 [3123cf0f ✓ unchanged]
e3b0c44298fc1c14…  application/review_comparison_staleness.py  [empty = byte-untouched ✓]

## tracked files whose diff DID change in fix round 3 — TEST REGISTRATION ONLY
207eb2671d00e203…  mcp/tests/evidence-lifecycle.toml                    (the four derived consumer rows)
6e26289e27c87803…  mcp/tests/test-evidence-lanes.toml                    (the new module's lane row)
1b404880b73aaabf…  mcp/tests/test_dependency_ownership_ast_helpers.py    (the Twenty-sixth re-pin)
```

**So: every production byte is identical to what round 3 graded; the aggregate tracked diff moved
(`a278a47c…` → `e4dd8b55…`) only through the three test-registration files the split made unavoidable.**
Round 3's verdict remains valid for the production modules it graded, and the delta is enumerable to three
files by name.

## Fix-round-3 re-runs (exact, with the module list this time)

The round-3 verdict recorded one `unreproduced` figure — my earlier "18 affected review/transport modules"
was not enumerated. It is now: the list is saved at `temp/icr/affected-modules-l22.txt` (19 paths) and is
exactly the round-2 eighteen plus the new `test_review_sync_movement_read.py`.

```
pytest <the 19 enumerated modules> + <census trio> -q -m '' -p no:randomly   → 274 passed, 22 subtests in 183.40s
pytest mcp/tests/test_review_sync_rebinding.py mcp/tests/test_review_sync_movement_read.py -q -m ''
                                                                             →  12 passed
pytest mcp/tests/test_dependency_ownership_ast_helpers.py -q -m ''           →   2 passed, no findings
                                                                                (after the four derived rows + pin)
ruff check / ruff format --check <10 touched files>                          → All checks passed! / 10 files already formatted
pyright --pythonpath …/mcp/.venv/bin/python <10 touched files>               → 0 errors, 0 warnings, 0 informations
new # noqa                                                                   → 0
unit population        2701 collected (budget 4000)     — unchanged
integration population 448 collected (budget 1000)      — 447 + the extracted module's own collection
whole-tree .py > 1200  26 (base) → 26 (candidate)       — back to the base count
whole-tree .py > 900  108 (base) → 109 (candidate)      — the sibling module, in the band by 42 lines
```

## Fix-round-3 fingerprint (same stated recipe, `temp/icr/fingerprint-l22.sh`)

Component 1, the filtered status list and each untracked file's own hash are all printed by the script; the
exclusions are unchanged (`temp/icr/report-l22.md`, `temp/icr/verify-l22*.md`). Computed twice after the
last write to the tree, identical both times:

```
component 1 (tracked diff)   e4dd8b55115e903a69113773b0df35dd73ed3e1e7a8d3428647979d14538a45c
combined                     f599945f321926634b1dba71cda41512472fbc01e95dd27ad860a74c6b16c6af
```

The combined value differs from round 3's `3be9eb2c…` because component 1 moved for the three
test-registration files named above and because the set of untracked files gained the extracted module, the
round-3 artefacts and the production-hash proof — not because any production byte changed.

## What remains open after fix round 3

1. **H2's field-level fix** — needs one production byte; escalated with the exact patch, awaiting the
   ruling.
2. **No case authors a `resolution_action="reconcile"` decision and then reads a rebinding.**
3. **`sync-pass-completed-source-moved-again`** is in the carrying set by construction, not by test.
4. **A generation recording `not-recorded`/`not-selected`** for its knowledge operand is handled by
   `_unmeasured_reason` and `_measured_clause` but has no fixture.
5. **G3's two unmounted readers** — routed debt with the named consumer (R25's acceptance journey /
   curator history drain).
6. **F7 (pre-existing, not this leaf)** — `park_worktree_wip` refusing on an untracked `.gitignore`;
   routed to the sync owner.
7. **Class-3/browser and the assembled A19/A20 rows remain L25's.**
