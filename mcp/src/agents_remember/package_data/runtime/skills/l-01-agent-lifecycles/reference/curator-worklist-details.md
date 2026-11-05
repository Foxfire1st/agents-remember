# Curator — worklist step detail

Read this file when answering the leaf's worklist, as the capsule says. Its writer refusal and
format passages are retained verbatim from `roles/curator.md` (L88 R125).

The writer refuses a row that
cannot govern the leaf's changed invariant or guarantee, and an itemless `still_rejected`,
`planned:` or `hunk:` row; a record first authored in the leaf needs no row unless an item
names it. A `raise` needs none. Wave and crossing owners need none.

One document can attach an entry with `invariant_id: "handoff:<entry id>"` to its own
invariant, regardless of list order; their statements must match. `replace_evidence: true`
replaces the evidence of a record this owner authored; otherwise evidence is appended.
A JavaScript or TypeScript test proof uses its plain `it`/`test` title, including enclosing
`describe` titles joined by ` > `; evidence quotes the title after the path. Skipped and todo
cases prove nothing; a parameterized or computed case is proved by its whole extent as a
`line_range`. The exact proof, `repoint` and raise-route forms are in the template's
§The file writer's sections.

For identical changes at uncovered paths, judge the representative once and name every grouped
source blob; card-bearing and covered paths keep their own items. Superseded decisions raise nothing. A deleted route or descendant rerouting can trigger reconsideration; a
`refresh_not_current` candidate stays open until its link is current or restored to the base.
A stale reconsideration refusal is cleared when you recompute the worklist and answer its
current item. Alternative updates move links by their exact unique option text; if it is gone
or ambiguous, restate the links. A `link_target` is cleared only by restating or removing
the broken link, never by a row. Equal generated copies need no unexplained row; a divergent copy
is judged with its named canonical file.

## Writer correction at list repair

A rerun that changes a target's `rationale` or `role` rewrites the
  `realizes` entry this leaf wrote from that entry; the entry keeps its ID.

## Worklist and gate response blocks

Read the persisted worklist and the full memory-quality result in its log together. The bounded
MCP answer names artifacts rather than inlining these blocks. On converted memory,
`knowledgeWorklist` summarizes the computed worklist; it is not a verdict that its items
are answered. A block is present only when that worklist or gate was computed; an absent block
is not a passing judgment or an empty result. Its fields mean:

| Field | Meaning |
| --- | --- |
| `state` | Whether the worklist computation is `complete` or `incomplete`. |
| `path` | The supplied persisted-worklist path, or `null` when no path is supplied. |
| `digest` | The computed worklist's content digest; read its document for the code/memory pairing. |
| `itemCount` | Total items in this worklist, including items already satisfied; not the open-item count. |
| `itemsByKind` | Counts of those items grouped by their `kind`. |
| `incomplete` | Inputs the computation could not read, with their reported details; an incomplete run is not measured absence. |

`knowledgeGate` is the compact gate judgment. It judges the exact paired candidate and the
current satisfying history rows, not whether a command merely exited zero. Its fields mean:

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
`knowledge-gate-refused`; its findings go to the Curator for repair, and it is not bypassed. A
changed candidate needs its own exact-tree check. The public command and tool reference is
[knowledge commands](knowledge-commands.md).
