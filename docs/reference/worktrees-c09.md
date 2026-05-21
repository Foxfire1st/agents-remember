# C-09 Worktrees And Closeout

`C-09-git-worktree-manager` owns worktree lifecycle and approved closeout sequencing.

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

During `start`, C-09 also prepares context providers for the new code worktree
when the target coordinator's `settings.json` enables `codegraphcontext-code`,
unless `--skip-provider-setup` is passed. Provider preparation is delegated to
the installed `scripts/provider-setup.py` instead of duplicating provider logic
inside the worktree manager. The default setup uses the source coordinator as
the CGC seed source, exports the existing CGC bundle for the source repo,
rewrites indexed paths to the new code worktree, and loads the result into a
worktree-local CGC backend under the worktree group. That gives worktree tasks
their own FalkorDB runtime/data while avoiding a full re-index when the source
and worktree commits match. If CGC is not enabled in settings, C-09 reports
provider setup as skipped.

## Closeout Order

For external-memory closeout, C-09 keeps code and memory aligned:

1. commit code
2. refresh affected onboarding metadata to the code commit
3. commit memory content
4. update and commit `memory.md`

## Integration

C-09 integration lands reviewed task work back onto source branches. It can use fast-forward integration when source ancestry has not moved, or replay when compatible parallel work landed first.

## Approval Gates

Implementation approval is not commit approval. For worktree-backed tasks, the agent should run a dry-run closeout preview and ask for explicit commit approval before C-09 creates commits.

Cleanup happens only after successful integration and explicit approval.
