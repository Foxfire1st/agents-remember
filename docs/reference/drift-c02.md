# Memory Quality Control

Two tools run the memory-quality checks: `context_packet` measures on request, and
`memory_quality_check` makes the Curator's checklist for a task. The checks show whether the
cards can be trusted before planning and whether a task's memory is clean enough to commit at
closeout. The Curator does the work around these checks as
the operation file [curation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/curation.md)
of the skill `l-01-agent-lifecycles` describes it.

## What It Checks

Each card has a JSON [sidecar](../concepts.md#sidecar). The numbered references of the sidecar
anchor the card's statements to places in code and test files: each reference carries an
[anchor](../concepts.md#anchor), the record of one place. Either tool observes each anchor in the
code repository and gives it one of two states:

| State | Meaning |
| --- | --- |
| `current` | The cited file has the recorded blob, or the cited lines or symbol still hold the recorded content. |
| `stale` | The content differs, the symbol does not resolve to exactly one place, or the path is absent. |

## Measuring Drift

A [drift check](../concepts.md#drift-check) is a call of `context_packet` with
`include_drift: true`: only then does the tool measure the anchors and validate the memory tree.
Without that argument the call measures nothing. The whole result, in the file that the answer
names in `log`, holds the block `drift`; that file is a JSON file beside the
[tool log](../concepts.md#tool-log), and any text editor opens it
(see [Tool answers](mcp-tools.md#tool-answers)):

| Field | Contents |
| --- | --- |
| `status` | `checked`, `notChecked` or `error`. |
| `sidecarCount`, `count`, `states` | The sidecars read, the anchors observed, and the number of anchors in each state. |
| `actionableCount`, `actionableSample` | The number of stale anchors, and the first of them. |
| `violationCount`, `violationSample` | The number of refusing validator findings other than stale anchors, and the first of them. |
| `reportOnlyCount` | The number of findings that are reported and refuse nothing. The stale anchors are part of this number. |
| `unreadableSidecars` | The sidecars the check could not read. |

When stale anchors exist, the answer of the tool carries one line in `actions` that gives their
number.

The drift check refuses nothing and repairs nothing. Every route that commits or lands memory,
and the full `memory_quality_check` run, refuse a memory tree that holds a stale code or test
anchor. The refusal names the rule `R198.1-reference-anchor`: a code or test reference anchor that does not name the blob of the paired code tree or does not resolve there to its recorded content. <!-- docs-check: identifier: the product prints this code -->

Two repairs exist. `citation_fix` records an anchor again when its cited content is unchanged and
only its position, or the Git blob of its file (the identity of the file's whole content), moved.
A Curator handles every other stale anchor: the Curator reads the source, then updates the card
or records why the card needs no change.

## The Curator's Checklist

The Curator runs the full contract-scoped `memory_quality_check` on a task. Each full run replaces
the two files of the checklist. The Markdown file is the checklist to read. The JSON file is the
attestation of the run: it names the code tree and the memory tree that the run judged and the
SHA-256 of the Markdown file. The answer names the two files in `reportPath` and
`attestationPath`.

```text
<enclosure>/reports/curator-memory-quality.md
<enclosure>/reports/curator-memory-quality.json
```

The summary of the checklist counts these classes, under the names that the checklist prints:

| Class | Meaning for readiness |
| --- | --- |
| Repairable memory findings | Must reach zero. |
| Missing onboarding | Source files eligible for a card that have none. Must reach zero. |
| Stale route indexes | Must reach zero. |
| Source-change reconciliation candidates | The cards, route overviews and rows of the entity catalog that the task's change reaches. The Curator inspects each one and records its disposition in the [coherence record](../concepts.md#coherence-record). |
| Real-commit provenance findings | Findings that only the closeout can clear, such as the missing provenance stamp of a card that the task created. The closeout owns them. |
| Noteworthy report-only findings | Findings for review. They refuse nothing. |

`curatorActionableCount` is the sum of the first three classes. Each open item of the
[knowledge worklist](knowledge-system.md#worklist-validator-and-gate), an item that no current
judgment row answers yet, counts as one repairable finding. The checklist also lists the invariants without a proof, as information.

The Curator repeats the run after each repair until `curatorActionableCount` is `0` and
`qualityChecklistStatus` reads `ready-for-closeout`. `checklistStatus` then reads
`coherence-required` while the coherence record is missing or stale. `closeoutReady`
becomes `true` only when the record that `curator_coherence` prepared and published also
validates.

The checklist is working state outside both Git worktrees. It is removed with the enclosure.

## Boundary

`context_packet` and `memory_quality_check` detect and report. They change no card. Writing and
maintaining a card follows the [Curator card procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md).
For a structural change of routes, follow the [guides](../guides/README.md) and the card
procedure. The [knowledge command reference](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/commands.md)
gives the invocation of each tool.

These procedures give no role and no tool extra authority. Approval gates, the prohibition on raw
Git writes, closeout ownership and the separate push approval stay in force.
