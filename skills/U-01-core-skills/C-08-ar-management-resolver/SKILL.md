---
name: C-08-ar-management-resolver
description: "Resolve the active Agents Remember context for a target repository, including topology, coordination root, memory root, settings, storage, pathRules, worktree contract facts, ledger path, and branch-gated cross-repo allowances."
---

# C-08 AR Management Resolver

Use this skill whenever an agent needs the active Agents Remember context for a repository.

In the normal workflow, pass only the repository name. C-08 decides whether that repository is using repo-local internal memory or selected shared memory, then returns the resolved coordination, memory, settings, task, worktree, ledger, and cross-repo facts that downstream skills must use.

## Inputs

- `repo_name`: repository being worked on. This is the normal input.
- `workspace_root`: optional workspace root used to find `repo_name` when the caller is not already in the workspace root.
- `requested_topology`: optional `internal` or `shared` override for repair, compatibility, or explicit shared operations.
- `shared_root`: optional shared-root hint. Normal resolution may infer an already selected shared root from `agents-remember-md/.env` or `.env.example`.
- `settings_path`: optional override for repair or compatibility cases.
- `onboarding_root`: optional compatibility override when a caller has already resolved the repository onboarding root.
- `target_repo`: optional full repository path for callers that already have the path. This does not replace `repo_name` as the normal agent-facing contract.
- `contract_path`: optional `contract.md` path for worktree-backed task context.
- `task_name`: optional task name used to locate `ar-management/tasks/<repo-name>/<task-name>-ar/contract.md`.
- `worktree_name`: optional worktree name used to compute the worktree group when no contract exists.

When a sibling `settings.json` exists beside `settings.md`, C-08 prefers that JSON file for machine-readable storage, `pathRules`, and `crossRepo` data. `settings.md` remains the human and agent instruction file, and legacy fenced settings in `settings.md` are still accepted as a fallback when JSON is absent.

## Outputs

The resolver returns one management context for the target repository:

- `topology`: `internal` or `shared`
- `repo_name`
- `target_repo`
- `coordination_root`
- `memory_root`
- `memory_mode`
- `management_root`: compatibility alias for `coordination_root`
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

1. If `onboarding_root` is supplied, treat it as a compatibility override and infer the surrounding roots and settings path from that root.
2. If `requested_topology` is `internal`, use `<target-repo>/ar-memory/` as `memory_root` and `<target-repo>/ar-management/` as `coordination_root`.
3. If `requested_topology` is `shared`, use the supplied or inferred shared `ar-management/` root as `coordination_root` and `ar-management/memory-repos/ar-<repo-name>/` as `memory_root`.
4. If no topology override is supplied, inspect the active shared root and resolved settings. A repository is shared-managed only when the shared root contains repository-specific evidence such as `memory-repos/ar-<repo_name>/`, a legacy `onboarding/<repo_name>/`, or a scoped `pathRules` entry for `path: <repo_name>` or `path: <repo_name>/<subtree>`.
5. If a worktree contract exists, use it for task root, worktree group, code worktree, memory worktree, and ledger path. Do not guess missing contract values from folder names.
6. If no shared selection applies, default to internal topology and use `<target-repo>/ar-memory/` for memory.

Mixed workspaces are resolved per target repository. One shared-managed repository does not move neighboring local repositories onto the shared root, and one local repository does not prevent another repository from using shared management.

## Helper

Use the bundled helper as the single source of truth for resolver logic:

```bash
<this-skill-dir>/scripts/ar_management_resolver.py \
  --repo-name <repo-name> \
  --workspace-root <workspace-root> \
  --format json
```

Compatibility callers that already have a repository path can pass `--repo <repo-root>`. Explicit shared operations can pass `--topology shared --shared-root <shared-ar-management-root>`.
Worktree-aware callers can pass `--task-name`, `--worktree-name`, or `--contract-path`.

The helper uses only the Python standard library, including the built-in JSON parser for `settings.json`. If the executable bit is unavailable, invoke it with the machine's Python 3 interpreter.

## Consumers

- `AGENTS.md` Gate 1 uses this skill to resolve coordination root, memory root, task root, temp root, onboarding root, settings, storage, `pathRules`, worktree facts, ledger path, and cross-repo allowances.
- `C-02-onboarding-drift-detection` consumes the resolved context and remains responsible only for drift classification and trust reporting.
- `C-03-repo-bootstrap`, `C-04-discovery`, `C-05-create-or-update-onboarding-files`, and task workflows use the resolved roots instead of rebuilding topology rules.
- `C-09-git-worktree-manager` consumes the resolved context and owns Git worktree mutation, task contract updates, and closeout sequencing.

## Boundaries

1. C-08 owns topology detection, coordination-root and memory-root resolution, JSON-first settings parsing with legacy Markdown fallback, storage semantics, `pathRules`, task-contract fact loading, and cross-repo allowance parsing.
2. Other skills may import or call the C-08 helper, but they must not keep parallel resolver implementations.
3. The top `AGENTS.md` topology explanation remains fallback guidance for humans and agents if the helper cannot run.
4. C-08 resolves where context lives; it does not create missing scaffolding or Git worktrees. Use `C-00-initialize-management-root` for scaffold creation and C-09 for worktree lifecycle mutation.
