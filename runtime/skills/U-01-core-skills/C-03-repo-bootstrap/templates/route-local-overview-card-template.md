# Overview Card — <source route>

| Field | Value |
|---|---|
| repo | <repo> |
| sourceRoute | `<source route>` |
| targetOverview | `<onboarding route>/overview.md` |
| parentOverview | `<nearest parent overview.md>` |
| generated | <YYYY-MM-DDThh:mm> |
| priority | high / medium / low |
| confidence | [HIGH/MEDIUM/LOW] |

## Why This Overview Exists

<Explain why this subtree needs its own route-local overview.>

## Governs

| Path | Role | Coverage Status |
|---|---|---|
| `<path>` | core logic / boundary / mapper / UI / config | planned / covered / deferred / excluded |

## What This Overview Must Explain

- What this subtree does.
- What structures can be found here.
- What belongs here.
- What does not belong here.
- How this subtree connects to nearby code.
- Which files are load-bearing.
- Which workflows pass through this subtree.
- Which docs or cross-repo behavior affect this subtree.
- Which files should get file-level onboarding later.

## Inputs

| Input | Path | Required? |
|---|---|---|
| Root overview | `overview.md` | yes |
| Area report | `bootstrap/areas/<area>.md` | yes |
| Area brief | `bootstrap/areas/<area>.brief.md` | yes |
| Interface report | `bootstrap/areas/<area>/interfaces.md` | if relevant |
| Concerns report | `bootstrap/areas/<area>/concerns.md` | if relevant |
| Docs pack | `bootstrap/evidence/docs/<area-or-route>.docs-pack.md` | if available |
| Boundary pack | `bootstrap/evidence/cross-repo/<area-or-route>.boundary-pack.md` | if available |

## Required Links

### Backlinks

- Parent overview: `<nearest parent overview.md>`

### Downlinks

- Child overview candidates
- Planned file onboarding files
- Existing file onboarding files

## Open Questions

- [LOW] <question>
