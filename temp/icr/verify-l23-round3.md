# Independent adversarial verification — leaf L23 / ICR-R23@v1 — ROUND 3 (fix round 2, targeted confirmation)

**Verdict: `pass`** — no blocking finding, no finding.

**Subject.** CODE worktree
`/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l23-ar/260921-icr-l23`,
branch `ar/260921-icr-l23`, base `473ad824…`, plus the fix-round-2 delta. Report §"Fix round 2"
(G1–G5). My round-2 verdict (`verify-l23-round2.md`) is the baseline I diff against.

## Fingerprints — recipe reproduces; candidate never moved

| | declared | mine |
| --- | --- | --- |
| component 1 `git diff \| sha256sum` | `217be3a52753321a146d099075b4db02cd978992fab4ab3e4311d4bbf07f188f` | identical (unchanged, as the two edited files are untracked) |
| component 2 | 17 entries, `^?? temp/icr/` excluded | identical |
| untracked app module | `8b9016dcd0bdae186e37b3685ef4794c6b5c425f7d44235fff0f06ef9f631047` | identical |
| untracked model | `78766cec…` (unchanged) | identical |
| untracked test module | `1f736f9c96a46b7a4b0e8551e657f231b1d66b6503167ea61dbdee937ff6459b` | identical |
| **combined** | `13d0791b959c74b018fea9b05f210dbf8bb5277d0402cb67b61ec1f6ce796d` | identical |

Start 17:45:27; gate held `13d0791b…` for 100 s (5 samples) → open ~17:46:54; **end 17:48:52 →
`13d0791b…`**, component 1 `217be3a5…`, same 17 entries and the same three untracked hashes. **The
candidate did not move.** My frozen copy (`temp/verify-l23/frozen-r3`, `56a14bde…`) equals the leaf on
both changed files.

## 1. The one production change — confirmed by driving both causes myself

I drove the production entry point the tools call (`external_git_movement_result_block`) plus the real
closeout tools, on the round-3 bytes (`r3-causes.txt`):

```
[not-a-leaf]      state=not-measured  unsupported == unsupported_transitions(): True
  no boundary was measured for this result: this contract records kind 'series' rather than a leaf
  enclosure, so no leaf's published comparison generation resolves under it
[no-work-branch]  state=not-measured  unsupported == unsupported_transitions(): True
  no boundary was measured for this result: this contract declares no work branch, so there is no
  declared work-branch head to compare against the repository
[failed payload]  keys ['ok'] — nothing carried
no-generation (real preview + apply over a leaf that froze nothing), both results:
  detail: no boundary was measured for this result: no comparison generation is published for
          260921-icr-l1, so there is no reviewed generation to record a final output against
```

* the two driven causes are the real **series** contract (`kind == 'series'`) and the leaf contract with
  its `code_work_branch` emptied; the third is the store's own `no-generation` detail;
* **each sentence names its own cause and not its neighbours** ("declares no work branch" absent from the
  kind sentence; "records kind" absent from the branch sentence; neither carries the generation
  sentence) — the disjunction is gone and no sentence is false about the store;
* **typed absence unchanged**: `state: "not-measured"`, `unsupported` == `unsupported_transitions()` in
  every block, `ok: false` carries nothing;
* **review path unchanged**: `external_git_movement_for_contract` still returns `None` for both absence
  causes;
* **still a statement, never a gate**: preview `ok=True would-closeout`, apply `ok=True state=closed`
  (integration was driven in round 2 on the same attachment; MT6/MT13b byte-identically unchanged here).

## 2. Nothing else moved — AST-level proof, not just a hash

Per-file comparison against my round-2 frozen copy: **14 of 17 entries byte-identical** (all tracked
files, the model, `worktree_tools.py`, `docs/…`), and exactly two changed — the app module and its new
test module, both untracked (hence component 1 unchanged).

Parsing the app module on both revisions and comparing **every function body with docstrings stripped**:

```
added    _BoundaryAbsence, _measure_contract, _measure_generation
changed  external_git_movement_for_contract, external_git_movement_result_block
(no other definition differs)
```

So the delta is the absence-cause machinery plus prose; the measurement, state composition, shapes,
statements, matrix and recovery derivation are **code-identical** to the round-2 bytes I confirmed.
`test_review_external_git_movement_read.py` gained `test_each_cause_of_a_missing_boundary_gets_its_own_sentence`
and the strengthened `test_a_result_whose_leaf_published_nothing_states_the_absence`; nothing else.
No other re-run artefact was produced (only the report was written).

## 3. Report counts — corrected and accurate

§F8 now states **881** and **358** (with the correction and its reason); §G2 gives the post-round-2
sizes, and `wc -l` on the frozen candidate agrees on all three: app module **895** (under the 900 soft
rail; the disclosed 937 → 895 compression landed), new test module **879**, split module **358**
(base 356 + the 2 docstring lines). The recipe and its recompute agree (above).

## 4. Rails on these bytes

```
delivery modules  test_review_external_git_movement_read.py + test_review_sync_movement_read.py
                  -> 19 passed, 3 subtests passed          (matches §G3)
fast regression   dependency-ownership-ast / evidence-lanes / layering / file-size / suite-budget
                  + test_review_sync_rebinding.py          -> 24 passed
pyright (venv)    the changed module, its test module, worktree_tools.py -> 0 errors, 0 warnings, 0 informations
ruff check / format --check (same three)                   -> All checks passed! / 3 files already formatted
>=1200 census     base2 2116 / 27 offenders / band 100  ->  candidate 2119 / 27 / band 101  (no new offender)
catalog pin       sha256sum mcp/tests/evidence-lifecycle.toml = caf1b9ee2b0a0b82356ee65339e77328e6f632d1848a9ba05fc4a0fc88831f78
                  16 contracts / 66 artifacts (unchanged, matching LIFECYCLE_CATALOG_SHA256)
```

## Observations (not findings)

* The module crossed the 900 soft rail during this round (937) and came back to 895; the crossing and the
  compression are disclosed in §G1, and the disclosed side effect — correcting a stale docstring sentence
  that still repeated the round-1 B2 claim — is an improvement, not a regression.
* The five opens §G5 records (undriven `ambiguous` tie, unwired authoring boundary with its search,
  the disclosed band crossing, pick/revert indistinguishability, the client lane beyond the one mounted
  sentence) are the same set I confirmed or accepted in round 2; none is new.

Artifacts: `temp/verify-l23/r3-causes.txt`, `probe_l23_r3.py`, `delivery-modules-r3.txt`, `rails-r3.txt`,
`frozen-r3/`, `frozen-r2/`, `fingerprint-l23-fix1.sh`, `gate-l23-round3.sh`. No production code and no
memory worktree was touched, and nothing was committed.
