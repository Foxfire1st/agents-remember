# `c-02-memory-quality-control` Memory Quality Control

`c-02-memory-quality-control` controls whether onboarding can be trusted before planning and whether refreshed memory is clean enough to commit during closeout.

## What It Checks

For file-level sidecar onboarding, the `c-02-memory-quality-control` skill reads verification metadata and compares the source file against the recorded commit through `HEAD`, including staged or unstaged local changes.

The task-start `drift_check` writes its temporary drift report under the resolved coordination
root:

```text
ar-coordination/temp/drift-reports/<repo>/
```

The curator uses the full contract-scoped `memory_quality_check` instead. That call atomically
replaces one combined worklist at:

```text
<worktree-enclosure>/reports/curator-memory-quality.md
```

The worklist includes repairable quality findings, missing onboarding, stale route indexes,
source-change reconciliation candidates, closeout-owned provenance, and noteworthy report-only
evidence. The curator runs it at intake and after repairs until `curatorActionableCount=0` and
`checklistStatus=ready-for-closeout`. It is operational state outside both Git worktrees, and
cleanup or abandon removes `reports/` with the enclosure.

## Common Classifications

| Classification | Meaning |
| --- | --- |
| `up to date` | Source has not changed since onboarding verification. |
| `drifted` | Source changed, so onboarding needs review. |
| `missing verification` | The onboarding unit lacks required metadata. |
| `missing` | An eligible source file has no onboarding where one is expected. |
| `orphaned` | The onboarding file points at a source path that no longer exists. |
| `disabled` | Path rules disable onboarding for that source. |
| `unsupported` | The helper cannot safely verify the storage or file shape. |

## Trust Levels

Drifted onboarding can still have directional value. The report should qualify that value instead of silently treating stale content as current truth.

## Boundary

The `c-02-memory-quality-control` skill detects and reports. It does not refresh onboarding. Use the `c-05-create-or-update-onboarding-files` skill for file-level onboarding maintenance and the `c-03-repo-bootstrap` skill when structural route or slice changes need bootstrap-style maintenance.
