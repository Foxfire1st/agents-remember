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

## Fixer State

A fixer dry run over the tree reports nothing left to author and no refused card.
Record the exact dry-run result here; the bootstrap is not complete until the run is clean.

| Check | Result | Notes |
|---|---|---|
| dry run: nothing left to author | pass / fail | <`citation_fix` dry-run summary> |
| dry run: no refused card | pass / fail | <refused list, or `none`> |

## Baseline State

| Item | Value |
|---|---|
| `c-10-adopt-memory-baseline` run? | yes / no |
| memory-content commit | <commit or `not adopted yet`> |

## Knowledge Foundation State

The state that `memory_init` reports at that moment — context-not-admitted
(`memory_revision_unavailable`) before the first baseline, while the memory line has no
commit yet, and `not-recorded` after the baseline — with the next step
it names. This skill produces no knowledge records; never say a knowledge foundation exists
because cards exist.

| Item | Value |
|---|---|
| knowledge state | not-recorded / recorded / unusable / context-not-admitted |
| identity / code and path | <identity, or code and path> |
| next step | <the next step `memory_init` names> |

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
