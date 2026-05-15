# <repo> — Onboarding Overview

| Field | Value |
|---|---|
| repository | <repo-name> |
| doc_type | `repo-overview` |
| sourceRoute | `<repo-root>` |
| lastUpdated | <YYYY-MM-DDThh:mm> |
| lastVerifiedCommitHash | `<full 40-char SHA or empty during bootstrap>` |

## What This Repo Is

<Purpose, deployment model, core responsibilities, and the technologies that define the repo.>

## Architecture At A Glance

```text
<ASCII diagram showing the major components and how they interact>
```

## Code Structure

| Area | Source Route | Tech | Purpose | Local Overview |
|---|---|---|---|---|
| <area> | [`<path>`](path) | <tech> | <what lives here> | [`<path>/overview.md`](path/overview.md) / planned / deferred |

## Functional Areas

### <Area Name>

<Short, high-signal summary. Keep detailed local routing in the route-local overview once it exists.>

## Cross-Repo References

<Explain important repo-level cross-repo or external-boundary behavior. Back the explanation with the table below. If nothing relevant exists, keep the table and record what was checked plus `No relevant cross-repo evidence found.`>

| Finding | Citations | Source Path |
|---|---|---|
| <Concise summary of the cross-repo tie, interface, or service boundary.> | L10-L18 | [<source-or-onboarding.md>](relative/path/to/source-or-onboarding.md) |

## Build & Dev

- <build command>
- <run command>
- <test command>

## Key Invariants

- <repo-wide invariant>
- <repo-wide invariant>

## Glossary Terms

| Term | Meaning | Notes |
|---|---|---|
| <term> | <definition> | <optional scope or nuance> |

## Docs References

<Explain documentation context that matters for understanding this repo. Back the explanation with the table below. If nothing relevant exists, keep the table and record what was checked plus `No relevant documentation found.`>

| Finding | Citations | Source Path |
|---|---|---|
| <Concise summary of the cited lines and why they matter.> | L20-L33 | [<doc-title-or-id>](https://example.com/canonical-doc-url) |

## What To Explore Next

| Priority | Area / Path | Why Next | Suggested Artifact |
|---|---|---|---|
| high | [`<source-route>`](source-route) | <why this area should be researched next> | route-local overview / file card / docs pack / boundary pack |

## Needs Verification

- <Any unresolved or low-confidence findings that should not be stated as settled fact.>

## Update History

<!-- newest first; append-only -->
