# Onboarding-Coherency Template

A durable report a fan-out sub-agent writes for the **adversarial reviewer's** third lens
(onboarding-vs-code) and for the **orchestrator's** memory-quality checks. It is the paired
`read_ar_files` + `memory_quality_check` + `drift_check` review made durable — the check that the
memory repo stayed in lockstep with the code, since **orchestrator quality ∝ memory-repo quality**.

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
| for       | reviewer (<seam>) | orchestrator         |
| author    | <sub-agent id>                          |
| scope     | <change set reviewed>                    |
| written   | <YYYY-MM-DDTHH:MM>                        |

## Changed Files — Sidecar Refresh
| Source file | Sidecar updated same pass? | Body reflects change? | Finding |
| ----------- | -------------------------- | --------------------- | ------- |

## New Files — Missing Onboarding
| New source file | Sidecar created? | check_missing_onboarding clean? | Finding |
| --------------- | ---------------- | ------------------------------- | ------- |

## Drift & Quality
- drift_check: clean | <N> actionable (list)
- memory_quality_check: pass | <findings>
- Ledger maps code HEAD: yes | <gap>

## Overviews
- Route/repository overviews current for touched routes: yes | <which are stale>
- Moved/added/deleted slices reflected in governing overviews: yes | <gap>

## Bottom Line
- Onboarding-vs-code coherent: yes | NO — <the specific gaps, as candidate fix leaves>
```
