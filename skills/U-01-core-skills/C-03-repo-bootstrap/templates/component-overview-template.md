# <Component> — Component Overview

> <One-sentence description of the component's role in the repo>

Use this template for `onboarding/<repo>/<component>/overview.md`.

## Architecture

```text
<ASCII diagram showing the component's internal structure and main dependencies>
```

## Responsibilities

<Describe what this component owns and what it deliberately does not own.>

## Key Flows

<Summarize the important runtime, data, or control flows within this component.>

## Patterns & Conventions

- <important local pattern>
- <important local convention>

## Invariants & Boundaries

- <load-bearing invariant>
- <ownership or sequencing boundary>

## Onboarding File Index

| Priority | Source File                                  | Onboarding Path                                            | Why                                        |
| -------- | -------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------ |
| high     | [<path/to/source-file>](path/to/source-file) | [<path/to/onboarding-file.md>](path/to/onboarding-file.md) | <why this file needs dedicated onboarding> |

## Cross-Repo References

<Keep this as a table. Every row must include a clickable source path, exact line ranges, and a concise finding summary. If nothing relevant exists, keep the table and record what was checked plus `No relevant cross-repo evidence found.`>

| Source Path                                                          | Citations | Finding                                                                                                  |
| -------------------------------------------------------------------- | --------- | -------------------------------------------------------------------------------------------------------- |
| [<path/to/source-or-onboarding.md>](path/to/source-or-onboarding.md) | L15-L29   | <Concise summary of the cross-repo tie, interface, or external contract established by the cited lines.> |

## Docs References

<Keep this as a table. Every row must include a clickable source path, exact line ranges, and a concise finding summary. If nothing relevant exists, keep the table and record what was checked plus `No relevant documentation found.`>

| Source Path                                                    | Citations | Finding                                                                                    |
| -------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------ |
| [<path/to/doc-or-onboarding.md>](path/to/doc-or-onboarding.md) | L20-L33   | <Concise summary of the cited lines and why they matter for understanding this component.> |

## Tech Stack

- <language/framework>
- <supporting runtime or library>

## Needs Verification

- <Any unresolved or low-confidence findings that should not be stated as settled fact>

## Notes

- `Docs References` and `Cross-Repo References` stay as citation-backed tables, not prose lists.
- `Source Path` must be the clickable link to the cited file.
- `Citations` should use exact line ranges like `L10-L18` or `L10-L18; L42-L47`.
- `Finding` should be a concise summary of what the cited lines establish.
