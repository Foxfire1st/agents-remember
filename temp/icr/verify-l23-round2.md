# Independent adversarial verification — leaf L23 / ICR-R23@v1 — ROUND 2 (fix round 1)

**Verifier.** Same independent adversarial verifier; no part in the implementation. Every claim below was
reproduced by me on the round-2 frozen bytes with my own fixtures, my own probes and my own commands.
Twelve mutations were applied one at a time to my own scratch copy, the attack re-run, and every file
restored and re-hashed (`restored=OK` on every row).

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l23-ar/260921-icr-l23`,
branch `ar/260921-icr-l23`, base `473ad8242bb4c22bdabed5d5253767350381eb3e`, plus the worker's
fix-round-1 delta. Report `temp/icr/report-l23.md` §"Fix round 1" (F1–F13) — claims checked, never
treated as evidence. Round-1 verdict graded: `temp/icr/verify-l23.md`.

---

## 0. Fingerprints — the recipe reproduces byte for byte and the candidate never moved

Recomputed from the recipe §F11 states literally (`fingerprint-l23-fix1.sh`), run by me:

| component | declared | mine | match |
| --- | --- | --- | --- |
| `git status --porcelain` filtered (`^?? temp/icr/` excluded) | 17 entries (14 `M`, 3 `??`) | 17 entries, same list and order | yes |
| `git diff \| sha256sum` | `217be3a52753321a146d099075b4db02cd978992fab4ab3e4311d4bbf07f188f` | `217be3a5…` | yes |
| untracked `application/review_external_git_movement.py` | `af680ef9a82cfecbddc43b6633b24962ca232c0e81acbd1ba010408f66235df3` | `af680ef9…` | yes |
| untracked `models/knowledge/review_external_movement.py` | `78766cec80902721101204fdd46808f220607f772091fd5091770f946fb63ad7` | `78766cec…` | yes |
| untracked `tests/test_review_external_git_movement_read.py` | `a66605d9eb2ca080f27b95d2d08b59849cea0641e3924d8c693084899a14bc05` | `a66605d9…` | yes |
| **combined** | `a58a3a73101e90bee167b304afc0263545c3bfac6ed3e006b82f5c6518e86fc7` | `a58a3a73…` | yes |

**Start** 17:06:03 → `a58a3a73…`. **Gate** held it for 100 s (5 samples, 20 s apart) → open 17:07:44.
**End** 17:27:46 → `a58a3a73…`, component 1 `217be3a5…`, 17 entries, the same three untracked hashes.
**The candidate did not move** across the whole verification. Every candidate file in my frozen copy
(`temp/verify-l23/frozen-r2`, commit `31def829…`) is byte-identical to the leaf (checked path by path).

**L6 is closed**: the recipe now excludes `temp/icr/` from *both* the status and the hash components, so
its declared components and its command agree (round 1's one-liner printed a third hash).

---

## 1. B1 — **fixed** (the value no longer names an event that did not happen)

**Control state (untouched leaf, all three identities standing)** — shipped read, my probe:

```
external.state    : current
external.transitions: ('unchanged',)
external.evidence : (('code-work-branch','current'),('declared-source-branch','current'),('memory-work-branch','current'))
external.stmt     : no raw Git operation moved a declared identity of the reviewed generation, and this
                    boundary observed no transition: every declared identity still stands exactly where
                    the generation recorded it -- <three "is still at the recorded identity" clauses>.
                    unchanged: none required for the declared identities; this boundary compared all of
                    them and found each still exactly where the reviewed generation recorded it, so no
                    shape was observed to reconcile
```

No "advanced", no "hash-level", no transition named. `ordinary-append` now requires `advanced`
(`_transition`: `branch_left → branch-switch`, `replaced → rebase`, `advanced → ordinary-append`, else
`None`), and the row's signature reads "…still an ancestor of the branch tip **and the tip has moved
past it**".

**The two states where the sub-facts disagree**, both driven by me:

| state | measured | value |
| --- | --- | --- |
| recorded object gone, nothing else moved (amend + loose-object removal) | check-out on its declared branch; object absent | `transitions == ()`, `not-measured`, reason "…is not a readable commit object…"; statement "did not compare every declared identity … **the identities it did compare are reported beside this value**: <super …; memory …>" |
| recorded object gone **and** the declared source genuinely advanced | source `advanced` | `transitions == ('ordinary-append',)` — the one shape that happened — and no `branch-switch` |

**Falsification attempts.** I tried to make *any* state name a transition that did not happen: a switched
checkout (→ `branch-switch` only, and it did switch); a missing object with and without a source advance
(→ `()` and `('ordinary-append',)`); an untouched leaf with a managed sync in its history (→ per the
delivery's own case, `current`/`ordinary-append` with R22's block separate). None produced a fabricated
shape, and no state renders `('unchanged',)` beside another shape (the validator refuses it — MT8).

**Mutations.** MT1 `_shapes` stops publishing `unchanged` → **NOTICED** (1 failed); MT2 `branch_left`
ignored → **NOTICED** (1 failed); MT8 the `unchanged` validator clause disabled → **NOTICED** (1 failed).

---

## 2. B2 — **fixed** (the row now names only measurements that exist)

The row's recovery is now: *"publish a successor generation from the advanced tip -- the pick is never
identified from an ancestry check alone, and no record that already exists measures it: the managed-sync
rebinding exists only once a sync has carried the official line and resolved a pair, the reopen channel
reports the recorded generation's availability rather than the pick, and this boundary's own state stays
'current'. A sync that carries nothing resolves no pair and records no rebinding, so it is not a
measurement of the pick either"*. Every clause reproduces on my own re-drive:

```
probe pick-measurement-claim (real git cherry-pick, no sync):
  payload.sync_movement = None;  reopen = available;  reopen.sync_rebinding.state = not-recorded
  external = current / ('ordinary-append',)
probe pick-then-sync (same pick, then the REAL worktree_sync_tool):
  sync state = already-current;  block = {'state': 'no-movement', 'detail': '… resolved no pair to
  measure the review against'};  rebinding reader = not-recorded;  sync_movement on read = None
```

The old phrase is gone (`"read the existing measurements that do take it" not in pick`), the rendered
production table still equals the documented table (`render_git_transition_support() in doc: True`, 6
rows each), and MT10 (restore the round-1 wording) → **NOTICED** (1 failed).

---

## 3. H1 — **fixed** (unreadable/ambiguous is `unavailable`; no-generation stays `None`)

```
probe unreadable-generation (manifest overwritten with junk):
  external binding_state: unavailable
  reason  : the leaf's published comparison generation could not be read, so no declared identity was
            compared -- no boundary measurement exists to report (1 generation director(y/ies) exist for
            260921-icr-l1 and none holds a readable manifest, so no generation could be selected and none
            is named)                       <- the selection's OWN detail
  recovery: restore the readable comparison generation this leaf published under
            <task_root>/notes/reports/comparison-generations, then read the boundary again; …
  generation_readable: False;  staleness.state: not-measured;  submission.state: unavailable

probe no-generation (leaf published nothing):  external_git_movement is None
```

MT3 (unreadable/ambiguous mapping disabled) → **NOTICED** (1 failed). **`ambiguous`:** not driven by any
case (grep of the new module) and no operation produces the tie; I judge the disclosure **sufficient** —
it is the same membership expression whose load-bearingness MT3 proves, and driving it would need a
hand-written store state, which is the one thing the master's "real operation" rule keeps out of a
production claim.

---

## 4. H2 (the master's ruling) — **fixed** (statement on all three owners, never a gate)

I drove the real tools myself over an enclosure whose reviewed head was amended (same tree, new identity),
using R21's own fixture only for provisioning:

```
preview   ok=True  binding_state=stale  transitions=['rebase','ordinary-append']
                   moved=['code-work-branch:<recorded head>']  generation=<frozen id>
                   unsupported=[rebase,cherry-pick,revert,branch-switch]:unsupported
                   statement "…the head the review captured (<sha>) is no longer in its history…"
applied   ok=True state=closed   R21 final_output_receipt.state=recorded   (same block beside it)
integrate ok=True state=integrated  landed code commit == the amended head  (same block beside it)

no-generation variant: preview would-closeout + applied closed, both carrying
  {'state': 'not-measured', 'detail': 'no boundary was measured for this result: …', 'unsupported': [4]}
```

**It is a statement, never a gate**: nothing refused, the closeout reached `closed` with its R21 receipt,
and the integration landed. MT6 (drop the closeout attachment) → **NOTICED** (1 failed).

**Authoring-boundary absence claim — verified, not accepted.** My own search: the new value is referenced
only by the two review entries, the payload model, `worktree_tools` and the tests; `worktree_tools`
carries receipt/rebinding attachments for closeout/integration only (`:375` R22's block, `:453`
integration, `:1012` preview, `:1015` apply); the ingest/publication route (`cli/knowledge_ingest.py` →
`application/knowledge_ingest.py` → `application/published_intent.py`) resolves no published comparison
generation and carries no per-leaf block; the freeze owner **creates** a generation rather than validating
one. There is no natural authoring owner, and the leaf reports it as an unwired boundary with that
evidence — correct disposition.

---

## 5. H3 — **fixed** (the one mounted sentence can no longer claim currency)

```
probe branch-switch:  staleness.state = not-measured
                      staleness.stmt  = "this boundary did not compare every declared identity …:
                                         code-work-branch was not compared: the code worktree is on
                                         super, not on the declared work branch ar/icr-l01-l1 …"
                      "current comparison" absent; submission.state = unavailable (not disabled_stale)
probe unreadable-generation: staleness.state = not-measured, submission.state = unavailable
```

**Both directions.** A recorded measured movement still wins over an absence — my `sync-then-switch` probe
(a real `worktree_sync` that measured a moved candidate tree, then a raw branch switch):
`staleness.state = stale` carrying the **sync's** sentence, `sync_movement = stale`,
`external = not-measured`, `submission = disabled_stale`. And an absence does not disable submission
(`unavailable`, above), which is what the documentation now claims.

**The mounted case is real.** `dashboard/src/panels/review/ReviewSurface.tsx:561` renders
`staleness.state === "not-measured"` as `data-testid="review-staleness-unmeasured"` with the boundary's
own sentence, and `ReviewSurface.outcomes.test.tsx` (884 → 915 L) asserts the sentence, the absence of a
"previous input" clause, and the absence of "current comparison". `vitest run src/panels/review/` →
**7 files / 57 tests passed**; MT11 (disable the client branch) → **NOTICED** (exactly 1 failed,
`ReviewSurface.outcomes.test.tsx` 1 failed | 18 passed) and the file restored (`restored=OK`). The
transport does not narrow the value (`data/review.ts` widens the union; `reviewTransport.ts` passes the
payload through), so the host state my probes measured reaches that branch.

---

## 6. L1–L6 — all six fixed, each re-checked by me

| Low | my re-check | mutation |
| --- | --- | --- |
| L1 doc count | doc now reads "…and the four\nthis system does not reconcile say so…"; the old wording is gone; the case asserts the count from `GIT_TRANSITION_SUPPORT` | MT9 → **NOTICED** |
| L2 `generation_readable` unpinned | forgery rebuilt so only that clause can refuse it; my direct bite-proof: shipped validator REFUSES the forged value, accepts it once the clause is disabled | MT7 → **NOTICED** (was silent in round 1) |
| L3 `_object_readable` unpinned | their case rebases, then removes the recorded commit's **loose object** and proves `cat-file -e` is false; my own fixture (the one the round-1 report asked for) reproduces it | MT4 → **NOTICED** (was silent in round 1) |
| L4 dead constants | `_SUCCESSOR_GENERATION`, `_MEASURED_STATES` (and the comment claiming a guard nothing read) and `_UNMEASURED_RECOVERY` are gone (`grep` = 0 occurrences); `_UNCOMPARED_RECOVERY` is read by `_recovery_actions` and exercised by the missing-object case | — |
| L5 unreachable clause | the `coverage` clause is removed from `_agreement_statement`; the control statement contains no "not compared" | — |
| L6 recipe mismatch | closed above (components and command agree) | — |

---

## 7. The fix round's own new claims — checked, both true

* **F7 (self-reported false sentence).** The round-1 absence sentence said "this boundary compared no
  declared identity" while `_state` reports `not-measured` when **some** channel could not be compared.
  The shipped sentence is now *"this boundary did not compare every declared identity of the reviewed
  generation: &lt;reason&gt;."* followed by `_compared_clause`, which names what was compared ("the
  identities it did compare are reported beside this value: …") and says "No declared identity was
  compared" **only** when nothing was. Verified in the state where it used to be false (branch switch,
  missing object — both had compared channels and both name them) and in the only state where the
  all-uncompared clause is reachable (a contract whose every channel is unreadable — code path read, not
  driven; recorded as such). **True in every state I can render.**
* **F1 (tightened advance sentence).** It now says *"a declared branch advanced from its recorded
  identity without replacing it"*. I drove a **memory-only** advance: the sentence says "a declared
  branch", and the per-channel detail beside it names the memory branch with both identities — so a
  memory-line advance is no longer described as the code branch's.

---

## 8. Regression and rails (my own commands, round-2 frozen bytes)

```
delivery modules          test_review_external_git_movement_read.py + test_review_sync_movement_read.py  -> 18 passed, 3 subtests
rails quintet             dependency-ownership-ast / evidence-lanes / layering / file-size / suite-budget -> 17 passed
review family (mine)      34 modules (test_*review*.py + sync/rebinding/parked/guarded-merge)  -> 383 passed, 12 subtests
worktree family (mine)    17 modules importing worktree_tools                                  -> 143 passed, 91 subtests
pyright (venv)            11 touched files                    -> 0 errors, 0 warnings, 0 informations
ruff check / format       11 files                            -> All checks passed! / 11 files already formatted
new # noqa                0 in the new module, new model, new test module
tsc --noEmit              clean           vitest src/panels/review/  -> 7 files / 57 tests passed
>=1200 census (same scope, mine)  base2 2116 files / 27 offenders / band 100
                                  candidate 2119 files / 27 offenders / band 101   -> no new offender
catalog & lane            sha256sum evidence-lifecycle.toml = caf1b9ee2b0a0b82356ee65339e77328e6f632d1848a9ba05fc4a0fc88831f78
                          == LIFECYCLE_CATALOG_SHA256 (fresh, matching); 16 contracts / 66 artifacts
                          new module's lane row at test-evidence-lanes.toml:302 (integration)
                          four DERIVED consumer rows; MT12 removes one and the census FAILS (2 failed) -> rows are required, not decorative
case budgets              unit 2701/2722 (base 2701/2722 — unchanged), integration 461 (base 448)  — both far under 4000/1000
split                     test_review_sync_movement_read.py 836 -> 358 L; new module 820 L; new application module 881 L
moved cases on base bytes base2 + only the new/changed test modules + registries:
                          ERROR during collection — ModuleNotFoundError: No module named
                          'agents_remember.application.review_external_git_movement'
                          round-1 production bytes + the new module -> 8 failed, 5 passed, 3 subtests
                          (exactly the report's §F9 figure, reproduced)
```

---

## 9. Findings

### F1 — **low** — the closeout/integration typed absence states a disjunction where the code knows the cause

* **file:line** `mcp/src/agents_remember/application/review_external_git_movement.py:429-437`
  (`external_git_movement_result_block` → `_absence_block`).
* **What.** When no measurement could be made the attached block's `detail` reads *"no boundary was
  measured for this result: this contract declares no work branch, **or** the leaf has published no
  comparison generation whose declared identities could be compared"*. `external_git_movement_for_contract`
  (`:378-385`) distinguishes exactly which of those applies (non-leaf kind / no declared work branch /
  `no-generation`), and in my probe the contract **does** declare a work branch, so the first arm is
  inapplicable even though the sentence offers it.
* **Evidence.** `closeout-probe-r2.txt`: the no-generation variant's block is
  `{'state': 'not-measured', 'detail': 'no boundary was measured for this result: this contract declares
  no work branch, or the leaf has published no comparison generation whose declared identities co…',
  'unsupported': [4]}` while the same contract's `code_work_branch` is non-empty (the closeout's own
  `declared_work_branch` is in the measured block of the other variant).
* **Why it matters.** It is not false — it is an explicit disjunction — so it is not blocking; but it is
  the one place this round's attachment is less precise than the rest of the leaf's vocabulary, where an
  absence always names the fact behind it (the review read's `reason`, the H1 selection detail). A reader
  of a closeout result cannot tell "this leaf was never reviewed" from "this checkout is not a leaf".
* **Suggested fix.** Build the detail from the same branch `external_git_movement_for_contract` already
  takes (`contract.kind != "leaf"` / `not contract.code_work_branch` / `selection.state ==
  "no-generation"`), the way `_unavailable` carries the selection's own detail.

### F2 — **low** — two line counts in the fix round's own rail table are off by one and two

* **file:line** `temp/icr/report-l23.md` §F8.
* **What.** The table says `application/review_external_git_movement.py` is **880** (it is **881**) and
  `tests/test_review_sync_movement_read.py` is **356** (it is **358**; the leaf's base is 356, and the +2
  are the docstring lines pointing at the new sibling module).
* **Evidence.** `wc -l` on the frozen candidate and on the leaf: `881`, `358`; `git show
  473ad824:mcp/tests/test_review_sync_movement_read.py | wc -l` → `356`.
* **Why it matters.** Neither number is a rail breach (both far under 900/1200) and neither is
  store-facing, so this is a report-accuracy nit of the same class the round-1 L6 finding was about: a
  stated measurement that does not reproduce. The *substance* of the claim — the round-1 growth to 836 is
  gone and nothing approaches 1200 — is true.
* **Suggested fix.** State 881 / 358, or derive the numbers in the table from `wc -l` in the same pass
  that takes the fingerprint.

**`unreproduced`, said plainly:** I did not drive the `ambiguous` selection tie (no operation produces it,
and none of my fixtures does either); I did not drive the *all-channels-unreadable* arrangement that would
exercise `_compared_clause`'s "No declared identity was compared" arm (I read the branch and confirmed the
control flow, but no fixture of mine reaches it); and I did not re-run the two large families against the
worker's exact module lists (my derived sets are 34 and 17 modules and both are green).

**One correction about my own round-1 method.** My first round-2 mutation batch reported MT4/MT7 "silent";
that was a bug in my harness (its `PYTHONPATH` still pointed at the round-1 frozen tree while the
mutations were applied to the round-2 copy), not a property of the delivery. On the corrected runs every
one of MT1–MT12 is NOTICED. I state it because a verifier's own instrument is not exempt from the
evidence rules.

---

## 10. What is strong in this fix round

1. **Both blocking findings are fixed at the root, not at the symptom.** The vocabulary gained the state it
   was missing (`unchanged`) and the `branch-switch` label is now earned by a *branch sub-fact*; the
   cherry-pick row was rewritten to the route that measurably exists, including the sync outcome the
   verifier actually measured.
2. **The self-reported false sentence (F7) is real and the fix is exact** — the absence sentence now says
   what was and was not compared, with the all-uncompared clause reachable only when it is true.
3. **The closeout ruling was applied as a statement, proven on the real tools, with the closeout still
   closing and the integration still landing** — and the typed absence rides even when there was never a
   generation.
4. **The `ambiguous` limitation is disclosed rather than hidden**, with the mapping and its driving
   constraint named.
5. **Every previously silent guard is now pinned by a case that fails without it**, and the census
   registration for the new module is genuinely derived (my perturbation makes it fail).
6. **The fingerprint recipe is now a script whose declaration and command agree**, and the candidate held
   its combined digest from the gate through the end reading.

---

## Verdict

**Verdict: `pass-with-findings`** — no blocking finding.

The two blocking findings are closed on the shipped bytes, each reproduced by me through the real
operation: the control state now publishes `unchanged` with a sentence that asserts no transition (and the
transition vocabulary can no longer name an append, a rebase or a switch that did not happen — I attacked
that from five states, including the two where the sub-facts disagree, and the missing-object state no
longer fabricates a switch), and the cherry-pick row's recovery now points only at what measurably exists
(the sync run after a pick returns `already-current`/`no-movement` and records no rebinding, exactly as
the row now says). H1 is closed with the selection's own detail and the `no-generation → None` contrast
intact; H2 is closed on all three real owners as a statement that never gates (closeout closed with its
R21 receipt, integration landed), and the authoring-boundary absence is verified rather than accepted; H3
is closed on the one mounted sentence, in both directions, with the client case proving the renderer.
L1–L6 are all fixed, the two previously unpinned guards are now pinned (MT4/MT7 NOTICED where they were
silent), and the fix round's own two truthfulness repairs (F7's absence sentence, F1's "a declared branch
advanced") are true in every state I can render. Rails are green: pyright 0/0/0, ruff check and format
clean, no new suppression, tsc clean, 57/57 review-pane vitest, census 27 offenders (base 27) with one
disclosed soft-band crossing, catalog pin freshly measured and matching at 16 contracts / 66 artifacts
with derived consumer rows that fail the census when perturbed, budgets 2701 unit / 461 integration, and
the moved cases failing on base bytes and on the round-1 production bytes exactly as claimed. Twelve
mutations, all NOTICED, all restored and re-hashed. What remains is one low (an absence detail that
states a disjunction where the code knows the cause) and one low report-accuracy nit (two line counts off
by one and two), neither store-facing and neither blocking; the `ambiguous` arm is disclosed and
undriven, and I did not drive it either.

Artifacts under `/home/firefox/projects/ar-coordination/temp/verify-l23/`: `probes-r2.txt`,
`probes-frozen-final.txt` (round 1), `closeout-probe-r2.txt`, `bite_proofs_l23.py`,
`mutations_l23_r2.py`, `mutations-r2.txt`, `mt11-client.txt`, `mt12.txt`, `base-defect-r2.txt`,
`round1-vs-newcases.txt`, `regression-review-r2.txt`, `regression-worktree-r2.txt`, `census.py`,
`fingerprint-l23-fix1.sh`, `gate-l23-round2.sh`, `frozen-r2/`, `frozen/` (round 1), `base2/`.
