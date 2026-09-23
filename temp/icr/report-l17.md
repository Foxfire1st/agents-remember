# L17 — ICR-R17@v1 "Coherent live refresh": implementation report

**Leaf:** `260921-ICR-L17` (sole primary requirement ICR-R17@v1) under atomic master `260921-ICR`.
**Code worktree:** `/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l17-ar/260921-icr-l17`
(branch `ar/260921-icr-l17`), base commit `c422dc00273d4ae7a5d8c9c8db97365b8c85d640`.
**Requirement packet:** `.../requirements/ICR-R17-v1-coherent-live-refresh.md` (read in full, SHA256 `ae35e2df…4a48d7`).
**Git:** no commit, no push, no closeout, no memory operation was run. The worktree is left dirty; the
orchestrator commits.

---

## 1. What the packet asked, clause by clause, and what implements it

| Required Behaviour clause | Implementation |
| --- | --- |
| "Use existing event/invalidation infrastructure" | `dashboard/src/panels/detail-panel/changeSetBar.tsx`: `DocChangeSetBar` subscribes to the store's existing projection value (`useDashboard((s) => s.analytics)`, the `/api/state` snapshot + `analytics` delta channel) and folds it into `reviewDependencyFacts(analytics)`; `useReviewCatalogue` re-asks `/api/review/intent/entries` when that value moves. No new channel, no new endpoint, no timer. |
| "plus an explicit refresh control" | `ReviewCatalogueRefresh` ("⟳ refresh subjects") beside the entry; `ReviewRefresh` ("⟳ refresh") in the review pane header (`dashboard/src/panels/review/ReviewRefresh.tsx`). Both are the reader's own action; nothing polls. |
| "While viewing a pinned generation, show a new-candidate notice or deliberately replace the entire bundle" | Both, and they are the two halves of one read: the entry re-reads the catalogue when the projection moves (replacing the catalogue wholesale, keeping the reader's selection when the new answer still lists it); the review pane replaces the **entire** response and shows `review-generation-notice` with `data-generation-state="superseded"`, `data-previous-binding`, `data-current-binding`, while the server's own `staleness.statement` and previous reference render beside it (`data-testid="review-stale"`). |
| "Carry previous binding identity where staleness is compared" | New optional request field `ReviewSurfaceRequest.previous_binding_digest` (`models/knowledge/review.py`), admitted at the route from `?previousBindingDigest=…` (`serving/review.py`), supplied by the browser (`data/review.ts::intentReview` 9th argument, sent only by a read that replaces a display), and compared in `application/review_comparison_staleness.py::review_staleness` (the moved `_staleness`). |
| "Ignore or cancel superseded requests" | `ReviewReadCycle.ts`: every read takes the next sequence number; only the newest may write the read state, and the effect's cleanup supersedes the read it started. The entry hook keeps the same guard (`reads`/`live()`). |
| "preserve the selected target across response races" | `LeafEntries`' selection is only ever narrowed to the catalogue (`listed.find(...) ?? listed[0]`), and the sequence guard means a superseded catalogue answer cannot rewrite the picker. |
| "A failed refresh retains the labeled old generation with its error" | The failure branch writes `read` only; `retained` is untouched, so `shownPayload` keeps rendering the last coherent payload, still labelled, with the failure block beside it. Asserted by a case. |
| "A late response must never overwrite a newer subject/task" | The sequence guard above; asserted by a case that fails on the base bytes (§4). |
| "No new unbounded polling loop" | Verified by inspection: no `setInterval`/`setTimeout`/`requestAnimationFrame` in any changed file (`grep` in §5). Reads happen on question change or on the reader's click. |

**Server-side seam (binding master policy, `notes/03-adapter-seam.md`).** The responsibility R17 touches
inside the over-limit adapter — the comparison's declared identity and the staleness it earns against a
previous binding — was moved out into a purpose-named adjacent module:

- **new** `mcp/src/agents_remember/application/review_comparison_staleness.py` (97 L): `comparison_identity`
  and `review_staleness` (the moved `_comparison_identity` and `_staleness`, one implementation each).
- `application/knowledge_review.py` **1077 → 1041 L**: the two private helpers are gone, the adapter
  imports the new module's names and calls them; no logic was added there, and its module docstring now
  records the sixth delegated responsibility (as L1's pattern records the others).
- No re-export shim was needed because both moved names were private to the adapter and nothing under
  `mcp/` imported them (verified: `grep -rn "_comparison_identity\|_staleness" mcp/`), which is the same
  posture the seam note records for the earlier private-only moves.
- `read_knowledge_review` and `compose_review` lost their `previous_binding_digest` keyword: the previous
  identity now travels on the **request** (one spelling of what was asked), and the composition reads
  `request.previous_binding_digest`. `serving/review.py` gained `AdmittedQuestion` +
  `_admitted_binding_digest` (shape-admitted in the route's own vocabulary, like `pageSize`, so a bad
  spelling is a 400 naming the offending input rather than an uncaught validation error).

**File:line of every production change**

| File | Change |
| --- | --- |
| `dashboard/src/data/review.ts:480-527` | `intentReview(..., previousBindingDigest?)` + `reviewQuery` (the query assembled once); `PREVIOUS_BINDING_QUERY` constant at `:552`. |
| `dashboard/src/panels/detail-panel/changeSetBar.tsx:121-136` | `reviewDependencyFacts` — the projection as the invalidation value, with what it is and is not honest about. |
| `…/changeSetBar.tsx:138-165` | `ReviewCatalogueRefresh` — the explicit control and its "workspace facts changed" marker. |
| `…/changeSetBar.tsx:192-215` | `catalogueAnswer` — one body → one read state (pure). |
| `…/changeSetBar.tsx:217-273` | `useReviewCatalogue(repo, master, leaf, facts)` — invalidation, sequence guard, refresh. |
| `…/changeSetBar.tsx:398-412, 503-512, 530-538` | `LeafEntries`/`DocChangeSetBar` thread the facts and render the control. |
| `dashboard/src/panels/review/ReviewReadCycle.ts` (new, 257 L) | `targetKeyOf`, `askReview`, `startRead`, `useReviewReadCycle` — one question, one in-flight read, newest-read-wins, refresh carrying the displayed binding. |
| `dashboard/src/panels/review/ReviewRefresh.tsx` (new, 101 L) | `ReviewRefresh` (control + notice) and `generationOf` (which generation the notice describes, read off the payload the panes render). |
| `dashboard/src/panels/review/ReviewSurface.tsx:911-935, 955-975` | uses the read cycle; header hosts the control; retry re-reads through the same path. |
| `mcp/src/agents_remember/models/knowledge/review.py:248-300` | `ReviewSurfaceRequest.previous_binding_digest` (sha256-shaped, optional) + docstring. 1189 → 1198 L, still `under-limit`. |
| `mcp/src/agents_remember/serving/review.py:98-101, 236-241, 350-420, 441-505, 622-645` | the query alias, `_SHA256_DIGEST` compiled from the models' own `SHA256_PATTERN`, `_admitted_binding_digest`, `AdmittedQuestion`, `_admitted_question`, and the route passing the admitted digest onto the request. |
| `mcp/src/agents_remember/application/review_comparison_staleness.py` (new) | the moved identity + staleness rule. |
| `mcp/src/agents_remember/application/knowledge_review.py` | thin delegator: imports the two names, reads `request.previous_binding_digest`, drops the keyword parameters, keeps `__all__` unchanged. |

---

## 2. Preservation boundaries and forbidden overreach — what I checked

- **A frozen historical link stays pinned.** `history="recorded"` reads (the leaf's durable generation,
  L12) are unchanged. The refresh control sends `previousBindingDigest` only when a payload is displayed;
  for a recorded read the payload's comparison is the recorded one, and a subsequent recorded re-read is
  still a read of that same record — the route resolves `recorded` and nothing else, and the digest is
  only ever compared, never resolved. No fallback to a live candidate was added.
- **No silent fallback / no browser-selected dataset.** The only new inputs are (a) the store's own
  projection value and (b) an opaque digest the server itself published. Neither addresses a dataset: the
  route still resolves the candidate from canonical task context, and the digest field's shape is checked,
  not resolved.
- **No generated semantic verdict.** The notice states two *identities* and which one is on screen; it
  says nothing about the candidate's meaning. The entry's marker says "workspace facts changed", not "the
  candidate changed" — the projection carries no candidate digest, so the stronger claim would be a
  measurement nobody made.
- **No parallel store/merge/review authority, no schema rewrite, no new assessment/approval authority.**
  Nothing was persisted; the request model gained one optional field that is not serialized back; no new
  record kind, table, or route.
- **Canonical identity / authored meaning / mechanical detection / execution observations / judgement stay
  separate.** The moved module compares two published digests and copies four identity fields; it authors
  no record and classifies nothing.
- **What the client cannot do.** `previousBindingDigest` cannot select a generation: the comparison
  rendered is always the resolved candidate's, and a digest that does not match is reported as `stale`
  (the honest answer for a reader who was looking at another generation), never as a request to read it.

---

## 3. Tests added (all existing modules; no new test module, so no catalog re-pin)

| Module | Case | Protects |
| --- | --- | --- |
| `dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx` | "re-reads the pair's subjects when the workspace republishes, and keeps the reader's subject" | the entry's invalidation dependency (packet's core defect) and selection preservation |
| same | "offers an explicit refresh control that re-reads the pair on the reader's own click" | the explicit control is a real re-read of the real route |
| same | "never lets an answer for a previous leaf overwrite the leaf on screen now" | the request-race invariant across a prop change |
| `dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx` | "re-reads the same question carrying the binding identity on screen, and names what moved" | the carried identity, the whole-bundle replacement, both identities on screen, the server's staleness statement |
| same | "keeps the labelled old comparison and its error when the refresh fails" | failed refresh retains the labelled old generation with its error |
| same | "never renders a slow earlier subject's answer over the subject selected now" | the packet's boundary example (slow A after selecting B) |
| `mcp/tests/test_knowledge_review_surface.py` | `test_the_previous_binding_identity_reaches_the_port_and_is_compared_against_the_read` | the whole server path: admission, forwarding on the request, `stale` + `previous_comparison_ref` + `disabled_stale`, `current` when nothing is carried, 400 on a malformed spelling |
| same (extended helper) | `render(..., previous_binding_digest=…)` now builds the request | the existing staleness rules still hold through the request-carried path |

**How the tests mount production composition.** The dashboard cases render the **real** `DocChangeSetBar` /
`ReviewSurface` over the **real** clients (`intentReviewEntries`, `intentReview`) and the **real** store;
only `fetch` is stubbed, and the invalidation is delivered through the store's own public delta channel
(`dashboardStore.getState().applyDelta("analytics", …)` — the same call the SSE `analytics` frame makes).
Assertions read the rendered DOM and the query strings the real client built. The Python case drives the
real route registration over the real fixture and the real composition.

**Population/case-budget impact:** +1 Python case in an already-registered module
(`test_knowledge_review_surface.py`, 30 collected at base → 31 on the candidate) and +6 dashboard vitest
cases in two already-registered modules. No `LIFECYCLE_CATALOG_SHA256`/lane/census change was required: no artifact was added, so the population is still 16 contracts / 66 artifacts
(`test_dependency_ownership_ast_helpers.py`, `test_evidence_lanes.py` re-run green in §5).

---

## 4. Base-defect reproduction (exact commands, exact observed results)

A scratch worktree was created at the base commit and **only the new test files** were copied into it, so
the production bytes under test are exactly `c422dc00`:

```bash
git -C <leaf> worktree add --detach /home/firefox/projects/ar-coordination/temp/l17-base c422dc00273d4ae7a5d8c9c8db97365b8c85d640
ln -sfn /home/firefox/projects/agents-remember/dashboard/node_modules <base>/dashboard/node_modules
cp -r <leaf>/dashboard/styled-system <base>/dashboard/styled-system
cp <leaf>/dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx <base>/dashboard/src/panels/detail-panel/
cp <leaf>/dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx <base>/dashboard/src/panels/review/
git -C <base> status --short      # M only the two test files
```

### 4.1 The packet's core defect, on the base bytes

```bash
cd /home/firefox/projects/ar-coordination/temp/l17-base/dashboard
./node_modules/.bin/vitest run src/panels/detail-panel/reviewEntryRefusal.test.tsx src/panels/review/ReviewSurface.outcomes.test.tsx
```

Observed (base): **5 failed | 17 passed (22)**

```
× re-reads the pair's subjects when the workspace republishes, and keeps the reader's subject
    AssertionError: expected [ Array(1) ] to have a length of 2 but got 1
× offers an explicit refresh control that re-reads the pair on the reader's own click
    TestingLibraryElementError: Unable to find an element by: [data-testid="review-catalogue-refresh"]
× re-reads the same question carrying the binding identity on screen, and names what moved
    TestingLibraryElementError: Unable to find an element by: [data-testid="review-refresh"]
× keeps the labelled old comparison and its error when the refresh fails
    TestingLibraryElementError: Unable to find an element by: [data-testid="review-refresh"]
× never renders a slow earlier subject's answer over the subject selected now
    AssertionError: expected 'aaaaaaaa…' to be 'bbbbbbbb…'   # the late A answer overwrote B
```

The first line **is** the packet's defect: the entry never asked again after the workspace published, and
the only way to discover the data was to reopen the panel. The last line **is** the packet's boundary
example: at base a slow subject-A response *did* replace newly selected subject B's comparison.

The same two modules on the candidate: **22 passed (22)** (see §5).

### 4.2 The server-side gap, on the base bytes

The R17 identity had no reachable source at base: `previous_binding_digest` was a parameter nothing in
production passed (only tests did). Probe (kept as evidence):

```bash
# temp/icr/probe-l17-route-refresh.py (a copy of the evidence probe kept beside this report) registers
# the real routes over a capturing port and asks WITH the parameter
cd /home/firefox/projects/ar-coordination/temp/l17-base
PYTHONPATH=$PWD/mcp/src /home/firefox/projects/agents-remember/mcp/.venv/bin/python \
  /home/firefox/projects/ar-coordination/temp/l17-evidence/probe-l17-route-refresh.py
```

Observed (base, `c422dc00`):

```
status: 404
forwarded previous_binding_digest: '<field absent from the request model>'
reaches the port: False
malformed digest status: 404
malformed body offendingInput: None
body: {"operation": "read_knowledge_review", "refusal": {…}, "state": "refused"}
```

Observed (candidate, same probe):

```
status: 404
forwarded previous_binding_digest: '9999999999999999999999999999999999999999999999999999999999999999'
reaches the port: True
malformed digest status: 400
malformed body offendingInput: 'not-a-digest'
body: {"detail": "previousBindingDigest is not a comparison binding digest; …", "expected": "omitted, or a 6…
```

(`status: 404` is the stub port's own `subject_unresolved` refusal — the probe measures what the
transport forwarded, not the review.) The new Python case asserts exactly these two facts, so it fails on
base (`ReviewSurfaceRequest` has no such field / the route returns 404 rather than 400) and passes on the
candidate.

### 4.3 What I could NOT measure here (acceptance A12/A13)

- **A real publication while a real browser keeps the panel open** (A12): no browser and no live
  dashboard process are available inside this enclosure. I exercised the *mounted* React tree, the real
  clients, the real store and its own delta channel, and the server's real route/composition over real
  fixtures — but not a real ingest/publication event travelling through a real SSE stream into a real
  page. The packet's own mapping (notes `01-leaf-owner-map.md` §4) records A12's boundary as "actual
  publication/invalidation + browser".
- **Generation identity of a *newly published* candidate**: the dashboard's projection exposes no
  candidate digest, so the entry's invalidation is keyed on the projection's own content; the review
  pane's identity comparison is measured against what the payload publishes. The strongest available
  in-enclosure statement is the one the tests make.
- **The routed R12/R24 "committed" action that swallows its refusal detail** was **not** touched, as
  instructed.

---

## 5. Exact commands and observed results (candidate)

```bash
cd <leaf> && PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests -q -m 'not integration' -p no:randomly
# → 2620 passed, 62 skipped, 321 subtests passed in 375.13s

PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests -q -m 'integration' -p no:randomly
# → 420 passed, 15 skipped, 74 subtests passed in 208.53s

PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests/test_knowledge_review_surface.py -q -m '' -p no:randomly
# → 31 passed in 58.34s            (31 collected; base had 30)

PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py mcp/tests/test_layering.py mcp/tests/test_file_size_detector.py -q -m '' -p no:randomly
# → 10 passed in 49.36s

# collected-case budgets (pyproject: unit 4000 / integration 1000)
pytest mcp/tests --collect-only -q -m 'not integration'  → 2682/2703 collected (21 deselected)
pytest mcp/tests --collect-only -q -m 'integration'      → 435/3117 collected (2682 deselected)

cd <leaf>/dashboard
./node_modules/.bin/tsc --noEmit -p tsconfig.json                      # → no output (clean)
./node_modules/.bin/vitest run                                          # → 158 files, 1671 tests passed
./node_modules/.bin/vitest run src/panels/review/ src/panels/detail-panel/ src/data/
                                                                        # → 65 files, 839 tests passed
./node_modules/.bin/eslint src/panels/detail-panel/changeSetBar.tsx src/panels/review/ReviewSurface.tsx \
    src/panels/review/ReviewRefresh.tsx src/panels/review/ReviewReadCycle.ts src/data/review.ts \
    src/panels/detail-panel/reviewEntryRefusal.test.tsx src/panels/review/ReviewSurface.outcomes.test.tsx
                                                                        # → no output (clean; base was clean too)

<venv>/python -m ruff check <the 5 changed Python files>            # → All checks passed!
<venv>/python -m ruff format --check <the 5 changed Python files>   # → 5 files already formatted

grep -n "setInterval\|setTimeout\|requestAnimationFrame" <the 5 changed dashboard files>
# → no matches (exit 1): no polling loop was introduced

# file sizes, measured with the repo's own detector (code_quality.file_size.line_count)
#   application/knowledge_review.py            1077 (base) → 1041  under-limit
#   application/review_comparison_staleness.py   —         →   97  under-limit
#   models/knowledge/review.py                 1189 (base) → 1198  under-limit (was already under)
#   serving/review.py                           672 (base) →  757  under-limit
#   tests/test_knowledge_review_surface.py     1313 (base) → 1395  hard-limit-exceeded (WAS ALREADY OVER)
#   dashboard ReviewSurface.tsx                1031 (base) →  986  under-limit (eslint max-lines green)
#   dashboard changeSetBar.tsx                  435 (base) →  543  under-limit
#   dashboard data/review.ts                    633 (base) →  672  under-limit
#   dashboard ReviewReadCycle.ts / ReviewRefresh.tsx  new   →  257 / 101  under-limit
```

**Pre-existing red formatting/lint at base, left alone as instructed.** `prettier --check` is red for 419
dashboard files at the base commit, including **all five** of the dashboard files I touched
(`changeSetBar.tsx`, `ReviewSurface.tsx`, `data/review.ts`, and the two test modules — verified by running
prettier in the base worktree). I did **not** reformat them: it is a pre-existing, repo-wide condition,
and a formatting-only rewrite of these files would balloon the diff the verifier must read. `ruff check`
and `ruff format --check` are **green** on every Python file I touched, at base and at head.

---

## 6. Limitations, honest boundaries, and anything needing a ruling

1. **`test_knowledge_review_surface.py` is 1395 L and therefore still `hard-limit-exceeded`.** It was
   already over at base (1313 L) and is in the repo's existing set of 26 over-rail Python files; the module
   is in **17 leaves'** test scope, so extracting from it here would collide with every other leaf. My +1
   case sits in the module that already owns the route/composition cases and the shared diff fixture. If
   the master wants this file split, that is a cross-leaf refactor and needs a ruling — I did not start it.
2. **The routed debt "in-flight prop-change race in the review entry hook" was not reproducible on the
   base bytes as a *failing* case.** I built the scenario (leaf A's answer held open until after the bar
   moved to leaf B and B answered) and the base implementation drops the late answer: the effect's cleanup
   flag (`let current = true`) is set synchronously when the dependency changes, so the stale closure sees
   `current === false`. My sequence guard is therefore **defence in depth** (it covers the case the cleanup
   flag cannot see: a re-read triggered while a request is in flight, and any future read path that does
   not replace the effect), and the case is a regression guard rather than a base-failure witness. The
   base-failing race I *did* find and fix is the **surface-side** one (slow subject A after selecting B),
   which is one of the packet's own boundary examples. I am flagging this rather than claiming a
   reproduction I did not get.
3. **`previous_binding_digest` cannot distinguish "a refresh" from "any read that carries a digest".**
   A caller could pass a digest on a first read and receive `stale` with a previous input it never
   displayed. That is the honest answer to the question it asked (the identity it named is not the one
   rendered) and it grants no authority, but if the master wants the route to require "a previous read for
   this subject actually happened" that is a session/state contract this surface does not hold — it would
   need a ruling.
4. **The entry's invalidation granularity is the whole projection.** Any projection change re-reads the
   leaf's catalogue once. That is deliberate (the projection is the only channel that carries
   publication-side movement) and it is bounded by the server's change gate: an unchanged snapshot
   serializes to an identical string and performs no read. It is not a claim that the candidate changed.
5. **`ruff format` was not applied to the four already-formatted Python files' neighbours**, only to the
   files I touched, which were already green.
6. **No `# noqa` suppressions were added** anywhere; complexity/PLR0911/0912/0915 and eslint
   `complexity`/`max-lines`/`max-lines-per-function` are green on every changed file (the three eslint
   errors my first draft produced were removed by extraction, not by suppression).

---

# Fix round 1 (verifier verdict `fail`: F1, F2 high; F3, F4, F5)

Verifier report read in full: `temp/icr/verify-l17.md` (findings L17-F1 … L17-F5, then "True strengths").
Its fingerprints prove it graded exactly the bytes this leaf left: tracked diff
`1c46bff40d835cb8f268a2a09eed7c1efbc8ec690f56ac97cd11fd4efe4e7d70`, HEAD `c422dc00`. I reproduced those
two pre-fix module hashes before changing anything and preserved them under `temp/icr/prefix/`:

| preserved pre-fix byte | sha256 | matches verifier's |
| --- | --- | --- |
| `temp/icr/prefix/ReviewReadCycle.ts` | `5c5968c86bc98d9f808b2dcbdac4f8ba5b926722dc29f6b04d9ec2db18ba97c6` | `5c5968c8…ba97c6` ✔ |
| `temp/icr/prefix/ReviewRefresh.tsx` | `f6b30544c296a4397af22c31e296f037f1412c1edde786d336bdf90a19c46281` | `f6b30544…c46281` ✔ |
| `temp/icr/prefix/changeSetBar.tsx` (F4) | `f6c92cad926561d8d3a0b731bd31439fe01beac06b11e1fa24f53cf95fbd63f3` | (F4's file) |
| `temp/icr/prefix/ReviewSurface.tsx`, `ReviewSurface.outcomes.test.tsx` | pre-fix copies for the scratch rebuild | — |

**Pre-fix scratch tree** (the F1/F2/F4 defects present, the new tests overlaid — nothing in the leaf was
reverted):

```bash
git -C <leaf> archive c422dc00… | tar -x -C /home/firefox/projects/ar-coordination/temp/l17-fix1
ln -sfn /home/firefox/projects/agents-remember/dashboard/node_modules <scratch>/dashboard/node_modules
cp -r <leaf>/dashboard/styled-system <scratch>/dashboard/styled-system
cp <leaf>/temp/icr/prefix/{ReviewReadCycle.ts,ReviewRefresh.tsx,ReviewSurface.tsx} <scratch>/dashboard/src/panels/review/
cp <leaf>/temp/icr/prefix/changeSetBar.tsx                              <scratch>/dashboard/src/panels/detail-panel/
cp <leaf>/dashboard/src/{data/review.ts}                                <scratch>/dashboard/src/data/
cp <leaf>/dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx   <scratch>/dashboard/src/panels/review/
cp <leaf>/dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx <scratch>/dashboard/src/panels/detail-panel/
sha256sum <scratch>/dashboard/src/panels/review/ReviewReadCycle.ts      # 5c5968c8…ba97c6 (pre-fix)
sha256sum <scratch>/dashboard/src/panels/detail-panel/changeSetBar.tsx  # f6c92cad…b63f3 (pre-fix)
```

---

## F1 (high) — the carried identity is sticky — FIXED

**What I changed.** `dashboard/src/panels/review/ReviewReadCycle.ts`.

- `CarriedBinding` (`:181-190`) is now `{readNumber, key, digest}`: the identity is filed with the
  question it was displayed under **and the one read the refresh asked to replace it**.
- `refresh()` (`:254-268`) captures `{readNumber: reads.current + 1, key: shown.key, digest: shownBinding}`
  and bumps the nonce in the same update, so the read the identity names is the read the effect starts.
- `startRead` (`:124-176`) sends `previous` **only** when `carried.key === askedFor && carried.readNumber === seq`;
  every other read sends nothing. The sequence number is what closes the case a key check alone cannot:
  switching away from a question and back again restores the key, and a key-only guard would carry the
  stale identity into the returning read (my own F1 case (b) catches exactly that on the first attempt).
- `carried` was removed from the effect's dependency array (`:252`): the read is triggered by the nonce,
  and depending on the identity's own write would start a second read of the same question.
- The module header now states the corrected rule (`:27-35`), including why clearing on a key change is
  not sufficient.

**Pre-fix failure output** (`cd /home/firefox/projects/ar-coordination/temp/l17-fix1/dashboard &&
./node_modules/.bin/vitest run src/panels/review/ReviewSurface.outcomes.test.tsx`, exit 1):

```
× keeps the labelled old comparison and its error when the refresh fails
    AssertionError: expected <span …(4)></span> to be null        (test line 623 — the F2 assertion)
× carries the identity into a read that replaces it, and into no other question (L17-F1)
    AssertionError: expected '…' not to contain 'previousBindingDigest'
    Received: "/api/review/intent?…&selectorId=subject-b&previousBindingDigest=1111…1111"
× never carries a live identity into a recorded read, nor a recorded one into a live read (L17-F1)
    AssertionError: expected '…' not to contain 'previousBindingDigest'
    Received: "/api/review/intent?…&selectorId=subject-a&history=recorded&previousBindingDigest=1111…1111"
Test Files  1 failed (1)   Tests  3 failed | 14 passed (17)
```

The two leak URLs are the verifier's ATTACK-1 and ATTACK-3 reproductions exactly. On the fixed bytes the
same module is `17 passed (17)`.

**Case (c) of the requirement** (the identity IS still carried when it replaces the display it names) is
asserted in the same case: `expect(String(seen[1])).toContain("previousBindingDigest=" + "1".repeat(64))`
after clicking refresh, and the generation notice is found.

## F2 (high) — an unmeasured currency claim — FIXED

**What I changed.** `dashboard/src/panels/review/ReviewRefresh.tsx`: `generationOf(read, carried, shown)`
(`:102-121`) now takes the read phase and returns `null` unless `read.phase === "reviewed"` — the claim is
rendered only when the read that carried the identity actually answered. The docstring states the rule
with its reason (`:88-101`). `dashboard/src/panels/review/ReviewSurface.tsx:964` passes `read`.
The identity reaching that call is already the current read's (`ReviewReadCycle`'s `carriedHere`), so a
carried value that survives to a `reviewed` phase has an answer behind it by construction.

**The pinned wrong behaviour was changed, on the record.**
`dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx:599-624` used to assert
`generationState === "current"` after a failed refresh (the assertion now at `:623`). It now asserts
`expect(view.queryByTestId("review-generation-notice")).toBeNull()` with a comment naming this fix round,
the verifier finding and why the old assertion pinned the defect. This is a deliberate reversal of a
delivered assertion, not a silent edit.

**Pre-fix failure output** — first line of the block above:
`AssertionError: expected <span …(4)></span> to be null` at the new assertion (it found the
"still current" notice). Fixed bytes: the whole module passes.

## F4 (low) — a dead affordance and a comment that did not obtain — FIXED

**What I changed.** `dashboard/src/panels/detail-panel/changeSetBar.tsx`.

- `:243-251`: `const stale = read.facts !== facts;` — no longer gated on `loading`. `read.facts` is the
  facts recorded **with the last answer** (already the case), so the mark means "the list beside it was
  read before the projection moved", which is exactly what it says.
- `:146-153`: the comment now states the window the mark is visible in — from the publication that moved
  the projection until the answer for that projection is filed, including a refusal or a failure, because
  then the projection *has* been answered.
- New case `dashboard/src/panels/detail-panel/reviewEntryRefusal.test.tsx:475-534` (describe "the entry's
  pre-click freshness marker"): the read the publication triggers is **held open**, so the mark is
  asserted in a settled DOM (`findByTestId("review-catalogue-stale")` + `data-catalogue-stale="true"` +
  `entryReads === 2`), and its absence is asserted after the answer is filed.

**Pre-fix failure output** (`cd /home/firefox/projects/ar-coordination/temp/l17-fix1/dashboard` with the
preserved pre-fix `changeSetBar.tsx`, `./node_modules/.bin/vitest run src/panels/detail-panel/reviewEntryRefusal.test.tsx`, exit 1):

```
× marks the list while the projection has moved past the answer on screen, and clears it on the answer
    TestingLibraryElementError: Unable to find an element by: [data-testid="review-catalogue-stale"]
 Test Files  1 failed (1)   Tests  1 failed | 7 passed (8)
```

Fixed bytes: `8 passed (8)`.

## F3 (medium) — A12 browser boundary — DEFERRED through the sanctioned route

No gate was bypassed: I did **not** run the Playwright configs, did not touch
`dashboard/scripts/require-dagger-test-environment.mjs`, invented no certificate and claim no browser
journey. The client-side bar was raised with non-browser evidence instead: the new F1 cases assert the
**exact query bounds the real client builds** (which read carries `previousBindingDigest` and which does
not, per subject and per `history`), and the retained-generation/failure cases assert the rendered DOM.
**The A12 boundary (a real ingest/publication observed by a real browser across the production HTTP
composition) remains unmeasured and is deferred to R25**, per the orchestrator's instruction. My earlier
report §4.3 wording ("no browser and no live dashboard process inside this enclosure") was partly
inaccurate — Playwright chromium is installed and a Vite dev server answers; what is absent is a
dashboard process wired to this leaf's real publication. Corrected here.

## F5 (medium, rail) — NO code change; ruling recorded

`mcp/tests/test_knowledge_review_surface.py` is **1395 L** against the 1200 hard rail (1313 at base, +82
from this leaf's one new case and extended helper). **The master ruled the growth accepted for this
series** because the module sits in 17 leaves' test scope and extraction would collide mid-series; the
whole-tree census stays at 22 offenders (no new offender). No extraction was started. The precise +82 is
one new test plus the helper's request-carried form; no other module grew.

---

## Re-run results after the fix (exact)

```bash
cd <leaf>/dashboard
./node_modules/.bin/vitest run src/panels/review/ src/panels/detail-panel/
# → Test Files 16 passed (16) | Tests 108 passed (108)
./node_modules/.bin/vitest run
# → Test Files 158 passed (158) | Tests 1674 passed (1674)     (was 1671; +3 new cases)
./node_modules/.bin/tsc --noEmit -p tsconfig.json
# → exit 0, no output
./node_modules/.bin/eslint src/panels/detail-panel/changeSetBar.tsx src/panels/review/ReviewSurface.tsx \
    src/panels/review/ReviewRefresh.tsx src/panels/review/ReviewReadCycle.ts src/data/review.ts \
    src/panels/detail-panel/reviewEntryRefusal.test.tsx src/panels/review/ReviewSurface.outcomes.test.tsx
# → exit 0, no output (the react-hooks/exhaustive-deps error the first draft produced was removed by
#   memoising the request fields, NOT by a suppression)

cd <leaf> && PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests/test_knowledge_review_surface.py -q -m ''
# → 31 passed in 42.79s
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests/test_dependency_ownership_ast_helpers.py mcp/tests/test_evidence_lanes.py -q -m ''
# → 5 passed in 59.52s
PYTHONPATH=$PWD/mcp/src:$PWD/mcp/test_support <venv>/python -m pytest mcp/tests -q -m 'not integration' -p no:randomly
# → 2620 passed, 62 skipped, 321 subtests passed
```

**New fingerprint** (after this fix round):

| Point | value |
| --- | --- |
| `git -C <leaf> diff \| sha256sum` | `29b787e9b14bce430cc005622cc617bda6e9238b92114d24f26aa073dda048c3` |
| `HEAD` | `c422dc00273d4ae7a5d8c9c8db97365b8c85d640` (unchanged) |
| `dashboard/src/panels/review/ReviewReadCycle.ts` | `d9fd53c2422f7bdb26b8e29f6360955fd319e2652784d37692683761ab667592` |
| `dashboard/src/panels/review/ReviewRefresh.tsx` | `055bc06b32bf83271a856a87f1706e55189fe04f97c801c05f4bfce0a6dc2857` |
| `dashboard/src/panels/detail-panel/changeSetBar.tsx` | `8d32616a42dd184929ff1eb44e356fe4faa6acaa3fe0a611a3f3ffdd19b62ebd` |
| `mcp/src/agents_remember/application/review_comparison_staleness.py` | `11e81de2d0360287791aa8d09c7e4790133ee97e98bd530389f7ea1e009dfe89` (unchanged) |
| `git status --porcelain` | the same 9 `M` + 3 `??` (+`temp/`) |

**Size deltas from this round:** `ReviewReadCycle.ts` 257 → 278, `ReviewRefresh.tsx` 101 → 116,
`changeSetBar.tsx` 543 → 552, `ReviewSurface.tsx` 986 (unchanged), the two test modules
534 / 809 (both under the rail). No new `# noqa`/`eslint-disable`/`@ts-ignore`/`type: ignore` anywhere
(grep over the tracked diff and the new files: no matches).

---

## Residual limits after fix round 1

1. **F1's reachability in today's shell is still latent** — the verifier could not click a path to it
   (`RailedBody` is hidden under a takeover, no URL routing), and I did not change the shell. The fix is
   at the component boundary the leaf's own R17 tests treat as in scope, and the leak is now impossible
   from that boundary rather than merely unreachable.
2. **F3's A12 browser boundary remains deferred to R25**, as instructed. No browser journey is claimed.
3. **The entry's freshness mark is now observable but still brief** in a paused-response test it is a
   settled state; against a very fast local read the window is a single network round trip. It is
   honest (it describes the list on screen), and it is no longer withdrawn inside the same flush.
4. **`mcp/tests/test_knowledge_review_surface.py` remains over the rail** by the master's ruling (F5).
5. **The carried digest still cannot distinguish "a refresh" from "any read carrying a digest" at the
   server** — unchanged from §6.3. The client now sends it only on the one read the refresh asked for, so
   the defect the verifier found is closed at the boundary that can produce it; a server-side session
   requirement remains a ruling item, not a claim of this leaf.

---

# Fix round 2 (round-2 verdict `pass-with-findings`: R2-F1, R2-F2 — both treated as BLOCKING per the master's rule)

Round-2 verifier report read in full before any edit. Its fingerprints reproduce against this leaf: the
tracked diff was `29b787e9b14bce430cc005622cc617bda6e9238b92114d24f26aa073dda048c3` and HEAD `c422dc00` at
the start of this round, and the two modules it indicted had exactly the hashes it recorded
(`ReviewReadCycle.ts` = `d9fd53c2…b667592`, `ReviewRefresh.tsx` = `055bc06b…dc2857`). Those **round-2
pre-fix bytes are preserved under `temp/icr/prefix2/`** so the bite-proof below tests the actual prior
code:

| preserved round-2 pre-fix byte | sha256 | matches round-2 verifier |
| --- | --- | --- |
| `temp/icr/prefix2/ReviewReadCycle.ts` | `d9fd53c2422f7bdb26b8e29f6360955fd319e2652784d37692683761ab667592` | `d9fd53c2…b667592` ✔ |
| `temp/icr/prefix2/ReviewRefresh.tsx` | `055bc06b32bf83271a856a87f1706e55189fe04f97c801c05f4bfce0a6dc2857` | `055bc06b…dc2857` ✔ |

**Pre-fix scratch tree** (`/home/firefox/projects/ar-coordination/temp/l17-fix2`, the ROUND-2 pre-fix
modules with the new test bytes overlaid — nothing in the leaf was reverted):

```bash
git -C <leaf> archive c422dc00… | tar -x -C /home/firefox/projects/ar-coordination/temp/l17-fix2
ln -sfn /home/firefox/projects/agents-remember/dashboard/node_modules <scratch>/dashboard/node_modules
cp -r <leaf>/dashboard/styled-system <scratch>/dashboard/styled-system
cp <leaf>/temp/icr/prefix2/{ReviewReadCycle.ts,ReviewRefresh.tsx} <scratch>/dashboard/src/panels/review/
cp <leaf>/dashboard/src/panels/review/{ReviewSurface.tsx,ReviewSurface.outcomes.test.tsx} <scratch>/dashboard/src/panels/review/
cp <leaf>/dashboard/src/{data/review.ts} <scratch>/dashboard/src/data/
cp <leaf>/dashboard/src/panels/detail-panel/{changeSetBar.tsx,reviewEntryRefusal.test.tsx} <scratch>/dashboard/src/panels/detail-panel/
sha256sum <scratch>/dashboard/src/panels/review/ReviewReadCycle.ts   # d9fd53c2…b667592 (round-2 pre-fix)
sha256sum <scratch>/dashboard/src/panels/review/ReviewRefresh.tsx    # 055bc06b…dc2857 (round-2 pre-fix)
```

## R2-F1 (blocking) — the notice's derivation never checked the question key — FIXED

**What I changed.** `dashboard/src/panels/review/ReviewReadCycle.ts:280-283`. The render-time guard now
carries all three conjuncts, so the value **described** and the value **sent** (`startRead`'s own check)
cannot disagree:

```ts
const carriedHere =
  carried !== null && carried.readNumber === reads.current && carried.key === targetKey
    ? carried.digest
    : null;
```

The comment above it now states why the key is not redundant with the read number: when the subject
change and the reader's refresh land in the same React flush, the refresh captures the **previous**
subject's identity while the single effect run that follows asks for the new subject and consumes exactly
that read number — a read-number check alone then describes another subject's identity, and the surface
announces a publication the request never carried and the server never answered (`current`). This is the
one-line fix the round-2 verifier verified in scratch, applied verbatim at the site it named.

**New case** (added BEFORE the fix, so its bite is proven):
`dashboard/src/panels/review/ReviewSurface.outcomes.test.tsx:692-771` —
"renders no generation claim when the subject change and the refresh land in one flush (L17-R2-F1)". It
displays subject A, then performs the subject change **and** the refresh inside ONE `await act(...)` (the
passive-effect gap), waits for B's payload, and asserts: exactly two reads, the B read carries **no**
`previousBindingDigest`, **no** `review-generation-notice`, and the header still names subject B.

**Pre-fix failure output** (`cd /home/firefox/projects/ar-coordination/temp/l17-fix2/dashboard &&
./node_modules/.bin/vitest run src/panels/review/ReviewSurface.outcomes.test.tsx`, exit 1):

```
× renders no generation claim when the subject change and the refresh land in one flush (L17-R2-F1)
    AssertionError: expected <span …(4)></span> to be null
    ❯ src/panels/review/ReviewSurface.outcomes.test.tsx:752:60
 Test Files  1 failed (1)   Tests  2 failed | 16 passed (18)
```

Fixed bytes: the same module is `18 passed (18)`.

## R2-F2 (blocking) — the superseded sentence misdescribed the panes it sits beside — FIXED

**What I changed.** `dashboard/src/panels/review/ReviewRefresh.tsx:77-78`. Every clause is now true of the
DOM it sits in: the route publishes the CURRENT comparison on a `stale` answer and merely *names* the
carried identity as `previous_comparison_ref`, so:

```
the candidate's comparison moved (was <previous>, is now <current>) — the panes below have been replaced
whole with the current one
```

The old wording ("the panes below are the comparison you were reading (Y), replaced whole on the next
read") is gone from the source — a grep finds it only in the new test's **negative** assertion and the
comment explaining the finding. The module header's own example (`:24-27`) was corrected the same way, and
the "current" sentence is unchanged (`the comparison below is still the candidate's current one (…)`).

**The delivered test was updated on the record.** `ReviewSurface.outcomes.test.tsx:591-604` no longer
merely *notices* the mismatch: it asserts the sentence names both identities
(`was 1…1, is now 2…2`), asserts the false clause is **absent**, and — the assertion the finding asked
for — asserts the panes agree with what the sentence claims about them
(`notice.dataset.currentBinding === view.getByTestId("review-surface").dataset.comparison`).

**Pre-fix failure output** (same scratch run):

```
× re-reads the same question carrying the binding identity on screen, and names what moved
    AssertionError: expected 'the candidate published a new compari…' to contain 'was 1111…1111, is now 2222…2222'
    Expected: "was 11111111…1111, is now 2222…2222"
    Received: "the candidate published a new comparison (2222…2222) — the panes below are the
               comparison you were reading (1111…1111), replaced whole on the next read"
    ❯ src/panels/review/ReviewSurface.outcomes.test.tsx:599:32
```

Fixed bytes: `18 passed (18)`.

Both cases were written and run against the pre-fix bytes **before** the fix was applied, so each finding
has a bite-proof rather than an assertion that merely mirrors the new code.

## Constraints honoured

- **Round-1 repairs intact**: the F1 read/key/number carry, F2's phase gate, and F4's observable mark are
  untouched — `diff -u temp/icr/prefix2/ReviewReadCycle.ts <live>` is the one added conjunct plus its
  comment, and `diff -u temp/icr/prefix2/ReviewRefresh.tsx <live>` is the one sentence plus the module
  header bullet that stated the same wrong thing. Every R17 case passes: 18 in
  `ReviewSurface.outcomes.test.tsx` (was 17) and 8 in `reviewEntryRefusal.test.tsx`.
- **Scope**: only the two dashboard modules and the one dashboard test module were touched this round.
- **No gate bypass**, no browser claim (A12 remains deferred to R25).
- **No new suppressions and no timers**: `git diff -U0 | grep '^+' | grep -iE
  "noqa|eslint-disable|@ts-ignore|@ts-expect-error|type: ignore|setInterval|setTimeout|requestAnimationFrame|
  setImmediate|queueMicrotask"` → **no matches**.

## Re-run results after the fix (exact)

```bash
cd <leaf>/dashboard
./node_modules/.bin/vitest run src/panels/review/ src/panels/detail-panel/
# → Test Files 16 passed (16) | Tests 109 passed (109)          (was 108; +1 new case)
./node_modules/.bin/vitest run
# → Test Files 158 passed (158) | Tests 1675 passed (1675)      (was 1674; +1)
./node_modules/.bin/tsc --noEmit -p tsconfig.json
# → exit 0, no output
./node_modules/.bin/eslint src/panels/detail-panel/changeSetBar.tsx src/panels/review/ReviewSurface.tsx \
    src/panels/review/ReviewRefresh.tsx src/panels/review/ReviewReadCycle.ts src/data/review.ts \
    src/panels/detail-panel/reviewEntryRefusal.test.tsx src/panels/review/ReviewSurface.outcomes.test.tsx
# → exit 0, no output
```

Sizes after this round: `ReviewReadCycle.ts` 286, `ReviewRefresh.tsx` 117,
`ReviewSurface.outcomes.test.tsx` 884, `changeSetBar.tsx` 552, `ReviewSurface.tsx` 986 — all far under the
1200 rail. The server seam is byte-identical to round 1
(`review_comparison_staleness.py` = `11e81de2…09dfe89`, unchanged).

**New fingerprint** (after fix round 2):

| Point | value |
| --- | --- |
| `git -C <leaf> diff \| sha256sum` | `0d44b92b8a0de8b8f580e1d4a7f6b22f909a47b3253da120fcdf63800881b088` |
| `HEAD` | `c422dc00273d4ae7a5d8c9c8db97365b8c85d640` (unchanged) |
| `dashboard/src/panels/review/ReviewReadCycle.ts` | `5f6735bd365391e9572bba3807d2dd68e031d4ec9920e9e79d923f5365cdd130` |
| `dashboard/src/panels/review/ReviewRefresh.tsx` | `89ab451c4a8739b80fdccea4b9170991fccb4f63f5b34173b3d83e7a4598d3d0` |
| `dashboard/src/panels/detail-panel/changeSetBar.tsx` | `8d32616a42dd184929ff1eb44e356fe4faa6acaa3fe0a611a3f3ffdd19b62ebd` (unchanged this round) |
| `mcp/src/agents_remember/application/review_comparison_staleness.py` | `11e81de2d0360287791aa8d09c7e4790133ee97e98bd530389f7ea1e009dfe89` (unchanged this round) |
| `git status --porcelain` | the same 9 `M` + 3 `??` (+`temp/`) |

## Residual limits after fix round 2

1. **Both findings were latent in today's shell for reachability** (R2-F1 needs the subject change and the
   refresh inside one React flush; R2-F2 fires on every superseded refresh but only as a wording defect) —
   the fixes close the defects at the component boundary, and neither changes what a reader can click
   today. No shell change was made or claimed.
2. **A12 (real publication observed by a real browser) remains deferred to R25**, unchanged by this round;
   no browser journey is claimed.
3. **`mcp/tests/test_knowledge_review_surface.py` remains over the 1200-line rail** by the master's F5
   ruling; untouched this round.
4. **A server-side "was there really a previous read for this question?" requirement** is still a ruling
   item, not a claim: the client now checks the question key at both the send and the describe sites, and
   the server continues to compare whatever digest it is handed.
