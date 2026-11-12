# Execution-topology authoring

The execution topology of a sprint says how its masters run and which of them wait for others.
Two fields of the [task documents](../concepts.md#task-document) hold it: `executionNature` on a
master and `executionGraph` on a sprint. This page describes both fields and the operation that
writes the graph. [Concepts](../concepts.md) explains sprints, masters and tasks.

## The Two Fields

### `executionNature`

`executionNature` is a field of a master that a sprint commands (the sprint names it in its field
`orchestrates`). It has one of two values:

| Value | Meaning |
| --- | --- |
| `atomic` | The master has a branch of its own. Its tasks land on that branch, and the master then lands on the sprint's branch as a whole. |
| `organizational` | The master groups tasks. Its tasks land on the sprint's branch. |

A sprint document has no `executionNature`. A master that no sprint commands runs as an atomic
master when the field is absent.

### `executionGraph`

`executionGraph` is a field of a sprint. It holds two lists:

| Field | Content |
| --- | --- |
| `nodes` | At least one node. A node is a whole master, or a segment of a master: a subset of its tasks. |
| `edges` | Dependencies between nodes. The successor of an edge waits for its predecessor. |

A node of a whole master is stored as the reference of the master's task document:
`{"repository": "<repo-id>", "path": "<master-folder>/task.json"}`. The path is relative to the
[task folder](../concepts.md#task-folder) of the repository,
`<coordination-root>/tasks/<repo-id>/`. A segment is stored as
`{"kind": "segment", "ref": {...}, "leafIds": [...]}`. Here `leafIds` lists the ids of the tasks
in the segment; `leaf` is the product's word for a task of a master.

An edge holds `predecessor`, `successor`, `reason` and `judgmentId`. An endpoint is the reference
of a master that has one node. For a master with more than one segment, the endpoint is
`{"ref": {...}, "leafId": "<id>"}`: `ref` is the reference of the master in the form above, and
`leafId` names one task of the wanted segment.

Positions and waves are not stored. The product derives them from the nodes and edges at each
read.

## A Sprint Without A Graph

A sprint without `executionGraph` is valid. The product calls its mode `atomic-sequential`. No
tool answer has a field for the mode: whether the sprint document holds `executionGraph` decides
it. In this mode:

- Every master of the sprint runs as an atomic master, whatever its `executionNature` says.
- The sprint declares no dependency. Independent masters proceed at the same time, and work on
  one master never pauses another.

## A Sprint With A Graph

A sprint with a graph has the mode `dag`. Then:

- The declared `executionNature` of each master decides how it runs.
- Every master of the sprint needs an `executionNature`. A master without one is refused with
  `task-execution-topology-migration-required`. The refusal names the mutation `set_nature`.
- The nodes of the graph must cover exactly the masters that the sprint commands.
- A successor waits for its predecessors. The tool `closeout_queue` names an unfinished
  predecessor in the field `reasons` of a waiting member, as `predecessor-incomplete: <node>`. A
  task is a member only while its [closeout door](../concepts.md#closeout-door) waits, and no tool
  of the tool server creates a closeout door, so the queue has no member that names one.

Writing a graph is the only step that turns dependency-aware scheduling on: the waiting of a
successor for its predecessors.

## Read The Sprint First

No tool lists a proposal for a graph. Before you write one, read the sprint's `task.json`
(`<coordination-root>/tasks/<repo-id>/<sprint-folder>/task.json`) and the task documents of its
masters, and note:

- the masters that the sprint commands;
- the `executionNature` of each master, or that it is absent;
- whether the sprint has a graph;
- which masters must wait for which.

Advice: give a master the nature `atomic` when the code repository has a branch
`ar/<master-folder>`, named after the master's folder in the task folder, and `organizational`
otherwise. `git branch --list 'ar/*'` in the code repository lists these branches. Declare an
edge only for a real dependency. The order of files, names and statuses is no evidence of one.

## Write The Graph

The operation `author_execution_graph` of the tool `task_doc` applies one batch of mutations to
the graph of one sprint. The batch is applied as a whole or not at all.

```text
task_doc(
  repo_id="<repo-id>",
  task_name="<sprint-folder>",
  operation="author_execution_graph",
  dry_run=true,
  fields={
    "mutations": [
      {"op": "add_node", "ref": {"repository": "<repo-id>", "path": "<master-folder>/task.json"}},
      {"op": "set_nature", "ref": {"repository": "<repo-id>", "path": "<master-folder>/task.json"},
       "executionNature": "atomic", "judgmentId": "<judgment-id>"},
      {"op": "add_edge", "predecessor": {"repository": "<repo-id>", "path": "<first-master>/task.json"},
       "successor": {"repository": "<repo-id>", "path": "<second-master>/task.json"},
       "reason": "<why the successor waits>", "judgmentId": "<judgment-id>"}
    ]
  }
)
```

`repo_id` names the code repository, and `task_name` names the sprint's folder in its task
folder; the operation reads the `task.json` of that folder. `fields` holds the batch under
`mutations`. With `dry_run=true` the call writes nothing; see [The Answer](#the-answer). The
[MCP Tool Reference](mcp-tools.md) describes `task_doc` and its other operations.

| Mutation | Fields | Effect |
| --- | --- | --- |
| `add_node` | `ref`, `kind` (`master` or `segment`, default `master`), `leafIds`, `judgmentId` | Adds a master or a segment. |
| `remove_node` | `ref`, `leafId`, `judgmentId` | Removes a node. `leafId` names one task of the segment to remove. |
| `add_edge` | `predecessor`, `successor`, `reason`, `judgmentId` | Adds a dependency. |
| `remove_edge` | `predecessor`, `successor`, `judgmentId` | Removes a dependency. |
| `move_leaf` | `ref`, `leafId`, `toSegment`, `judgmentId` | Moves a task to another segment. `toSegment` names one task of the target segment. |
| `set_nature` | `ref`, `executionNature`, `judgmentId` | Sets the nature of a master. |

On a sprint without a graph, the first batch creates the graph, and the answer carries
`bootstrapped: true`. The batch must then add a node for every master of the sprint. It must also
hold a `set_nature` mutation for every master whose document has no `executionNature`.

### Judgment Ids

A mutation that expresses a decision needs a `judgmentId`: `add_edge`, `remove_edge`, `move_leaf`
and `set_nature` always, `add_node` for a segment, and `remove_node` for a segment. A batch
without a needed id is refused with `task-execution-graph-judgment-required`.

The id names a row of the section "Judgment Register (canonical judgment authority)" of the
sprint document. `task_doc` adds that section, as an empty table, when it creates a sprint
document. The table has nine columns, from "Judgment id" to "Supersedes"; the id stands in the
first cell of a row, and the operation `set_section` writes the rows. The cell "Author" of the
row must hold `architect` or `orchestrator`. The operation refuses a sprint without the section
(`task-execution-graph-judgment-register-missing`), an id without a row
(`task-execution-graph-judgment-unknown`) and a row of another author
(`task-execution-graph-judgment-author-refused`). The operation never creates an id.

### The Answer

| Field | Content |
| --- | --- |
| `state` | `would-author` for a dry run, `authored` for a write. |
| `bootstrapped` | `true` when the sprint had no graph before the call. |
| `sprintTaskDocumentRef` | The reference of the sprint document. |
| `appliedMutations` | The mutations of the batch. |
| `executionWaves` | The waves that follow from the graph: the nodes that may run at the same time, in order. |
| `leafPlacementFacts` | Facts about the placement of tasks in segments. |
| `numberingHints` | Hints where a task whose id ends in a lower number lies in a later wave than a task of the same master whose id ends in a higher number. A task id without a number at its end gives no hint. A hint refuses nothing. |
| `documents` | Every document that the batch changes. |
| `projectionEffects` | For each affected sprint, the effect of the write on its closeout queue. |

With `dry_run=true` the call writes nothing. Each entry of `documents` then holds the rendered
Markdown, a `diff` against the file on disk, and `wouldLose`: `true` when a non-blank line of the
file on disk is absent from the rendered text.

Three more operations of `task_doc` change the masters of a sprint. `attach_master` adds a
master, `detach_master` removes one and refuses while an edge touches its node, and
`retire_master` archives one. Each names the master in `fields` as `masterRef`, a reference in
the form above. The [tool reference](mcp-tools.md#task_doc-operations) describes their other
fields.

## Undo A Graph

- To correct a graph, write the opposite mutations with `author_execution_graph`: `remove_edge`
  undoes `add_edge` and `add_edge` undoes `remove_edge`; `remove_node` undoes `add_node` once no
  edge touches the node, and `add_node` undoes `remove_node`; `move_leaf` back to the earlier
  segment and `set_nature` with the earlier value undo themselves.
- To return to the state before a batch, restore a copy of the task folder that you made before
  the batch. Agents Remember keeps the task folder in no Git repository, so Git cannot give a
  batch back. Advice: copy `<coordination-root>/tasks/<repo-id>/` to a place outside the
  coordination root before you write a graph. Do not run Git on the coordination root as a whole:
  Agents Remember keeps no Git repository there, only below it, in the memory repositories and
  the worktrees of the enclosures.
- `create`, `replace` and `set_field` cannot remove the graph of a sprint. They refuse and name
  `author_execution_graph`.

Advice: change the nature of a master that already has a branch only after the Architect or the
Orchestrator has recorded the decision in the Judgment Register.
