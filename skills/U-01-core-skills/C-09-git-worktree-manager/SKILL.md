---
name: C-09-git-worktree-manager
description: "Create, attach to, report on, and close out Agents Remember worktree-backed tasks while preserving human approval gates and shared-memory ledger alignment."
---

# C-09 Git Worktree Manager

Use this skill when a task should run through an explicit code/memory worktree wrapper rather than directly in the source checkout.

C-09 wraps the existing chat, light-task, heavy-task, or external workflow. It owns Git worktree state, task contracts, shared-memory compatibility checks, and approved closeout sequencing. It does not replace the workflow that performs the actual implementation.

## Commands

The bundled helper exposes these subcommands:

```bash
<this-skill-dir>/scripts/git_worktree_manager.py start --repo-name <repo> --task-name <task> --worktree-name <name>
<this-skill-dir>/scripts/git_worktree_manager.py attach --repo-name <repo> --task-name <task>
<this-skill-dir>/scripts/git_worktree_manager.py status --repo-name <repo> --task-name <task>
<this-skill-dir>/scripts/git_worktree_manager.py bootstrap-memory --repo-name <repo>
<this-skill-dir>/scripts/git_worktree_manager.py closeout --contract-path <contract.md> --approved ...
<this-skill-dir>/scripts/git_worktree_manager.py integrate --contract-path <contract.md> --approved --strategy ff-only
<this-skill-dir>/scripts/git_worktree_manager.py cleanup --contract-path <contract.md> --approved
```

## Pre-Worktree Intake

C-09 starts after the normal task intake and onboarding gate, not before them.

The intended order is:

1. run the C-08 resolver for the target repository
2. run C-02 drift detection and follow the existing AGENTS Gate 3/4 choice point
3. when onboarding is refreshed, commit the memory content and ledger before starting any worktree
4. decide whether the work is chat-only, W-02 light task, heavy task, or external workflow
5. choose or review the task slug and workflow variables
6. create the durable task wrapper when one is needed
7. run C-09 `start` only after the task identity is stable and shared memory is clean

For W-02 light tasks, the durable artifact shape is `<task-root>/<task-slug>/task.md`. C-09 then places `contract.md` beside that `task.md` when worktrees are created.

## Start / Attach / Status

`start` resolves C-08 context, creates or loads `contract.md`, prepares the code worktree first, and then prepares shared-memory state when enabled. Shared-memory start refuses to continue when the source memory repo has uncommitted changes; refreshed onboarding and the ledger must be committed first so the new worktree starts from an auditable memory baseline.

When shared memory is enabled, C-09 validates the memory repo and `memory.md` ledger before allowing memory to be used as trusted context. If no compatible memory state exists, it stops and reports the allowed human choices:

1. `reconciliation`
2. `clean-start`
3. `disabled-memory`
4. `custom`

`attach` and `status` read the existing contract and report recoverable state without mutating Git. `status` includes a lifecycle phase, dirty worktree flags, a summary, and the next safe command.

## Closeout

Closeout is explicitly human-gated. The helper refuses to commit unless `--approved` is supplied, and it stops if the recorded code or shared-memory source branch moved since task start.

Shared-memory closeout order is:

1. commit code worktree changes and capture `C2`
2. commit memory-content changes and capture `M2`
3. prepend `C2 | M2` to `memory.md`
4. commit the ledger update as `L2`
5. update the task contract closeout state

Push behavior is not automatic.

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
2. C-09 may bootstrap a local shared memory repo when explicitly requested or when `start --memory-choice clean-start` is used.
3. C-09 must not use divergent memory as semi-trusted reference context.
4. C-09 must not commit or clean up without explicit human approval.
5. C-09 must not move source branches during integration until replay/preflight has produced fast-forwardable code and memory commits.
6. C-08 remains the facts-only resolver; C-09 owns worktree and lifecycle mutation.
