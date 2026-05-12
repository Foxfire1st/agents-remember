---
name: c-08-ar-coordination-context-resolver
description: "Resolve the active Agents Remember context for a target repository, including topology, coordination root, memory root, settings, storage, pathRules, worktree contract facts, ledger path, and branch-gated cross-repo allowances."
---

# C-08 AR Coordination Resolver

Use this skill whenever an agent needs the active Agents Remember context for a repository.

In the normal workflow, pass the code repository name. C-08 decides whether that repository is using repo-local internal memory or selected shared memory, then returns the resolved code repository root, coordination, memory, settings, task, worktree, ledger, and cross-repo facts that downstream skills must use.

## Inputs

- `code_repository_name`: name of the code repository being worked on. This is the normal input.
- `workspace_root`: optional workspace root used to find `code_repository_name` when the caller is not already in the workspace root.
- `requested_topology`: optional `internal` or `shared` override for repair or explicit shared operations.
- `shared_root`: optional shared-root hint. Normal resolution uses explicit input first, then `agents-remember-md/.env`, then the built-in default `../ar-coordination`. `.env.example` is documentation only and is not runtime input.
- `settings_path`: optional override for repair cases.
- `onboarding_root`: optional override when a caller has already resolved the repository onboarding root.
- `code_repository_root`: optional root directory of the code repository for callers that already have the path. This does not replace `code_repository_name` as the normal agent-facing contract.
- `contract_path`: optional `contract.md` path for worktree-backed task context.
- `task_name`: optional task name used to locate `ar-coordination/tasks/<code-repository-name>/<task-name>/contract.md`, with persisted `*-ar` task contract folders still checked.
- `worktree_name`: optional worktree name used to compute the worktree group when no contract exists.

When a sibling `settings.json` exists beside `settings.md`, C-08 prefers that JSON file for machine-readable storage, `pathRules`, and `crossRepo` data. `settings.md` remains the human and agent instruction file, and fenced settings in `settings.md` are accepted when JSON is absent.

## Outputs

The resolver returns one coordination context for the target repository:

- `topology`: `internal` or `shared`
- `code_repository_name`
- `code_repository_root`
- `coordination_root`
- `memory_root`
- `memory_mode`
- `onboarding_root`
- `settings_path`
- `path_settings_path`: sibling machine-readable settings path when `settings.json` exists, otherwise empty in JSON output
- `task_root`
- `temp_root`
- `docs_root`
- `system_root`
- `sources_path`
- `tools_path`
- `contract_path`
- `worktree_group`
- `code_worktree`
- `memory_worktree`
- `ledger_path`
- `storage`: storage mode, default, and storage rule data
- `pathRules`: include/exclude eligibility rules by source path and file type
- `crossRepo`: branch-gated allowed adjacent repositories, with included/excluded state and reasons

## Resolution Rules

1. If `onboarding_root` is supplied, treat it as an explicit override only when it points under a supported memory location: `<code-repository-root>/ar-memory/onboarding` or `<ar-coordination>/memory-repos/ar-<code-repository-name>/onboarding`.
2. If a worktree contract path is supplied, use the contract's `coordination_root` before validating memory so task worktrees resolve against their own coordinator.
3. Resolve the shared coordinator from explicit `shared_root`, `agents-remember-md/.env`, or the built-in default `../ar-coordination`.
4. If `requested_topology` is `internal`, require `<code-repository-root>/ar-memory/` to exist and use it as `memory_root`.
5. If `requested_topology` is `shared`, require `<coordination-root>/memory-repos/ar-<code-repository-name>/` to exist and use it as `memory_root`.
6. If no topology override is supplied, check `<code-repository-root>/ar-memory/` first, then `<coordination-root>/memory-repos/ar-<code-repository-name>/`.
7. If neither supported memory location exists, fail with a missing-memory error that lists both checked paths. The agent should ask the developer whether to bootstrap memory, explain that C-00 creates the scaffold/settings, and then run C-03 only if onboarding content should be generated.

Mixed workspaces are resolved per target repository. One shared-memory repository does not move neighboring local repositories onto the shared root, and one local repository does not prevent another repository from using shared memory.

## Helper

Use the bundled helper as the single source of truth for resolver logic:

```bash
<this-skill-dir>/scripts/ar_coordination_context_resolver.py \
  --code-repository-name <code-repository-name> \
  --workspace-root <workspace-root> \
  --format json
```

Callers that already have the code repository root can pass `--code-repository-root <code-repository-root>`. Explicit shared operations can pass `--topology shared --shared-root <shared-ar-coordination-root>`.
Worktree-aware callers can pass `--task-name`, `--worktree-name`, or `--contract-path`.

The helper uses only the Python standard library, including the built-in JSON parser for `settings.json`. If the executable bit is unavailable, invoke it with the machine's Python 3 interpreter.

## Consumers

- `AGENTS.md` Gate 1 uses this skill to resolve coordination root, memory root, task root, temp root, onboarding root, settings, storage, `pathRules`, worktree facts, ledger path, and cross-repo allowances.
- `C-02-onboarding-drift-detection` consumes the resolved context and remains responsible only for drift classification and trust reporting.
- `C-03-repo-bootstrap`, `C-04-discovery`, `C-05-create-or-update-onboarding-files`, and task workflows use the resolved roots instead of rebuilding topology rules.
- `C-09-git-worktree-manager` consumes the resolved context and owns Git worktree mutation, task contract updates, and closeout sequencing.

## Boundaries

1. C-08 owns topology detection, coordination-root and memory-root resolution, JSON-first settings parsing with Markdown fallback, storage semantics, `pathRules`, task-contract fact loading, and cross-repo allowance parsing.
2. Other skills may import or call the C-08 helper, but they must not keep parallel resolver implementations.
3. The top `AGENTS.md` topology explanation remains fallback guidance for humans and agents if the helper cannot run.
4. C-08 resolves where context lives; it does not create missing scaffolding or Git worktrees. Use `C-00-initialize-coordination-root` for scaffold creation and C-09 for worktree lifecycle mutation.
