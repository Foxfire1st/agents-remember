# C-08 Resolver

`C-08-ar-coordination-context-resolver` is the facts resolver for a target code repository.

It answers: where is memory, where is coordination state, what settings apply, what task root should be used, and what cross-repo context is allowed?

## Normal Inputs

Use one of:

```text
code_repository_name = my-app
code_repository_root = /path/to/my-app
```

Optional inputs include `coordination_root`, `requested_topology`, `task_name`, `worktree_name`, and `contract_path`.

## Resolution

C-08 checks:

1. explicit contract or coordination inputs
2. repo-local memory at `<repo>/ar-memory/`
3. external memory at `<coordination-root>/memory-repos/ar-<repo>/`

If neither memory location exists, C-08 fails with a missing-memory error and lists the checked paths.

## Key Outputs

Common outputs include:

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

For worktree-backed tasks, C-08 can also report contract, worktree, and ledger facts.

## Boundary

C-08 does not mutate Git, create memory roots, update onboarding, or start worktrees. Use C-00, C-03, C-05, and C-09 for those actions.
