# Independent adversarial verification — leaf L17 (ICR-R17@v1 "Coherent live refresh")

**Verifier:** independent adversarial verifier, atomic master `260921-ICR`, leaf `260921-ICR-L17`.
**Candidate under test:** `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l17-ar/260921-icr-l17`
(branch `ar/260921-icr-l17`, base `c422dc00273d4ae7a5d8c9c8db97365b8c85d640`).
**Posture:** falsification. The worker's report was treated as marketing until reproduced. Everything
below was produced by my own commands in my own scratch trees under
`/home/firefox/projects/ar-coordination/temp/l17-verify/`. **No leaf byte was modified** — every
experiment ran against a byte-copy of the candidate in scratch (`…/l17-verify/cand/`), and the two
attack test modules live only there.

---

## Fingerprints

**Guard: PASSED — the candidate did not move during verification.**

| Point | `git -C <leaf> diff \| sha256sum` |
| --- | --- |
| start | `1c46bff40d835cb8f268a2a09eed7c1efbc8ec690f56ac97cd11fd4efe4e7d70` |
| end | `1c46bff40d835cb8f268a2a09eed7c1efbc8ec690f56ac97cd11fd4efe4e7d70` |

Untracked new files (start = end, byte-identical):

| file | sha256 |
| --- | --- |
| `dashboard/src/panels/review/ReviewReadCycle.ts` | `5c5968c86bc98d9f808b2dcbdac4f8ba5b926722dc29f6b04d9ec2db18ba97c6` |
| `dashboard/src/panels/review/ReviewRefresh.tsx` | `f6b30544c296a4397af22c31e296f037f1412c1edde786d336bdf90a19c46281` |
| `mcp/src/agents_remember/application/review_comparison_staleness.py` | `11e81de2d0360287791aa8d09c7e4790133ee97e98bd530389f7ea1e009dfe89` |

Start tracked hash matches the orchestrator's pre-measured `1c46bff40d835cb8…`. `HEAD` is still
`c422dc00…`; `git status --porcelain` is the same 9 `M` + 3 `??` (+`temp/`) at start and end.

---

## Obligation 1 — Base-defect witness (my own rebuild)

I rebuilt the base experiment **myself**, from `git archive` of the base commit (no worktree was
added to the shared repo), and proved purity by hashing every tracked path against
`git show c422dc00:<path>`:

```bash
git -C <leaf> archive c422dc00273d4ae7a5d8c9c8db97365b8c85d640 | tar -x -C $SCRATCH/base
ln -sfn /home/firefox/projects/agents-remember/dashboard/node_modules $SCRATCH/base/dashboard/node_modules
cp -r <leaf>/dashboard/styled-system $SCRATCH/base/dashboard/styled-system
cp <leaf>/dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx  $SCRATCH/base/dashboard/src/panels/detail-panel/
cp <leaf>/dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx    $SCRATCH/base/dashboard/src/panels/review/
# per-path proof: for every tracked file except those two, base-archive bytes == c422dc00 bytes
# → "0 UNEXPECTED DIFF" (loop printed nothing)
```

`test-utils.tsx` (which both candidate test modules import) is **byte-identical** between base and
candidate (`cmp … && echo IDENTICAL` → `IDENTICAL`), so the only overlaid bytes are test bytes.

```bash
cd $SCRATCH/base/dashboard && ./node_modules/.bin/vitest run \
  src/panels/detail-panel/reviewEntryRefusal.test.tsx src/panels/review/ReviewSurface.outcomes.test.tsx
```

Observed — **`Test Files 2 failed (2)` / `Tests 5 failed | 17 passed (22)`**, exit 1:

```
× re-reads the pair's subjects when the workspace republishes, and keeps the reader's subject
× offers an explicit refresh control that re-reads the pair on the reader's own click
× re-reads the same question carrying the binding identity on screen, and names what moved
× keeps the labelled old comparison and its error when the refresh fails
× never renders a slow earlier subject's answer over the subject selected now
    AssertionError: expected 'aaaa…' to be 'bbbb…'   # the late A answer overwrote B
```

Same two modules on the candidate (`$SCRATCH/cand/dashboard`): **`Tests 22 passed (22)`**, exit 0.

**Verdict on the claim: reproduced exactly.** The worker's §4.1 numbers are correct.

**Honest note the worker volunteered and I confirm:** of the three new entry-module cases, the case
"never lets an answer for a previous leaf overwrite the leaf on screen now" **passes on base** (it is
among the 17 passed). It is a regression guard, not a base-failure witness — exactly as report §6.2
says. The entry's base-failing case is the *invalidation* one (`expected [ Array(1) ] to have a
length of 2 but got 1`).

---

## Obligation 2 — Attacking the race handling

I wrote my own attack modules in scratch and drove the **real** `ReviewSurface` over the real client
(`$SCRATCH/cand/dashboard/src/panels/review/ReviewSurface.verify*.test.tsx`,
`…/detail-panel/reviewEntryRefusal.verify.test.tsx`). My stub server models the real route's admitted
semantics (shape-check, compare-only) — and I verified that model against the real composition
before trusting it (see below).

| Attack | Scenario the brief named | Result |
| --- | --- | --- |
| ATTACK-4 | failure response after a newer success | **PASS** (guard holds) |
| ATTACK-10 | refresh clicked while a read is in flight | **PASS** (`comparison = 2222…`, the older in-flight answer loses) |
| ATTACK-11 | unmount / remount | **PASS** (first read after remount carries nothing) |
| ATTACK-1 | two rapid subject switches after one refresh | **FAIL (defect)** |
| ATTACK-2 | same, user-visible sentence | **FAIL (defect)** |
| ATTACK-3 | recorded/history read interleaved with a live read | **FAIL (defect)** |
| ATTACK-5 | the failure path's own sentence | **FAIL (defect)** |

### The stub is faithful — proven against the real composition

Before reporting anything from ATTACK-1/2/3 I checked whether `binding_digest` is subject-scoped or
candidate-wide. **It is subject-scoped**, so a foreign carried digest really does produce `stale`:

```bash
PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support <venv>/python $SCRATCH/probe-subject-scope.py
```
```
subject A (retry_invariant_id)   : f5fab459-daef-4963-8ddf-69f90e951a1c
  comparison.binding_digest      : 4a1c5cc213f33efb6d875bdffe0533f3ee5d229da383a6bd02cf1e694aa8b512
subject B (sibling_invariant_id) : 1c6b2f03-1845-4f9c-84c4-48b0eefddcdc
  comparison.binding_digest      : 0b8619be3987dab29565b6834e794f661efd185fadcf835cdd22fd2dd8b74794
BINDING DIGESTS EQUAL (candidate-scoped)? False
reading subject B carrying A's binding digest (the leak):
  staleness.state                : stale
  staleness.previous_comparison_ref: 4a1c5cc213f33efb6d875bdffe0533f3ee5d229da383a6bd02cf1e694aa8b512
  submission.state               : disabled_stale
```

(I also probed the *page* dimension: `pageOf=records` / `pageOf=knowledge` produce the **same**
binding digest, so paging is not a false-stale trigger. Good — that removes an over-claim I would
otherwise have made.)

### ATTACK-1/2/3 output (candidate bytes)

```
ATTACK-1 B-READ = /api/review/intent?repo=agents-remember&master=260921_complete-code-and-intent-review
  &leaf=260921-ICR-L16&selectorKind=invariant&selectorId=subject-b
  &previousBindingDigest=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
ATTACK-2 notice = "the candidate published a new comparison (bbbb…bbbb) — the panes below are the
  comparison you were reading (aaaa…aaaa), replaced whole on the next read"
ATTACK-2 stale  = "Candidate changed — open a new comparison — previous input: aaaa…aaaa"
ATTACK-3 RECORDED-READ = …&selectorId=subject-a&history=recorded&previousBindingDigest=aaaa…aaaa
```

### ATTACK-5 output (candidate bytes)

```
ATTACK-5 notice = "the comparison below is still the candidate's current one (1111…1111)"
ATTACK-5 state  = current
ATTACK-5 failure= "the review could not be opened (network): the review read could not reach the
  server: fetch failed…retry the review read"
```

### ATTACK-9 (MutationObserver — the one place I corrected myself)

ATTACK-6 (marker absent fresh / in flight / after settle) was not decisive on its own, so I captured
**every DOM commit** while the projection moved:

```
ATTACK-9 stale-marker nodes ever added to the DOM: [" · workspace facts changed"]
ATTACK-9 catalogueStale attribute after settle: ["false"]
```

So the marker **is** committed exactly once and withdrawn in the same flush; it is never present in a
settled DOM state. Finding F4 is worded to say that, not the stronger thing.

### Is the delivered guard only defence-in-depth, and is the report honest about it?

**Yes and yes.** The sequence guard in `ReviewReadCycle.ts` demonstrably covers what the base effect
cleanup flag cannot (ATTACK-10 passes on the candidate; the same scenario is the class the worker
describes). Report §6.2's wording — "defence in depth … a regression guard rather than a
base-failure witness" — matches my measurement, and the worker names the base-failing race it *did*
find (the surface-side slow-A-after-B). That is honest reporting.

---

## Obligation 3 — Server route contract (my own probe)

```bash
PYTHONPATH=<leaf>/mcp/src:<leaf>/mcp/test_support <venv>/python $SCRATCH/probe-route-contract.py
```
```
A. declared fields: ['history', 'previous_binding_digest', 'selector_id', 'selector_kind']
A. type metadata: Annotated[str | None, Query(alias='previousBindingDigest')]
A. ADMITTED ALIAS: previousBindingDigest
B. valid digest -> status 404 | port received: 'aaaa…aaaa'
C. malformed -> status 400 | offendingInput: 'not-a-digest'
   spelling=''          len=0   -> 404 offendingInput=None
   spelling='AAAA…AAAA' len=64  -> 400 offendingInput='AAAA…AAAA'
   spelling='aaa…a'     len=63  -> 400 offendingInput='aaa…a'
   spelling='aaa…a'     len=65  -> 400 offendingInput='aaa…a'
   spelling='ggg…g'     len=64  -> 400 offendingInput='ggg…g'
   spelling='aaa…a '    len=65  -> 400 offendingInput='aaa…a '
D. omitted -> status 404 | port received: None
E. request model field present: True ; model_dump contains it: True
   json schema: {'anyOf': [{'pattern': '^[0-9a-f]{64}$', 'type': 'string'}, {'type': 'null'}],
                 'default': None, 'title': 'Previous Binding Digest'}
F. every use of previous_binding_digest in the comparison module:
      identity: ComparisonIdentity, previous_binding_digest: str | None
      if previous_binding_digest is None or previous_binding_digest == identity.binding_digest:
      previous_comparison_ref=previous_binding_digest,
```

* **Malformed → 400 naming the offending input**, never 500 and never an uncaught validation error,
  for six malformed spellings. ✅ (status 404 in B/D is the *stub port's* own refusal — the probe
  measures what the transport forwarded.)
* **Client spelling matches the server alias exactly**: `data/review.ts` `PREVIOUS_BINDING_QUERY =
  "previousBindingDigest"` vs the route's `Query(alias='previousBindingDigest')`. ✅
* **Optional** (default `None`, omitted → port sees `None`). ✅
* **Only ever compared**: the module has exactly one `==` against `identity.binding_digest` and one
  copy into `previous_comparison_ref`; it never resolves, selects or hashes. ✅
* On "not serialized back": the field is on the **request** model (so `model_dump()` of the request
  contains it), and it does **not** appear as an echoed response field — the response carries only
  `ReviewStaleness.previous_comparison_ref`, which the packet requires ("carry previous binding
  identity where staleness is compared"). The report's phrasing is accurate in the sense that matters.
* `stale`/`current` semantics are the packet's: `current` when the carried digest equals the
  rendered comparison's binding digest **or** nothing was carried; `stale` (with
  `previous_comparison_ref` + `moved=("comparison-binding",)` + `submission.state="disabled_stale"`)
  otherwise. ✅

---

## Obligation 4 — Preservation boundaries and no polling

**Frozen/historical read stays pinned — PASS (ATTACK-7/8):**
```
ATTACK-7 recorded first read   = …&selectorId=subject-a&history=recorded
ATTACK-7 recorded refresh read = …&selectorId=subject-a&history=recorded&previousBindingDigest=aaaa…aaaa
✓ ATTACK-7   (refresh does not turn a recorded read into a live one)
✓ ATTACK-8   (live read carries no history=; recorded read does)
```
The record addressed never changes: the refresh re-asks the **same** `history=recorded` question. The
live binding it carries is wrong (F1), but it is only ever *compared*, so the frozen link itself is
not un-pinned — there is no fallback to the live candidate.

**No polling loop introduced — PASS:**
```bash
grep -n "setInterval\|setTimeout\|requestAnimationFrame\|setImmediate" <the 5 changed dashboard files>
# → no matches in any of them (base: 0 occurrences in the same files' base bytes)
```
Reads are triggered only by (a) the question changing (`ReviewReadCycle.ts:220-248` effect keyed on
`targetKey`), (b) the reader's click (`review-refresh` / `review-catalogue-refresh`), and (c) the
store's projection value moving for the entry (`changeSetBar.tsx:266` deps). The channel is real:
`serving/delta.py:150-151` appends `DeltaEvent("analytics", current.analytics)` only when
`prev.analytics != cur.analytics`, and `data/store.ts:213-216` replaces `analytics` wholesale behind
`stableEquals` (returns `null` — no state change — when equal). ✅

---

## Obligation 5 — Truthfulness of every new user-visible sentence

| Sentence | True about the store? |
| --- | --- |
| `⟳ refresh` / `⟳ refresh subjects` + their `title` text | **true** — they are re-reads of the real route |
| `the comparison below is still the candidate's current one (…)` | **false/unmeasured after a failed refresh** — F2 |
| `the candidate published a new comparison (X) — the panes below are the comparison you were reading (Y)` | **true when the reader refreshed; false when it fires after a question change** — F1 |
| `· workspace facts changed` | **true but imperceptible** — committed once, never in a settled state — F4 |
| the entry's marker claim "never that the candidate changed" (code comment) | **true** — the projection carries no candidate digest and the code claims only that facts moved |

**Does the notice claim the candidate changed when the projection carries no candidate digest?**
The *entry* does not — it says "workspace facts changed", and its comment says so deliberately. The
*review pane* notice does claim a candidate publication, but it claims it off the server's own
`staleness` answer (a real comparison), not off the projection. So the specific failure mode the
brief worried about is avoided; the false-claim risk that does exist is F1/F2.

**Failure path:** the old generation is retained and labelled and the error stays visible — verified
by the worker's own passing case plus my ATTACK-5 run, where `comparison = 1111…` survived the
failure and `review-failure` rendered. The **addition** the requirement did not ask for is the
currency claim beside it (F2).

---

## Obligation 6 — Seam policy compliance (my own checks)

```bash
grep -rn "_comparison_identity\|_staleness\b" mcp/ --include=*.py   # only unrelated sidecar_staleness names
grep -n "def _comparison_identity\|def _staleness" mcp/src/…/knowledge_review.py   # NONE
grep -rn "review_comparison_staleness" mcp/ --include=*.py   # exactly: adapter docstring + the import
grep -c "return ComparisonIdentity(" mcp/src/agents_remember/application/*.py
#   review_comparison_staleness.py:1   — every other file: 0
git diff -U0 -- mcp/src/…/knowledge_review.py | grep "__all__"   # __all__ NOT touched
```

* **One implementation**: `comparison_identity` and `review_staleness` exist in exactly one module;
  the adapter keeps no copy. ✅
* **Thin delegator**: the adapter imports the two names and calls `review_staleness(identity,
  request.previous_binding_digest)` at `knowledge_review.py:466`; the two private helpers are gone
  (the diff removes `_comparison_identity` and `_staleness` entirely). ✅
* **No stale importer of the old private names** anywhere under `mcp/`. ✅
* **`__all__` unchanged**; the moved names are now public and *are* imported by the adapter, so the
  de-facto re-export posture holds. The report's justification for needing no alias shim ("both moved
  names were private to the adapter and nothing under `mcp/` imported them") is accurate at base.
* **Adapter line count falls** 1077 → 1041 — the seam moved *out*, no logic added. ✅
* **Docstring claims accurate**: the new module's claims (carries, never recomputes; compared, never
  resolved; adapter over rail) all match the bytes. Its "the model itself refuses a `stale` state with
  no previous reference" claim is a real model invariant
  (`models/knowledge/review.py` `_require_the_submission_state_to_follow_staleness`). ✅
* **One behavioural change is smuggled in, and it is disclosed**: `read_knowledge_review` and
  `compose_review` lose the `previous_binding_digest` keyword and read it from the request instead.
  Both call sites were updated; the Python suite is green. This is a contract change to internal
  callables, disclosed in report §1 and §6.3 — not hidden.

---

## Obligation 7 — Rails

| Check | Command | Observed |
| --- | --- | --- |
| pyright | `<venv>/bin/pyright --pythonpath <venv>/bin/python <5 changed .py>` (`npx --no-install pyright` fails: no npm package installed; the venv ships pyright 1.1.411) | **`0 errors, 0 warnings, 0 informations`** |
| ruff check | `<venv>/bin/python -m ruff check <5 files>` | **`All checks passed!`** |
| ruff format | `<venv>/bin/python -m ruff format --check <5 files>` | **`5 files already formatted`** |
| eslint | `./node_modules/.bin/eslint <7 changed dashboard files>` | **exit 0, no output** |
| tsc | `./node_modules/.bin/tsc --noEmit -p tsconfig.json` | **exit 0, clean** |
| dashboard suite | `./node_modules/.bin/vitest run` | **`Test Files 158 passed (158)` / `Tests 1671 passed (1671)`** — matches the report exactly |
| review-surface module | `pytest mcp/tests/test_knowledge_review_surface.py -q -m ''` | **`31 passed in 37.39s`** (base 30 → +1, as claimed) |
| census `-m ''` | `pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''` | **`5 passed in 57.05s`** |
| new `# noqa` | `git diff -U0 \| grep "^+" \| grep -i "noqa\|eslint-disable\|@ts-ignore\|type: ignore"` | **NONE added** |
| budgets | `pytest mcp/tests --collect-only -q -m 'not integration'` / `-m 'integration'` | **`2682/2703 collected`** vs budget 4000; **`435/3117 collected`** vs budget 1000 — both satisfied |
| catalog | `grep -c "^\[\[contract\]\]" / "^\[\[artifact\]\]" mcp/tests/evidence-lifecycle.toml` | **16 contracts / 66 artifacts**; TOML sha256 `4ab067e360c7807c3051ad71058c09058225f1e9155e7111da6e95c73cac0258` == the pinned `LIFECYCLE_CATALOG_SHA256`; the pin file and the TOML are untouched by the candidate (`git status --porcelain` empty) → **no re-pin required** |
| gates | `pytest mcp/tests/test_evidence_catalog_gate_boundaries.py -q -m ''` | **`1 passed in 17.04s`** |

**File-size rail (repo's own detector, `FILE_SIZE_HARD_LIMIT = 1200`):**

```
scope: file-size | config=File Size Budget standard; hard limit 1200 … | 5 measured files
1 file(s) at or above the 1200-line hard limit:
    1395 lines  hard-limit-exceeded  mcp/tests/test_knowledge_review_surface.py
result: file-size FAIL (hard limit exceeded)
```

| file | base | candidate | band |
| --- | --- | --- | --- |
| `application/knowledge_review.py` | 1077 | **1041** | under (improved) |
| `application/review_comparison_staleness.py` | — | 97 | under (new) |
| `models/knowledge/review.py` | 1189 | **1198** | under, 2 lines of headroom |
| `serving/review.py` | 672 | 757 | under |
| `tests/test_knowledge_review_surface.py` | **1313** | **1395** | **hard-limit-exceeded at base AND at candidate — made worse by +82** |
| `ReviewSurface.tsx` | 1031 | 986 | improved |
| `changeSetBar.tsx` | 435 | 543 | under |
| `data/review.ts` | 633 | 672 | under |
| `ReviewReadCycle.ts` / `ReviewRefresh.tsx` | — | 257 / 101 | under |

**Whole-tree census:** files ≥1200 lines — **22 at base, 22 at candidate** → the candidate introduced
**no new offender**; it did make one existing offender worse (F5).

---

## Obligation 8 — Packet clause by clause, against the bytes

| Packet clause | Status |
| --- | --- |
| "An open task/review notices new candidate generations" | **delivered for the entry** (base-failing case reproduced: re-read on projection movement, no remount). **For the review pane it is click-gated** — conforming, because the packet itself mandates an explicit control rather than auto-replacement. The pre-click half is imperceptible (F4). |
| "…and never mixes or races their contents" | **delivered** — sequence guard + keyed retention; ATTACK-4/10/11 pass, the surface's base-failing slow-A-after-B is fixed. |
| "Use existing event/invalidation infrastructure" | **delivered** — real `analytics` delta channel, no new channel/endpoint. |
| "plus an explicit refresh control" | **delivered** — two real controls, both re-read the real route. |
| "While viewing a pinned generation, show a new-candidate notice **or** deliberately replace the entire bundle" | **delivered at the refresh boundary** (both, together). The pre-click notice is not perceptible (F4). |
| "Carry previous binding identity where staleness is compared" | **delivered but leaky** (F1): the identity is carried on the request and compared in the moved rule, but it is carried into reads that are not replacing the display it names. |
| "Ignore or cancel superseded requests and preserve the selected target across response races" | **delivered** — newest-read-wins plus narrowing-only selection. |
| "A failed refresh retains the labeled old generation with its error" | **delivered** — retained payload + labelled comparison + visible failure. The notice adds an unmeasured currency claim (F2). |
| "A late response must never overwrite a newer subject/task" | **delivered** — proven base-failing on the surface, and re-proven independently by ATTACK-4/10. |
| "No new unbounded polling loop" | **delivered** — no timers, two trigger classes only. |
| Preservation: "A frozen historical link stays pinned and never silently follows live changes" | **delivered** — ATTACK-7/8. |
| Preservation: "existing Git/publication authorities stay the owners" | **delivered** — nothing persisted, no new record kind/table/route; the request gained one optional field. |
| Forbidden: "silent fallback to current HEAD/current knowledge, browser-selected dataset" | **not present** — the digest cannot select or resolve; the route still resolves from canonical task context (probe F and `review_comparison_staleness.py`'s only `==`). |
| Forbidden: "generated semantic verdict" | **not present** — the notice names two identities only. |
| Forbidden: "parallel store/merge/review authority" | **not present** — no store, one implementation per rule. |
| Scope: "Move a touched responsibility out of the over-limit review adapter before adding behavior; do not duplicate its implementation" | **delivered** — 1077 → 1041, one implementation, no fork (obligation 6). |
| Expected Evidence: "a focused behavioral test **or reproducible operation/browser scenario**"; "User acceptance mapping: A12 A13" | **partially delivered** — see F3. The dashboard cases mount the real component over the real client and real store (asserting the query strings the real client built and the rendered DOM), and the Python case drives the real route and real composition over a real fixture. A **browser + real publication** scenario was not run. The master's own owner map marks R17 **yes** on "Mounted browser / production composition" and lists R17 among the mandatory leaves. |
| Expected Evidence: "Tests that merely mirror implementation or assert returned prebuilt payloads are insufficient" | **satisfied** — no dashboard assertion reads a prop the test passed; every one reads rendered DOM or the client-built query string. |

---

## Findings

### F1 — `{id: L17-F1, severity: high, file: dashboard/src/panels/review/ReviewReadCycle.ts:195,216-217,236-248,250-254}`

**What.** `carried` is sticky. It is set only in `refresh()` (`:250-254`) and never reset when the
question changes, while the read effect passes `previous: context.carriedRef.current` on **every**
read (`:147`) — including reads for a different subject, a different leaf, or `history="recorded"`.
The effect's dependency array (`:236-248`) includes `carried`, so once it is set it is carried into
every subsequent question until the component unmounts.

**Evidence (reproduced on candidate bytes).**
```
ATTACK-1 B-READ = …&selectorId=subject-b&previousBindingDigest=aaaa…aaaa
ATTACK-2 notice = "the candidate published a new comparison (bbbb…) — the panes below are the
                  comparison you were reading (aaaa…), replaced whole on the next read"
ATTACK-2 stale  = "Candidate changed — open a new comparison — previous input: aaaa…"
ATTACK-3 RECORDED-READ = …&selectorId=subject-a&history=recorded&previousBindingDigest=aaaa…aaaa
```
and on the real composition (`probe-subject-scope.py`): subject-scoped binding digests
(`4a1c5cc2…` ≠ `0b8619be…`), and a foreign carried digest yields `staleness.state = stale`,
`submission.state = disabled_stale`.

**Why it matters.** Two false user-visible sentences (the refresh notice and the server's stale
region) plus **submission disabled** for a subject the reader merely selected — and, in the ATTACK-3
case, a **frozen recorded read labelled as stale against a live identity**. The refresh notice is the
feature's own headline artefact, so this is a correctness defect in the delivered guard, not cosmetics.

**Reachability, stated precisely.** In today's shipped shell this is **latent**: the only mount is
`Cockpit.tsx:580` inside `ChangeSetTakeover`, with **no `key`**, but `RailedBody` is
`display:none` + `aria-hidden` whenever `state.takeover` is true (`Cockpit.tsx:825-834`, line 453
`takeover = Boolean(changeSet) || notesOpen`), so the reader cannot open another target while the
surface is mounted, and there is no URL routing (`grep popstate|pushState|useNavigate` → none). I
could **not** demonstrate a click path to the false notice in the shipped shell. It is nonetheless a
real defect at the component boundary the delivery's **own** R17 test treats as in scope: that test
re-renders the same mounted surface with `selectorId="subject-newer"`
(`ReviewSurface.outcomes.test.tsx:657`) and asserts correctness across exactly the transition on
which `carried` leaks.

**Suggested fix.** Make the carried identity part of the question: store `{key, digest}` and send
`previous` only when `key === targetKey` (and clear it in `startRead` when `askedFor` changes), so a
read that is not replacing the display it names carries nothing.

### F2 — `{id: L17-F2, severity: high, file: dashboard/src/panels/review/ReviewRefresh.tsx:74,89-100}`

**What.** `generationOf(carried, shown)` decides `current` vs `superseded` from two digests alone and
never consults the read phase. After a **failed** refresh, `carried` was just set from the retained
payload (`ReviewReadCycle.ts:251-252`) and `shown` is that same retained payload, so the notice
renders `current`.

**Evidence (candidate bytes).**
```
ATTACK-5 notice = "the comparison below is still the candidate's current one (1111…1111)"
ATTACK-5 state  = current
ATTACK-5 failure= "the review could not be opened (network): the review read could not reach the server"
```
The two blocks render side by side. Reachable on the first network flap after clicking refresh.

**Why it matters.** The refresh exists to answer "has the candidate moved?". When the answer is
unknown, the UI answers "still current". The module's own docstring states the rule it breaks:
"nothing has been compared against the displayed identity yet, and claiming either state would assert
a measurement nobody made" (`ReviewRefresh.tsx:31-34`). In the requirement's own scenario — first
ingest, then a subsequent edit — a failed refresh tells the reader the comparison is still current
when it may have moved. The delivery's own test pins this behaviour
(`ReviewSurface.outcomes.test.tsx:599-620`, asserting `generationState === "current"` after a failed
refresh), so it is a deliberate implementation choice, not an oversight. Under this master's rule
that a false user-visible sentence is blocking, a stricter reading of the same evidence would grade
this blocking; I grade it **high** because the sentence is an unverified present-tense claim rather
than a provably false one.

**Suggested fix.** Thread the read phase (or a `carryingReadAnswered` flag) into `generationOf` and
render no generation claim when the read that carried the identity did not answer; keep the failure
block as the only statement in that state.

### F3 — `{id: L17-F3, severity: medium, file: temp/icr/report-l17.md:200-211}`

**What.** The A12 boundary the master's own owner map makes mandatory for this leaf was not
demonstrated in any form.

**Evidence.** `notes/01-leaf-owner-map.md:227-228` — `R17 | yes (2 new modules) | no | **yes** — A12
boundary = actual publication/invalidation + browser`; `:241` — "Browser / production-composition
leaves: R14, **R16, R17**, R24, R25, R26 mandatory". Packet §Expected Evidence requires
"a focused behavioral test **or reproducible operation/browser scenario**". The worker's §4.3
discloses the gap and attributes it to "no browser and no live dashboard process … inside this
enclosure". That environment claim is **partly inaccurate**: Playwright chromium **is** installed
(`~/.cache/ms-playwright/chromium-1223`, `chromium_headless_shell-1223`) and
`dashboard/playwright.production.config.ts` exists (`webServer: npm run preview … :4173`), and a Vite
dev server answers on `127.0.0.1:5173` (`curl` → 200). What is genuinely absent is a live dashboard
process wired to *this leaf's* real publication.

**Why it matters.** The delivered evidence stops at jsdom + a stubbed `fetch` on the browser side.
The Python case proves the production route and composition; the browser↔server transport and a real
ingest/publication are not exercised end-to-end, so the packet's strongest verification class is
unmet. This is a verification gap, not an implementation gap.

**Suggested fix.** Either run an e2e scenario (open the review, publish, refresh, assert both
identities and request bounds) against the production config, or have the master record an explicit
acceptance deferral for A12 naming what remains to be measured and by whom.

### F4 — `{id: L17-F4, severity: low, file: dashboard/src/panels/detail-panel/changeSetBar.tsx:156,161,242,266}`

**What.** The entry's pre-click affordance `· workspace facts changed` is not observable. `stale` is
`!read.loading && read.facts !== facts` (`:242`), and the same effect that observes the projection
move immediately sets `loading: true` and re-reads (`:266` deps; the effect body sets
`{...previous, loading: true, stale: false}`).

**Evidence.** ATTACK-6 — marker `null` when fresh, `null` while the re-read is in flight, `null`
after settling. ATTACK-9 (MutationObserver over every DOM commit) —
`[" · workspace facts changed"]` added exactly once, and `data-catalogue-stale="false"` after settle.
The delivered tests only ever assert `"false"`.

**Why it matters.** The code comment at `:146-149` claims "it is marked when the projection has moved
since the catalogue was read, **so a reader is told the list may be behind before they click**". That
reader-facing effect does not obtain; the reader's click is the only path, and by then the marker is
gone. It is a dead affordance plus an inaccurate comment — not a false sentence, hence low.

**Suggested fix.** Either remove the marker and its comment claim, or record the facts value at the
moment each answer was filed and derive `stale` from that stored value rather than from the live one.

### F5 — `{id: L17-F5, severity: medium, file: mcp/tests/test_knowledge_review_surface.py:1}`

**What.** The module is **1395** lines, above the repo's 1200-line hard rail
(`mcp/test_support/agents_remember_test_support/code_quality/file_size.py:28`), and the candidate made
an **already-over** file worse: base **1313** → candidate **1395** (+82).

**Evidence.** Repo's own detector, on the changed files:
`1 file(s) at or above the 1200-line hard limit: 1395 lines hard-limit-exceeded
mcp/tests/test_knowledge_review_surface.py` / `result: file-size FAIL (hard limit exceeded)`.
Whole-tree census: **22 files ≥1200 at base, 22 at candidate** → no new offender, but this one grew.

**Why it matters.** The brief instructs that an existing offender made worse must be reported as a
finding, not hidden. The worker disclosed it (report §6.1) with the cross-leaf justification (the
module is in 17 leaves' test scope) and asked for a ruling — the right posture — but the fact still
stands against the rail.

**Suggested fix.** Master-level ruling: either accept the growth for the remaining leaves of the
series, or schedule a dedicated extraction leaf for this module's shared fixture helpers.

### Unreproduced

* **The routed "in-flight prop-change race in the review entry hook" as a base failure** — I confirm
  the worker's own admission. On base the entry case "never lets an answer for a previous leaf
  overwrite the leaf on screen now" **passes** (it is among the 17 passed at base); the base-failing
  entry case is the invalidation one. The delivered sequence guard is genuine defence-in-depth
  (ATTACK-10 passes on the candidate) and the report describes it accurately. **No finding.**
* **A12 — a real publication while a real browser keeps the panel open.** Not reproduced; not
  attempted by me either, since no dashboard process is wired to this leaf's real coordination
  state. Reported as F3 rather than as a falsified claim.
* I found **no** case where `previousBindingDigest` selects, resolves or redirects a generation, and
  **no** case where a late response wins (ATTACK-4/10/11 all pass).

---

## True strengths of the delivery (recorded so the record is balanced)

1. **The packet's core defect is genuinely fixed and the worker's base witness is exact.** My own
   base tree reproduces `5 failed | 17 passed (22)`; the candidate is `22 passed (22)`. The entry
   really does now discover data published after the panel opened, without a remount.
2. **The packet's boundary example is genuinely base-failing and genuinely fixed.**
   `never renders a slow earlier subject's answer over the subject selected now` fails on base with
   `expected 'aaaa…' to be 'bbbb…'` and passes on the candidate.
3. **Race handling is real and layered.** ATTACK-4 (late failure for a superseded subject),
   ATTACK-10 (refresh clicked while a read is in flight beats the older in-flight answer) and
   ATTACK-11 (unmount/remount starts clean) all pass. The sequence guard covers precisely the case
   the base effect-cleanup flag cannot see, exactly as claimed.
4. **The server contract is clean and independently verified**: exact client/server alias match, 400
   with a named `offendingInput` for six malformed spellings, tolerant of the empty form spelling,
   optional, and provably compare-only (`probe-route-contract.py` F).
5. **The seam move is textbook**: one implementation, no fork, `__all__` untouched, the adapter
   shrinks 1077 → 1041, and no module under `mcp/` still references the old private names.
6. **Preservation holds**: a `history="recorded"` read stays a recorded read across a refresh
   (ATTACK-7), and a live read carries no history (ATTACK-8).
7. **No polling, and the invalidation channel is the real one** — verified on both sides of the wire
   (`delta.py:150-151` gated on a changed projection; `store.ts:213-216` wholesale replacement behind
   `stableEquals`).
8. **Rails are green**: pyright 0/0/0; ruff check + format clean; eslint clean; `tsc` clean; the full
   dashboard suite passes 158 files / 1671 tests; the review-surface module 31 passed (base 30); the
   census passes under `-m ''`; both case budgets are satisfied (2682/4000 and 435/1000); the catalog
   is still 16 contracts / 66 artifacts with the pinned sha matching and no re-pin; no new `# noqa`;
   no new file crosses the 1200-line rail.
9. **The report is unusually honest.** It volunteers the non-reproduction of the routed race, the
   over-rail test module, the scope of what was not measured, and the semantic limitation of the
   carried digest (§6.1-6.6). I falsified none of its factual claims except the environment assertion
   in §4.3 that no browser was available (F3).

---

## Verdict

**`fail`** — two **high** findings (F1, F2), plus one medium (F3), one low (F4) and one medium rail
finding (F5).

The delivery is a real, working implementation of most of ICR-R17: the packet's core defect and its
own boundary example are both genuinely base-failing and genuinely fixed, the race guards hold under
five independent attacks, the server contract is exact and independently verified, the seam move is
clean, preservation holds, no polling was introduced, and every rail is green with a report whose
factual claims I could reproduce almost without exception. It fails on truthfulness of the added
sentences and on the correctness of the identity it carries. `ReviewReadCycle.ts` sends the
identity of a comparison the reader was looking at into reads that are not replacing it (F1) —
reproduced end to end, including the real composition's `stale` + `disabled_stale` answer — and
`ReviewRefresh.tsx` then prints both a false "the candidate published a new comparison" notice and,
after a failed refresh, an unmeasured "still the candidate's current one" (F2). F1 is latent in
today's shell (I could not reach it by clicking; the railed body is hidden under a takeover and there
is no URL routing), but it fires on exactly the no-remount question change that this delivery's own
R17 test asserts must be correct, so it cannot be waved off as unreachable-by-design. Both are
localised fixes in two new modules and neither requires touching the server contract.
