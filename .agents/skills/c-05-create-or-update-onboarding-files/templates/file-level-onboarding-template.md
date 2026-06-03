# <SourceFileName.ext>

This is the canonical file-level onboarding content model. Sidecar onboarding mirrors it into markdown files; inline onboarding serializes the same sections into a structured source-file comment block via `inline-onboarding-block-template.md`.

| Field                  | Value                                 |
| ---------------------- | ------------------------------------- |
| repository             | <repo-name>                           |
| path                   | `<repo-relative path to source file>` |
| doc_type               | `file-level-onboarding`               |
| lastUpdated            | <YYYY-MM-DDThh:mm>                    |
| lastVerifiedCommitHash | `<full 40-char SHA>`                  |
| lastVerifiedCommitDate | <YYYY-MM-DDThh:mm>                    |
| governingOverview       | `<nearest governing overview.md>`     |

## Governing Overview

<Backlink to the nearest route-local `overview.md` that governs this file. If no route-local overview exists yet, link to the closest ancestor overview, falling back to the root `overview.md`.>

## Purpose

<What this source file is responsible for and why it matters. Do not replace this with a generic “see overview.md”; the file onboarding must remain useful when opened directly.>

## Code Commentary

### Logic

<What the code does. Key functions or methods, data flow, notable branching, and the parts that are easiest to misunderstand.>

### Conventions

<Patterns, naming conventions, or local style choices specific to this file or area. Mention route-local conventions only when they specifically affect this file.>

### Invariants And Boundaries

<Rules that must continue to hold, ownership boundaries, sequencing constraints, and what this file should not do.>

### Todos

<Known issues or technical debt that are not tied to one active task file.>

## Docs References

<Start with a short prose summary if there is meaningful external or domain-documentation context to explain, then add the citation table. Use the `c-08-ar-coordination-context-resolver` skill resolved `system/sources.md` as a discovery aid, not as the citation target, and search beyond the registry until you find the actual proving document.
Cite the actual online, intranet, library, or product documentation that directly proves the statement. Read local mirrors if needed, but treat them as orientation caches, link the table row to the canonical live document reference, and health-check that canonical reference during create/update work. If the reference cannot be verified, record the blocker explicitly. Investigate and preserve useful explanation already present in this section; correct it if needed rather than deleting it. If nothing relevant exists after checking live sources, keep the table and record what was checked plus `No relevant documentation found after checking live sources.`>

| Finding                                                                | Citations | Source Path                                                |
| ---------------------------------------------------------------------- | --------- | ---------------------------------------------------------- |
| <Concise summary of the cited lines and why they matter to this file.> | L10-L18   | [<doc-title-or-id>](https://example.com/canonical-doc-url) |

## Repo-Internal References

<Start with a short prose summary if there is meaningful same-repository context to explain, then add the citation table. Cite the actual code, onboarding evidence, config, test, or generated artifact that directly proves the statement. Use workspace-relative links for same-repository evidence; never absolute filesystem paths. Health-check each workspace-relative target during create/update work; if the target moved or no longer exists, repair the entry before finishing. Investigate and preserve useful explanation already present in this section; correct it if needed rather than deleting it. If nothing relevant exists, keep the table and record what was checked plus `No relevant internal references found.`>

| Finding                                                                | Citations | Source Path                                                                         |
| ---------------------------------------------------------------------- | --------- | ----------------------------------------------------------------------------------- |
| <Concise summary of the cited lines and why they matter to this file.> | L20-L35   | [<same-repo-source-or-onboarding-file>](relative/path/to/source-or-onboarding-file) |

## Cross-Repo References

<Start with a short prose summary if there is meaningful cross-repo or external-boundary behavior to explain, then add the citation table. Use the `c-08-ar-coordination-context-resolver` skill resolved `system/sources.md` only to choose the search space; never cite the registry itself. Cite the actual sibling-repo file, generated handoff artifact, boundary contract, or authoritative external-system document that directly proves the boundary. Use workspace-relative links when the proving target is in the workspace, and canonical external URLs otherwise. Health-check each cited target during create/update work; if the target moved or no longer exists, repair the entry before finishing. Investigate and preserve useful explanation already present in this section; correct it if needed rather than deleting it. If nothing relevant exists, keep the table and record what was checked plus `No meaningful cross-repo references found.`>

| Finding                                                                                 | Citations | Source Path                                                                     |
| --------------------------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------- |
| <Concise summary of the interface, external repo/service involved, and why it matters.> | L42-L58   | [<source-or-adjacent-repo-file>](relative/path/to/source-or-adjacent-repo-file) |

## Update History

<!-- newest entry by date and time is prepended at the top of the list; prepend-only; preserve earlier entries and add later entries for corrections, superseded notes, or follow-up clarification -->
