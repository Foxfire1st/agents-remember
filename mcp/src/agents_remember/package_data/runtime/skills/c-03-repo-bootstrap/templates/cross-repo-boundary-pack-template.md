# Cross-Repo Boundary Pack — <area-or-route>

| Field | Value |
|---|---|
| repo | <repo> |
| areaOrRoute | `<area-or-route>` |
| generated | <YYYY-MM-DDThh:mm> |
| topology | internal / external / mixed |
| status | complete / partial / blocked / no-boundary-found |

## Allowed Adjacent Repos

| Adjacent Repo | Expected Branch | Actual Branch | Status | Use |
|---|---|---|---|---|
| `<repo-b>` | `<branch>` | `<branch or unknown>` | matched / mismatch / missing | read-only / ignored |

## Boundary Summary

<Explain what this route sends, receives, exposes, or depends on.>

## Confirmed Interfaces

| Direction | This Repo | Other Repo/System | Interface | Evidence | Confidence |
|---|---|---|---|---|---|
| outbound | `<file/function>` | `<repo/path>` | HTTP `/api/x` | producer + consumer matched | [HIGH] |

## Shared Contracts

| Contract | This Repo Location | Other Repo Location | Sync Requirement | Confidence |
|---|---|---|---|---|
| <enum/schema/topic> | `<path>` | `<path>` | names must match | [HIGH] |

## Branch And Topology Notes

- <Adjacent repo> was used only because it is explicitly allowed and on the expected branch.
- <Adjacent repo> was ignored because branch did not match `<expected>`.

## Same-Repo Facts That Must Not Be Classified As Cross-Repo

| Fact | Correct Bucket |
|---|---|
| <same-repo helper call> | Repo-Internal References |

## Boundary Risks

- [HIGH] <risk>
- [MEDIUM] <risk>

## Needs Developer Confirmation

- [LOW] <possible tie>
