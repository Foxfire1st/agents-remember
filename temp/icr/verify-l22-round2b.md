# Independent adversarial verification — leaf L22 / ICR-R22@v1 — ROUND 3 (fix round 2)

**Verifier.** Same independent adversarial verifier; no part in the implementation. Every claim below was
reproduced by me on the round-2 frozen bytes. The rules I call load-bearing were disabled one at a time in my
own scratch copy, the attack re-run on each mutant, and every file restored and re-hashed.

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22`,
branch `ar/260921-icr-l22`, base `e605822eb3bf83bf63a45963c5f51d5fc28859ee`, plus the worker's
uncommitted fix-round-2 delta. Packet `ICR-R22-v1-managed-git-recovery-rebinding.md`; report
`temp/icr/report-l22.md` §"Fix round 2" (claims checked, never treated as evidence). Harnesses reused from
rounds 1–2 under `/home/firefox/projects/ar-coordination/temp/verify-l22/`.

---

## Fingerprints — the stated recipe reproduces byte for byte

The round-2 gap (an unstated concatenation convention) is closed: `temp/icr/fingerprint-l22.sh` states the
recipe literally, and I recomputed it two ways — by executing their script, and by implementing the stated
recipe myself from the prose.

| | component 1 (sha256 of `git diff`) | combined | filtered status entries |
| --- | --- | --- | --- |
| **declared** | `a278a47c11721d9510984e2527de7a8a86702b5c19139dc090cb2530f72a6623` | `3be9eb2c0b4fa20ff86b9b4b0fce4606f31424bbc89364cabbc3213d8feec45f` | 20 |
| **their script, executed** | `a278a47c…` | `3be9eb2c…` | — |
| **my independent implementation of the stated recipe** | `a278a47c…` | `3be9eb2c…` | 20 |

**Exact match on every component — no divergence to report.** The recipe is deterministic (I ran it twice),
its three components are printed in the report, and the exclusions are named (`temp/icr/report-l22.md` and
`temp/icr/verify-l22*.md` — documents about the candidate). Raw `git status --porcelain` is 23 entries;
20 after those exclusions, as declared.

**Stability.** The fingerprint was sampled every 30 s from 14:10:41; it held `3be9eb2c…` for 150+ s before
grading opened, and the **end reading at 14:39:54 is identical** (`3be9eb2c…`, component 1 `a278a47c…`,
20 entries) after every probe, mutation, census and regression run below. **The candidate did not move.**

Untracked candidate hashes at both ends (identical):

```
3a036d2c98c178f700826ccb2435002bab6ca972312297dad81fac71c359c8ac  application/review_sync_rebinding.py
92ab515fe6da27a992cab15d0e4d19d9ef521ffc9509abcf0b94b38ae86ebf8f  application/review_sync_movement.py
e73b58b51c4d1eaa577f761ba0e3995c72452732a47c0925780c1ca20e9fc494  models/knowledge/review_staleness.py
39ef099955b1963c173bf59f6ec845f9a59e38b0f367f23ba85ed4fc282c68e2  models/knowledge/review_sync_rebinding.py
e8abefe6973dc19f62540302ca314036f26520a71abdc9a6056559f83add4c27  tests/test_review_sync_rebinding.py
```

---

## 1. G1 — **closed**

Re-driven through the **shipped** `read_knowledge_review` on my round-2 reproduction scenario (retained
knowledge operand, **no** publication at the declared location, official line carried without changing the
reviewed capture):

```
record state        : unmeasured
record knowledge    : unmeasured
location state      : not-recorded
movement state      : not-measured                      (was `current`)
moved_identities    : ()                                 (no fabricated movement)
reason              : "the retained knowledge operand was not compared (knowledge_match unmeasured): the
                       declared publication location is not-recorded, and no published knowledge dataset is
                       recorded for <repo> at <path>, so no recorded intent was read …"
statement           : "… does not fully describe the pair it resolved: <reason>. Only the part that was
                       compared is reported as measured, and the rest is unmeasured; <supersession>"
agreement clause?   : False          ("…are the ones the leaf holds" absent)
"reviewed dataset"? : False
R17 staleness       : current        (its own fact about the comparison this read composed)
submission          : unavailable    (display-only surface)
```
No agreement clause anywhere in the movement, the absence is carried with the record's own reason, and R17's
`staleness`/`submission` are neither upgraded nor fabricated — the movement is a separate field, and it is
`stale` (not `not-measured`) only when an input measurably moved, which is the round-2 moved case already
verified.

**Validator — every claimed refusal fires** (`ReviewSyncMovement.model_validate`, my probe 7c):

| combination | result |
| --- | --- |
| absence with no reason (`reason=None`) | REFUSED |
| measurement carrying a reason | REFUSED |
| moved input that is not `stale` | REFUSED |
| `stale` with nothing moved | REFUSED |
| `record_readable` disagreeing with `unavailable` | REFUSED |
| resolved identity on a non-`stale` state | REFUSED |
| valid `not-measured` (control) | ACCEPTED |

**Mutations I ran myself (rule 4).** Each applied to my scratch copy, attack re-run, file restored and
re-hashed to its frozen sha256:

* **MT1** `_MOVEMENT_STATES["unmeasured"] = "current"` (the round-1 collapse restored) → the delivery's case
  `test_an_uncompared_knowledge_channel_is_rendered_unmeasured` **FAILS** and my probe renders
  `movement state: current`. The mapping rule is load-bearing.
* **MT2** the absence-needs-a-reason clause disabled → the delivery's case **FAILS** and my probe prints
  `absence with no reason (reason=None) -> ACCEPTED`.
* **MT3** the resolved-identity-on-a-non-`stale` clause disabled → my probe prints
  `resolved identity on a non-stale state -> ACCEPTED`, but the delivery's suite is **green** (see H1).

**`unavailable` driven independently** (my probe 8, shipped read): junk bytes at the record's location →
`unavailable`, `record_readable: False`, reason names the unreadable record; a valid record copied to the
wrong generation's file name → `unavailable`, reason says it does not describe this generation. Both render
**no** agreement clause, and both are distinct from "nothing recorded", which round 2 verified as
`movement is None`. The five facts stay five.

**The second rendering state from round 2 is handled by construction**: `_measured_clause` names only the
channels that were compared and matched, and for a generation that retained no knowledge operand it says
"the generation records <state> for its knowledge operand, so no dataset was compared to a reviewed one".
I did not drive a fixture whose manifest after side is `not-recorded`/`not-selected` — **`unreproduced`**, and
I say so rather than implying a pass; the guard is read-verified in both `_measured_clause` and
`_unmeasured_reason`.

## 2. G2 — **closed**

```
genuine completion   : state sync-pass-completed-memory-skipped, ok True
                       -> resolved_pair_completed True, block {'state': 'moved', ...}   (measures)
same payload, ok:false -> resolved_pair_completed False
                       -> block {'state': 'not-measured', 'detail': "the sync reported
                          'sync-pass-completed-memory-skipped' with a failed result, so its pair is not
                          measured and no review rebinding is recorded for it"}
```
The sentence is true about the store, the carrying-but-failed state is honestly not measured, and no durable
file is written (delivery case asserts it; my probe confirms the block).

## 3. G3 — the disclosure is sufficient

Both readers' docstrings now state the debt and name the consumer concretely:
`read_review_sync_rebindings` — *"This is the acceptance and curation route, not a shipped one. No mounted
tool calls it today: the per-generation reader is reached in production from the reopen owner …, and this
leaf-wide form is what an acceptance reader (ICR-R25's recorded-comparison journey) or a curator draining a
leaf's history holds."*; `discard_review_sync_rebindings` — *"No mounted tool calls this yet … the route
that will drain them is the acceptance/curation one. Until it does, a discarded record and one that was
never written read the same at the location — which is what the reader's sentence says rather than claiming
no sync ever measured the generation."* `grep -rn` over `mcp/src` confirms there is still no mounted caller.
Named route, named consumer, and the honest statement that it is unmounted: sufficient for the low I graded.

## 4. Housekeeping — verified, and it changes the ownership as stated

```
git diff --quiet -- mcp/tests/test_worktree_sync.py   -> exit 0 (unmodified vs HEAD)
wc -l                                                  -> 937
sha256sum <worktree file> == git show HEAD:<file> | sha256sum
                                                       -> 84672f0edd8208d8a0172dee29d6cda8a468d768098ef5c422918a8ac7cd3510
                                                       -   (both sides)
```
**Measured, not asserted: `mcp/tests/test_worktree_sync.py` is byte-identical to its base bytes and is no
longer in the modified list at all.** This leaf now touches that module not once, so the R22 coverage lives
entirely in `mcp/tests/test_review_sync_rebinding.py` (1186 L, 11 cases) — a real ownership change, recorded
here. The rest of the split still holds:

```
new module in its lane        integration lane, tomllib-verified
pin                           sha256sum mcp/tests/evidence-lifecycle.toml = 7c8c1646272ca4a17f87a16ecbe105a4aea558c0e0d0c4d62b1883d6d69e683c
                              LIFECYCLE_CATALOG_SHA256                   = 7c8c1646…  (match)
>=1200 census                 26 (base 26)             soft band 109 (base 108; the +1 is the new module)
population                    16 contracts / 66 artifacts (census trio 7 passed)
moved cases on base bytes     pristine base + the module -> ERROR during collection
                              (ModuleNotFoundError: No module named 'agents_remember.application.review_sync_rebinding')
```
And my behavioural base-defect witness re-run on these bytes: base records **no** `review_rebinding` while
the capture moves `4bca9de2…` → `c1ae10f1…`; the candidate records `moved` with both identities.

## 5. Regression and rails (my own runs, frozen bytes)

```
delivery module            tests/test_review_sync_rebinding.py -q -m ''   -> 11 passed
my whole probe suite       8 probe modules (rounds 0-3)                  -> 19 passed
derived affected set       20 modules importing the changed vocabulary   -> 314 passed
all review modules         tests/test_*review*.py                        -> 343 passed in 255.21s
census + lane + size trio  (clean tree)                                  -> 7 passed in 54.40s
populations                unit 2701/4000, integration 447/1000          (both claimed values reproduce)
pyright --pythonpath venv  9 touched files                               -> 0 errors, 0 warnings, 0 informations
ruff check / format        9 touched files                               -> All checks passed! / 9 files already formatted
new suppressions           0 new # noqa (movement, staleness, rebinding, new test module)
seam unchanged             models/knowledge/review.py 1164; knowledge_review.py +14/-1 (delegation);
                           review_comparison_staleness.py 97 L, byte-untouched
```
I could **not** reproduce the report's exact *"266 passed / 22 subtests across 18 affected review/transport
modules"*: the 18 modules are not enumerated, and my own derived set (20 modules) gives 314 passed while the
full review family gives 343. The substance — every module that imports the changed vocabulary is green —
reproduces; the exact figure is **`unreproduced`** and I do not claim it matched.

---

## 6. Findings

No blocking finding. The two below are non-blocking.

### H1 — **low** — four of the seven `ReviewSyncMovement` validator clauses are pinned by no case

* **file:line** `mcp/src/agents_remember/models/knowledge/review_staleness.py:144-190`.
* **What.** I disabled each validator clause in turn and ran the delivery's 11-case module:

  | clause | delivery suite |
  | --- | --- |
  | moved input implies `stale` | **1 failed** (covered) |
  | absence needs a reason | **1 failed** (covered) |
  | a measurement carries no reason | **1 failed** (covered) |
  | `stale` needs something moved | 11 passed (**uncovered**) |
  | `record_readable` agrees with `unavailable` | 11 passed (**uncovered**) |
  | no resolved identity unless `stale` | 11 passed (**uncovered**) |
  | `stale` names a resolved identity | 11 passed (**uncovered**) |

  Each uncovered clause is a real invariant — my probe 7c shows the model **accepts** the correspondingly
  false record once the clause is disabled — so the guards are load-bearing as model promises; what is
  missing is a case that would notice their removal.
* **Why it matters.** Rule 4's "a guard that cannot be shown to matter is a finding". I can show these matter
  as invariants but **not** that any shipped producer can emit those shapes: `_project` and `_unavailable`
  are the only producers and both are internally consistent, and the movement value is derived in memory
  rather than deserialised from the durable store. So the exposure is a silent-regression one (a future
  producer, or a refactor that drops a clause, changes nothing observable today), which is why this is low
  and not the medium I gave round 0's F2, where disabling the branch made a false sentence render.
* **Suggested fix.** One case with four forged-for-refusal movements (the delivery's G1 case already forges
  three of the six combos it tests, so the pattern exists): a `stale` with no moved input, an `unavailable`
  whose `record_readable` is `True`, a resolved identity on `current`, and a `stale` naming no resolved
  identity — each required to raise.

### H2 — **low** — `record_readable: False` is reported for a record that *was* read and names another generation

* **file:line** `mcp/src/agents_remember/models/knowledge/review_staleness.py:127` (the field) with
  `application/review_sync_movement.py:138-152` (`_unavailable`), reached from the shipped read.
* **What.** Two different facts — bytes that cannot be read, and a valid record that does not describe the
  selected generation — both render `binding_state: unavailable, record_readable: False`. Probes 8a/8b:
  junk bytes → `record_readable False`; a valid record copied to another generation's file name →
  `record_readable False`. The second record *was* read (parsed and schema-validated) and merely fails the
  generation cross-check.
* **Why it matters.** The field's name says "readable"; the validator's prose defines it as "whether the
  record could be used at all". A consumer branching on `record_readable` cannot tell an unreadable artifact
  from a valid-but-foreign one, which is the same one-word-two-facts shape round 1's F3 was graded on. It is
  low here because **no false sentence is emitted** — the `statement` is "no measurement of comparison
  generation G (index N) is reported here: &lt;reason&gt;" and the `reason` carries the precise sub-fact
  ("does not describe comparison generation …", versus the unreadable record's own message) — and because
  the delivery has a case for each sub-case.
* **Suggested fix.** Either rename the field to what it means (`record_usable`, or `record_applies`), or
  narrow its docstring to the sentence "false when the record could not be used for *this* generation — an
  unreadable artifact and a record describing another generation read the same here".

---

## 7. What is strong in fix round 2

1. **The blocking finding is fixed at its root, not at its symptom.** The state now comes from the record's
   own three-way verdict through a table, so there is no two-way reading left to collapse; the agreement
   clause is reachable only from `current`; and the absence carries the record's own reason rather than a
   restated one.
2. **The vocabulary is R15's, four-valued, and the fourth value is real**: `unavailable` is distinguished
   from "nothing recorded" (`None`), which I verified independently on both sub-cases — the five facts stay
   five.
3. **The fingerprint recipe is now written down and reproduces byte for byte**, which closes the round-2
   evidence-reproducibility gap; the report prints every component and names its exclusions.
4. **The housekeeping claim is true and stronger than asked**: the sibling test module is byte-identical to
   base, so the leaf's coverage ownership is unambiguous and the census derives no consumer change from it.
5. **The bite-proof and mutation matrix are honest about their own limits** — MG1a–MG1f and MG2 are run
   against the frozen bytes with the pre-fix module preserved and hashed, and the four uncovered clauses
   above are a gap in *coverage*, not a misreported bite.
6. **G2's restored conjunct is carried for the right reason** and its failure path has its own true
   sentence rather than borrowing the "not a completed sync" wording that round 0 graded blocking.

---

## Verdict

**Verdict: `pass-with-findings`** — no blocking finding.

I recomputed the declared fingerprint from the stated recipe and it matches on every component, so the
evidence is reproducible; the candidate held `3be9eb2c…` from before grading opened through the end reading.
The blocking G1 is closed: the shipped live read now renders an uncompared knowledge channel as
`not-measured`, with the record's own reason and no agreement clause, and R17's staleness and submission are
left as their own facts; I re-drove it through the shipped read, falsified the validator's six claimed
refusals, drove the `unavailable` state on both sub-cases, and mutation-proved the mapping and two validator
clauses myself. G2 and G3 are closed as asked, the sibling test module is measurably byte-identical to base,
the split's lane/pin/census/population properties all reproduce, and the affected-module regression and every
rail are green on the frozen bytes. The two findings are non-blocking: four of seven validator clauses are
pinned by no case (a silent-regression gap I can demonstrate but whose shapes no shipped producer can emit),
and `record_readable` is a narrower name than its documented meaning for a valid record that names another
generation (no false sentence results; the precise fact travels in `reason`). One figure is `unreproduced`:
the report's exact "266 passed / 22 subtests", because its 18 modules are not enumerated — my derived
20-module set passes 314 and the full review family passes 343.

Artifacts: `temp/verify-l22/{probe_l22.py … probe8_l22.py, probe-base-defect.py, frozen/, base/, mut3.sh,
mut3b.sh, mut3c.sh, mut3d.sh, mut3-backup/, r3-regression.log, affected.txt}` under
`/home/firefox/projects/ar-coordination/temp/`.
