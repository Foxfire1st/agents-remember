# Repo Entity Catalog Workflow

Use this workflow when creating or maintaining the repo-level entity catalog at `entities.md` under the C-08 resolved `onboarding_root`.

Template: `../templates/repo-entity-catalog-template.md`

## Goal

Create or update one repo-level entity catalog documenting load-bearing real entities and their cross-layer projections.

## Scope

1. exactly one entity catalog per repo
2. focus on load-bearing real entities that recur across layers and cause review, migration, or naming confusion
3. do not use the file as a glossary or an exhaustive ontology of every noun in the repo

## Source Discovery Rules

1. Start by reading the C-08 resolved `system/sources.md` and use its `Domain Documentation` category for the repo entities under review.
2. Use the `Domain Documentation` sources from the resolved `system/sources.md` when deciding canonical source of truth, naming drift, and cross-layer projections.
3. Do not rely on adjacent onboarding alone when the `Domain Documentation` category contains more authoritative domain, protocol, or architecture context.
4. If `Domain Documentation` includes both local and live variants, use the local material first for direct access and evidence gathering, but link onboarding output to the canonical online reference rather than the local mirror.
5. The resolved `system/sources.md` is a discovery index only. Do not cite it as evidence; cite the actual documentation, code, generated artifact, or sibling-repo source that proves each entity claim.

## Placement Rules

```text
<resolved-onboarding-root>/
  overview.md
  entities.md
```

1. The file lives directly under the resolved onboarding root.
2. It complements `overview.md`; it does not replace it.

## Metadata Rules

1. `lastUpdated`: use `mcp_time_get_current_time` in `YYYY-MM-DDThh:mm` format.
2. `status`: use `draft` while the structure is still evolving and `active` once it is stable enough for routine reuse.

## Entity Fingerprint Rules

1. Every `## Entity Inventory` entry must have one matching row in `## Entity Fingerprints`; missing rows are actionable drift.
2. Use `git-blob-set-v1` for alpha: sort the evidence paths, resolve each current `HEAD:<path>` Git blob hash, hash the `path + blob_hash` list, and store the aggregate as `sha256:<digest>`.
3. Evidence paths must be repo-relative source paths and should be the smallest practical set of load-bearing files that define the entity. Do not list every consumer or every textual mention.
4. Prefer high-signal definition files, mapper files, schema/interface files, or lifecycle contracts over broad package roots.
5. False-positive review prompts are acceptable. A fingerprint that changes only means the entity entry needs review; it does not automatically prove the prose is wrong.
6. Refresh a fingerprint only after inspecting the changed evidence paths and deciding whether the entity prose, relationships, naming drift, or source references need updates.
7. If an evidence path disappears, a fingerprint row has no matching inventory entry, or an inventory entry has no matching fingerprint row, review whether the entity was removed, renamed, or moved before deleting rows or replacing evidence paths.

## Entity Entry Rules

1. Use canonical entity names as section headings.
2. Prefer the real entity over a drifted UI or storage label.
3. Record current naming drift instead of creating duplicate entries for synonyms.
4. State the current canonical source of truth when it is knowable.
5. Use layer comparisons to show how the same entity appears across systems.

## Recommended Entry Criteria

Add an entity when at least one is true:

1. the same real thing appears under multiple names across layers
2. the entity is central to a current migration or refactor
3. developers regularly confuse it with a nearby entity
4. the entity is important in cross-repo tracing

Avoid entries that are:

1. pure vocabulary with no stable entity behind it
2. very local implementation details with no cross-layer relevance
3. duplicates of an existing entry with only wording differences

## Create Workflow

1. confirm the repo does not already have an entity catalog
2. gather current time via MCP time tool
3. read the C-08 resolved `system/sources.md`, then read the repo overview and the relevant materials from its `Domain Documentation` category needed to identify the first load-bearing entities; cite those actual materials, not the registry
4. choose deterministic evidence paths for each seeded entity before writing the catalog
5. compute and record `git-blob-set-v1` fingerprints for those entities
6. fill the template from `../templates/repo-entity-catalog-template.md`
7. seed the catalog with the most confusion-prone entities first
8. add a lightweight pointer from the repo overview when it improves discoverability

## Maintain Workflow

1. re-read the C-08 resolved `system/sources.md`, the current catalog, and the relevant source materials for the entity being updated; use the registry only to locate evidence
2. prefer updating an existing entry over creating a near-duplicate
3. when C-02 reports fingerprint drift, inspect the changed evidence paths and update the entity prose only if the entity meaning, identifiers, relationships, naming drift, source references, or cross-layer projections changed
4. when C-02 reports a missing fingerprint row, curate evidence paths and add the row before treating that entity as verified
5. when C-02 reports an orphaned fingerprint row or a missing evidence path, verify whether the entity was removed, renamed, or moved before deleting the row or replacing paths
6. refresh the stored fingerprint after review, even when the prose remains correct
7. update `lastUpdated` whenever the entity set, entity prose, evidence path set, or stored fingerprint meaningfully changes
8. append a newest-first `Update History` entry without deleting or rewriting earlier entries
9. keep the file selective; expand only when the extra entries materially improve understanding

## Review Heuristics

Before finalizing changes, check:

1. are the entities real and stable rather than just terms?
2. does each entry separate the entity from commonly confused neighbors?
3. is naming drift documented without becoming the new canonical label?
4. do the layer representations help a reviewer trace the same entity across systems?
5. does every inventory entry have one matching fingerprint row, and are there no leftover fingerprint rows for removed or renamed entries?
6. are fingerprint evidence paths small, deterministic, and load-bearing rather than broad or exhaustive?
