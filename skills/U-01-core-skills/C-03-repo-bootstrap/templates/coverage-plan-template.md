# Bootstrap Coverage Plan — <repo>

| Field | Value |
|---|---|
| generated | <YYYY-MM-DDThh:mm> |
| controlMode | gated / automated |
| bootstrapMode | <mode> |
| basedOn | scout report, area reports, area briefs, root overview, input ledger, developer notes |

## Strategy

<Explain the memory-building strategy. Example: start with command boundary routes, then create route-local overviews for status mappers, then document high-risk boundary files.>

## Area Coverage Summary

| Area | Priority | Coverage Goal | Evidence Needed | Suggested First Wave |
|---|---|---|---|---|
| <area> | high | root + route overview + top files | docs / boundary / none | overview-wave-001 |

## Route Classification Queue

| Source Route | Classification | Priority | Reason | Suggested Action |
|---|---|---|---|---|
| `<path>` | core-logic / cross-repo-boundary / workflow / routine-support | high / medium / low | <why> | overview card / defer / exclude |

## File Classification Queue

| Source File | Area | Classification | Priority | Reason | Suggested Action |
|---|---|---|---|---|---|
| `<path>` | <area> | landmine / boundary / core-logic / routine-support | high | <why> | file card + onboarding-wave-001 |

## Evidence Pack Queue

| Area Or Route | Docs Pack Needed? | Boundary Pack Needed? | Reason |
|---|---|---|---|
| `<route>` | yes / no | yes / no | <why> |

## Deferred Routes And Files

| Path | Reason For Deferral | Revisit Trigger |
|---|---|---|
| `<path>` | simple DTO / generated / routine helper | when modified / when promoted by task |

## Developer Review Questions

1. Are these the right routes to document first?
2. Are any deferred files actually risky?
3. Are any high-priority files deprecated or scheduled for deletion?
4. Are any classifications wrong?
5. Which route should be the first overview wave?

## Decision Log

| Date | Decision | Source |
|---|---|---|
| <date> | <decision> | developer / evidence / automated assumption |
