---
name: c-05-create-or-update-onboarding-files
description: "Create and maintain file-level onboarding and repo entity catalogs. Enforces strict 1-to-1 source mapping, governing overview links, metadata, and references, and routes package/module/source-slice create, refresh, move, or delete cases to the c-03-repo-bootstrap skill."
---

# c-05-create-or-update-onboarding-files Create Or Update Onboarding Files

This package owns durable onboarding maintenance for two artifact types and one storage-specific adapter layer:

1. file-level onboarding units for concrete source files
2. repo-level entity catalogs for load-bearing cross-layer entities

Inline onboarding does not replace the file-level content model. It reuses the same semantic sections and adds storage-specific syntax, placement, and digest rules through the inline adapter workflow/template.

Planning stays in task artifacts. This package defines how onboarding itself is created, updated, and kept structurally consistent.

Before maintaining onboarding, use `c-08-ar-coordination-context-resolver` to resolve the target repository's active coordination context. It must use the `Domain Documentation` category declared in the resolved `system/sources.md` for the onboarding slice being maintained, rather than assuming that adjacent onboarding alone is sufficient or hard-coding one particular documentation system into the skill.

When onboarding maintenance is happening inside a `c-09-git-worktree-manager`
worktree, check `worktree_status` before starting memory entries. If its
`freshness` block reports that the recorded code or memory source branch moved,
run `worktree_sync` before writing onboarding content unless that sync is blocked
by conflicts or an explicit developer decision. Carrying the moved official line
first keeps later onboarding updates and ledger rows on top of current source
history instead of forcing a conflict-heavy carryover after the task is otherwise
done.

The `c-05-create-or-update-onboarding-files` skill remains the normal public entry point for create or update onboarding requests. When the request is route-level slice creation, refresh, move handling, or deletion cleanup, the `c-05-create-or-update-onboarding-files` skill should route the work to the `c-03-repo-bootstrap` skill `existing-memory-slice-maintenance` mode instead of reducing it to unrelated file-level updates.

## Routing

Choose the artifact type first, then use the matching workflow and template.

| Artifact type             | Use when                                                                        | Workflow                                      | Template                                      |
| ------------------------- | ------------------------------------------------------------------------------- | --------------------------------------------- | --------------------------------------------- |
| File-level onboarding     | You are creating or updating file-level onboarding for one concrete source file | `workflows/file-level-onboarding-workflow.md` | `templates/file-level-onboarding-template.md` |
| Repo-level entity catalog | You are creating or updating `entities.md` under the resolved onboarding root   | `workflows/repo-entity-catalog-workflow.md`   | `templates/repo-entity-catalog-template.md`   |
| Route/slice maintenance   | A package, module, or source route was added, refreshed, moved, or deleted      | the `c-03-repo-bootstrap` skill `existing-memory-slice-maintenance` mode      | the `c-03-repo-bootstrap` skill bootstrap templates                      |

Storage-specific adapter additions for file-level onboarding:

1. `workflows/inline-onboarding-workflow.md`
2. `templates/inline-onboarding-block-template.md`

## Route-Level Maintenance Routing

Before starting file-level maintenance, classify the change shape.

Handle directly in the `c-05-create-or-update-onboarding-files` skill when:

1. one concrete source file needs onboarding creation or update
2. a small behavior-preserving file move has a clear one-to-one mirrored onboarding move
3. a deleted file is proven to have no behavior that moved elsewhere and only requires mirrored file-level onboarding cleanup plus nearby link repair
4. a repo-level entity catalog needs a targeted entry update

Route to the `c-03-repo-bootstrap` skill `existing-memory-slice-maintenance` mode when:

1. a new package, module, feature area, or source route appears
2. a deleted package, module, feature area, or source route requires coordinated route-local overview, child onboarding, and bootstrap artifact cleanup
3. a package or module route moves and the route-local overview plus child file onboarding need to move together
4. many files appear or disappear together
5. a route purpose changes enough that the governing route-local overview should be created, refreshed, moved, retired, or removed before file-level work continues
6. a file split, merge, or behavior relocation crosses multiple source files and file-by-file `c-05-create-or-update-onboarding-files` skill work would lose the structural meaning of the source change

When routing to the `c-03-repo-bootstrap` skill, pass the affected source routes, known old/new paths, existing onboarding paths, and whether the desired outcome is expansion, refresh, move handling, or deleted-slice cleanup.

## Onboarding Preservation Rule

Existing onboarding is durable memory, not disposable generated output. Preserve and move useful onboarding before creating replacement artifacts.

1. For behavior-preserving file renames or moves, move the mirrored onboarding file to the new source path and update path metadata, verification metadata, governing overview links, reference links, route indexes, and `Update History`.
2. For splits, merges, or behavior relocation, inspect the old onboarding and the new source targets before deleting anything. Reuse accurate purpose, commentary, invariants, references, and history in the new target onboarding, updating only the parts that changed.
3. For source deletion, first prove whether the documented behavior is truly gone or merely moved. Delete or retire onboarding only when no safe current target remains for the preserved knowledge.
4. When preservation cannot be done safely, record why in the maintenance notes or task artifact before removing the old onboarding content.

## Sidecar Placement Rules

```text
<resolved-onboarding-root>/
  overview.md
  overview.index.json
  entities.md
  <mirrored-source-folder>/
    overview.md
    overview.index.json
    <mirrored-source-file>.md
```

1. File-level onboarding keeps strict 1-to-1 mirroring with source files.
2. Route-local `overview.md` files live in the mirrored onboarding hierarchy at the source folder they govern. They are governing context, not replacements for file-level onboarding.
3. File-level onboarding should record the nearest `governingOverview` when one exists; otherwise point to the closest ancestor overview, falling back to the root `overview.md`.
4. Generated `overview.index.json` files live adjacent to route overviews and record route scope, child routes, existing file sidecars, coverage counts, routing terms, hot-path summary/hints, and governing-overview fallback.
5. Repo-level entity catalogs exist once per repo as `entities.md` under the resolved onboarding root.
6. Use `mcp_time_get_current_time` for onboarding timestamps; never guess.
7. Keep durable onboarding commentary separate from task-local planning and output docs.

## Quick Rules

1. Prefer updating an existing onboarding artifact over creating parallel duplicates.
2. File-level onboarding explains one concrete source file and has to be self-sufficient;
3. File-level onboarding links back to the nearest governing route-local overview so agents can reconstruct local area context from a file drop point. When updating onboarding, check whether the nearest governing overview has changed and update the link if needed.
4. The canonical file-level onboarding content model is common to sidecar and inline storage.
5. Repo-level entity catalogs document real entities and cross-layer projections, not generic glossary content.
6. Repo-level entity catalogs use deterministic `git-blob-set-v1` fingerprints over curated load-bearing evidence paths; the `c-05-create-or-update-onboarding-files` skill chooses and refreshes those paths, while the `c-02-memory-quality-control` skill compares them and verifies that every inventory entry has a matching fingerprint row.
7. If both a file-level onboarding document and a repo entity catalog need updates, handle both in the same pass when the task materially affects both.
8. This package may be invoked immediately from `c-01-findings-capture` when a verified factual current-state clarification qualifies for onboarding propagation.
9. When updating `Docs References`, `Repo-Internal References`, or `Cross-Repo References`, do not optimize by deleting existing explanation. Investigate the existing prose, correct it if needed, and back it with citations.
10. Start reference discovery from the `c-08-ar-coordination-context-resolver` skill resolved `system/sources.md`, then use its `Domain Documentation` category as the required domain-evidence input for the file or entity being documented.
11. Treat onboarding as supporting context, not as a substitute for the `Domain Documentation` category.
12. Treat the `c-08-ar-coordination-context-resolver` skill resolved `system/sources.md` as a routing index only. Never cite it as evidence, and never let it stand in for the direct proof that belongs in `Docs References`, `Repo-Internal References`, or `Cross-Repo References`.
13. Reference health checking is mandatory during onboarding maintenance. Do not assume existing `Docs References`, `Repo-Internal References`, or `Cross-Repo References` are still valid.
14. `Update History` sections are append-only: preserve existing entries and add newer entries for corrections, superseded notes, or follow-up clarification.
15. When changed paths imply route-level creation, refresh, move, or deletion, route to the `c-03-repo-bootstrap` skill `existing-memory-slice-maintenance` mode before creating piecemeal file-level onboarding.
16. After creating, updating, moving, or deleting route overviews or file-level sidecars, refresh generated route indexes with MCP `route_index_refresh`.
17. Keep each route overview's `## Hot Path Summary` short and current; it is copied into generated indexes for `c-04-retrieval-strategy-router` skill discovery.
18. Do not delete onboarding for moved, split, merged, or deleted source until checking whether its documented behavior can be moved into a current onboarding target.
19. Body before metadata: a changed source file's sidecar (and the nearest governing route overview) needs a real content update plus an `Update History` entry, or an explicit `No content impact:` / `No route impact:` reviewed-no-impact history entry. Closeout enforces this and surfaces attested documents; header-only or unmarked history-only refreshes fail.
20. In a worktree-backed task, run `worktree_status` and any needed `worktree_sync` before starting onboarding writes so memory entries are based on the current parent branch.

## Route Index Refresh

Route indexes are generated availability metadata. Do not hand-maintain long
missing-sidecar lists in overview prose.

Use (`route_index_refresh` applies by default; preview first with
`route_index_refresh(repo_id="<repo-id>", dry_run=true)`):

```text
route_index_refresh(repo_id="<repo-id>")
```

The generator:

1. discovers every `overview.md` under the resolved onboarding root
2. writes adjacent `overview.index.json` files
3. derives child routes from nested overviews
4. derives `coveredFiles` from existing file-level sidecars whose source files
   still exist
5. derives route source scope from the overview route
6. copies a bounded `## Hot Path Summary` into `hotPath.summary`
7. derives `hotPath.candidateHints` and `hotPath.anchorHints` mechanically
8. lets the `c-04-retrieval-strategy-router` skill infer absent sidecars from source scope plus generated coverage

If the generator reports stale or surprising coverage, fix the onboarding
structure or source-sidecar mismatch before treating the index as current.
If `hotPath.summary` is empty or stale, update the owning overview; do not hand
edit the generated index.

The skill tree is instruction-only; installed and development workflows use the
MCP/package route.

## Source Discovery Rule

Before writing or revising onboarding content, read the `c-08-ar-coordination-context-resolver` skill resolved `system/sources.md` and use its `Domain Documentation` category as the required discovery path for technical and behavioral documentation.

1. Use the `Domain Documentation` category from the resolved `system/sources.md` as the discovery plan for `Docs References` and any load-bearing explanatory prose that depends on external technical or domain documentation.
2. Preserve useful adjacent onboarding context, but do not let it replace the required `Domain Documentation` pass.
3. Do not hard-code one external documentation system into this package. The operative rule is to use whatever sources are listed under `Domain Documentation` in the resolved `system/sources.md`.
4. If `Domain Documentation` includes both a local mirror/cache and a live retrieval path, treat the live source named by the registry as authoritative and the local material as an orientation cache. Use local material only for quick reading and line discovery, then verify relevance against the live source when the domain fact matters. Emit onboarding links to the canonical live reference, never filesystem paths to local documentation mirrors.
5. If the local mirror/cache has no matching page, appears stale, or lacks enough evidence, immediately search or retrieve the live documentation source through the registry's named tool or MCP before recording that no domain documentation exists.
6. If no relevant material is found after checking the live `Domain Documentation` source, or live retrieval is blocked, record what was checked and any blocker instead of pretending the search space was limited to onboarding or local files only.
7. Do not put the resolved `system/sources.md`, source registries, search result pages, or “see this source list” rows in citation tables. After using the registry to choose where to look, cite the actual documentation, source file, generated artifact, onboarding file, or boundary document that directly proves the statement.
8. If the registry points to a documentation source but the live source does not prove anything relevant for the file, say that no relevant documentation was found in `Docs References`. Route same-repository implementation facts to `Repo-Internal References`, and use `Cross-Repo References` only when the evidence proves a real repository or external-system boundary.

## Reference Section Rule

`Docs References`, `Repo-Internal References`, and `Cross-Repo References` are explanation-first sections.

1. Explain domain behavior, same-repository implementation context, or system-boundary interactions in prose.
2. Correct outdated or unsupported prose instead of deleting it reflexively.
3. Add citation-backed tables so the explanation is auditable.
4. Do not treat the citation table as a replacement for the explanation when the section is carrying real behavioral context.
5. In `Docs References`, link the last-column cell to the canonical online document URL even when the cited lines were read from a local mirror.
6. In `Repo-Internal References`, link same-repository code, onboarding, config, tests, or generated artifacts with workspace-relative markdown paths. Never emit absolute filesystem paths.
7. In `Cross-Repo References`, use workspace-relative markdown paths when the proving boundary target is available in the workspace, and otherwise link to the canonical external document or system reference.
8. Every citation table row must point to evidence, not to the discovery registry that helped find evidence.
9. Reference health checking is mandatory during create and maintain passes.
10. For `Docs References`, verify the canonical online URL still resolves to the intended document when retrieval tools are available. If it cannot be verified, record the blocker instead of leaving the entry implicitly trusted.
11. For `Repo-Internal References`, verify each workspace-relative target still exists and that the cited lines still support the stated finding. Repair or remove broken entries before finishing.
12. For `Cross-Repo References`, verify each cited boundary target still exists, still supports the stated boundary, and still belongs in this bucket rather than `Repo-Internal References`.

## Lifecycle Rules

### When code changes

1. update the relevant onboarding sections that no longer match the code — body
   updates come first; metadata and history updates never substitute for them
2. refresh verification metadata after the content update
3. append a newest-first `Update History` entry instead of deleting or rewriting earlier history
4. pair every body update with its `Update History` entry in the same pass:
   closeout rejects a changed source whose sidecar gained only metadata or
   history edits, and rejects a body edit without a new history entry
5. when a changed source genuinely warrants no onboarding content change (for
   example a version bump), record an explicit reviewed-no-impact entry instead
   of skipping silently: `No content impact: <reason>` in the file sidecar's
   `Update History`, or `No route impact: <reason>` in a governing route
   overview's `Update History`. These markers are deliberate attestations after
   reviewing the document against the change — never a default shortcut — and
   closeout surfaces every attested document in its tool response for review

### When code is moved, split, merged, or deleted

1. classify the source change as one-to-one move, split, merge, behavior relocation, true deletion, or route-level move/deletion
2. for behavior-preserving one-to-one moves, move the mirrored onboarding file to match the real source tree and update metadata, governing overview links, references, route indexes, and `Update History`
3. for splits, merges, or behavior relocation, reuse accurate old onboarding content in the new target sidecars or route overview before deleting the old mirrored file
4. update overview indexes and cross-references that point at the old location
5. check whether repo-level entity catalogs or related onboarding now need cleanup
6. when an entity fingerprint goes stale because an evidence path disappeared or a fingerprint row has no matching inventory entry, verify whether the entity was removed, renamed, or moved before deleting catalog content or evidence paths
7. delete or retire onboarding only after proving that the documented behavior is gone and no safe current target remains
8. if a package, module, feature area, or source route moved or disappeared, route to the `c-03-repo-bootstrap` skill `existing-memory-slice-maintenance` mode for coordinated route-local overview, child onboarding, and bootstrap artifact cleanup
