# Curator card formats and citation checks

Curators use this detail before creating or maintaining cards, overviews or the entity catalog, writing card history, or checking citations. It gives the layouts and evidence procedures; the role and curation operation retain scope, writer and handover authority.

## Choose the history disposition

Before ingesting a row, read the subject's earlier rows. For invariant history:

| Disposition | Meaning |
| --- | --- |
| `changed` | Changes the record's meaning and increments its revision. |
| `moved` | Re-anchors without changing meaning. |
| `deleted` | Removes entries or retires the invariant. |
| `extended` | Adds an entry without changing meaning. |
| `no_impact` | Preserves meaning and the entry set, including admission, origin or slug-only changes. |

Family and onboarding rows retain their own schemas. A family row uses its family identifier as `subject` and one of `changed`, `rerouted`, `assigned`, `retired` or `no_impact` as `disposition`.

Its `examined` field lists each examined member's identifier and revision in the candidate memory at the time of examination. The family record names members only by identifier, so a member's meaning change does not itself require a new family revision. If a member's revision changes after the row was written, examine the family again; the row is no longer current.

## Choose card and overview scope

A file card explains one concrete source file. Keep its purpose, important logic, conventions, contracts, limits and relationships understandable without opening an overview. Include documentation and cross-repository behavior when they affect that file.

Before creating a source-file card, use the [file-card layout](../../templates/file-card.md) for its title, governing-overview link, purpose, commentary and evidence. Use repository-relative source identities and relative links between cards, rather than workstation paths.

Keep durable explanations beside the source hierarchy they describe, under the supplied onboarding root:

```text
overview.md
<source folder>/overview.md
<source file>.md
```

An overview explains a cohesive area: shared models, workflows, invariants, boundaries, documentation dependencies, hotspots or a routing problem. Create one when that shared explanation helps; a directory's existence alone is insufficient. A file card still carries its own contracts.

For a cross-cutting concept, choose its primary local explanation and link participating areas to it. Record proposed and deferred routes, and any stale, moved or deleted coverage found in assigned maintenance, in the report.

Before creating the repository overview or a source-area overview, use the [repository-overview layout](../../templates/repo-overview.md) for the whole repository and the [route-overview layout](../../templates/route-local-overview.md) for a source area. Keep links to governed file cards and child overviews useful. The role's supplied reads identify the existing governing-overview chain; use that result for the relative backlink.

An overview starts with its title, an optional `## Governing Overview`, then its purpose under `## What This Repo Is`, `## What This Area Is` or `## Purpose`. Explain how the area works and its boundaries before the evidence. Put all numbered citation lines together at the end, with no later prose or heading. Put a change into the body section that owns its meaning.

## Keep current card content

Before routing or refreshing a card, read [card-sentence decisions](card-sentence-decisions.md) for current-truth decisions and repairs across its current carriers.

File cards and overviews hold current prose and numbered evidence lines. Keep metadata stamps, card-local change history and completed citation tables out of their bodies. The fixer turns its input table into `- <finding> [n]` lines and the corresponding references in the JavaScript Object Notation (JSON) sidecar.

The file card is `<source path>.md` and its sidecar is `<source path>.json`, relative to the onboarding root. An overview's pair has filenames overview.md and overview.json. A numbered marker requires its matching sidecar reference; a card with no references can have no sidecar. Let the fixer author references and the knowledge writer author `realizes` and `proves`, through the admitted surfaces.

An onboarding trace row uses subject `onboarding:<path>` or `onboarding:<route>/overview`. Use `updated` with a counted card change, or `no_impact` with a reason.

## Prepare citation input

Write the evidence as a table with these column names, leaving a blank line between separate tables:

```markdown
| Finding | Anchor | Source |
| --- | --- | --- |
| The journal is replayed on resume. | `replay_journal` | src/pkg/journal.py:10-40 |
| The module defines the adapter boundary. | n/a | src/pkg/adapter.py |
| The protocol defines the remote response. | n/a | https://docs.example.org/protocol/revision-2#response |
```

The examples show syntax; replace them with checked claims and actual evidence. `Finding` states what the cited evidence establishes. For same-repository claims, cite the source, test, configuration or artifact actually present in the paired code tree.

Use plain `path:start-end` in `Source` for a line range, rather than a Markdown link or a bare line number. In `Anchor`, write a backticked identifier, a backticked heading or a double-quoted literal the cited range contains. An identifier becomes a symbol anchor when the extractor finds it exactly once in a cited file. Other anchor text remains in the note, backed by the range; the Curator still checks its truth.

A bare repository-relative path names the whole file. For evidence outside the paired code tree, supply a bare, whitespace-free Uniform Resource Locator (URL) containing `://` in `Source`, such as a revision-specific document or source URL. The fixer accepts that as an external document target. A neighbouring repository's path, an absolute path or `../` does not name a file in the paired code tree. Preserve the external source's real identity instead of inventing a local anchor.

When both `Anchor` and `Source` are empty or “n/a”, the row becomes prose, not a reference. Use that form only when there is no evidence to cite; it does not establish a checked claim.

Check every changed claim, count and prose line reference against code. A prose line reference must lie inside its citation anchor. Passing structural checks and authentic anchors do not prove the prose true. Before completing the independent fact-check, follow [the curation fact-check procedure](../../operations/curation.md#normal-workflow) for its scope, evidence and completion.

## Run the named citation batch

Before calling a deciding or writing surface, read [serving-build binding](../knowledge/serving-build.md) for interpreter admission, package selection and stamp comparison. Pass every assigned changed card's onboarding-relative path as `documents` in one `citation_fix` call on `agents-remember-task`. Preview with a dry run, then repair the named cards in one call; do not make one call per card.

Read the authoring counts, unresolved targets, per-card refusals, unreadable sidecars and stale references in the full result. The operation works card by card: it keeps successful writes and continues after another card refuses. Keep the written cards and leave each refused card for review with its reason. A partial result is not a completed batch, and no batch-wide rollback is promised.

Correct the named input when a refusal identifies invalid JSON, an invalid sidecar, conflicting reference reuse or joined tables. Re-run the bounded repair through the same surface. Semantic or ambiguous items remain Curator decisions; retain the complete decision-item report in the workspace. Do not claim an unimplemented citation feature exists. The curation operation owns the subsequent complete checks and handover.

## Refresh or remove a reference

For a changed target, replace its old evidence line with one citation-table row whose `Finding` ends in the existing `[n]`. The fixer resolves the targets again and replaces that reference's note. Remove the old line rather than keeping both: another evidence line still using that number makes re-authoring refuse the card.

For a false note whose targets remain current, use `citation_note_revision`; its signature and digest precondition are in [related workflow tool signatures](../knowledge/commands.md#use-related-workflow-tools). This changes the note without re-resolving the targets.

To withdraw a reference, remove its evidence line and any prose citing its number, then run the named-card batch. Read `removedReferences`. The fixer removes references that the named card no longer cites; a tree-wide run does not prune them. Keep surviving numbers unchanged. New references use the next number above the sidecar's current maximum, so a removed highest number can be reused on a later run.

For a source rename, complete the entry moves through the writer before moving its card; the [handoff-list history rules](../../templates/curator-handoff-ingestion.md#history-rows) define those rows. Preserve valid current prose at the new mirrored path. Replace its numbered evidence lines with citation rows against the new path, without their old numbers, then run the fixer for the new card. Remove the old path-owning sidecar after its entries have moved, rather than moving that sidecar with its old `path`. Re-author reached references to the moved source at their existing numbers.

## Maintain the entity catalog

Before creating or maintaining the entity catalog, use the [entity-catalog layout](../../templates/repo-entity-catalog.md): for assigned entity-catalog work, maintain one selective catalog named entities.md at the onboarding root. It complements the overview. Include real, load-bearing entities; avoid a glossary of every noun or local implementation detail. Add an entity when it has different names across layers, matters to a current refactor, is repeatedly confused with a neighbour or is important in cross-repository tracing. Link the overview to the catalog when it improves discovery.

Before choosing domain evidence, read [governing documentation](governing-sources.md#read-governing-documentation) for discovery and source-authority rules.

Prefer an existing entry over a synonym. Use the canonical entity name, distinguish nearby entities and record current naming drift. Explain its real meaning, identifiers, ownership relationships, known canonical source and representations across layers. Add entries only when they materially improve understanding.

Keep the exact `## Entity Inventory` and `## Entity Fingerprints` headings. Each inventory name has exactly one matching fingerprint row, with no duplicate or orphan keys. Preserve the columns `Entity`, `Algorithm`, `Fingerprint` and `Evidence Paths`. Use backticked repository-relative evidence paths separated by semicolons; choose the smallest useful set of defining, mapping, schema or contract files, rather than every consumer.

Use `git-blob-set-v1`. For each sorted evidence path at the committed code revision being reviewed, read its Git blob hash. Concatenate each `path`, a null byte, its blob hash and a newline, in that order. Compute the 256-bit Secure Hash Algorithm (SHA-256) digest of those 8-bit Unicode Transformation Format (UTF-8) bytes. Store the result as `sha256:<digest>`.

A changed fingerprint calls for review, not an automatic prose change. Inspect the evidence and decide whether the entity's meaning, identifiers, relationships, naming or references changed before refreshing it. For a missing evidence path or unmatched row, establish whether the entity moved, was renamed or was removed before replacing paths or deleting rows. Keep the evidence and rows aligned even when the prose remains correct.

Set `lastUpdated` to the current Coordinated Universal Time (UTC) in `YYYY-MM-DDThh:mm` form after meaningful catalog changes. Use `draft` while the catalog is being developed and `active` when it is ready for routine use. These are catalog fields, not source-verification stamps.

Before handover, check that the entities are real, distinct and traceable across their relevant layers; each has exactly one supported fingerprint row; and the defining evidence is small and current. Catalog alignment checks shape and key correspondence, not the meaning of the prose or the truth of a fingerprint. Keep semantic review and the complete curation checks separate.

