# Curator worklist rules

Curators use this file before answering the persisted leaf worklist. It links the writer's row and evidence procedures and explains the returned worklist and gate fields.

## Answer rows and repair the list

Before answering a history row or grouping changed paths, read [the writer's input sections](../../templates/curator-handoff-ingestion.md#the-writers-input-sections) for row admission, owners exempt from a leaf worklist, generated copies and broken links.

Before answering a reconsideration candidate or updating alternatives, read [Reconsidering a rejected alternative](../../templates/curator-handoff-ingestion.md#reconsidering-a-rejected-alternative) for raising to the developer, repointing code, current refreshes and exact-option link updates.

Before attaching a document-local invariant, replacing evidence or authoring a test proof, read [Invariant entries](../../templates/curator-handoff-ingestion.md#invariant-entries) for list-order independence, matching statements, owner-only replacement, test titles, complete case extents and excluded cases.

Before correcting a target's rationale or role in a rerun, read [Realization rationale and role](../../templates/curator-handoff-list.md#realization-rationale-and-role-one-authored-explanation-per-target) for replacement of this leaf's realization entry while retaining its identifier.

## Worklist and gate response blocks

Read the persisted worklist and the full memory-quality result in its log together. The bounded Model Context Protocol (MCP) answer names artifacts rather than inlining these blocks. On converted memory, `knowledgeWorklist` summarizes the computed worklist; it is not a verdict that its items are answered. A block is present only when that worklist or gate was computed; an absent block is not a passing judgment or an empty result. Its fields mean:

| Field | Meaning |
| --- | --- |
| `state` | Whether the worklist computation is `complete` or `incomplete`. |
| `path` | The supplied persisted-worklist path, or `null` when no path is supplied. |
| `digest` | The computed worklist's content digest; read its document for the code/memory pairing. |
| `itemCount` | Total items in this worklist, including items already satisfied; not the open-item count. |
| `itemsByKind` | Counts of those items grouped by their `kind`. |
| `incomplete` | Inputs the computation could not read, with their reported details; an incomplete run is not measured absence. |

`knowledgeGate` is the compact gate judgment. It judges the exact paired candidate and the current satisfying history rows, not whether a command merely exited zero. Its fields mean:

| Field | Meaning |
| --- | --- |
| `state` | `pass` when there are no blocking findings; otherwise `refused`. |
| `owner` | The owner identifier supplied to this gate judgment, or `null`. |
| `worklistState` | The state of the worklist the gate judged. |
| `worklistDigest` | That worklist's digest. |
| `worklistPath` | Its supplied persisted path, or `null`. |
| `findingCount` | Total blocking gate findings. |
| `openItemCount` | Findings for worklist items without a current satisfying row. |
| `incompleteCount` | Findings for worklist inputs that could not be read. |
| `violationCount` | Findings whose codes belong to the knowledge validator. |
| `reportOnlyCount` | Nonblocking report-only observations, separate from `findingCount`. |

Do not add these subtotals to infer a new verdict or count report-only observations as blocking
findings. Use the returned judgment and read each actual finding. A closeout gate refusal is
`knowledge-gate-refused`. A
changed candidate needs its own exact-tree check. Before using the public gate commands or tools, read [related workflow tools](../knowledge/commands.md#use-related-workflow-tools) for check and closeout effects, bindings and result meanings.
