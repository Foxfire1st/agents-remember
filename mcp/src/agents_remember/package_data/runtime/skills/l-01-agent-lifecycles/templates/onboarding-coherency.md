# Onboarding-Coherency Template

The Reviewer uses this template to compare affected onboarding with code, using the Curator’s supplied handoff and curation-check result. The Curator, Orchestrator and developer can inspect the reported findings and readiness.

Record the sidecar, overview, index and entity changes, and the complete curation-check result supplied by the [curation operation](../operations/curation.md#run-the-complete-curation-checks).

## Rules

1. Before reporting a changed source file and its nearest governing overview, read [onboarding trace answers](../reference/curator/card-formats-and-citations.md#keep-current-card-content) for the `updated` and `no_impact` meanings. Record the current row, its reason and unresolved findings.
2. Report onboarding for each new source file under the resolved storage policy (`check_missing_onboarding` clean).
3. Route/repository overviews must reflect the change set; a moved/added/deleted slice must be
   reflected in the governing overview.
4. This is a report; the reviewer's verdict or the orchestrator's main loop acts on it.

## Shape

```md
# Onboarding Coherency — <scope: master id | super branch | leaf group>

| Field     | Value                                   |
| --------- | --------------------------------------- |
| for       | curator handoff \| reviewer (<seam>) \| orchestrator         |
| author    | <analysis role / helper label identifying the assigned routes> |
| scope     | <change set reviewed>                    |
| written   | <YYYY-MM-DDTHH:MM>                        |

## Changed Files — Sidecar Refresh
| Source file | Sidecar updated same pass? | Body reflects change? | Finding |
| ----------- | -------------------------- | --------------------- | ------- |
| <source file> | <yes \| no — reviewed, `no_impact`> | <review result> | <finding or none> |

## New Files — Missing Onboarding
| New source file | Sidecar created? | check_missing_onboarding clean? | Finding |
| --------------- | ---------------- | ------------------------------- | ------- |

## Checks — curation is complete
- Full `memory_quality_check` operation: pass | failed | blocked | not-run — <command/result/finding codes>
- `curator_coherence` when the checklist requires it: published | blocked | not-required — <result>
- affected-onboarding check: pass | failed | blocked | not-run — <scope/command/result>
- git diff --check: pass | failed | blocked | not-run — <command/result>
- Other named checks: pass | failed | blocked | not-run — <scope/command/result>
- drift suite when the workflow requests it: pass | failed | blocked | not-run — <request/result if any>
- Ledger maps code HEAD: yes | <gap>

## Overviews
- Route/repository overviews current for touched routes: yes | <which are stale>
- Moved/added/deleted slices reflected in governing overviews: yes | <gap>

## Handoff
- Affected onboarding ready for closeout transaction: yes | NO — <specific gaps>
- Failed/not-run checks reported without a full-green claim: yes | NO — <gap>
```
