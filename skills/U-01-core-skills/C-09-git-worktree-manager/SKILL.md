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
```

## Start / Attach / Status

`start` resolves C-08 context, creates or loads `contract.md`, prepares the code worktree first, and then prepares shared-memory state when enabled.

When shared memory is enabled, C-09 validates the memory repo and `memory.md` ledger before allowing memory to be used as trusted context. If no compatible memory state exists, it stops and reports the allowed human choices:

1. `reconciliation`
2. `clean-start`
3. `disabled-memory`
4. `custom`

`attach` and `status` read the existing contract and report recoverable state without mutating Git.

## Closeout

Closeout is explicitly human-gated. The helper refuses to commit unless `--approved` is supplied, and it stops if the recorded code or shared-memory source branch moved since task start.

Shared-memory closeout order is:

1. commit code worktree changes and capture `C2`
2. commit memory-content changes and capture `M2`
3. prepend `C2 | M2` to `memory.md`
4. commit the ledger update as `L2`
5. update the task contract closeout state

Push behavior is not automatic.

## Boundaries

1. C-09 may create or reuse worktrees and task contracts.
2. C-09 may bootstrap a local shared memory repo when explicitly requested or when `start --memory-choice clean-start` is used.
3. C-09 must not use divergent memory as semi-trusted reference context.
4. C-09 must not commit or clean up without explicit human approval.
5. C-08 remains the facts-only resolver; C-09 owns worktree and lifecycle mutation.
