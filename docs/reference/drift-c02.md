# `c-05-create-or-update-onboarding-files` Memory Quality Control

`c-05-create-or-update-onboarding-files` controls whether onboarding can be trusted before planning and whether refreshed memory is clean enough to commit during closeout.

## What It Checks

For file-level sidecar onboarding, the `c-05-create-or-update-onboarding-files` skill reads verification metadata and compares the source file against the recorded commit through `HEAD`, including staged or unstaged local changes.

Before MIK-R112, the task-start `drift_check` wrote its temporary drift report under the resolved coordination
root (the tool has since left the surface):

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
evidence. The curator runs it at intake and after repairs until `curatorActionableCount=0` and the
**raw** `qualityChecklistStatus` reads `ready-for-closeout`; the combined `checklistStatus` is rewritten
to `coherence-required` only when the coherence record is then missing or stale — the coherence gate
that `prepare` → `publish` → `validate` must clear — and `closeoutReady` becomes true only once that
validation passes. It is operational state outside both Git
worktrees, and
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

The [memory-quality diagnostic procedure](../../skills/c-05-create-or-update-onboarding-files/workflows/memory-quality-control.md) detects and reports; that procedure does not refresh onboarding. File-level authorship and maintenance belong to the same skill's [converted-card workflow](../../skills/c-05-create-or-update-onboarding-files/workflows/converted-card-workflow.md). Use `c-03-repo-bootstrap` when structural route or slice changes need bootstrap-style maintenance. These procedure boundaries grant no extra role/tool authority: approval gates, the prohibition on raw Git writes, closeout ownership and the separate push approval remain in force.
