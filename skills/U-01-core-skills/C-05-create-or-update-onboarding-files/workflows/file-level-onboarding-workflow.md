# File-Level Onboarding Workflow

Use this workflow when creating or maintaining the common file-level onboarding content model for one concrete source file. Sidecar onboarding stores that content under the C-08 resolved `onboarding_root` using the source file's repo-relative path directly; inline onboarding uses the same sections with storage-specific rules from `inline-onboarding-workflow.md`.

Template: `../templates/file-level-onboarding-template.md`

## Goal

Create or update the file-level onboarding content for one concrete source file.

## Scope

1. one file-level onboarding unit per source file
2. sidecar storage keeps the strict mirrored path directly under the repo root using the source file's repo-relative path
3. file-level onboarding records the nearest governing route-local `overview.md` when one exists
4. inline storage reuses the same content model but follows storage-specific syntax and placement rules
5. durable commentary only; planning stays in task artifacts
6. route-level slice creation, refresh, move, or deletion is out of scope for this workflow and routes to C-03 `existing-memory-slice-maintenance`

## Source Discovery Rules

1. Start by reading the C-08 resolved `system/sources.md` and use its `Domain Documentation` category for the file being documented.
2. Use the `Domain Documentation` sources from the resolved `system/sources.md` when building `### Docs References` and any load-bearing explanatory prose that depends on technical or behavioral documentation.
3. Treat adjacent onboarding as supporting input, not the whole discovery plan and not a substitute for the `Domain Documentation` pass.
4. If `Domain Documentation` includes both local and live variants, use the local material first for direct access and line citations, but write the onboarding link to the canonical online reference rather than the local mirror path.
5. If relevant material cannot be found in the `Domain Documentation` sources, record what was checked and that no relevant evidence was found.
6. The resolved `system/sources.md` is discovery-only. It must not appear as a cited source in `### Docs References`, `## Repo-Internal References`, or `## Cross-Repo References`.
7. Cite the actual evidence source: the library documentation page, canonical local mirror, repository source file, generated artifact, onboarding file, or sibling-repo file that directly proves the statement.

## Placement Rules

```text
<resolved-onboarding-root>/
  overview.md
  <mirrored-source-folder>/
    overview.md
  <mirrored-source-path>.md
```

1. File name matches the source file name with `.md` appended.
2. Never group multiple source files into one onboarding file.
3. Keep the mirrored path stable so reviewers can move directly between source and onboarding.
4. Add `governingOverview` metadata and a `## Governing Overview` section pointing to the nearest route-local overview. If no route-local overview exists, point to the closest ancestor overview, falling back to root `overview.md`.
5. When inline storage is configured, keep the section semantics identical and use `inline-onboarding-workflow.md` only for syntax, placement, digesting, and fallback behavior.

## Metadata Rules

1. `lastUpdated`: use `mcp_time_get_current_time` in `YYYY-MM-DDThh:mm` format.
2. `lastVerifiedCommitHash` and `lastVerifiedCommitDate`: use the latest commit that touched the source file once the content has been verified.
3. For planned code not yet created, leave verification hash and date empty until the file exists and can be verified.

## Section Rules

Required top-level sections:

1. metadata table, including `governingOverview`
2. `## Governing Overview`
3. `## Purpose`
4. `## Code Commentary`
5. `## Repo-Internal References`
6. `## Cross-Repo References`
7. `## Update History`

`## Update History` is append-only and newest-first. Preserve earlier entries even when they are superseded; add a later entry that corrects, supersedes, or clarifies them.

Subsections under `## Code Commentary`:

1. `### Logic`
2. `### Conventions`
3. `### Invariants And Boundaries`
4. `### Todos`
5. `### Docs References`

Citation requirements for reference sections:

1. `### Docs References` must include a concise prose summary when there is meaningful domain context to explain, followed by a markdown table with columns `Finding`, `Citations`, and `Source Path`.
2. `## Repo-Internal References` must include a concise prose summary when there is meaningful same-repository context to explain, followed by a markdown table with columns `Finding`, `Citations`, and `Source Path`.
3. `## Cross-Repo References` must include a concise prose summary when there is meaningful system-boundary behavior to explain, followed by a markdown table with columns `Finding`, `Citations`, and `Source Path`.
4. In `### Docs References`, `Source Path` must link to the canonical online document URL. Read local mirrors if needed, but do not link to them.
5. In `## Repo-Internal References`, `Source Path` must use a workspace-relative markdown link to the cited same-repository code, onboarding, config, test, or generated artifact. Do not use absolute filesystem paths.
6. In `## Cross-Repo References`, `Source Path` must use a workspace-relative markdown link when the cited boundary evidence exists in the workspace; otherwise link to the canonical external document or system reference.
7. `Citations` must list exact line ranges, for example `L10-L18` or `L10-L18; L42-L47`.
8. `Finding` must be a concise summary of what those cited lines establish.
9. Do not rely on uncited prose alone in any reference section. Investigate and preserve useful explanation, then support it with the citation table. If nothing relevant exists, keep the table and note what was checked and that no relevant evidence was found.
10. Do not cite source registries, search pages, or “where to look” files as evidence. They are allowed only as discovery inputs before reading the actual source.

## Create Workflow

Before creating file-level onboarding, confirm the target is one concrete source file. If the changed paths imply a new or newly important route-local slice, route to C-03 `existing-memory-slice-maintenance` first so the governing overview placement is decided before file docs are created.

1. identify the exact source file path
2. confirm the mirrored onboarding path
3. identify the nearest governing route-local overview by walking ancestor onboarding paths from the source file folder toward the root; read it when it exists
4. read the C-08 resolved `system/sources.md`, then read the source file and the relevant materials from its `Domain Documentation` category, capturing the exact citation ranges needed for `Docs References`, `Repo-Internal References`, and `Cross-Repo References`
5. gather metadata:
   - current time via MCP time tool
   - latest source-file commit via `git log --oneline -1 --format="%H %ci" -- <source-file>`
6. fill the template from `../templates/file-level-onboarding-template.md`, including `governingOverview` and the `## Governing Overview` backlink
7. update the repo-level or route-local overview index if the file should be indexed or cross-referenced there
8. cross-check all reference sections before finishing: preserve any load-bearing explanation, ensure the cited material is the actual evidence source selected via the resolved `system/sources.md` rather than the registry itself, ensure docs rows link to the canonical online reference, ensure repo-internal rows use same-repository workspace-relative links, ensure cross-repo rows still represent a real external or sibling boundary, health-check the cited targets when retrieval tools are available, and ensure every table row has exact line ranges plus a concise finding summary

## Maintain Workflow

When code changes:

1. re-read the source file and the onboarding file
2. re-read the C-08 resolved `system/sources.md` when the domain-documentation discovery path may have changed, then update any changed purpose, logic, conventions, invariants, docs references, repo-internal references, or cross-repo references, including correcting existing explanation, refreshing citation line ranges when the source moved or changed, and health-checking the cited targets before treating those references as current
3. for inline storage, apply the updated content through `inline-onboarding-workflow.md` so comment syntax, placement, and source digest stay consistent
4. update metadata after the content has been verified
5. append a newest-first `Update History` entry without deleting or rewriting earlier entries

When code is deleted or moved:

1. for a one-to-one file move or delete, delete or move the onboarding file to match the source tree
2. update affected repo-level overview indexes and cross-links
3. check whether repo-level entity catalogs or nearby onboarding need follow-up because of the move or deletion
4. if the move or deletion affects a whole package, module, feature area, or source route, route to C-03 `existing-memory-slice-maintenance` for coordinated cleanup or move handling
