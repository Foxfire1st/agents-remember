# Independent adversarial verification — ROUND 2 (fix round 1), leaf L17 / ICR-R17@v1

**Verifier.** A different independent adversarial verifier than the one that filed F1–F5. Scope: verify
the REPAIR, not re-litigate the findings. The fixer's report was treated as marketing until measured.

**Candidate under test** (never modified): `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l17-ar/260921-icr-l17`
(branch `ar/260921-icr-l17`, HEAD `c422dc00273d4ae7a5d8c9c8db97365b8c85d640` + uncommitted delta).

**Scratch (mine).** `/home/firefox/projects/ar-coordination/temp/l17-verify-r2/` — `cand/` (byte-copy of the
candidate, used for every attack), `base2/` (base bytes + the delivered test modules), `keep/` (pristine
copies for restore), `probe-r2-server.py`. **No leaf byte was modified**; the leaf's own `git diff`
fingerprint and `git status --porcelain` are identical at start and end (below).

---

## Fingerprints

**Guard PASSED — the candidate did not move during verification.**

| Point | `git -C <leaf> diff \| sha256sum` |
| --- | --- |
| start | `29b787e9b14bce430cc005622cc617bda6e9238b92114d24f26aa073dda048c3` |
| end | `29b787e9b14bce430cc005622cc617bda6e9238b92114d24f26aa073dda048c3` |

`HEAD` = `c422dc00…` at both points; `git status --porcelain` = the same 9 `M` + 3 `??` (+`temp/`) at both
points. The three untracked new files, hashed at start *and* end (identical):

| file | sha256 (start = end) |
| --- | --- |
| `dashboard/src/panels/review/ReviewReadCycle.ts` | `d9fd53c2422f7bdb26b8e29f6360955fd319e2652784d37692683761ab667592` |
| `dashboard/src/panels/review/ReviewRefresh.tsx` | `055bc06b32bf83271a856a87f1706e55189fe04f97c801c05f4bfce0a6dc2857` |
| `mcp/src/agents_remember/application/review_comparison_staleness.py` | `11e81de2d0360287791aa8d09c7e4790133ee97e98bd530389f7ea1e009dfe89` |

Scratch construction and purity proof (every changed/added file compared byte-for-byte against the leaf,
and the base tree compared per-path against `HEAD:<path>`): `OK` for all 12 candidate files; the base tree
differs from `HEAD` in exactly the two overlaid test modules.

The fixer's preserved PRE-FIX bytes under `<leaf>/temp/icr/prefix/` are the real pre-fix bytes: their
hashes equal the ones the round-1 verifier recorded for the pre-fix candidate
(`5c5968c8…ba97c6` / `f6b30544…c46281`), so the bite-proofs below test the actual prior code.

---

## Obligation F1 (was high: sticky carried identity)

My own module `$S/cand/dashboard/src/panels/review/R2Probe.test.tsx` (own fixture: distinct digests
`0a…`, `1b…`, `2c…`, distinct subjects) driven over the **real** `ReviewSurface` and the **real** client;
only `fetch` is stubbed by a model of the route that I checked against the real composition
(`probe-r2-server.py`, below).

**Model faithfulness (real composition).**
```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support <venv>/python \
  $S/probe-r2-server.py
# A digest : 0ca41d3c…f9f7 ; B digest : e9f799f5…5aad ; SUBJECT-SCOPED (digests differ)? True
# none carried   -> staleness current, previous_comparison_ref None, submission unavailable
# equal carried  -> staleness current, previous_comparison_ref None
# FOREIGN carried-> staleness stale,  previous_comparison_ref = the carried digest,
#                   moved = ('comparison-binding',), submission disabled_stale
```
So a foreign carry really is a mismatch with real consequences, and the stub's semantics are the
server's.

```bash
cd $S/cand/dashboard && ./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx
# after all cases were added: Test Files 1 passed (1) | Tests 13 passed (13)
```

| Case the brief named | My case | Observed on the fixed bytes |
| --- | --- | --- |
| (a) refresh A, then select B | P1 first half | B read = `…&selectorId=subject-b` with **no** `previousBindingDigest`; `review-generation-notice` **null**; `review-stale` **null** ✅ |
| (b) recorded ↔ live both ways | P2a, P2b | live→recorded read = `…&history=recorded` no digest; recorded→live read = `…&selectorId=subject-a` no `history=` and no digest; no notice ✅ |
| (c) refresh A, refresh A again | P3 | call 2 = `…&selectorId=subject-a&previousBindingDigest=0a…0a` — carried; notice present; **exactly one read per click** (3 clicks → 3 reads, no extra flush read) ✅ |
| (d) away from a question and back | P1 second half | returning read (`selectorId=subject-a`, 4th call) contains **no** `previousBindingDigest`; no notice, no stale ✅ |
| (e) a late/superseded response | P5 | held-open refresh of A released *after* B answered: B's comparison and header survive, **no notice, no stale**; and B's own later refresh carries **B's** digest (`previousBindingDigest=1b…1b`), never A's ✅ |

**(d) is real, not decoration — proven by mutation.** I replaced only the read-number conjunct with a
key-only guard in my scratch copy (`carried.key === askedFor`):
```bash
# mutation applied to $S/cand/dashboard/src/panels/review/ReviewReadCycle.ts (key-only guard)
./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx
#   × P1 (the switch-BACK read)  AssertionError: expected '…' not to contain 'previousBindingDigest'
#     Received: "…&selectorId=subject-a&previousBindingDigest=0a0a…0a0a"
#   × P2a (the live read after the recorded read)  same assertion
#   Test Files 1 failed (1) | Tests 2 failed | 9 passed (11)
```
So the read-number guard closes precisely the case a key-only guard cannot, exactly as the fixer claims.
Fixed bytes restored (hash re-verified) before continuing.

**Bite-proof on the pre-fix bytes** (I overlaid `temp/icr/prefix/{ReviewReadCycle.ts,ReviewRefresh.tsx,
ReviewSurface.tsx,changeSetBar.tsx}` in scratch):
```bash
./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx
# × P1, × P2a, × P2b, × P5, × P6, × P8  →  Tests 6 failed | 5 passed (11)
./node_modules/.bin/vitest run src/panels/review/ReviewSurface.outcomes.test.tsx \
                              src/panels/detail-panel/reviewEntryRefusal.test.tsx
# × keeps the labelled old comparison and its error when the refresh fails
#     AssertionError: expected <span …(4)></span> to be null
# × carries the identity into a read that replaces it, and into no other question (L17-F1)
# × never carries a live identity into a recorded read, nor a recorded one into a live read (L17-F1)
# × marks the list while the projection has moved past the answer on screen, and clears it on the answer
#     TestingLibraryElementError: Unable to find an element by: [data-testid="review-catalogue-stale"]
# Test Files 2 failed (2) | Tests 4 failed | 21 passed (25)
```
This reproduces the fixer's claimed pre-fix failure set exactly (3 in the outcomes module, 1 in the entry
module). **Bite-proof honest.**

**Residual hole in F1's notice derivation — see finding R2-F1 (reproduced).**

---

## Obligation F2 (was high: unmeasured currency claim)

```bash
./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx -t "P6" -t "P7" -t "P8"
```
| Case | Observed |
| --- | --- |
| P6 — refresh rejects (network) | `review-failure` state `network`; retained comparison still `0a…`; **`review-generation-notice` is null**; still null after a drained flush ✅ |
| P6b — refresh answers a typed refusal (404 `candidate_dataset_absent`) | `review-refusal` renders; **notice null** ✅ |
| P7 — refresh answers and the candidate MOVED | notice `data-generation-state="superseded"`, `data-previous-binding=0a…`, `data-current-binding=2c…`, text names **both** identities; `review-stale` reads `previous input: 0a…` ✅ |
| P7b — refresh answers, unchanged | notice `data-generation-state="current"`, names the identity ✅ |
| P8 — the phase gate at unit level | `generationOf(failed,…)`, `(refused,…)`, `(loading,…)` → **all null**; `(reviewed, carried=old, shown=new)` → `superseded`; `(reviewed, carried=current)` → `current`; `(reviewed, carried=null)` → null ✅ |

**The gate cannot be satisfied by a failure path.** `generationOf` returns at `if (read.phase !==
"reviewed") return null;` before either digest is read; and the failure is *in* that phase at the surface
(P6 renders `review-failure` from the same `read` while the notice is absent — the two cannot both be
reading different phases). A `reviewed` phase is only ever written by an applied answered read
(`apply(readFrom(result), …)`), and a superseded answer is dropped by `current()` before it can write.

**The changed delivered assertion is not vacuous and not tampered with.** I read the new bytes at
`dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx:618-623`: it keeps
`expect(view.getByTestId("review-surface").dataset.comparison).toBe("1".repeat(64))` (the labelled old
generation is still on screen) and replaces the old `generationState === "current"` assertion with
`expect(view.queryByTestId("review-generation-notice")).toBeNull()`, with a comment naming the fix round,
the finding and why the old assertion pinned the defect. It bites: on the pre-fix bytes the same
assertion fails with `expected <span …(4)></span> to be null` (block above), i.e. it finds the notice the
old assertion demanded. The sibling case in the same `describe` still asserts the notice DOES render
after a successful refresh, so absence here is not an untestable null.

**New observation (not a finding):** after a successful refresh that moved the comparison, a *second*
refresh that fails withdraws the client's notice but the measured claim survives in the payload's own
region — P6c observed `notice: ABSENT`, `review-stale: "Candidate changed — open a new comparison —
previous input: 0a…"`, panes `2c…`. The packet's failure clause ("a failed refresh retains the labeled old
generation with its error") therefore still holds; the withdrawal is the disclosed F2 rule, not a lost
measurement.

---

## Obligation F4 (was low: dead affordance)

My own module `$S/cand/dashboard/src/panels/detail-panel/R2EntryProbe.test.tsx` (own fixture, own store
publication through `dashboardStore.getState().applyDelta("analytics", …)` — the store's own channel):

```bash
./node_modules/.bin/vitest run src/panels/detail-panel/R2EntryProbe.test.tsx
# R2-F4a mark-bearing observer callbacks: 2 with mark: 1
# ✓ R2-F4a (mark present in a SETTLED DOM while the re-read is in flight; gone after the answer)
# ✓ R2-F4b (mark clears when the post-publication re-read FAILS)
# ✓ R2-F4c (the reader's own click re-reads; mark false)
# Tests 3 passed (3)
```
R2-F4a holds the post-publication read open, then drains the microtask queue **and a macrotask** before
asserting: marker node present with text ` · workspace facts changed`, `data-catalogue-stale="true"`,
`entryReads === 2`. After the answer: `data-catalogue-stale="false"` and the node is gone. This is a
settled DOM, not a transient commit.

**It bites against the pre-fix `changeSetBar.tsx`** (same probe, prefix overlay):
```bash
# × R2-F4a  TestingLibraryElementError: Unable to find an element by: [data-testid="review-catalogue-stale"]
# Tests 1 failed | 2 passed (3)
```
**Comment matches the bytes.** `changeSetBar.tsx:146-153` claims the mark lasts "from the publication that
moved the projection until the answer for that projection is filed … including when the answer is a
refusal or a failure"; the derivation is `:251` `const stale = read.facts !== facts;` with `read.facts`
written only by an answer (`catalogueAnswer(result, askedFor)`, and the failure path
`{loading:false, problem, facts: askedFor, stale:false}`). R2-F4b measures the failure clause and it holds.
R2-F4c measures the reader's control. No timers: `grep -n "setInterval\|setTimeout\|requestAnimationFrame\|setImmediate\|queueMicrotask"`
over the five changed dashboard files → **NONE** (and `NONE` for timers added anywhere in `git diff -U0`).

---

## Regression sweep on the fixes themselves

| Attack | Result |
| --- | --- |
| A carry suppressed that should have been sent | **None found.** P3 (two refreshes both carry), P2b (a *recorded* read's own refresh carries its identity), P6 (the retry the failure offers is the same path), P9 all pass. `refresh()` still files the identity whenever a displayed binding exists; the only reads that now send nothing are reads that replace nothing. |
| A notice suppressed that should have shown | **None found** in the sequential flows: P3 (second refresh re-renders the notice), P7/P7b (answered refreshes claim). The withdrawal on failure (P6c) is the disclosed F2 rule, not a loss (the labelled generation survives). |
| `stale` derivation sticks or flickers (F4) | Does not stick: R2-F4a/F4b end at `false`. Does not flicker: present across a drained settle, with the read still unanswered. |
| Effect-dependency change reintroduces a race or a loop | **No loop.** P9: a re-render with byte-identical props starts **no** read (`calls` stays 1); one click = one read, stable after flushing. The removed `carried` dep and the new `asked` memo behave: `asked` is memoised on value deps (`ReviewReadCycle.ts:235-238`), and `useReviewReadCycle` has exactly one caller (`ReviewSurface.tsx:924`) whose `selection` is component state. |
| The packet's own base-failing cases still pass on the fixed bytes | ✅ `vitest run src/panels/review/ src/panels/detail-panel/` → **16 files / 108 tests passed**; the whole dashboard suite → **158 files / 1674 tests passed** (matches the fixer's number, +3 from round 1). |
| The delivered tests still fail on BASE (they still bite) | ✅ base bytes + the delivered test modules → outcomes module **5 failed / 12 passed (17)** (both new F1 cases, the changed F2 case, and the two packet cases), entry module **3 failed / 5 passed (8)**, combined **8 failed / 17 passed (25)**. |
| F1(e) variant: does a late carry leak into the NEXT question's refresh? | ✅ P5: B's later refresh carries B's identity only. |

---

## Rails on the fixed bytes

| Check | Command | Observed |
| --- | --- | --- |
| `tsc` | `(cd <leaf>/dashboard && ./node_modules/.bin/tsc --noEmit -p tsconfig.json)` | **exit 0, no output** |
| eslint (7 changed dashboard files) | `./node_modules/.bin/eslint src/panels/detail-panel/changeSetBar.tsx src/panels/review/ReviewSurface.tsx src/panels/review/ReviewRefresh.tsx src/panels/review/ReviewReadCycle.ts src/data/review.ts src/panels/detail-panel/reviewEntryRefusal.test.tsx src/panels/review/ReviewSurface.outcomes.test.tsx` | **exit 0, no output** |
| vitest (named dirs) | `./node_modules/.bin/vitest run src/panels/review/ src/panels/detail-panel/` | **Test Files 16 passed (16) / Tests 108 passed (108)** |
| vitest (whole suite) | `./node_modules/.bin/vitest run` | **Test Files 158 passed (158) / Tests 1674 passed (1674)** |
| review-surface module | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support <venv>/python -m pytest mcp/tests/test_knowledge_review_surface.py -q -m '' -p no:cacheprovider` | **31 passed in 41.54s** |
| census `-m ''` | `… -m pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m '' -p no:cacheprovider` | **5 passed in 57.74s** |
| new suppressions | `git diff -U0 \| grep "^+" \| grep -i "noqa\|eslint-disable\|@ts-ignore\|@ts-expect-error\|type: ignore"` → **NONE**; the three new files → **NONE** | ✅ (the fixer's first-draft `react-hooks/exhaustive-deps` error really was removed by memoising `asked`, not by a suppression) |
| timers | grep over the 5 changed dashboard files and over all added diff lines → **NONE** | ✅ |
| file-size rail | `wc -l` | `ReviewReadCycle.ts` 278, `ReviewRefresh.tsx` 116, `changeSetBar.tsx` 552, `ReviewSurface.tsx` 986, `data/review.ts` 672, tests 809/534 — all far under 1200; `models/knowledge/review.py` 1198 (2 lines of headroom, **unchanged** this round); `mcp/tests/test_knowledge_review_surface.py` 1395 — over the rail, unchanged by this round (F5, ruled). |

---

## Findings

### R2-F1 — `{id: R2-F1, severity: medium, file: dashboard/src/panels/review/ReviewReadCycle.ts:274-275}`

**What.** The notice's own derivation never checks the question key. `carriedHere` is
`carried !== null && carried.readNumber === reads.current ? carried.digest : null`. `startRead` checks
BOTH key and read number before sending `previous`, but the render-time value the notice describes checks
only the read number. When the subject changes and the reader's refresh reach the **same React flush**, the
refresh captures `{key: A}` from the still-retained A payload while the one effect run that follows asks
for B and takes exactly the captured read number — so `carried.readNumber === reads.current` holds for a
read of a *different question*, and a generation claim is rendered from another subject's identity.

**Evidence (reproduced, my own fixture; scratch candidate bytes).**
```bash
./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx -t "P10"
# R2-P10 calls: ["…&selectorId=subject-a", "…&selectorId=subject-b"]        <- exactly two reads
# R2-P10 B-read carried a digest? false                                     <- the REQUEST is clean
# R2-P10 notice: the candidate published a new comparison (1b1b…1b1b) — the panes below are the
#                comparison you were reading (0a0a…0a0a), replaced whole on the next read
# × P10  AssertionError: expected '0a0a…' to be '1b1b…'
# Test Files 1 failed (1) | Tests 1 failed | 1 passed | 11 skipped (13)
```
The interleaving is the passive-effect gap: the props commit with the new subject while the effect that
would clear `retained`/advance the read has not run yet, and the click lands in that gap. The server never
made this claim — in the reproduction the B payload's own `staleness.state` is `current`; the client
invented the publication from two digests that belong to two different subjects. This is the same
user-visible sentence class as round 1's F1 ("the notice would announce a candidate publication that never
happened" — `ReviewReadCycle.ts:32-34`), reached through the derivation rather than the request.

**Why it matters.** The round-1 F1 repair is complete on the wire (no foreign digest, no server `stale`, no
`disabled_stale` — all proven above) but not in the UI: with the key change and the refresh in one flush,
the reader is told a comparison they never read was replaced. Reachability: **latent in today's shell** for
the reasons round 1 gave (no URL routing; the railed body is hidden under a takeover), and it needs two
interactions inside one flush — I could not produce it from a single user action. It is nonetheless a real
failure of obligation (a)'s second half at the component boundary this leaf's own R17 test treats as in
scope.

**Suggested fix (verified to close it).** Add the question key to the render-time guard:
```ts
const carriedHere =
  carried !== null && carried.readNumber === reads.current && carried.key === targetKey
    ? carried.digest
    : null;
```
With that one-line change in my scratch copy: `Tests 13 passed (13)` (P10 now logs `notice: ABSENT`) and the
delivered modules stay green at `25 passed (25)`. The legitimate carry is untouched because `carried.key`
is `shown.key === targetKey` on the refresh path.

### R2-F2 — `{id: R2-F2, severity: medium, file: dashboard/src/panels/review/ReviewRefresh.tsx:77}`

**What.** The superseded notice's own clause is false. It prints "the panes below are the comparison you
were reading (Y), replaced whole on the next read" while the panes below render the **new** comparison (X):
on a `stale` answer the route publishes the CURRENT comparison identity and merely names the carried one as
`previous_comparison_ref`. Pre-existing (it is in the pre-fix bytes and round 1 graded the sentence "true
when the reader refreshed"), but this is exactly the component the fix round modified, and the fix round's
own new test asserts the mismatch without noticing it.

**Evidence (measured twice, independently).**
```bash
# 1. the real composition: probe-r2-server.py, FOREIGN carried case
#    payload comparison.binding_digest: e9f799f5…5aad   (the CURRENT one)
#    staleness.previous_comparison_ref: 0ca41d3c…f9f7  (the carried one)
#    payload renders the CARRIED identity? False
# 2. the mounted DOM: R2Notice.test.tsx (own fixture, OLD=7f…, NEW=8e…)
#    R2-P7c NOTICE TEXT  : the candidate published a new comparison (8e8e…8e8e) — the panes below are
#                          the comparison you were reading (7f7f…7f7f), replaced whole on the next read
#    R2-P7c PANES DIGEST : 8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e8e
#    R2-P7c ATTRIBUTES   : {"generationState":"superseded","previousBinding":"7f7f…7f7f",
#                           "currentBinding":"8e8e…8e8e"}
# 3. the delivered test's own bytes say the same thing:
#    ReviewSurface.outcomes.test.tsx:591-593 "The panes below were replaced whole with the candidate's
#    current comparison" / expect(dataset.comparison).toBe("2".repeat(64)) — while the component's
#    sentence (line 77) asserts the panes are "1".repeat(64).
```
**Why it matters.** The one sentence the refresh feature exists to deliver misdescribes what is on screen.
The identities are all named correctly and the payload's own `review-stale` region states the truth
("Candidate changed — open a new comparison — previous input: Y"), so this is a misleading sentence beside
correct data rather than a false measurement — hence medium rather than blocking. If the master's rule that
a provably false user-visible sentence is blocking is applied, this finding should be read at that severity.

**Suggested fix.** Word it as what the answer is: `the candidate's comparison moved (was Y, is now X) — the
panes below have been replaced whole with the current one`, or drop the clause and keep the two named
identities. Either keeps the required "name both identities" behaviour.

### Unreproduced / not claimed

* **R2-F2's severity as a blocking defect** — reproduced as a false sentence, but I do not claim it is
  reachable in a flow a reader performs today beyond the refresh click itself (it fires on every
  superseded refresh).
* **R2-F1 from a single user action** — I could not produce the flush interleaving without batching the
  subject change and the refresh in one `act`; the browser equivalent (the passive-effect gap) is
  sub-frame. Marked accordingly in the finding.
* **A12 (a real ingest/publication observed by a real browser)** — not attempted by me; unchanged and
  deferred to R25 by the master, as the fixer reports. Not re-litigated.
* **F3 and F5** — untouched by this round (F3 deferred to R25; F5 ruled accepted, module still 1395 lines,
  whole-tree offender census unchanged). Not re-litigated.

### Smaller observations (no finding)

* After a failed refresh `carried` is still filed, and the failure leaves it filed (nothing clears it on a
  failure path). The read-number guard makes the leftover inert in every sequential case I attacked
  (P1/P2/P5/P6); it is only the key check that is missing (R2-F1).
* `refresh()` leaves `carried` unchanged when the retained payload carries no binding (the task-context
  review) — correct (nothing to compare), and inert because `generationOf` needs a rendered identity.
* `reviewDependencyFacts` serialises the whole analytics projection per render (`changeSetBar.tsx`) — a
  bounded comparison, no timer, and the dep is a string so equal content does not re-read. Not a finding.

---

## True strengths of the repair (recorded so the record is balanced)

1. **The F1 defect is closed on the wire, and the guard is real.** No foreign `previousBindingDigest` in
   any of my 13 cases, including the two the fixer claimed only the read number can close (switch-away-and-
   back; the live read after a recorded one). The key-only mutation I built fails exactly those two cases,
   so `{readNumber, key, digest}` is load-bearing, not decoration.
2. **The bite-proof is honest.** The preserved prefix bytes hash-match the round-1 verifier's recorded
   pre-fix hashes, and on them the delivered modules fail 4/25 and my probe fails 6/11 — the fixer reported
   the same four delivered failures, including the exact `expected <span …(4)></span> to be null` line.
3. **F2's gate is structural, not a flag.** `generationOf` returns before reading either digest unless the
   read answered; the unit cases and the surface cases agree; a typed refusal is also plain enough to
   suppress the claim (P6b).
4. **The pinned wrong assertion was reversed on the record**, with a comment naming the finding, and the
   reversal is non-vacuous (it fails on the pre-fix bytes; its sibling case proves the notice can render).
5. **F4 is genuinely observable now**, in a settled DOM, with the read held open; the comment's window
   ("including when the answer is a refusal or a failure") is true as measured, and the constant-comparison
   cost is unchanged.
6. **No loop, no lost read, no new timer, no suppression.** A byte-identical re-render starts no read; one
   click is one read; the whole dashboard suite is green at 158 files / 1674 tests, matching the report.
7. **No scope creep**: the fix round touched two new modules and one panel (`changeSetBar.tsx`), the server
   seam is byte-identical to round 1 (`review_comparison_staleness.py` hash unchanged), and every rail the
   brief named is green.

---

## Verdict

**`pass-with-findings`.**

The repair round does what it claims. Every obligation the brief names verifies on the fixed bytes with my
own fixtures: F1 (a)–(e) all hold in the sequential flows, including the switch-away-and-back case whose
necessity I proved by mutation; the legitimate carry still works once per click; a late superseded response
installs nothing; F2's notice is absent after a network failure and after a typed refusal, present with both
identities after a successful refresh, and the phase gate cannot be reached from a failure path; F4's mark
is observable in a settled DOM while the re-read is in flight and gone after the answer, with its comment
matching the bytes; and the regression sweep found no lost carry, no lost notice, no sticky or flickering
derivation, no loop, and no reintroduced race — the packet's base-failing cases still fail on base and pass
here. The rails are green (tsc, eslint, 16/108 and 158/1674 vitest, 31 pytest, 5 census, no new
suppression, no timer), and the fixer's factual claims reproduced almost without exception, including the
pre-fix failure set and the +3 test count. Two findings remain, both reproduced: the notice's render-time
guard still lacks the question key, so one same-flush interleaving renders a false publication claim from
another subject's identity (R2-F1, medium; the one-line fix is verified to close it and to keep everything
else green), and the superseded notice's own sentence contradicts the DOM it sits in (R2-F2, medium;
pre-existing, in the component this round modified). Neither reopens the high findings F1/F2 as they were
filed: the wire is clean and no unmeasured `current` claim is rendered. Because the delivery is a genuine,
verified repair with two reproduced residual defects in the same feature's user-facing claims, the verdict
is pass-with-findings rather than pass; if the master treats a provably false user-visible sentence as
blocking, R2-F2 (and R2-F1's false notice) should be read at that severity and the verdict would tip to
fail, and both are localised to `ReviewReadCycle.ts:274-275` and `ReviewRefresh.tsx:77`.
