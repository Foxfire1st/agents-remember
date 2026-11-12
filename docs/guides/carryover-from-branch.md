# Carry Memory From a Branch

Use this guide when the memory of a source branch, a branch of the code repository whose work
has a memory of its own, is richer than the memory of the
[official line](../concepts.md#official-line), and the code of that branch has landed.
[Carryover](../concepts.md#carryover) copies cards and knowledge records from the source memory
into the memory worktree of one open task. It is **not a Git merge**. It writes only into that
memory worktree and moves no branch.

The tools and their answers call a task a `leaf`.

## Plan, then apply

Ask the Architect for a task for the carryover, as
[Start your first task](../getting-started.md#start-your-first-task) describes. Its enclosure
starts at the newest commits of the official line, in the code and in the memory, so it holds the
landed code, and its code worktree stays clean at the tip of the official line.

```text
memory_carryover_plan(repo_id="<repo-id>", contract_path="<contract of the open task>",
  source_memory="<source memory checkout>", official_code_ref="<official ref>",
  source_code_ref="<source ref>", old_base="<base ref or commit>")
memory_carryover_apply(repo_id="<repo-id>", contract_path="<contract of the open task>",
  source_memory="<source memory checkout>", official_code_ref="<official ref>",
  source_code_ref="<source ref>", old_base="<base ref or commit>",
  intent_note="<the reason for this carryover>")
```

| Argument | Tool | Meaning |
| --- | --- | --- |
| `repo_id` | both | Required. The id of the code repository in the settings file. |
| `contract_path` | both | Required. The path of the [worktree contract](../concepts.md#worktree-contract) of the open task that receives the memory. |
| `source_memory` | both | Required. The folder of the memory checkout that holds the source memory. |
| `official_code_ref` | both | Required. The ref of the code branch of the official line. |
| `source_code_ref` | both | Required. The ref of the source code branch. |
| `old_base` | both | Required. The ref or commit from which the source branch and the official line both descend. |
| `replace_existing` | both | `true` lets a candidate replace a card of the task's memory that differs. The default is `false`. |
| `intent_note` | apply | Required. Your reason for the carryover. |
| `include_review_required` | apply | The `source_path` of each candidate with the decision `review-required` that you include after your review. |

The plan changes nothing and files no report. It lists the units of the next section as
candidates, in the field `candidates`, each with its decision. Review its candidates, then approve
the apply.
`replace_existing` and `include_review_required` do not override an uncommitted edit in the
task's memory or a knowledge record of the task's memory that differs.

## Carried units

A unit is a Markdown file together with its JSON sidecar. The two always travel together. The
kinds of unit are file cards, route overviews, memory-only documents and the entity catalog. A
file card is here the card of a source file that changed on the source branch. A route overview
counts as one when its route holds a path that changed on both branches. A memory-only document
is every other card of the source memory. The plan lists each unit that it found as one entry of
`candidates`; this page calls such an entry a candidate. It leaves out a memory-only document or
an entity catalog that equals the task's memory.

The plan names each unit by a key: the source path for a file card, the path of the route for a
[route overview](../concepts.md#route-overview) (`.` for the root), the path or route that the
sidecar of a memory-only document names, and the literal `entity-catalog` for the
[entity catalog](../concepts.md#entity-catalog). An overview or a catalog that differs in the
task's memory needs a review of the whole unit.

The plan classifies each file card by the evidence that its code landed:

| Code evidence | Meaning | Decision |
| --- | --- | --- |
| `exact-landed-commit` | Every source commit that touches the path is an ancestor of the official line. | `auto-carry` |
| `patch-id-match` | The patch from the old base to the source equals the patch from the old base to the official line. | `auto-carry` |
| `final-content-match` | The bytes of the file are equal on the source and on the official line. | `auto-carry` |
| `same-path-changed` | Both lines changed the path, and no evidence shows equal code. | `review-required` |
| `not-landed` | The path changed on the source and did not land. | `reject` |

A route overview carries the evidence `route-covers-landed-paths` and the entity catalog the
evidence `entity-catalog-differs`. A route overview is `auto-carry` when it equals the task's
memory and `review-required` otherwise; an entity catalog in the plan differs from the task's
memory and is `review-required`. A memory-only document carries the evidence that the last
paragraph of this section names.

The plan compares every [anchor](../concepts.md#anchor) of a sidecar with the **official code**,
the code at `official_code_ref`. A unit whose anchors are all current keeps the decision of its
code evidence, and the apply records moved blobs and lines again. A stale or unverifiable anchor
makes the unit `review-required`; the plan names the entries, the references and the failures. An
anchor that could not be read never counts as current.

A memory-only document is carried automatically only with the evidence
`memory-only-anchors-current`. It needs three conditions: the document has anchors, every anchor
is current, and the task's memory has changed neither file of the unit since the merge base of
the two memories. A document without anchors, or with an anchor that is not current, has the
evidence `unverifiable`; a document that the task's memory changed, or that has no resolvable
merge base, has `target-memory-moved`. Both need a review, as does a document whose files cannot
be read; it keeps the evidence `memory-only`.

## Records, validation and publication

The plan names the knowledge records that the carried units need and that the task's memory
lacks: the targets of `realizes`, of `proves` and of record references, and the records those
link to in turn. The apply carries the JSON and the paired Markdown of each such record.

The apply never overwrites a record of the task's memory that differs. The candidate shows
`record-differs` and needs a review, and the Curator changes the record through the tool
`knowledge_write`. Judgment rows of the source are reading aids only: **history files never
travel**.

The apply writes the selected units and the missing records, and it generates the route indexes
again. It then validates the exact resulting tree of the memory worktree against the memory base
of the task, paired with the official code, with the [validator](../concepts.md#validator) as the
tool `knowledge_write` uses it: a finding of a rule that refuses a commit but not a write is only
reported. When the validation refuses, the answer lists the violations, and the apply restores the
memory worktree byte for byte, with the edits that were there before.

The apply commits nothing.

A repeated apply skips files that are equal already. Other uncommitted bytes in the task's memory
are reported as `target-edited` and are never overwritten. After an interruption, a repeated
apply skips the writes that are complete and validates the whole tree. Its refusal names the
carried paths that differ from the `HEAD` of the memory worktree.

| State | Meaning |
| --- | --- |
| `would-carryover` | The answer of the plan. Nothing changed. |
| `carried-into-worktree` | The apply wrote validated, uncommitted files. The answer lists the written paths and a summary of the computed worklist. |
| `nothing-to-carryover` | No unit and no record needs writing. The validation of the whole tree still runs. |
| `refused` | The answer carries `ok: false`. The field `reason` names `target-edited`, `knowledge-validator-refused` or `carryover-failed`, or the field `code` names `legacy-format` for a memory without the layout marker. The memory worktree is as it was before the call. |

The Curator of the task answers the worklist with its own
[judgment rows](../concepts.md#judgment-row). The closeout of the task commits the tree that the
knowledge gate judged, once, with the `Code-Commit:` trailer of the task's code commit, which is
the tip of the official line. The task then lands like any task.

The tool `worktree_status` reports the phase `carryover-pending` for the task whose code landed
without its memory, the task whose memory this guide carries. While that phase holds, cleanup and
finalizing of that task are refused until the carried memory has reached its line.

See [Adopt Existing Memory](adopt-existing-memory.md) for the baseline. The
[knowledge command and tool reference](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/commands.md)
describes `memory_quality_check` and `curator_coherence`, which the Curator runs before the
closeout.
