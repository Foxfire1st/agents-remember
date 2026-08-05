---
name: c-09-git-worktree-manager
description: "Create, attach to, report on, integrate, finalize, and clean up Agents Remember worktree-backed tasks while preserving delegated/human approval gates and external-memory compatibility."
---

# c-09-git-worktree-manager Git Worktree Manager

Use this skill when a task should run through an explicit code/memory worktree wrapper.

The `c-09-git-worktree-manager` skill wraps the existing light-task or external workflow (a build that fits one session still rides a THIN `w-02-light-task-workflow` doc — chat is never a build route, per the `l-01-agent-lifecycles` invariant). It owns Git worktree state, series contracts, leaf enclosures, external-memory compatibility checks, integration, lifecycle finalization, and cleanup. It does not replace the workflow that performs the actual implementation.

For closeout, use the `c-12-closeout` skill. The `c-09-git-worktree-manager` skill only supplies the worktree-specific
contract path and integration/finalization follow-up rules.

## MCP Tools

Use the Agents Remember MCP worktree tools as the normal installed runtime
entry point:

> **Preview first.** `worktree_start`, `worktree_integrate`, `worktree_cleanup`,
> and `lifecycle_finalize_task` now **apply by default**. Run each once with `dry_run=true`
> to inspect the plan, confirm, then run the real apply (omit `dry_run`).

```text
worktree_start(repo_id="<repo-id>", task_name="<task>", worktree_name="<leaf-worktree>", leaf_id="<leaf-id>", workflow_kind="light-task")
worktree_attach(repo_id="<repo-id>", task_name="<task>", leaf_id="<leaf-id>")
worktree_status(repo_id="<repo-id>", task_name="<task>", leaf_id="<leaf-id>")
worktree_sync(contract_path="<enclosure series-contract.md>")
worktree_closeout_preview(contract_path="<enclosure series-contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<enclosure series-contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_integrate(contract_path="<enclosure series-contract.md>", strategy="ff-only")
worktree_cleanup(contract_path="<enclosure series-contract.md>")
task_reopen(contract_path="<enclosure series-contract.md>", dry_run=true)
lifecycle_finalize_task(contract_path="<enclosure series-contract.md>", task_doc_path="<task.json>", master_doc_path="<parent task.json>", subtask_number="<N>", dry_run=true)
```

Callers identify repositories by configured MCP `repo_id`. The MCP server owns
workspace root, coordination root, provider setup settings, and path containment.
The skill tree is instruction-only; installed and development workflows use the
MCP/package route.

## Lifecycle Resume And Promotion

The worktree is the **commitment boundary** for the observable lifecycle (design
`docs/design/observable-lifecycle.md` §1.5). The worktree verbs carry the
lifecycle, with identity kept server-side:

- `worktree_start` **promotes** the session's current (fleeting) lifecycle to
  **persistent** and writes its id into the contract's `lifecycle:` block. The
  leaf enclosure (`tasks/<repo>/<task>/enclosures/<leaf-id>/series-contract.md`)
  is the durable anchor that outlives worktree cleanup. One leaf enclosure = one
  lifecycle. A master task's root `series-contract.md` is a separate integration
  contract and is not itself worktree material.
- `worktree_attach` **resumes** that lifecycle: it reads the id from the contract
  and re-adopts it, so a new chat session continues the same observable lifecycle.
  The model never passes an id.
- Attaching while still holding an unsaved **fleeting** lifecycle hits the **save
  gate** — pass `on_unsaved="save"` (promote the fleeting work to a landing zone)
  or `"discard"` (abandon it). Unsaved work is never dropped silently.

## Pre-Worktree Intake

The `c-09-git-worktree-manager` skill starts after the normal task intake and onboarding gate, not before them.

The intended order is:

1. run the `c-08-ar-coordination-context-resolver` skill for the target repository
2. run the `c-02-memory-quality-control` skill's task-start drift check and follow the existing AGENTS Gate 3/4 choice point
3. when onboarding is refreshed, commit the memory content and ledger before starting any worktree
4. decide whether the work is a `w-02-light-task-workflow` light task — possibly a THIN one for single-session work; chat never builds — a master + light sub-task series, or external workflow
5. read the repository's `system/git-workflow.md` and identify the parent branch
   edge. For a standalone task, the leaf worktree branches from the approved
   source branch. For a master series, the master owns a root `series-contract.md`
   and a pushable integration branch first; each leaf enclosure branches from that
   integration branch and integrates back into it. For a nested master, create
   the child integration branch from the parent integration branch.
6. choose or review the task slug and workflow variables
7. establish the applicable worktree-start authority: for standalone/new work, hand off the
   **Worktree Intent Gate** for explicit developer approval; for subordinate leaves/edges inside an
   accepted orchestrated series, record the accepted-series authority and continue without a new
   developer hand-off
8. create the durable task wrapper when one is needed
9. request the `worktree_start` MCP tool only after the task identity is stable, the
   correct landable `source_branch` is selected, external memory is clean, and
   the applicable authority has been recorded

The Worktree Intent Gate must name:

1. target repo and build mode
2. discovered branch policy from `system/git-workflow.md`
3. proposed protected/source branch and, for a master series, the integration branch
4. proposed leaf work branch, worktree name, and `leaf_id`
5. memory mode and memory branch behavior
6. intended landing path from closeout through integration, PR/merge when needed,
   memory carryover, and lifecycle finalization
7. material risks, unusual choices, or unresolved branch-policy questions

If the repo is PR-gated, the intent packet must make the protection boundary
visible: leaf work branches integrate into the pushable integration/source branch
recorded by their enclosure; protected targets are reached later through the
repo's PR flow.

For developer-gated starts, run the applicable dry-run/preflight first, then **hand off**: call
`lifecycle_turn_end_notification(summary={…the intent packet + the approve/revise ask…})` as the **last
tool call**, then deliver the intent packet as your final prose and **STOP / end your turn**. The
notification sets the `awaiting-developer` lifecycle state, surfaces a
dashboard attention item, and returns immediately (no wait, no inbox). The developer approves from the
dashboard or in the leaf's attached chat; the **first AR tool call of your next turn** auto-resumes the
lifecycle (`running`), clears the attention item, and proceeds to `worktree_start` — you send no explicit
`lifecycle_resume`.

For subordinate orchestrated-series starts, do the same dry-run/preflight, record the accepted
planner/series authority in the task decision log or worktree intent note, and continue. Do not add
a developer stop for every leaf worktree.

Parked fallback: the block-and-wait `lifecycle_gate` junction (plus the operator inbox and dashboard
GateResponder) still works if you deliberately raise it for a durable, developer-attributed,
mutation-blocking record:

```text
lifecycle_gate(
  kind="worktree-intent",
  ask={"kind": "decision", "prompt": "<the intent ask>", "options": ["approve", "revise"]},
  packet={ ...the intent packet facts... },
)
```

It is no longer the active path and nothing routes toward it. On that path the agent's own `gate_decide`
is model-attributed and never counts as approval; once the developer has approved, the agent **always**
sends `lifecycle_resume()` to clear the block, then calls `worktree_start`. A chat "approved" does not
propagate itself.

For `w-02-light-task-workflow` light tasks, the durable artifact shape is `<task-root>/<task-slug>/task.md`. A standalone worktree-backed task stores its leaf enclosure at `<task-folder>/enclosures/<leaf-id>/series-contract.md`. A master series additionally stores its integration contract at `<master-task-folder>/series-contract.md`.

## Start / Attach / Status

The `worktree_start` MCP tool resolves `c-08-ar-coordination-context-resolver` context, creates or loads the leaf `series-contract.md`, prepares the code worktree first, and then prepares external-memory state when enabled. If the task root is a master and no root series contract exists yet, start first creates the master integration branch and root `series-contract.md`, then starts the leaf from that integration branch. External-memory start refuses to continue when the source memory repo has uncommitted changes; refreshed onboarding and the ledger must be committed first so the new worktree starts from an auditable memory baseline.

Start runs a **stale-base preflight** (GitHub #54) before any worktree exists:
when the code or memory source branch is `behind` or `diverged` from its remote
tracking branch, start blocks with `choose_stale_base_recovery` — a stale base
produces wrong code and silently converts the provider seed fast-path into a
multi-minute reindex. Recoveries: re-run with
`stale_base_choice="fast-forward"` (the tool fast-forwards the stale local
branches, then starts) or `stale_base_choice="proceed-stale"` (explicit
override). Offline (`unknown`) and `no-upstream` states never block. A missing
external-memory source branch is no longer a manual step: start auto-creates it
at the official memory tip using the code source branch name as template
(reported as `memorySourceBranch` in the result).

The recorded leaf `source_branch` is not merely the base branch. It is the branch
that `worktree_integrate` will later fast-forward or replay into. In a master
series this is the master integration branch; in a nested master it is the
nearest parent integration branch. Protected targets are handled after the
integration branch lands.

When external memory is enabled, the `c-09-git-worktree-manager` skill validates the memory repo and `memory.md` ledger before allowing memory to be used as trusted context. Missing external memory is not a `c-09-git-worktree-manager` bootstrap path; run the `c-00-initialize-memory-repo` skill first. If no compatible memory state exists, the `c-09-git-worktree-manager` skill stops and reports the allowed human choices:

1. `reconciliation`
2. `disabled-memory`
3. `custom`

The common trigger is starting a worktree off a **freshly-merged gated branch**: the PR merge commit
lands on top of the verified tip with a new SHA the ledger has not mapped. Running
`c-11-memory-carryover-from-branch` against the merged spear *after* the PR merges maps that merge
commit automatically — even when nothing else needs carrying — so the next worktree starts cleanly
without needing `reconciliation`.

`worktree_attach` and `worktree_status` read the existing contract and report recoverable state without mutating Git. `worktree_status` includes a lifecycle phase, dirty worktree flags, a summary, typed next hints such as `nextOperation`, `nextTool`, and `nextArgs`, and a fetch-free `freshness` block comparing the contract's recorded base commits against the current local source branch tips — when behind, it carries a `syncHint` recommending `worktree_sync`.

## Mid-Task Sync

A live worktree's base pair decays while parallel cycles land (a sibling leaf may
advance the integration/source branch; carryover may advance official memory). `worktree_sync`
(GitHub #54) pulls the moved official line in **atomically**: it fetches the
source upstreams, requires the new code tip to be ledger-mapped at the official
memory tip (a mid-cycle official line blocks with guidance to run
`c-11-memory-carryover-from-branch` first), merges the source branch into the
code work branch (conflicts abort cleanly), fast-forwards the memory work
branch, and advances the contract's recorded base pair with a `sync_log` entry.
Preview with `dry_run=true` first.

**Sync early — before memories are written.** With parked memory the sync is a
pure fast-forward: the other cycle's sidecars and ledger rows end up beneath
this task's future memory work, closeout appends on top, and end-of-series
integration stays `ff-only` with no carryover reconciliation. If the memory
work branch already has local commits and official memory moved, sync blocks
with `memory_sync_choice` recoveries: `merge-memory` (merge attempted; ledger
conflicts abort — the ledger is never auto-merged) or `skip-memory` (memory
deferred to end-of-task carryover; only the code base advances).

## Worktree Closeout

Use the `c-12-closeout` skill for worktree closeout. The `c-12-closeout` skill owns the approval gate,
missing-onboarding check, code commit, onboarding and entity refresh, memory
quality gate, memory content commit, ledger update, and ledger commit.

For worktree-backed tasks, pass the leaf enclosure `series-contract.md` to
`worktree_closeout_preview` / `worktree_closeout_apply`. The apply step records
the applicable closeout authority in the contract and updates the contract closeout state after the
code, memory, and ledger commits are created.

Worktree closeout stops if the recorded code or external-memory source branch
moved since task start.

## Integration

Integration runs only after closeout completed and is authority-gated by context. It lands the
closed task branches back onto the recorded source branches and records the landed commits
separately from the closeout commits. In an accepted orchestrated run, dependency-ordered
leaf→master and master→super integrations ride the series' standing approval (the developer's
portfolio-gate approval recorded in the planner master) — the developer hand-off concentrates at
the super PR/carry-over gate per the `l-01-agent-lifecycles` loop/orchestrator doctrine. A raised
durable `integration-approval` gate still awaits the developer.

On an orchestrated master's exit (master → super integration) the integrate step additionally enforces the delegated `master-handover-approval` seam: an undecided or policy-invalid handover gate addressed to the master (by `enclosure` = master task name) returns `handover-gate-blocked` instead of landing — decide the gate per the `l-01-agent-lifecycles` seam doctrine, then rerun. When no gate addresses the integrating master but open `master-handover-approval` gates exist elsewhere, integrate still proceeds and its result carries a `handover_gate_warning` naming them — treat it as a spelling check on the raised gate's `enclosure`.

Run `worktree_integrate(..., dry_run=true)` first. For subordinate accepted-series integrations,
record the standing series authority and then run the real integration without a developer stop.
For developer-gated integrations, **hand off**: call
`lifecycle_turn_end_notification(summary={…the integration plan…})` as the **last tool call**, then
deliver the integration preview as your final prose and **STOP**.
The developer approves on the dashboard or in chat; the first AR tool call of your next turn auto-resumes
and runs `worktree_integrate` — you send no explicit `lifecycle_resume`. Parked fallback: the
block-and-wait `lifecycle_gate(kind="integration-approval", ask=…, packet={ ...the integration plan... })`
+ `lifecycle_resume` still works if deliberately raised; on that path the agent never self-approves — a
model-attributed decision is not a developer approval.

Before previewing integration, check out the recorded code and memory `source_branch` in their source repositories; `worktree_integrate` requires those active checkouts even for `dry_run=true`.

Integration always lands into the recorded `source_branch`. It does not open a
PR and it does not discover protected-branch policy on its own; that policy must
be reflected in the branch choice made before `worktree_start`.

Strategies:

1. `ff-only`: require current code and memory source branches to be ancestors of the closeout commits, then fast-forward both source branches.
2. `replay`: when source branches moved because parallel work landed first, replay the code task commit onto current code source, replay only the memory content commit onto current memory source, regenerate `memory.md` for the final landed code and memory content commits, then fast-forward both source branches.

Conflict rule: if code replay or memory-content replay conflicts, stop before moving source branches. The agent must discuss the resolution with the developer and decide what is true before continuing. Do not replay an old ledger commit over current memory main; always regenerate the ledger row after memory content has been mediated.

After successful integration, complete any repo-specific landing tail first: push/PR/merge for PR-gated code, pull the protected target back locally, and carry memory forward until the official memory branch maps the landed code commit. Then use `lifecycle_finalize_task` for the terminal edge.

## Lifecycle Finalization And Cleanup

Lifecycle finalization runs only after closeout, integration, and any PR/carryover tail are
complete, and its approval authority follows the same series boundary. For subordinate
accepted-series leaf/master edges, the owning manager/orchestrator may finalize and clean up after
the dry-run proves the landed edge. For final super→main cleanup, standalone work, or a deliberately
raised `cleanup-approval` gate, stop for developer approval. Finalization proves the current
parent-child branch edge, then removes the recorded code and memory worktrees, deletes local task
branches only when Git can prove they are merged, removes empty worktree group folders when safe,
records `cleanup: completed` in the contract, and updates task documents.

Run `lifecycle_finalize_task(..., dry_run=true)` first. For subordinate accepted-series cleanup,
record the standing authority and run the real finalizer. For developer-gated cleanup, **hand off**: call
`lifecycle_turn_end_notification(summary={…what cleanup removes…})` as the **last tool call**, then relay
the landed-commit proof, cleanup plan, and task-document updates as your final prose and **STOP**. The developer approves
on the dashboard or in chat; the first AR tool call of your next turn auto-resumes and runs
`lifecycle_finalize_task` — you send no explicit `lifecycle_resume`. Parked fallback: the block-and-wait
`lifecycle_gate(kind="cleanup-approval", ask=…, packet={ ...what cleanup removes... })` +
`lifecycle_resume` still works if deliberately raised; on that path a model-attributed decision is never
a developer approval.

`lifecycle_finalize_task` proves one immediate edge: the contract's landed code
commit (`integrated_code_commit` when present, otherwise `code_commit`) must be an
ancestor of the recorded local `code_source_branch`, and external-memory carryover
must already be done. This handles leaf-to-parent and parent-to-parent chains the
same way; for a PR-gated edge, the model first finishes the PR workflow and pulls
the target branch locally, then the finalizer sees the same local branch
relationship as a direct edge. The tool does not infer squash equivalence by
default; squash merges are emergency/manual recovery because they erase commit
lineage and can invalidate memory lookup history.

The finalizer resolves the leaf from the contract's task root and leaf id. An omitted
`task_doc_path` adopts that exact document; a supplied path is an assertion and must
match it. Before any cleanup (including a dry-run cleanup preview), every declared
top-level step and nested substep must be `done`. Use `task_doc.skip_step` with an
exact id and nonblank reason for an intentional skip; cleanup/finalization never
auto-checks work. When the bound leaf declares an existing immediate parent, the
finalizer always derives that parent and reconciles its exact row to `Completed`, even
when both optional parent assertions are omitted. `master_doc_path` and
`subtask_number` are independent identity assertions; when supplied, each must match
that derived edge. Standalone/no-parent leaves remain supported. The finalizer does
not mark the parent task itself complete or recursively complete ancestors; each
parent-child edge is finalized separately.

Standalone `worktree_cleanup` is deliberately non-terminal for task documents. If a
declared final step includes cleanup, run standalone cleanup first, then mark that
exact step `done`, and finally run `lifecycle_finalize_task` against the already-clean
contract. Do not mark a self-referential "make this task Completed" step prematurely;
split or reword it as the concrete cleanup/preparation work.

Cleanup is idempotent. If the worktrees or merged branches are already gone, it reports the already-clean state instead of failing. If Git refuses to delete an unmerged branch, cleanup leaves that branch in place and reports it for developer review.

## Reopening A Completed Leaf

Reopening reuses the EXACT same leaf id — never mint a suffixed leaf (`…-r1`).
`task_reopen(contract_path=…)` is a state reset, not a worktree creator: it refuses
anything but a fully landed leaf (closeout, integration, and cleanup completed, worktrees
gone), then resets the contract's review/closeout/integration state, clears the stale
lifecycle binding, marks `cleanup: reopened`, and puts the leaf's task document back to
`planning` (master index entry flipped, audit decision appended). Preview with
`dry_run=true` first. Afterwards: edit the doc's steps via `task_doc` (add, change, or
untick work), then run a NORMAL `worktree_start` with the same leaf id — it recreates the
worktrees off the current source tips, promotes/mints a fresh lifecycle, and restamps the
doc's `lifecycleId`, so doc, chat, and dashboard bindings hold by construction. Implementation
then proceeds as usual, including closeout → integrate → finalize.

## Boundaries

1. The `c-09-git-worktree-manager` skill may create or reuse worktrees, root series contracts, and leaf enclosure contracts.
2. The `c-09-git-worktree-manager` skill does not initialize memory roots; use the `c-00-initialize-memory-repo` skill before starting external-memory worktrees.
3. Closeout belongs to the `c-12-closeout` skill; the `c-09-git-worktree-manager` skill only supplies worktree contract context.
4. The `c-09-git-worktree-manager` skill must not use divergent memory as semi-trusted reference context.
5. The `c-09-git-worktree-manager` skill must not bypass the `c-12-closeout` skill's applicable
   closeout authority gate.
6. The `c-09-git-worktree-manager` skill must not create closeout commits outside the `c-12-closeout` skill's code-memory-ledger sequence.
7. The `c-09-git-worktree-manager` skill must not call `worktree_start` until
   the applicable authority has been recorded: developer-approved Worktree Intent Gate for
   standalone/new work, or accepted-series authority for subordinate orchestrated work.
8. The `c-09-git-worktree-manager` skill must not move source branches during integration until
   replay/preflight has produced fast-forwardable code and memory commits and applicable
   integration authority exists.
9. The `c-09-git-worktree-manager` skill must not finalize or clean up without applicable
   cleanup/finalization authority.
10. The `c-09-git-worktree-manager` skill must not treat squash-merged content as a normal landed edge.
11. The `c-08-ar-coordination-context-resolver` skill remains the facts-only resolver; the `c-09-git-worktree-manager` skill owns worktree and lifecycle mutation.
