# <Area Or Route Name> Overview

| Field | Value |
|---|---|
| repository | <repo> |
| doc_type | `route-local-overview` |
| sourceRoute | `<source route>` |
| onboardingRoute | `<onboarding route>/overview.md` |
| parentOverview | [`<parent overview.md>`](../overview.md) |
| lastUpdated | <YYYY-MM-DDThh:mm> |
| lastVerifiedCommitHash | `<full 40-char SHA or empty during bootstrap>` |

## What This Area Is

<Explain this subtree as if it were a small repo of its own.>

## What Belongs Here

| Path | Role |
|---|---|
| `<path>` | <role> |

## What Does Not Belong Here

| Nearby Thing | Belongs Instead In |
|---|---|
| <thing> | `<other route>` |

## Structures Found Here

<Packages, classes, services, components, mappers, handlers, configs, etc.>

## Operating Model

<Stepwise explanation of how this area works.>

## Main Flows

### <Flow Name>

1. <step>
2. <step>
3. <step>

## Load-Bearing Files

| File | Role | Why It Matters | Onboarding |
|---|---|---|---|
| `<path>` | boundary / mapper / state machine | <reason> | covered / planned / deferred |

## Local Invariants And Traps

- <invariant>
- <trap>

## Repo-Internal References

<Explain same-repository structures, flows, files, tests, configs, or generated artifacts that affect this route. Use direct evidence and workspace-relative links. If nothing relevant exists, record what was checked plus `No relevant internal references found.`>

| Finding | Citations | Source Path |
|---|---|---|
| <Concise summary of same-repository evidence that matters to this route.> | L20-L35 | [<same-repo-source-or-onboarding-file>](relative/path/to/source-or-onboarding-file) |

## Cross-Repo References

<Explain cross-repo or external-boundary behavior that affects this route. Use a boundary pack when available. If none exists, record what was checked plus `No relevant cross-repo evidence found.`>

| Finding | Citations | Source Path |
|---|---|---|
| <Concise summary of the boundary and why it matters to this route.> | L10-L18 | [<boundary-pack>](<relative-path-from-this-overview-to-repo-root>/bootstrap/evidence/cross-repo/<pack>.md) |

## Docs References

<Explain documentation context that affects this route. Use a docs pack when available. If none exists, record what was checked plus `No relevant documentation found.`>

| Finding | Citations | Source Path |
|---|---|---|
| <Concise summary of the cited documentation and why it matters to this route.> | L20-L33 | [<docs-pack>](<relative-path-from-this-overview-to-repo-root>/bootstrap/evidence/docs/<pack>.md) |

## File-Level Onboarding Map

| Source File | Onboarding File | Status | Reason |
|---|---|---|---|
| `<source>` | [`<source>.md`](<source>.md) | covered / planned / deferred | <reason> |

## Child Overviews

| Route | Why It Has Its Own Overview |
|---|---|
| [`<child route>/overview.md`](<child-route>/overview.md) | <reason> |

## How To Use This Area

When changing files under this route:

1. Read this overview.
2. Read any child overview closer to the file.
3. Read the file-level onboarding if it exists.
4. If no file-level onboarding exists, check whether this overview is sufficient or whether the file should be promoted.

## Needs Verification

- [LOW] <question or tentative finding>

## Update History

<!-- newest first; append-only -->
