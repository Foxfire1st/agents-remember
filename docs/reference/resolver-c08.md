# `c-08-ar-coordination-context-resolver` Resolver

`c-08-ar-coordination-context-resolver` is the facts resolver for a target code repository.

It answers: where is memory, where is coordination state, what settings apply, what task root should be used, and what cross-repo context is allowed?

## Normal Inputs

Use one of:

```text
code_repository_name = my-app
code_repository_root = /path/to/my-app
```

Optional inputs include `coordination_root`, `requested_topology`, `task_name`, `worktree_name`, and `contract_path`.

## Resolution

The `c-08-ar-coordination-context-resolver` skill checks:

1. explicit contract or coordination inputs
2. repo-local memory at `<repo>/ar-memory/`
3. external memory at `<coordination-root>/memory-repos/ar-<repo>/`

If neither memory location exists, the `c-08-ar-coordination-context-resolver` skill fails with a missing-memory error and lists the checked paths.

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

For worktree-backed tasks, the `c-08-ar-coordination-context-resolver` skill can also report contract, worktree, and ledger facts.

## Boundary

The `c-08-ar-coordination-context-resolver` skill does not mutate Git, create memory roots, update onboarding, or start worktrees. Use the `c-00-initialize-memory-repo`, `c-03-repo-bootstrap`, `c-05-create-or-update-onboarding-files`, and `c-09-git-worktree-manager` skills for those actions.
