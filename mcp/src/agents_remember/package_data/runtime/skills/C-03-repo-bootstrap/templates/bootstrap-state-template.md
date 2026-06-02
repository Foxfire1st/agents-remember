# Bootstrap State — <repo>

| Field | Value |
|---|---|
| started | <YYYY-MM-DDThh:mm> |
| lastUpdated | <YYYY-MM-DDThh:mm> |
| currentPhase | <phase and substep> |
| controlMode | gated / automated |
| bootstrapMode | quick-orientation / safe-starter-memory / cross-repo-focused / domain-doc-focused / existing-memory-slice-maintenance / full-bootstrap |
| memoryRoot | `<path>` |
| onboardingRoot | `<path>` |
| targetRepoBranch | `<branch>` |
| topology | internal / external / mixed |
| sourceInventoryStatus | pending / presented / accepted / corrected / blocked |

## Phase Status

| Phase | Status | Notes |
|---|---|---|
| Phase 0 — Setup / Source Intake | done / in-progress / blocked | |
| Phase 1 — Scout | done / in-progress / blocked | |
| Phase 2 — Area Deep-Dives | done / in-progress / blocked | |
| Phase 3 — Root Overview | done / in-progress / blocked | |
| Phase 4A — Coverage Plan | done / in-progress / blocked | |
| Phase 4B — Governing Route Map | done / in-progress / blocked | |
| Phase 4C-D — Route Overview Waves | done / in-progress / blocked | |
| Phase 4E — Docs Evidence | done / in-progress / blocked | |
| Phase 4F — Boundary Evidence | done / in-progress / blocked | |
| Phase 4G-H — File Onboarding Waves | done / in-progress / blocked | |
| Phase 4I — Curator Reviews | done / in-progress / blocked | |
| Phase 5 — Handoff | done / in-progress / blocked | |

## Areas

| Area | Priority | Scout | Deep-Dive | Brief | Root Overview | Coverage | Route Map | Status |
|---|---|---|---|---|---|---|---|---|
| <area> | high | done | done | done | done | planned | planned | active |

## Governing Routes

| Source Route | Overview Path | Status | Confidence | Notes |
|---|---|---|---|---|
| `<route>` | `<route>/overview.md` | planned / created / refreshed / moved / cleanup-planned / removed / retired / deferred / blocked | [HIGH/MEDIUM/LOW] | <notes> |

## Slice Maintenance

| Source Route | Change Type | Existing Memory | Planned Action | Status |
|---|---|---|---|---|
| `<route>` | added / refreshed / moved / deleted | none / route overview / child file onboarding / bootstrap artifacts | create / refresh / move / remove / retire / defer | planned / in-progress / complete / blocked |

## Waves

| Wave | Type | Focus | Status | Curator Result | Human Review |
|---|---|---|---|---|---|
| overview-wave-001 | route overview | `<routes>` | complete | pass / fixes-required | done / pending / automated |
| onboarding-wave-001 | file onboarding | `<files>` | complete | pass / fixes-required | done / pending / automated |

## Decisions

| # | Date | Decision | Context | Source |
|---|---|---|---|---|
| 1 | <date> | <decision> | <why> | developer / evidence / automated assumption |

## Parking Lot

- [LOW] <question or unresolved claim>

## Blockers

| Blocker | Phase | Owner | Needed To Unblock |
|---|---|---|---|
| <blocker> | <phase> | developer / agent | <action> |

## Deferred Files

| File | Reason | Revisit Trigger |
|---|---|---|
| `<path>` | routine DTO | when modified or promoted by coverage plan |

## Closeout Boundary

| Handoff Presented | Closeout Requested? | Notes |
|---|---|---|
| yes / no | yes / no / pending | closeout is separate from automated bootstrap |

## Next Recommended Action

<one clear next step>
