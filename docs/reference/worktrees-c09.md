# C-09 Worktrees And Closeout

`C-09-git-worktree-manager` owns worktree lifecycle, integration, and cleanup.
Closeout sequencing and the commit approval gate belong to `C-12-closeout`
(see [Skills](skills.md)); C-09 only supplies the worktree contract path that
C-12's closeout consumes.

## When To Use C-09

Use C-09 when:

- a task needs isolated code and memory worktrees
- external-memory closeout needs code and memory commits mapped in `memory.md`
- the developer wants explicit status, closeout, integration, and cleanup gates
- a small approved current-checkout edit needs external-memory direct closeout

## Worktree-Backed Tasks

C-09 creates a `contract.md` beside the task file:

```text
ar-coordination/tasks/<repo>/<task-slug>/
  task.md
  contract.md
```

The contract records worktree paths, review state, closeout commits, integration commits, and cleanup state.

Provider authority has moved to MCP settings outside the coordinator root, and
coordinator-local Python provider scripts are no longer installed runtime
assets. Legacy C-09 provider preparation is therefore skipped unless a future
MCP worktree/provider operation handles the isolated provider runtime for the
new code worktree.

## Closeout Order

Closeout is run by `C-12-closeout`. For worktree-backed tasks, C-09
hands C-12 the task `contract.md`; C-12 keeps code and memory aligned:

1. commit code
2. refresh affected onboarding metadata to the code commit
3. commit memory content
4. update and commit `memory.md`

C-12 records the closeout commits back into the contract, and C-09 owns the
later integration and cleanup of those committed branches.

## Integration

C-09 integration lands reviewed task work back onto source branches. It can use fast-forward integration when source ancestry has not moved, or replay when compatible parallel work landed first.

## Approval Gates

Implementation approval is not commit approval. For worktree-backed tasks, the agent should run a dry-run closeout preview and ask for explicit commit approval before C-12 creates the closeout commits.

Cleanup happens only after successful integration and explicit approval.
