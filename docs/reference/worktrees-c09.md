# `c-09-git-worktree-manager` Worktrees And Closeout

`c-09-git-worktree-manager` owns worktree lifecycle, integration, and cleanup.
Closeout sequencing and the commit approval gate belong to `c-12-closeout`
(see [Skills](skills.md)); the `c-09-git-worktree-manager` skill only supplies the worktree contract path that
the `c-12-closeout` skill's closeout consumes.

## When To Use `c-09-git-worktree-manager`

Use the `c-09-git-worktree-manager` skill when:

- a task needs isolated code and memory worktrees
- external-memory closeout needs code and memory commits mapped in `memory.md`
- the developer wants explicit status, closeout, integration, and cleanup gates

## Worktree-Backed Tasks

The `c-09-git-worktree-manager` skill creates a `contract.md` beside the task file:

```text
ar-coordination/tasks/<repo>/<task-slug>/
  task.md
  contract.md
```

The contract records worktree paths, review state, closeout commits, integration commits, and cleanup state.

Provider authority has moved to MCP settings outside the coordinator root, and
coordinator-local Python provider scripts are no longer installed runtime
assets. Legacy `c-09-git-worktree-manager` provider preparation is therefore skipped unless a future
MCP worktree/provider operation handles the isolated provider runtime for the
new code worktree.

## Closeout Order

Closeout is run by `c-12-closeout`. For worktree-backed tasks, the `c-09-git-worktree-manager` skill
hands the `c-12-closeout` skill the task `contract.md`; the `c-12-closeout` skill keeps code and memory aligned:

1. commit code
2. refresh affected onboarding metadata to the code commit
3. commit memory content
4. update and commit `memory.md`

The `c-12-closeout` skill records the closeout commits back into the contract, and the `c-09-git-worktree-manager` skill owns the
later integration and cleanup of those committed branches.

## Integration

The `c-09-git-worktree-manager` skill integration lands reviewed task work back onto source branches. It can use fast-forward integration when source ancestry has not moved, or replay when compatible parallel work landed first.

## Approval Gates

Implementation approval is not commit approval. For worktree-backed tasks, the agent should run a dry-run closeout preview and ask for explicit commit approval before the `c-12-closeout` skill creates the closeout commits.

Cleanup happens only after successful integration and explicit approval.
