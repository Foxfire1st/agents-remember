# File Card — <source path>

| Field | Value |
|---|---|
| repo | <repo> |
| sourceFile | `<source path>` |
| targetOnboardingFile | `<onboarding path>` |
| generated | <YYYY-MM-DDThh:mm> |

## Classification

| Field | Value |
|---|---|
| Priority | high / medium / low / defer |
| Role | entrypoint / boundary / core logic / mapper / utility / config / DTO |
| Risk | landmine / cross-repo / complex / routine / unknown |
| Suggested action | create onboarding / update onboarding / defer / index only |
| Suggested wave | onboarding-wave-001 / onboarding-wave-002 / later |

## Governing Context

| Field | Value |
|---|---|
| nearestGoverningOverview | `<mirrored-source-folder>/overview.md` |
| ancestorOverviews | `overview.md`, `<ancestor>/overview.md` |
| localArea | <area name> |

## Why This File Matters

<Short explanation derived from area findings and route-local overview.>

## What The Worker Must Explain

- <purpose>
- <main logic>
- <non-obvious behavior>
- <invariants>
- <interfaces>
- <failure modes>
- <update risks>

## Inputs For Worker

| Input | Path | Required? | Why |
|---|---|---|---|
| Source file | `<path>` | yes | concrete behavior |
| Nearest governing overview | `<path>/overview.md` | yes | local area model |
| Existing onboarding | `<path>.md` | if exists | preserve/update durable memory |
| Area report | `bootstrap/areas/<area>.md` | yes | local context |
| Docs pack | `bootstrap/evidence/docs/<area-or-route>.docs-pack.md` | if relevant | domain docs evidence |
| Boundary pack | `bootstrap/evidence/cross-repo/<area-or-route>.boundary-pack.md` | if relevant | cross-repo evidence |

## Files The Worker May Read

- `<source file>`
- `<nearest governing overview>`
- `<listed related file>`
- `<evidence pack>`

## Files The Worker Must Not Read Without Escalation

- unrelated area files
- adjacent repo files not listed in boundary pack
- broad directory scans
- all area reports from other areas

## Required Onboarding Sections

- metadata including `governingOverview`
- Purpose
- Code Commentary
- Docs References
- Repo-Internal References if applicable
- Cross-Repo References
- Update History

## Reference Expectations

| Section | Expected? | Evidence Source |
|---|---|---|
| Docs References | yes / no / maybe | docs pack |
| Repo-Internal References | yes / no / maybe | source/tests/config/overview |
| Cross-Repo References | yes / no / maybe | boundary pack |

## Known Traps

- <trap>

## Questions To Resolve

- [LOW] <question>

## Done When

- The file-level onboarding exists or is updated.
- The onboarding explains only this concrete source file.
- The onboarding backlinks to the nearest governing overview.
- Reference sections are evidence-backed.
- No task-local planning notes are included.
- Open questions are recorded in the wave review or STATE.md.
