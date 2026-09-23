# Independent adversarial verification — ROUND 3 (fix round 2), leaf L17 / ICR-R17@v1

**Verifier.** The same independent adversarial verifier as round 2, re-checking the round-2 findings
(R2-F1, R2-F2) on the round-3 bytes. Harnesses reused from
`/home/firefox/projects/ar-coordination/temp/l17-verify-r2/` (`R2Probe.test.tsx` — 13 cases incl. the
flush-race P10; `R2Notice.test.tsx` — the clause-vs-DOM P7c/P7d; `R2EntryProbe.test.tsx` — F4) and the
real-composition probe `probe-r2-server.py`. All attacks ran on byte-copies in that scratch tree; **no
leaf byte was modified** (the only leaf write is this report).

**Candidate:** `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l17-ar/260921-icr-l17`,
branch `ar/260921-icr-l17`, HEAD `c422dc00273d4ae7a5d8c9c8db97365b8c85d640` + uncommitted delta.

---

## Fingerprints

**Guard PASSED — the candidate did not move.**

| Point | `git -C <leaf> diff \| sha256sum` |
| --- | --- |
| start | `0d44b92b8a0de8b8f580e1d4a7f6b22f909a47b3253da120fcdf63800881b088` |
| end | `0d44b92b8a0de8b8f580e1d4a7f6b22f909a47b3253da120fcdf63800881b088` |

`HEAD` unchanged, `git status --porcelain` = the same 9 `M` + 3 `??` (+`temp/`) at both points.

| untracked module | sha256 (start = end) | expected |
| --- | --- | --- |
| `dashboard/src/panels/review/ReviewReadCycle.ts` | `5f6735bd365391e9572bba3807d2dd68e031d4ec9920e9e79d923f5365cdd130` | `5f6735bd…5cdd130` ✔ |
| `dashboard/src/panels/review/ReviewRefresh.tsx` | `89ab451c4a8739b80fdccea4b9170991fccb4f63f5b34173b3d83e7a4598d3d0` | `89ab451c…4598d3d0` ✔ |
| `mcp/src/agents_remember/application/review_comparison_staleness.py` | `11e81de2d0360287791aa8d09c7e4790133ee97e98bd530389f7ea1e009dfe89` | unchanged ✔ |

**`temp/icr/prefix2/` really is the round-2 pre-fix code:** `ReviewReadCycle.ts`
`d9fd53c2422f7bdb26b8e29f6360955fd319e2652784d37692683761ab667592` and `ReviewRefresh.tsx`
`055bc06b32bf83271a856a87f1706e55189fe04f97c801c05f4bfce0a6dc2857` — byte-identical to the two hashes
my round-2 report recorded for the indicted modules. The bite-proof below therefore tests the actual
prior code, not a doctored copy.

**Scope confirmed by mtime:** only `ReviewReadCycle.ts`, `ReviewRefresh.tsx` and
`ReviewSurface.outcomes.test.tsx` were written this round (06:29); `changeSetBar.tsx`,
`reviewEntryRefusal.test.tsx`, `ReviewSurface.tsx`, `data/review.ts` and the four `mcp/` files are
untouched since rounds 1/earlier.

---

## 1. R2-F1 — the render-time guard now checks the key (verified, and load-bearing)

The delta is exactly the one-line fix I verified in scratch plus its comment
(`ReviewReadCycle.ts:280-283`):
```ts
const carriedHere =
  carried !== null && carried.readNumber === reads.current && carried.key === targetKey
    ? carried.digest
    : null;
```

**My own same-flush attack, re-run on these bytes** (`R2-P10`: subject A displayed, then the subject
change to B and the refresh click inside ONE `act`, i.e. before the effect that would clear the retained
payload has run):
```bash
cd $S/cand/dashboard && ./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx
# R2-P10 calls: ["…&selectorId=subject-a", "…&selectorId=subject-b"]      <- exactly two reads
# R2-P10 notice: ABSENT
# R2-P10 B-read carried a digest? false
# ✓ R2Probe.test.tsx (13 tests)   Test Files 1 passed (1) | Tests 13 passed (13)
```
The probe was hardened this round: it now asserts `notice === null`, `review-stale === null`,
`leaked === false` and the header naming `subject-b` **unconditionally** (in round 2 the notice check was
conditional, which is how the defect slipped past it). ✅ **No false notice renders; the B read carries
nothing.**

**The conjunct is load-bearing, not cosmetic.** Removing only `&& carried.key === targetKey` from
`carriedHere` in the scratch copy:
```bash
# scratch mutation: carriedHere = carried !== null && carried.readNumber === reads.current ? …
# R2-P10 notice: the candidate's comparison moved (was 0a0a…0a0a, is now 1b1b…1b1b) — …
#   × P10  AssertionError: expected <span …(4)></span> to be null
# Test Files 1 failed (1) | Tests 1 failed | 12 passed (13)
```
The false cross-subject notice comes straight back, so the key conjunct is what closes it.

**Round-1 repairs intact on these bytes (cases (a)–(e) re-run).**
```bash
./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx   # 13/13 incl. P1, P2a, P2b, P3, P5
```
| Round-1 obligation | Case | Observed on the round-3 bytes |
| --- | --- | --- |
| (a) refresh A then select B | P1 | B read `…&selectorId=subject-b`, **no** `previousBindingDigest`; no notice; no stale ✅ |
| (b) recorded ↔ live, both ways | P2a/P2b | live→recorded carries nothing; recorded→live carries nothing; no notice ✅ |
| (c) refresh A twice | P3 | both refreshes carry; **one read per click**; notice present ✅ |
| (d) switch away and back | P1 | returning read carries nothing ✅ |
| (e) late superseded response | P5 | nothing installed; B's later refresh carries B's digest only ✅ |
| F2 gate (failed/refused) | P6/P6b/P8 | no notice; unit gate nulls `failed`/`refused`/`loading` ✅ |
| F2 positive | P7/P7b | superseded names both identities; current names the identity ✅ |
| F4 mark | R2EntryProbe | `Tests 3 passed (3)` — observable in a settled DOM, cleared after the answer and after a failure ✅ |

**The round-1 `startRead` guard was not weakened.** Removing only its read-number conjunct reproduces
exactly the two round-1 leak cases and nothing else:
```bash
# scratch mutation: previous = carried.key === askedFor ? … (no readNumber check)
#   × P1   Received: "…&selectorId=subject-a&previousBindingDigest=0a0a…0a0a"
#   × P2a  Received: "…&selectorId=subject-a&previousBindingDigest=0a0a…0a0a"
# Tests 2 failed | 11 passed (13)
```
So both conjuncts are present and each is independently load-bearing. No finding.

**Comment accuracy check.** The new comment says `startRead` "checks the same pair before sending, so the
value described and the value sent cannot disagree". That holds in the direction that matters: whenever
`carriedHere` is non-null the newest read is the one the refresh asked for and its `askedFor` is the
current `targetKey`, so the read sent exactly the digest being described; in the transient render between
a key change and its effect the description is `null` (never a wrong digest). No finding.

---

## 2. R2-F2 — every clause of the new sentence is true (verified against the route and the DOM)

New bytes (`ReviewRefresh.tsx:77-78`):
```
the candidate's comparison moved (was <previous>, is now <current>) — the panes below have been replaced
whole with the current one
```
Module header bullet corrected the same way (`:16-18`); the `current` sentence is unchanged (`:79`).

**Real route semantics re-measured on these bytes** (`probe-r2-server.py`, real `compose_review` over the
candidate's own test-support fixture):
```
SUBJECT-SCOPED (digests differ)? True
none carried    -> comparison.binding_digest = reference ; staleness current ; previous None
equal carried   -> comparison.binding_digest = reference ; staleness current ; previous None
FOREIGN carried -> payload comparison = the CURRENT digest (reference == binding_digest True)
                   staleness stale ; previous_comparison_ref = the carried digest ;
                   moved ('comparison-binding',) ; submission disabled_stale ;
                   payload renders the CARRIED identity? False
```
`comparison.reference == comparison.binding_digest` is **True in all three cases**, which matters because
the DOM's `data-comparison` renders `reference` while the sentence names `binding_digest`: on the real
route they are the same value, so the sentence's "is now X" and the pane attribute are comparable — which
is exactly the assertion I made.

**Clause-by-clause, in the DOM** (`R2Notice.test.tsx`, own fixture OLD `7f…`, NEW `8e…`; superseded case):
```
R2-P7c NOTICE TEXT  : the candidate's comparison moved (was 7f7f…7f7f, is now 8e8e…8e8e) — the panes
                      below have been replaced whole with the current one
R2-P7c PANES DIGEST : 8e8e…8e8e
R2-P7c STALE REGION : Candidate changed — open a new comparison — previous input: 7f7f…7f7f
```
| Clause | True? | Why |
| --- | --- | --- |
| "the candidate's comparison moved" | ✅ | only rendered when the client's digest comparison says `superseded`, which is equivalent by construction to the server's `stale` (server: `previous != identity.binding_digest`; client: `shown.comparison.binding_digest != carried`, and `shown` **is** the answer's payload) — measured on the real route above, and the server's own `review-stale` sentence renders beside it |
| "(was Y)" | ✅ | Y is the identity carried from the display being replaced; the route echoes it as `previous_comparison_ref` (`previous input: 7f…7f`) |
| "is now X" | ✅ | X is the digest the answer rendered; asserted equal to the DOM's `data-comparison` (`notice.dataset.currentBinding === data-comparison`) and, on the real route, to `comparison.reference` |
| "the panes below have been replaced whole with the current one" | ✅ | the panes render this read's answer payload (`shownPayload` returns `read.payload` in the `reviewed` phase), not a patch or the retained old payload |
| (removed) "the panes below are the comparison you were reading (Y)" | ✅ gone | see grep below |

**`current` state** (`R2-P7d`, equal-carry answer, fixture corrected to the route's real equal-carry
answer — `current`, no previous ref):
```
R2-P7d NOTICE TEXT : the comparison below is still the candidate's current one (7f7f…7f7f)
R2-P7d PANES DIGEST: 7f7f…7f7f
```
The sentence names the identity the panes render, and `review-stale` is absent (the route answered
`current`), so nothing claims a movement that did not happen. ✅

**The old false clause is gone from production source.** `grep -rn "panes below are the comparison\|replaced whole on the next read" dashboard/src --include=*.ts --include=*.tsx` (excluding tests) → only
`ReviewSurface.tsx:898`, which is the **recorded-history** sentence ("the panes below are the comparison it
bound, not whatever the repository holds now") — a different claim, about `history="recorded"` reads, and
one my P2a/P2b re-verify holds (a recorded read stays the recorded question and carries nothing foreign).
Every other "panes below" occurrence is the corrected header bullet or the new sentence:
```
ReviewRefresh.tsx:14  the candidate's comparison is still the one on screen (`current`), and the panes below are the
                      answer to the question that was asked -- the whole payload was replaced, not patched
ReviewRefresh.tsx:78  the candidate's comparison moved (was …, is now …) — the panes below have been
                      replaced whole with the current one
```
✅ No other sentence in the two new modules or the pane asserts the old claim.

**The delivered test now pins the agreement, not just the wording** (`ReviewSurface.outcomes.test.tsx:593-604`):
`expect(notice.textContent).toContain('was 1…1, is now 2…2')`,
`expect(notice.textContent).not.toContain("the panes below are the comparison you were reading")`, and
`expect(notice.dataset.currentBinding).toBe(view.getByTestId("review-surface").dataset.comparison)`. That
last assertion is the one the finding asked for: the claim and the attribute cannot drift apart again.

---

## 3. Regression sweep and rails

| Check | Command | Observed |
| --- | --- | --- |
| my probes (all three modules) | `./node_modules/.bin/vitest run src/panels/review/R2Probe.test.tsx src/panels/review/R2Notice.test.tsx src/panels/detail-panel/R2EntryProbe.test.tsx` | **Test Files 3 passed (3) / Tests 18 passed (18)** |
| `tsc` | `./node_modules/.bin/tsc --noEmit -p tsconfig.json` | **exit 0, no output** |
| eslint (7 changed dashboard files) | as round 2 | **exit 0, no output** |
| vitest (named dirs) | `./node_modules/.bin/vitest run src/panels/review/ src/panels/detail-panel/` | **Test Files 16 passed (16) / Tests 109 passed (109)** (was 108; +1 new case, matches the fixer) |
| vitest (whole suite) | `./node_modules/.bin/vitest run` | **Test Files 158 passed (158) / Tests 1675 passed (1675)** (was 1674; +1) |
| review-surface pytest | `… -m pytest mcp/tests/test_knowledge_review_surface.py -q -m '' -p no:cacheprovider` | **31 passed in 41.73s** |
| census `-m ''` | `… -m pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m '' -p no:cacheprovider` | **5 passed in 57.47s** |
| new suppressions / timers | `git diff -U0 \| grep "^+" \| grep -iE "noqa\|eslint-disable\|@ts-ignore\|@ts-expect-error\|type: ignore\|setInterval\|setTimeout\|requestAnimationFrame\|setImmediate\|queueMicrotask"` → **NONE**; the same grep over the two modules → **NONE** | ✅ |
| file sizes | `wc -l` | `ReviewReadCycle.ts` 286, `ReviewRefresh.tsx` 117, `ReviewSurface.outcomes.test.tsx` 884, `changeSetBar.tsx` 552, `ReviewSurface.tsx` 986 — all far under the 1200 rail |
| no notice lost where it should render / no carry lost where it should be sent | P3, P7, P7b, P7d, P9, R2-F4c | ✅ (notice renders after every answered refresh that carries; one click = one read, no loop) |
| F4 mark | R2-F4a/b/c | ✅ **3 passed** (settled-DOM presence, clears on answer, clears on failure) |

---

## 4. Do the fixer's two new cases genuinely bite?

Against the preserved round-2 pre-fix bytes (`temp/icr/prefix2/{ReviewReadCycle.ts,ReviewRefresh.tsx}`
overlaid in scratch, the delivered test module kept):
```bash
./node_modules/.bin/vitest run src/panels/review/ReviewSurface.outcomes.test.tsx
#   × re-reads the same question carrying the binding identity on screen, and names what moved
#     AssertionError: expected 'the candidate published a new compari…' to contain 'was 1111…1111, is now 2222…2222'
#     Expected: "was 11111111…1111, is now 22222222…2222"
#     Received: "the candidate published a new comparison (2222…2222) — the panes below are the
#                comparison you were reading (1111…1111), replaced whole on the next read"
#     ❯ src/panels/review/ReviewSurface.outcomes.test.tsx:599:32
#   × renders no generation claim when the subject change and the refresh land in one flush (L17-R2-F1)
#     AssertionError: expected <span …(4)></span> to be null
#     ❯ src/panels/review/ReviewSurface.outcomes.test.tsx:752:60
# Test Files 1 failed (1) | Tests 2 failed | 16 passed (18)
```
**Both bite, and the output reproduces the fixer's report exactly** (`2 failed | 16 passed (18)`, the same
two error strings and the same two line numbers). I also read the new case's bytes
(`ReviewSurface.outcomes.test.tsx:692-771`): it is a genuine reproduction of my P10 — one `act` containing
the subject change and the refresh click, asserting exactly two reads, no `previousBindingDigest` on the B
read, `review-generation-notice` null and the header naming `subject-b` — not a weakened imitation. Fixed
bytes: the module is `18 passed (18)`, and my own independent flush-race probe coincides with it.

---

## Findings

**None.** Both round-2 findings are fixed on the reviewed bytes, and I could not reproduce either defect in
any variant I attacked:

* **R2-F1 (was medium)** — closed at `ReviewReadCycle.ts:280-283`; my same-flush attack (`R2-P10`) now
  renders no notice and the request carries nothing, and removing the new key conjunct brings the false
  cross-subject notice straight back, so the repair is load-bearing rather than cosmetic.
* **R2-F2 (was medium)** — closed at `ReviewRefresh.tsx:77-78`; every clause is true against the real
  route's semantics and against the DOM, the false clause is absent from production source, and the
  delivered test now asserts the sentence agrees with `data-comparison`.

**Observations (not findings).**

* Reachability is unchanged: R2-F1 needed the subject change and the refresh inside one React flush (the
  passive-effect gap), which no single user action produces in today's shell. The fix closes the defect at
  the component boundary the leaf's own R17 test treats as in scope; no shell change was made and none is
  claimed.
* `ReviewSurface.tsx:897-898` keeps the recorded-history sentence ("the panes below are the comparison it
  bound"): a different claim, correct for `history="recorded"` reads (re-verified by P2a/P2b) and not the
  clause this round removed.
* A12 (a real ingest/publication observed by a real browser) remains deferred to R25, unchanged and not
  re-litigated; F5's over-rail test module is unchanged by this round (`mcp/tests/test_knowledge_review_surface.py`
  untouched at 06:29) and remains under the master's ruling.

---

## True strengths of this repair

1. **Both findings were fixed at the exact site the finding named, with the mechanism the finding
   identified** — one conjunct and one sentence — and neither fix is a suppression, a bypass or a widened
   guard.
2. **The bite-proofs are real and match their report**: the preserved `prefix2` bytes hash-match the round-2
   candidate, and the two new/updated cases fail on them with the exact assertions and line numbers the
   report claims (`2 failed | 16 passed (18)`).
3. **The tests now pin the property, not the wording**: the delivered case asserts the sentence names both
   identities, that the false clause is absent, and that the sentence's claim equals the panes'
   `data-comparison` — so the defect cannot return as a silent rewording.
4. **Nothing else moved**: the round-1 F1 carry guard, F2's phase gate and F4's observable mark are intact
   and each re-verified with its own mutation/negative control; the server seam is byte-identical; the
   change set is confined to three dashboard files.
5. **Every rail is green** (tsc, eslint, 16/109, 158/1675, 31 pytest, 5 census), with no new suppression and
   no timer anywhere in the diff.

---

## Verdict

**`pass`.**

The round-2 findings are both genuinely repaired on the reviewed bytes and I could not reproduce either
defect on any path I attacked. The render-time guard in `ReviewReadCycle.ts:280-283` now checks the read
number **and** the question key, so my same-flush interleaving attack — subject change plus refresh in one
flush, which in round 2 produced a false "the candidate's comparison moved" notice built from another
subject's identity — now renders no notice at all while the B read carries nothing; removing the new
conjunct in scratch makes my attack fire again, proving the fix load-bearing, and removing the round-1
read-number conjunct reproduces only the two round-1 leak cases, proving the earlier guard was not
weakened. Every clause of the new sentence in `ReviewRefresh.tsx:77-78` is true against the real
composition (`probe-r2-server.py`: a `stale` answer publishes the current identity, names the carried one as
the previous input, and `comparison.reference == binding_digest` so the sentence's "is now X" is the very
value the DOM shows as `data-comparison`) and against the mounted DOM in both the superseded and current
states; the old false clause survives nowhere in production source, and the delivered test now asserts the
sentence and the pane attribute agree. The fixer's two new cases genuinely bite against the preserved
round-2 pre-fix bytes, reproducing its reported output exactly, and every regression check — round-1 cases
(a)–(e), F2's phase gate, F4's observable mark, the packet's base-failing cases, the read-count/no-loop
checks, and all named rails (tsc, eslint, 16 files/109 tests, 158 files/1675 tests, 31 pytest, 5 census, no
new suppression, no timers) — is green. Both self-imposed mutations restored byte-identically, and the
candidate fingerprint is unchanged from the first minute to the last.
