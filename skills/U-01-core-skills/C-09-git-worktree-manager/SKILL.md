---
name: C-09-git-worktree-manager
description: "Create, attach to, report on, and close out Agents Remember worktree-backed tasks while preserving human approval gates and shared-memory ledger alignment."
---

# C-09 Git Worktree Manager

Use this skill when a task should run through an explicit code/memory worktree wrapper or when an approved direct checkout edit needs the same code-memory-ledger closeout discipline.

C-09 wraps the existing chat, light-task, heavy-task, or external workflow. It owns Git worktree state, task contracts, direct checkout closeout, shared-memory compatibility checks, and approved closeout sequencing. It does not replace the workflow that performs the actual implementation.

## Commands

The bundled helper exposes these subcommands:

```bash
<this-skill-dir>/scripts/git_worktree_manager.py start --repo-name <repo> --task-name <task> --worktree-name <name>
<this-skill-dir>/scripts/git_worktree_manager.py attach --repo-name <repo> --task-name <task>
<this-skill-dir>/scripts/git_worktree_manager.py status --repo-name <repo> --task-name <task>
<this-skill-dir>/scripts/git_worktree_manager.py bootstrap-memory --repo-name <repo>
<this-skill-dir>/scripts/git_worktree_manager.py closeout --contract-path <contract.md> --dry-run ...
<this-skill-dir>/scripts/git_worktree_manager.py closeout --contract-path <contract.md> --approved --approval-note <note> ...
<this-skill-dir>/scripts/git_worktree_manager.py direct-closeout --repo-name <repo> --dry-run ...
<this-skill-dir>/scripts/git_worktree_manager.py direct-closeout --repo-name <repo> --approved --approval-note <note> ...
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

Closeout is explicitly human-gated. Implementation approval is not commit approval. Agents must first run `closeout --dry-run` to prepare a non-mutating commit preview, relay the proposed code, memory, and ledger commit messages to the developer, and ask for explicit commit approval. Dry-run closeout does not require `--approved`, and it reports the closeout order plus the affected onboarding metadata refresh plan.

Real closeout creates commits and therefore requires both `--approved` and `--approval-note`. The note records the developer's explicit commit approval in the contract. Agents must not self-grant this approval from their own judgment or from earlier implementation approval.

Closeout stops if the recorded code or shared-memory source branch moved since task start.

Shared-memory closeout order is:

1. identify changed code worktree paths and their required sidecar onboarding files
2. fail before committing when a changed onboarding-eligible source file is missing current sidecar onboarding or verification metadata
3. commit code worktree changes and capture `C2` plus its commit date
4. refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`
5. commit memory-content changes and capture `M2`
6. prepend `C2 | M2` to `memory.md`
7. commit the ledger update as `L2`
8. update the task contract closeout state

Push behavior is not automatic.

## Direct Closeout

Use `direct-closeout` only for small approved edits made in the current source checkout, or for memory-only polish that does not need isolated worktrees or durable task artifacts. If the work is parallel, long-running, conflict-prone, review-heavy, or needs replay/integration bookkeeping, use the normal C-09 worktree flow instead.

Direct closeout is still explicitly human-gated. Agents must run `direct-closeout --dry-run` first, relay the proposed code, memory, and ledger commit messages to the developer, and ask for explicit commit approval. Real direct closeout requires both `--approved` and `--approval-note`.

Direct closeout resolves the current C-08 context, requires shared memory mode, requires the code checkout and memory repo to be on the same selected branch, and requires `memory.md` branch metadata to match that branch.

Shared-memory direct closeout order is:

1. identify changed current-checkout code paths and their required sidecar onboarding files
2. fail before committing when a changed onboarding-eligible source file is missing current sidecar onboarding or verification metadata
3. commit code checkout changes and capture `C2` plus its commit date
4. refresh affected onboarding `lastVerifiedCommitHash` and `lastVerifiedCommitDate` to `C2`
5. commit memory-content changes and capture `M2`
6. prepend `C2 | M2` to `memory.md`
7. commit the ledger update as `L2`

Direct closeout fails without mutation when required onboarding is missing, verification metadata is missing, shared memory is not resolved, branch metadata does not match, or no code or memory changes exist. Missing onboarding is the expected hard failure when the implementation/update pass somehow did not produce a required onboarding file; the next step is to run C-05 for that source file, then rerun the direct closeout preview.

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
3. C-09 may directly close out approved current-checkout edits when a worktree wrapper would add ceremony without isolation value.
4. C-09 must not use divergent memory as semi-trusted reference context.
5. C-09 must not commit without explicit commit approval after a closeout preview.
6. C-09 shared-memory closeout must not create a memory content commit whose affected onboarding metadata still points at pre-closeout code.
7. C-09 must not move source branches during integration until replay/preflight has produced fast-forwardable code and memory commits and explicit integration approval exists.
8. C-09 must not clean up without explicit cleanup approval.
9. C-08 remains the facts-only resolver; C-09 owns worktree and lifecycle mutation.
