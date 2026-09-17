# Onboarding-Coherency Template

A durable report a curator writes for the affected onboarding handoff and, when review is explicitly
requested, the reviewer's onboarding-vs-code lens. It records c-05 sidecar/overview/index/entity
changes and the complete curation checks for the affected paths: the full memory-quality operation is
part of curation and its result travels with this handoff as a closeout and integration prerequisite.
Drift suites are separate developer-requested operations.

## Rules

1. Every **changed** source file must have its sidecar body updated **in the same pass** — a refreshed
   `lastVerifiedCommitHash` over stale content silently defeats the drift check and is a finding.
2. Every **new** source file must have a created sidecar (`check_missing_onboarding` clean).
3. Route/repository overviews must reflect the change set; a moved/added/deleted slice must be
   reflected in the governing overview.
4. This is a report; the reviewer's verdict or the orchestrator's main loop acts on it.

## Shape

```md
# Onboarding Coherency — <scope: master id | super branch | leaf group>

| Field     | Value                                   |
| --------- | --------------------------------------- |
| for       | curator handoff | reviewer (<seam>) | orchestrator         |
| author    | <analysis role / bounded fan-out label> |
| scope     | <change set reviewed>                    |
| written   | <YYYY-MM-DDTHH:MM>                        |

## Changed Files — Sidecar Refresh
| Source file | Sidecar updated same pass? | Body reflects change? | Finding |
| ----------- | -------------------------- | --------------------- | ------- |

## New Files — Missing Onboarding
| New source file | Sidecar created? | check_missing_onboarding clean? | Finding |
| --------------- | ---------------- | ------------------------------- | ------- |

## Checks — curation is complete
- Full `memory_quality_check` operation: pass | failed | blocked | not-run — <command/result/finding codes>
- `curator_coherence` when the checklist requires it: published | blocked | not-required — <result>
- c-05 affected-onboarding check: pass | failed | blocked | not-run — <scope/command/result>
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
