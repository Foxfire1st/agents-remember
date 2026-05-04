## <Area> — Repo Overview Merge Guide

> <One-sentence description of the area's role in the repo>

Use this template while integrating area-specific findings into `<onboarding-root>/<repo>/overview.md`. Do not paste it into the repo overview as a standalone block. Use it to decide which existing overview sections need to be updated so the result reads like one coherent document.

## Area Summary

<Summarize what this area owns, why it matters, and which existing repo-overview sections should absorb the new detail.>

## Overview Sections To Update

| Repo Overview Section                     | What To Add Or Revise                                             |
| ----------------------------------------- | ----------------------------------------------------------------- |
| `Architecture at a Glance`                | <diagram or boundary changes, if any>                             |
| `Code Structure`                          | <paths, ownership, tech, or boundary clarifications>              |
| `Functional Areas`                        | <the durable area explanation that belongs in the main narrative> |
| `Cross-Repo References`                   | <interfaces or contracts this area introduces or clarifies>       |
| `Docs References`                         | <external docs that matter for this area>                         |
| `Key Invariants` / `What to Explore Next` | <any repo-wide invariants or remaining gaps to add>               |

## Architecture Notes

```text
<Optional ASCII diagram or structural notes if this area changes the repo-level architecture explanation>
```

## Responsibilities

<Describe what this area owns and what it deliberately does not own.>

## Key Flows

<Summarize the important runtime, data, or control flows within this area.>

## Patterns & Conventions

- <important local pattern>
- <important local convention>

## Invariants & Boundaries

- <load-bearing invariant>
- <ownership or sequencing boundary>

## Onboarding File Index

| Priority | Source File                          | Onboarding Path                                    | Why                                        |
| -------- | ------------------------------------ | -------------------------------------------------- | ------------------------------------------ |
| high     | [<source-file>](path/to/source-file) | [<onboarding-file.md>](path/to/onboarding-file.md) | <why this file needs dedicated onboarding> |

## Cross-Repo References

<Start with prose that explains the important cross-repo or cross-boundary behavior this area contributes to the repo overview. Then add the citation table to back that explanation up. Preserve and correct useful existing explanation rather than replacing it with the table. If nothing relevant exists, keep the table and record what was checked plus `No relevant cross-repo evidence found.`>

| Finding                                                                                                  | Citations | Source Path                                                           |
| -------------------------------------------------------------------------------------------------------- | --------- | --------------------------------------------------------------------- |
| <Concise summary of the cross-repo tie, interface, or external contract established by the cited lines.> | L15-L29   | [<source-or-onboarding.md>](relative/path/to/source-or-onboarding.md) |

## Docs References

<Start with prose that explains the documentation context that matters for the repo sections this area will enrich. Then add the citation table to back that explanation up. Preserve and correct useful existing explanation rather than replacing it with the table. If nothing relevant exists, keep the table and record what was checked plus `No relevant documentation found.`>

| Finding                                                                               | Citations | Source Path                                                |
| ------------------------------------------------------------------------------------- | --------- | ---------------------------------------------------------- |
| <Concise summary of the cited lines and why they matter for understanding this area.> | L20-L33   | [<doc-title-or-id>](https://example.com/canonical-doc-url) |

## Tech Stack

- <language/framework>
- <supporting runtime or library>

## Needs Verification

- <Any unresolved or low-confidence findings that should not be stated as settled fact>

## Notes

- This template is a merge guide for the repo overview, not a standalone `<repo>/<component>/overview.md` file and not an instruction to append a separate deep-dive block.
- Merge the durable findings into the existing repo overview sections that own them; do not create a catch-all appendix or a separate deep-dive block at the end of the overview.
- `Docs References` and `Cross-Repo References` are explanation-first sections backed by citation tables.
- In `Cross-Repo References`, `Source Path` must be a workspace-relative link to the cited code or onboarding file.
- In `Docs References`, `Source Path` must be the canonical online document link, even if a local mirror was used for reading.
- `Citations` should use exact line ranges like `L10-L18` or `L10-L18; L42-L47`.
- `Finding` should be a concise summary of what the cited lines establish.
