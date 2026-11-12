# Workflows

This page follows work on a code repository from your request to a landed task. It names the
task documents, the steps a task passes, and the tools and settings behind each step.
[Roles and role agents](roles.md) describes the role agents that do the work.

## From a request to a landed task

1. **Request.** You state what you want, in a chat of your harness
   ([a chat without a role](concepts.md#chat-without-a-role)) or in the chat of the Architect. A
   question that changes no code is answered in the chat; it needs no task document and no
   worktree.
2. **Requirements.** The Architect writes requirement packets and asks for your approval.
3. **Task documents.** The Architect creates the task documents: a sprint, its masters, and the
   tasks of each master. Every code change runs under a task document.
4. **Intent review.** For each task the Architect asks you "go, or review first?" and records
   your answer.
5. **Start.** The Manager of the task's master starts the task's Worker, with `role_start` or,
   together with the Reviewer and the Curator, with `role_start_set`. An Orchestrator or an
   Architect can start a Worker too. The start creates the task's enclosure.
6. **Build, review and memory update.** The Worker builds, the Reviewer reviews, and the Curator
   updates the memory.
7. **Closeout.** The closeout commits the task's code and memory.
8. **Landing.** The landing brings the commits onto the master's branch.
9. **Finalizing.** Finalizing completes the task in its documents and removes its worktrees.

## Rules for every task

Every task keeps these rules:

1. Resolve the active context with `context_packet`; [MCP tools](reference/mcp-tools.md#server-and-context)
   describes the call.
2. Measure drift before planning against cards; `context_packet` reports it when the call sets
   `include_drift`.
3. Wait for your approval before the build. The Architect obtains your approval of the
   requirement packets before it creates the tasks, and your answer to the intent review before a
   task starts.
4. The Curator curates current cards, sidecars and knowledge records against approved code
   changes in the task's memory worktree, before publication.
5. The Worker and the Reviewer run the checks listed in `<memory-root>/system/tools.md` when the
   memory repository holds that file.

## Task documents

A task document is a JSON document with a rendered Markdown copy. A task document belongs to a
sprint, a master or a task:

| Task document | What it is |
| --- | --- |
| sprint | groups masters; the `kind` of its document is `master`, and its field `orchestrates` names the masters it commands |
| master | groups tasks; an atomic master has a branch of its own |
| [task](concepts.md#task) | the unit that one Worker builds; tools and fields call it a `leaf` or a `subTask` |

The documents lie under `<coordination-root>/tasks/<repository>/`. A sprint and a master each
have a folder that holds `task.json` and `task.md`. A task is the pair `<slug>.json` and
`<slug>.md` in the folder of its master.

Role agents change a task document with `task_doc`. The JSON file is the source. The product
renders the Markdown copy from it, so an edit of the copy is lost at the next render.
`task_doc` creates the kinds `master` and `subTask`. Every change, also a change of one file,
runs as a task under a master. The templates for a
[master](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/master-task.md)
and a [task](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/leaf-task.md)
show the content.

A task document holds the objective, the requirements, the design, the non-goals and the
acceptance of its work. It also holds the steps, the decisions, the open questions, the
references and the records of its reviews.

### States

| Field | Values |
| --- | --- |
| `status` of a task document | `planning`, `inProgress`, `Completed`, `abandoned` |
| `status` of a step | `pending`, `inProgress`, `blocked`, `done` |
| `intentReview.state` of a task | `pending`, `review-first`, `go` |

A master is complete when each of its task rows is `Completed` or `abandoned`. An abandoned row
blocks nothing: the progress of the master counts the other rows and names the abandoned ones
beside the figure.

The dashboard shows its own states for a task; see [Dashboard](dashboard.md).
[MCP tools](reference/mcp-tools.md) lists the operations of `task_doc`.

## Requirement packets and your approval

A requirement packet is the approved requirement text that a task is built against. It is one
Markdown file in the folder of the task's master, and the task document names it by a stable id
and a version. The Architect obtains your approval before it creates the tasks. A task document
shows the approved scope; it does not replace the requirement packet.

## The intent review before a task starts

A task that `task_doc` creates starts with an open intent review: its field `intentReview.state`
is `pending`. While the state is `pending` or `review-first`, every start of the task is refused
with `intent-review-open`. The refusal comes before anything is created: no enclosure, no
contract, no role agent.

The Architect asks you once for each task: "go, or review first?"

- **Go.** The Architect records `go` with `task_doc`. The task can start.
- **Review first.** The Architect records `review-first`. The task stays refused while you read
  its task document and its requirement packets. When you agree, you tell the Architect, and it
  records `go`.

You give the answer to the Architect in its chat; the dashboard has no control that records it. A
master has no intent review.

## The enclosure a start creates

The first start of a Worker, a Reviewer or a Curator on a task creates the task's enclosure. The
enclosure holds a code worktree and a memory worktree, each on its own work branch, and the
folders for scratch files, reports and copies of the task's documents.
[Roles and role agents](roles.md#where-a-role-agents-files-lie) shows the layout. The tool
`worktree_start` creates the same pair of worktrees without starting a role agent.

A [worktree contract](concepts.md#worktree-contract) is a file `series-contract.md`; a master has
one and a task has one. It lies at
`<coordination-root>/tasks/<repository>/<master-folder>/series-contract.md` for a master and at
`<coordination-root>/tasks/<repository>/<master-folder>/enclosures/<task-id>/series-contract.md`
for a task. The worktree tools take the path of this file as `contract_path`; they own the
lifecycle, the closeout, the landing and the cleanup of the worktrees.
[Worktrees And Closeout](reference/worktrees-c09.md) is their reference.

A start on a task whose enclosure is closed and cleaned up is refused with `reopen-required`.
`task_reopen` reopens a completed task under the same id: the task document returns to
`planning`, and the next start creates the enclosure anew from the tip of the master's branch.
The tool reopens only a task that is closed out, landed and cleaned up and whose recorded landing
lies on that branch; otherwise it answers `blocked` and names what blocks. With the contract of a
completed master, it reopens the master and restores the master's branches from the branch of the
sprint. A repeated call on a reopened task answers `reopened` again.

## Build, review and memory update

The Worker builds the change, the Reviewer reviews it, and the Curator updates the memory;
[Roles and role agents](roles.md#the-order-on-a-task) gives the order and who starts whom.

A Worker repairs a Reviewer's finding in the files of its own task directly, without a plan or a
go-ahead before the repair. Its report of the repaired [candidate](concepts.md#candidate) holds one
entry for each repaired finding. The entry says what was wrong, which files and lines changed, and
which test fails before the repair and passes after it. The Reviewer compares every finding with its entry. A Manager
settles a matter that an earlier ruling of the same kind covers by that ruling.

## Closeout

The closeout commits the code and the memory of a task. The instructions of the roles give it to a
Manager or an Orchestrator; a Worker, a Reviewer or a Curator never runs it. Where neither a
Manager nor an Orchestrator coordinates, the Architect runs it. It does so only with your approval
of that commit, or with an authority that you delegated for the whole master and that the task
document records.

The closeout has two calls:

1. `worktree_closeout_preview` judges the exact candidate, the code tree and the memory tree that
   the closeout would commit, and changes nothing. It answers `would-closeout` or a refusal:
   `knowledge-gate-refused`, `closeout-gate-blocked`, `blocked` or `memory-opt-out-refused`.
2. `worktree_closeout_apply` creates the commits. It requires the argument `intent_note`, a note
   that records the approval under which the commits are made, and answers `closed`, `blocked` or
   `closeout-gate-blocked`.

Closeout and commit approval are separate from implementation approval. The agent presents a dry-run
preview before any authorized closeout creates commits.

## Landing

The landing brings closed-out work onto the branch above it. `worktree_integrate` lands a
closed-out task on the branch of its master. Its `strategy` is `ff-only` (the default) or
`replay`. Both land the task only when the master's branch has gained no commit since the task's
closeout. When it has, `ff-only` refuses with `blocked-non-ff` and names the repair:
`worktree_sync` for the task, then a new closeout; `replay` answers
`integration-resolution-planning-required` and asks for your decision. The landing runs inside the
call. A call with `dry_run` shows the result first. When the landing is blocked, the next attempt
needs your decision.

Three more tools belong to the landing:

- `worktree_checkpoint_landing` publishes the finished tasks of a master that is not finished.
- `worktree_operation_control` controls one run of a direct landing, the only run that leaves a
  record in the [operation journal](concepts.md#operation-journal): it recovers or cancels an
  interrupted run, or retires a completed run so that it stays only as a record. A closeout and
  `worktree_integrate` run inside their call, and for them the tool answers
  `lifecycle-operation-missing`.
- `closeout_queue` shows the tasks of a sprint whose [closeout door](concepts.md#closeout-door)
  waits. No tool of the tool server creates a closeout door, so the queue shows no task.

### The landing workflow of a repository

The setting `landing.workflow` decides how a landing reaches the repository's branch. The key
stands at the root of the memory settings file, `<memory-root>/system/settings.json`:

```json
{
  "landing": { "workflow": "pull-request" }
}
```

| Value | Meaning |
| --- | --- |
| `local` | The landing moves the master's branch in the local repository. A file without the key means `local`. |
| `pull-request` | Each landing of a task is published as a merged pull request. The product admits exactly that merge on the master's branch, and `worktree_record_landing` records a commit that the remote already merged. |

The product refuses any other value by name.

## Finalizing

`lifecycle_finalize_task` completes a landed task. It refuses while a step of the task document is
not `done`. Then it completes the task's row in the master document and removes the worktrees of
the task. The product archives the role agents of the task on the host.

`worktree_cleanup` removes the worktrees, the merged branches and the enclosure folder of
landed work. `worktree_abandon` gives a task up without a landing.

The result of each of the three calls reports the archiving under `agentArchive`, in the lists
`archived`, `alreadyArchived`, `gone`, `owed` and `leftAlone`. The dashboard's server repeats an
owed archive until the host confirms it.

## Direct execution

Direct execution is work on a task without an enclosure. It needs a grant for that one task:

- You give the grant with the switch "Direct execution" on the page of a task that is still in
  planning; see [Dashboard](dashboard.md).
- An Architect gives it with `direct_execution_grant`, whose `action` is `give`, `revoke` or
  `reclaim`.

A start of a role agent on a task with a grant creates no enclosure, and `worktree_start` refuses
such a task. The role agent works in the code worktree and the memory worktree of the master: the
checkouts of the master's branch that the master's worktree contract names. One task with a grant
is in work at a time in a master; a start for a second one is refused. Giving and revoking a grant
is refused once the task has started: once its status is no longer `planning`, a Worker, a
Reviewer or a Curator is registered on it, a code review is recorded on it, or one of its steps is
no longer `pending`.

`direct_landing` lands the work of such a task. It answers `landed`, `would-land` or `refused`.

## Task publication

A memory repository may hold the file `<memory-root>/system/task-publication.md`. It is an
instruction file: it tells the role agents how the repository publishes its tasks, for example as
issues and pull requests on GitHub. The Architect and the Manager follow it. No tool of the
product creates an issue. Without the file, the role agents publish nothing.

The [example file](https://github.com/Foxfire1st/agents-remember/blob/main/system/defaults/examples/memory-repo/task-publication.md)
shows the content. Pushing, pull requests and releases stay with you unless you delegate them.

## Knowledge in a task

The Worker reports changed behavior and hands off the exact candidate; the Curator
reconciles current cards, references, records and [judgment rows](concepts.md#judgment-row) in the
admitted memory worktree. A full contract-scoped `memory_quality_check` computes the
change-to-knowledge worklist and the findings of the knowledge gate. `knowledge_integrity_check` runs
the validator and returns that task's latest persisted worklist; it does not
replace a fresh memory-quality check.

The worklist binds the base trees, the code and the memory before the change, to the candidate
trees, the code and the memory that the closeout would commit.
The closeout preview and publication routes judge the exact candidate trees,
recomputing the knowledge gate rather than trusting a saved worklist. A
`knowledge-gate-refused` result names repair findings; the Curator repairs memory
and reruns checks. The knowledge gate has no waiver. The validated
[coherence record](concepts.md#coherence-record) remains a separate readiness requirement.

For file formats, commands and the read-only check of a
[hand-off list](concepts.md#hand-off-list) (`handoff-check`), see
[Knowledge system](reference/knowledge-system.md). Implementation
approval does not authorize a Git commit or landing.
