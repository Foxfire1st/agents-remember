---
name: c-09-git-worktree-manager
description: "Create, attach to, report on, integrate, and clean up Agents Remember worktree-backed tasks while preserving human approval gates and external-memory compatibility."
---

# C-09 Git Worktree Manager

Use this skill when a task should run through an explicit code/memory worktree wrapper.

C-09 wraps the existing chat, light-task, heavy-task, or external workflow. It owns Git worktree state, task contracts, external-memory compatibility checks, integration, and cleanup. It does not replace the workflow that performs the actual implementation.

For closeout, use `C-12-closeout`. C-09 only supplies the worktree-specific
contract path and integration/cleanup follow-up rules.

## MCP Tools

Use the Agents Remember MCP worktree tools as the normal installed runtime
entry point:

```text
worktree_start(repo_id="<repo-id>", task_name="<task>", worktree_name="<name>", workflow_kind="light-task", dry_run=false)
worktree_attach(repo_id="<repo-id>", task_name="<task>")
worktree_status(repo_id="<repo-id>", task_name="<task>")
worktree_closeout_preview(contract_path="<contract.md>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_closeout_apply(contract_path="<contract.md>", intent_note="<developer intent>", code_commit_message="<message>", memory_commit_message="<message>", ledger_commit_message="<message>")
worktree_integrate(contract_path="<contract.md>", strategy="ff-only", dry_run=false)
worktree_cleanup(contract_path="<contract.md>", dry_run=false)
```

Callers identify repositories by configured MCP `repo_id`. The MCP server owns
workspace root, coordination root, provider setup settings, and path containment.
The skill tree is instruction-only; installed and development workflows use the
MCP/package route.

## Pre-Worktree Intake

C-09 starts after the normal task intake and onboarding gate, not before them.

The intended order is:

1. run the C-08 resolver for the target repository
2. run C-02 memory quality control's task-start drift check and follow the existing AGENTS Gate 3/4 choice point
3. when onboarding is refreshed, commit the memory content and ledger before starting any worktree
4. decide whether the work is chat-only, W-02 light task, heavy task, or external workflow
5. choose or review the task slug and workflow variables
6. create the durable task wrapper when one is needed
7. request MCP `worktree_start` only after the task identity is stable and external memory is clean

For W-02 light tasks, the durable artifact shape is `<task-root>/<task-slug>/task.md`. C-09 then places `contract.md` beside that `task.md` when worktrees are created.

## Start / Attach / Status

`worktree_start` resolves C-08 context, creates or loads `contract.md`, prepares the code worktree first, and then prepares external-memory state when enabled. External-memory start refuses to continue when the source memory repo has uncommitted changes; refreshed onboarding and the ledger must be committed first so the new worktree starts from an auditable memory baseline.

When external memory is enabled, C-09 validates the memory repo and `memory.md` ledger before allowing memory to be used as trusted context. Missing external memory is not a C-09 bootstrap path; run `C-00-initialize-memory-repo` first. If no compatible memory state exists, C-09 stops and reports the allowed human choices:

1. `reconciliation`
2. `disabled-memory`
3. `custom`

`worktree_attach` and `worktree_status` read the existing contract and report recoverable state without mutating Git. `worktree_status` includes a lifecycle phase, dirty worktree flags, a summary, and typed next hints such as `nextOperation`, `nextTool`, and `nextArgs`.

## Worktree Closeout

Use `C-12-closeout` for worktree closeout. C-12 owns the approval gate,
missing-onboarding check, code commit, onboarding and entity refresh, memory
quality gate, memory content commit, ledger update, and ledger commit.

For worktree-backed tasks, pass the task `contract.md` to
`worktree_closeout_preview` / `worktree_closeout_apply`. The apply step records
the developer's explicit commit approval in the contract and updates the
contract closeout state after the code, memory, and ledger commits are created.

Worktree closeout stops if the recorded code or external-memory source branch
moved since task start.

## Integration

Integration is explicitly human-gated and runs only after closeout completed. It lands the closed task branches back onto the recorded source branches and records the landed commits separately from the closeout commits.

Strategies:

1. `ff-only`: require current code and memory source branches to be ancestors of the closeout commits, then fast-forward both source branches.
2. `replay`: when source branches moved because parallel work landed first, replay the code task commit onto current code source, replay only the memory content commit onto current memory source, regenerate `memory.md` for the final landed code and memory content commits, then fast-forward both source branches.

Conflict rule: if code replay or memory-content replay conflicts, stop before moving source branches. The agent must discuss the resolution with the developer and decide what is true before continuing. Do not replay an old ledger commit over current memory main; always regenerate the ledger row after memory content has been mediated.

After successful integration, ask whether to remove the code and memory worktrees plus merged local task branches. Cleanup is not automatic.

## Cleanup

Cleanup is explicitly human-gated and runs only after integration completed. It removes the recorded code and memory worktrees, deletes local task branches only when Git can prove they are merged, removes empty worktree group folders when safe, and records `cleanup: completed` in the contract.

Cleanup is idempotent. If the worktrees or merged branches are already gone, it reports the already-clean state instead of failing. If Git refuses to delete an unmerged branch, cleanup leaves that branch in place and reports it for developer review.

## Boundaries

1. C-09 may create or reuse worktrees and task contracts.
2. C-09 does not initialize memory roots; use `C-00-initialize-memory-repo` before starting external-memory worktrees.
3. Closeout belongs to `C-12-closeout`; C-09 only supplies worktree contract context.
4. C-09 must not use divergent memory as semi-trusted reference context.
5. C-09 must not bypass C-12's explicit closeout approval gate.
6. C-09 must not create closeout commits outside C-12's code-memory-ledger sequence.
7. C-09 must not move source branches during integration until replay/preflight has produced fast-forwardable code and memory commits and explicit integration approval exists.
8. C-09 must not clean up without explicit cleanup approval.
9. C-08 remains the facts-only resolver; C-09 owns worktree and lifecycle mutation.
