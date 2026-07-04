# Verdict Template (adversarial reviewer)

The artifact an **adversarial reviewer** writes at a seam (`jobs/adversarial-reviewer.md`). It lands
under the series `notes/reports/` directory and attaches to the handover gate as **judge evidence**.
There are two variants: **master-exit** (before a manager hands over to the orchestrator) and
**super-exit** (before the orchestrator hands over to the developer).

## Rules

1. **A verdict is evidence, not a decision.** State an explicit pass/block **recommendation**; the gate's
   decider decides. Never write the verdict as if it were the gate outcome.
2. **A block must decompose into fix leaves** — concrete, leaf-shaped findings the owning
   manager/orchestrator can dispatch. A block that cannot be named as fix leaves is not yet a block.
3. **Refute-or-confirm:** every finding must survive an attempt to refute it. Rank findings and cite
   the backing evidence file.
4. Cover all three lenses (completion · code quality · onboarding-vs-code) explicitly, even to say a
   lens is clean.
5. Record the gate evidence reference exactly: `kind=reviewer-verdict`, `ref=<artifact path>`, and
   `verdict=<pass | pass-with-notes | block>`.

## Master-Exit Variant

```md
# Adversarial Verdict — master-exit · <master id>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| seam            | master-exit                                        |
| scope           | <master integration branch ref>                    |
| reviewer        | <this reviewer's agent/lifecycle id>               |
| task docs       | <master task_doc + leaf task docs>                 |
| recommendation  | PASS | PASS-WITH-NOTES | BLOCK                       |
| decider         | <orchestrator | developer> (per L4 policy)         |
| artifact path   | notes/reports/<master-id>-master-exit-verdict.md   |
| gate evidence   | kind=reviewer-verdict; ref=<artifact path>; verdict=<pass | pass-with-notes | block> |
| written         | <YYYY-MM-DDTHH:MM>                                  |

## Lens 1 — Completion vs Task Docs
- Master requirement/substep coverage: addressed | delta justified | MISSING
- Leaf task docs and worker reports reconcile with the master task_doc: yes | <gaps>
- backing evidence file: <impact-analysis report path>

## Lens 2 — Code Quality
- resolved system/tools.md suite: green | failing:<which>
- regressions vs the past (route indexes · CGC · GrepAI): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- drift_check clean and memory_quality_check clean: yes | <finding>
- master-side route overviews current: yes | <finding>
- backing evidence file: <onboarding-coherency report path>

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ------- | ----------------- | -------------------- | --------- |

## If BLOCK — Manager Fix Leaves
1. <fix leaf under this master: scope · target files/docs · evidence refs · done-when>
   (A BLOCK with no fix leaves here is invalid — resolve to PASS-WITH-NOTES or name the leaves.)

## Judge-Evidence Note
This verdict attaches to the master-exit handover gate as judge evidence. The decider decides; this
reviewer does not.
```

## Super-Exit Variant

```md
# Adversarial Verdict — super-exit · <super branch>

| Field           | Value                                              |
| --------------- | -------------------------------------------------- |
| seam            | super-exit                                         |
| scope           | <super integration branch ref>                     |
| reviewer        | <this reviewer's agent/lifecycle id>               |
| task docs       | <portfolio task docs + master task docs>           |
| recommendation  | PASS | PASS-WITH-NOTES | BLOCK                       |
| decider         | developer (or delegated role per L4 policy)        |
| artifact path   | notes/reports/<series-id>-super-exit-verdict.md    |
| gate evidence   | kind=reviewer-verdict; ref=<artifact path>; verdict=<pass | pass-with-notes | block> |
| written         | <YYYY-MM-DDTHH:MM>                                  |

## Lens 1 — Completion vs Portfolio Task Docs
- Portfolio objective and dependency order satisfied: yes | <gaps>
- Master handover packets and prior master-exit verdicts reconciled: yes | <gaps>
- Cross-master conflicts, duplicate implementations, and deferred follow-ups surfaced: yes | <finding>
- backing evidence file: <impact-analysis report path>

## Lens 2 — Code Quality
- resolved system/tools.md suite for the accumulated super branch: green | failing:<which>
- branch-wide regressions vs the past and across masters (route indexes · CGC · GrepAI): none | <finding>
- backing evidence file: <quality/impact report path>

## Lens 3 — Onboarding vs Code
- changed files' sidecars refreshed same pass: yes | <gaps>
- drift_check clean and memory_quality_check clean: yes | <finding>
- route overviews describe the accumulated behavior: yes | <finding>
- C-11 carry-over and ledger mapping coherent for the final super branch: yes | <finding>
- backing evidence file: <onboarding-coherency report path>

## Findings (ranked; each refute-tested)
| # | Severity | Finding | Evidence file/ref | Refutation attempted | Survives? |
| - | -------- | ------- | ----------------- | -------------------- | --------- |

## If BLOCK — Orchestrator Fix Leaves
1. <fix leaf routed by the orchestrator: target manager/master/super-worktree · scope · target files/docs · evidence refs · done-when>
   (A BLOCK with no fix leaves here is invalid — resolve to PASS-WITH-NOTES or name the leaves.)

## Judge-Evidence Note
This verdict attaches to the super-exit handover gate as judge evidence. The decider decides; this
reviewer does not.
```
