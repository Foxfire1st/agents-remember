---
name: c-08-ar-coordination-context-resolver
description: "Resolve the active Agents Remember context for a target repository, including topology, coordination root, memory root, settings, storage, pathRules, worktree contract facts, ledger path, and branch-gated cross-repo allowances."
---

# c-08-ar-coordination-context-resolver AR Coordination Resolver

Use this skill whenever an agent needs the active Agents Remember context for a repository.

In the normal workflow, pass the code repository name. The `c-08-ar-coordination-context-resolver` skill decides whether that repository is using repo-local internal memory or selected external memory, then returns the resolved code repository root, coordination, memory, settings, task, worktree, ledger, and cross-repo facts that downstream skills must use.

## Inputs

- `code_repository_name`: name of the code repository being worked on. This is the normal input.
- `workspace_root`: optional workspace root used to find `code_repository_name` when the caller is not already in the workspace root.
- `requested_topology`: optional `internal` or `external` override for repair or explicit external-memory operations.
- `coordination_root`: optional coordination-root hint. Normal installed resolution uses MCP settings. Package-local resolver calls use explicit input first, then the installed runtime root when invoked from an installed coordinator, then the built-in source-development default `../ar-coordination`.
- `settings_path`: optional override for repair cases.
- `onboarding_root`: optional override when a caller has already resolved the repository onboarding root.
- `code_repository_root`: optional root directory of the code repository for callers that already have the path. This does not replace `code_repository_name` as the normal agent-facing contract.
- `contract_path`: optional `contract.md` path for worktree-backed task context.
- `task_name`: optional task name used to locate `ar-coordination/tasks/<code-repository-name>/<task-name>/contract.md`, with persisted `*-ar` task contract folders still checked. Without `task_name`, `task_root` resolves to the repository task namespace `ar-coordination/tasks/<code-repository-name>/`.
- `worktree_name`: optional worktree name used to compute the worktree group when no contract exists.

When a sibling `settings.json` exists beside `settings.md`, the `c-08-ar-coordination-context-resolver` skill prefers that JSON file for machine-readable storage, `pathRules`, and `crossRepo` data. `settings.md` remains the human and agent instruction file, and fenced settings in `settings.md` are accepted when JSON is absent.

## Outputs

The resolver returns one coordination context for the target repository:

- `topology`: `internal` or `external`
- `code_repository_name`
- `code_repository_root`
- `coordination_root`
- `memory_root`
- `memory_mode`
- `onboarding_root`
- `settings_path`
- `path_settings_path`: sibling machine-readable settings path when `settings.json` exists, otherwise empty in JSON output
- `task_root`: repository task namespace when no task name is supplied; task-specific folder when `task_name` or a contract is supplied
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
3. Resolve the coordinator from explicit `coordination_root`, the installed runtime root when invoked from an installed coordinator, or the built-in source-development default `../ar-coordination`.
4. If `requested_topology` is `internal`, require `<code-repository-root>/ar-memory/` to exist and use it as `memory_root`.
5. If `requested_topology` is `external`, require `<coordination-root>/memory-repos/ar-<code-repository-name>/` to exist and use it as `memory_root`.
6. If no topology override is supplied, check `<code-repository-root>/ar-memory/` first, then `<coordination-root>/memory-repos/ar-<code-repository-name>/`.
7. If neither supported memory location exists, fail with a missing-memory error that lists both checked paths. The agent should ask the developer whether to initialize memory with `c-00-initialize-memory-repo`, explain that the `c-00-initialize-memory-repo` skill creates the scaffold/settings, and then run the `c-03-repo-bootstrap` skill only if onboarding content should be generated.

Mixed workspaces are resolved per target repository. One external memory repo does not move neighboring local repositories onto the coordination root, and one local repository does not prevent another repository from using external memory.

## MCP Tools

Use the Agents Remember MCP resolver tools as the normal installed runtime entry
point:

```text
resolve_context(repo_id="<repo-id>", task_name="<task>", contract_path="<contract.md>", worktree_name="<worktree>", topology="<internal|external>")
```

For startup context that also needs provider status or drift summary, request:

```text
context_packet(repo_id="<repo-id>", include_providers=true, include_drift=false)
```

The MCP server owns the authority settings for repository IDs, workspace roots,
coordination root, and provider configuration. Callers that already know a
checkout path should still identify the configured repository through the MCP
settings rather than inventing a parallel resolver path.

The skill tree is instruction-only; installed and development workflows use the
MCP/package route.

## Consumers

- `AGENTS.md` Gate 1 uses this skill to resolve coordination root, memory root, task root, temp root, onboarding root, settings, storage, `pathRules`, worktree facts, ledger path, and cross-repo allowances.
- `c-02-memory-quality-control` consumes the resolved context and owns memory quality checks, including task-start drift classification and trust reporting.
- `c-03-repo-bootstrap`, `c-04-retrieval-strategy-router`, `c-05-create-or-update-onboarding-files`, and task workflows use the resolved roots instead of rebuilding topology rules.
- `c-09-git-worktree-manager` consumes the resolved context and owns Git worktree mutation, task contract updates, and closeout sequencing.

## Boundaries

1. The `c-08-ar-coordination-context-resolver` skill owns topology detection, coordination-root and memory-root resolution, JSON-first settings parsing with Markdown fallback, storage semantics, `pathRules`, task-contract fact loading, and cross-repo allowance parsing.
2. Other skills may request MCP `resolve_context` or `context_packet`, but they must not keep parallel resolver implementations.
3. The top `AGENTS.md` topology explanation remains orientation guidance for humans and agents, not a replacement runtime path.
4. The `c-08-ar-coordination-context-resolver` skill resolves where context lives; it does not create missing scaffolding or Git worktrees. Use the `c-00-initialize-memory-repo` skill for memory-root creation and the `c-09-git-worktree-manager` skill for worktree lifecycle mutation.
