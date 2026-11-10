# Product Context Resolution

The product resolves context facts for a target code repository.

It answers: where is memory, where is coordination state, what settings apply, what task root should be used, and what cross-repo context is allowed?

## Internal Resolution Inputs

The internal resolver accepts repository identity through inputs such as:

```text
code_repository_name = my-app
code_repository_root = /path/to/my-app
```

Internal hints and selectors include coordination-root, topology, task, worktree and contract facts. These are not additional mounted `context_packet` arguments. Mounted callers pass `repo_id` and retain a supplied leaf `task_context`; see [the current tool contract](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md).

## Resolution

The internal product resolver checks:

1. explicit contract or coordination inputs
2. the memory repo at `<coordination-root>/memory-repos/ar-<repo>/`

If that memory location does not exist, product context resolution fails
with a missing-memory error naming the checked path. A repository still carrying the removed
repo-local `<repo>/ar-memory/` root is refused with that exact path and the route out.

## Key Outputs

The internal context model includes these facts; a mounted tool exposes its own response shape:

- `topology`
- `code_repository_name`
- `code_repository_root`
- `coordination_root`
- `memory_root`
- `onboarding_root`
- `settings_path`
- `path_settings_path`
- `task_root`
- `temp_root`
- `docs_root`
- `tools_path`
- `sources_path`
- `pathRules`
- `crossRepo`

For worktree-backed tasks, the internal context also carries contract, worktree, and ledger facts.

## Boundary

Context resolution does not mutate Git, create memory roots, update onboarding, or start worktrees. Memory-root setup follows `docs/guides/onboard-existing-repo.md`, onboarding follows the [Curator card procedure](../../skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md), and worktree mutation uses the existing tools under [lifecycle authority](../../skills/l-01-agent-lifecycles/operations/closeout.md).
