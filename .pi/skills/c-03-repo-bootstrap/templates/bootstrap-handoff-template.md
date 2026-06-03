# Bootstrap Handoff — <repo>

| Field | Value |
|---|---|
| generated | <YYYY-MM-DDThh:mm> |
| controlMode | gated / automated |
| bootstrapMode | quick-orientation / safe-starter-memory / cross-repo-focused / domain-doc-focused / existing-memory-slice-maintenance / full-bootstrap |
| currentStatus | complete / partial / paused / blocked |

## What Exists Now

| Artifact | Path | Status |
|---|---|---|
| Root overview | `overview.md` | complete / partial |
| Coverage plan | `bootstrap/coverage-plan.md` | complete / partial |
| Governing route map | `bootstrap/governing-route-map.md` | complete / partial |
| Route-local overviews | `<paths>` | complete / partial |
| Docs packs | `bootstrap/evidence/docs/` | complete / partial / none |
| Boundary packs | `bootstrap/evidence/cross-repo/` | complete / partial / none |
| File-level onboarding | `<paths>` | complete / partial |

## Slice Maintenance Results

| Source Route | Change Type | Memory Action | Artifacts |
|---|---|---|---|
| `<route>` | added / refreshed / moved / deleted | created / refreshed / moved / removed / retired / deferred | `<paths>` |

## Trusted Coverage

| Route / Area | Coverage Level | Notes |
|---|---|---|
| `<route>` | root + route overview + high-risk files | <notes> |

## Deferred Coverage

| Area / File | Reason | Revisit Trigger |
|---|---|---|
| `<path>` | routine helper | when modified |

## Open Questions

- [LOW] <question>

## Known Risks

- <risk>

## Completed Waves

| Wave | Type | Curator Result | Notes |
|---|---|---|---|
| overview-wave-001 | route overview | pass / fixes-required | <notes> |

## Recommended Next Waves

| Wave | Focus | Why |
|---|---|---|
| wave-002 | <area/files> | <reason> |

## Closeout Boundary

Automated bootstrap stops at this handoff. Ask whether separate closeout should run.

| Closeout Requested? | Notes |
|---|---|
| yes / no / pending | <developer decision or next ask> |

## Developer Decisions Recorded

| Date | Decision | Impact |
|---|---|---|
| <date> | <decision> | <impact> |

## How Future Agents Should Use This Bootstrap

1. Read `bootstrap/STATE.md`.
2. Read `overview.md`.
3. If touching a source file, read ancestor `overview.md` files along the mirrored onboarding route.
4. Read the file-level onboarding if it exists.
5. If no file-level onboarding exists, check whether the nearest governing overview is sufficient or whether the file should be promoted.
6. If touching cross-repo boundaries, read relevant boundary packs.
7. If touching domain behavior, read relevant docs packs.
