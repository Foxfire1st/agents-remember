# Bootstrap Input Ledger — <repo>

| Field | Value |
|---|---|
| targetRepo | `<repo>` |
| controlMode | gated / automated |
| bootstrapMode | quick-orientation / safe-starter-memory / cross-repo-focused / domain-doc-focused / full-bootstrap |
| memoryRoot | `<path>` |
| topology | internal / external / mixed |
| targetBranch | `<branch>` |
| generated | <YYYY-MM-DDThh:mm> |

## Presented Source Inventory

| Source | Category | Location | Status | Planned Use | User Decision |
|---|---|---|---|---|---|
| `<source>` | Domain Documentation / Project Docs / Cross-Repo Context / Other | `<path or URL label>` | readable / unreadable / unavailable | <planned use> | approved / excluded / corrected / pending |

## Sources I Will Not Use

| Source | Category | Reason |
|---|---|---|
| `<source>` | `<category>` | unavailable / stale / unrelated / wrong branch / unsupported category |

## Missing Or Weak Source Categories

| Category | Why It May Matter | Action |
|---|---|---|
| Domain Documentation | <why it may matter> | user supplied / parked / not needed |

## Additional Sources Provided By User

| Source | Category | Location | Reason |
|---|---|---|---|
| `<source>` | Domain Documentation / Project Docs / Ops Docs / Cross-Repo Context | `<location>` | <why it matters> |

## Source Corrections

| Original Finding | Correction | Reason |
|---|---|---|
| `<agent finding>` | `<user correction>` | <reason> |

## Cross-Repo Context From Settings

| Adjacent Repo | Expected Branch | Actual Branch | Status | Allowed Use |
|---|---|---|---|---|
| `<repo-b>` | `<branch>` | `<branch or unknown>` | matched / mismatch / missing | read-only / ignored |

## Bootstrap Assumptions

- <assumption>

## Hard Stop Conditions For This Run

- memory root cannot be resolved
- required source cannot be read
- multiple plausible source meanings exist
- cross-repo branch mismatch for a required boundary
- LOW-confidence claim would become durable fact
- docs and code conflict in a way the agent cannot resolve
- output would require updating a non-target repo

## Operator Decision

Selected control mode: gated / automated
Additional sources supplied: yes / no
Proceed: yes / no
