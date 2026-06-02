# `c-02-memory-quality-control` Memory Quality Control

`c-02-memory-quality-control` controls whether onboarding can be trusted before planning and whether refreshed memory is clean enough to commit during closeout.

## What It Checks

For file-level sidecar onboarding, the `c-02-memory-quality-control` skill reads verification metadata and compares the source file against the recorded commit through `HEAD`, including staged or unstaged local changes.

It writes temporary drift reports under the resolved coordination root:

```text
ar-coordination/temp/drift-reports/<repo>/
```

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
