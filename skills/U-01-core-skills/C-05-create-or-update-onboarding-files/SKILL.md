---
name: create_or_update-onboarding-file
description: "Create and maintain onboarding artifacts including file-level onboarding MDs and repo-level entity catalogs. Covers template, folder organization, section conventions, and metadata. Enforces strict 1-to-1 mapping for source files and one entity catalog per repo. Use this whenever creating new onboarding files, repo entity catalogs, or auditing existing onboarding artifacts for format compliance."
---

# C-05 Create Or Update Onboarding Files

This package owns durable onboarding maintenance for two artifact types and one storage-specific adapter layer:

1. file-level onboarding units for concrete source files
2. repo-level entity catalogs for load-bearing cross-layer entities

Inline onboarding does not replace the file-level content model. It reuses the same semantic sections and adds storage-specific syntax, placement, and digest rules through the inline adapter workflow/template.

Planning stays in task artifacts. This package defines how onboarding itself is created, updated, and kept structurally consistent.

Before maintaining onboarding, use `C-08-ar-management-resolver` to resolve the target repository's active management context. It must use the `Domain Documentation` category declared in the resolved `system/sources.md` for the onboarding slice being maintained, rather than assuming that adjacent onboarding alone is sufficient or hard-coding one particular documentation system into the skill.

## Routing

Choose the artifact type first, then use the matching workflow and template.

| Artifact type             | Use when                                                                        | Workflow                                      | Template                                      |
| ------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------- | --------------------------------------------- |
| File-level onboarding     | You are creating or updating file-level onboarding for one concrete source file | `workflows/file-level-onboarding-workflow.md` | `templates/file-level-onboarding-template.md` |
| Repo-level entity catalog | You are creating or updating `<onboarding-root>/<repo>/entities.md`             | `workflows/repo-entity-catalog-workflow.md`   | `templates/repo-entity-catalog-template.md`   |

Storage-specific adapter additions for file-level onboarding:

1. `workflows/inline-onboarding-workflow.md`
2. `templates/inline-onboarding-block-template.md`

## Shared Placement Rules

```text
<onboarding-root>/
  index.md
  <repo>/
    overview.md
    entities.md
    <component>/
      overview.md
      src/<mirrored-source-tree>.md
```

1. File-level onboarding keeps strict 1-to-1 mirroring with source files.
2. Repo-level entity catalogs exist once per repo at `<onboarding-root>/<repo>/entities.md`.
3. Use `mcp_time_get_current_time` for onboarding timestamps; never guess.
4. Keep durable onboarding commentary separate from task-local planning and output docs.

## Quick Rules

1. Prefer updating an existing onboarding artifact over creating parallel duplicates.
2. File-level onboarding explains one concrete source file.
3. The canonical file-level onboarding content model is shared by external and inline storage.
4. Repo-level entity catalogs document real entities and cross-layer projections, not generic glossary content.
5. If both a file-level onboarding document and a repo entity catalog need updates, handle both in the same pass when the task materially affects both.
6. This package may be invoked immediately from `C-01-findings-capture` when a verified factual current-state clarification qualifies for onboarding propagation.
7. When updating `Docs References` or `Cross-Repo References`, do not optimize by deleting existing explanation. Investigate the existing prose, correct it if needed, and back it with citations.
8. Start reference discovery from the C-08 resolved `system/sources.md`, then use its `Domain Documentation` category as the required domain-evidence input for the file or entity being documented.
9. Treat onboarding as supporting context, not as a substitute for the `Domain Documentation` category.
10. Treat the C-08 resolved `system/sources.md` as a routing index only. Never cite it as evidence in `Docs References` or `Cross-Repo References`.
11. Reference health checking is mandatory during onboarding maintenance. Do not assume existing `Docs References` or `Cross-Repo References` are still valid.

## Source Discovery Rule

Before writing or revising onboarding content, read the C-08 resolved `system/sources.md` and use its `Domain Documentation` category as the required discovery path for technical and behavioral documentation.

1. Use the `Domain Documentation` category from the resolved `system/sources.md` as the discovery plan for `Docs References`, `Cross-Repo References`, and any load-bearing explanatory prose.
2. Preserve useful adjacent onboarding context, but do not let it replace the required `Domain Documentation` pass.
3. Do not hard-code one external documentation system into this package. The operative rule is to use whatever sources are listed under `Domain Documentation` in the resolved `system/sources.md`.
4. If `Domain Documentation` includes both a local mirror and a live retrieval path, use the local material first for direct access and line citations, but emit onboarding links to the canonical live reference. Never emit filesystem paths to local documentation mirrors in onboarding output.
5. If no relevant material is found in the `Domain Documentation` sources, record what was checked instead of pretending the search space was limited to onboarding only.
6. Do not put the resolved `system/sources.md`, source registries, search result pages, or “see this source list” rows in citation tables. After using the registry to choose where to look, cite the actual documentation, source file, generated artifact, or local mirror that directly proves the statement.
7. If the registry points to a documentation source but the source does not prove anything relevant for the file, say that no relevant documentation was found and cite the repository source code in `Cross-Repo References` for implementation facts.

## Reference Section Rule

`Docs References` and `Cross-Repo References` are explanation-first sections.

1. Explain domain behavior, system boundaries, or cross-repo interactions in prose.
2. Correct outdated or unsupported prose instead of deleting it reflexively.
3. Add citation-backed tables so the explanation is auditable.
4. Do not treat the citation table as a replacement for the explanation when the section is carrying real behavioral context.
5. In `Docs References`, link the last-column cell to the canonical online document URL even when the cited lines were read from a local mirror.
6. In `Cross-Repo References`, link code or onboarding evidence with workspace-relative markdown paths. Never emit absolute filesystem paths.
7. Every citation table row must point to evidence, not to the discovery registry that helped find evidence.
8. Reference health checking is mandatory during create and maintain passes.
9. For `Docs References`, verify the canonical online URL still resolves to the intended document when retrieval tools are available. If it cannot be verified, record the blocker instead of leaving the entry implicitly trusted.
10. For `Cross-Repo References`, verify each workspace-relative target still exists and that the cited lines still support the stated finding. Repair or remove broken entries before finishing.

## Lifecycle Rules

### When code changes

1. update the relevant onboarding sections that no longer match the code
2. refresh verification metadata after the content update

### When code is deleted or moved

1. delete or move the mirrored onboarding file to match the real source tree
2. update overview indexes and cross-references that point at the old location
3. check whether repo-level entity catalogs or related onboarding now need cleanup
