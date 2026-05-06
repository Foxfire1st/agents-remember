---
name: C-08-ar-management-resolver
description: "Resolve the active Agents Remember management context for a target repository, including topology, roots, settings, storage, pathRules, and cross-repo allowance."
---

# C-08 AR Management Resolver

Use this skill whenever an agent needs the active `ar-management/` context for a repository.

In the normal workflow, pass only the repository name. C-08 decides whether that repository is using repo-local internal management or selected shared management, then returns the resolved roots and settings that downstream skills must use.

## Inputs

- `repo_name`: repository being worked on. This is the normal input.
- `workspace_root`: optional workspace root used to find `repo_name` when the caller is not already in the workspace root.
- `requested_topology`: optional `internal` or `shared` override for repair, compatibility, or explicit shared operations.
- `shared_root`: optional shared-root hint. Normal resolution may infer an already selected shared root from `agents-remember-md/.env` or `.env.example`.
- `settings_path`: optional override for repair or compatibility cases.
- `onboarding_root`: optional compatibility override when a caller has already resolved the repository onboarding root.
- `target_repo`: optional full repository path for callers that already have the path. This does not replace `repo_name` as the normal agent-facing contract.

When a sibling `settings.json` exists beside `settings.md`, C-08 prefers that JSON file for machine-readable storage, `pathRules`, and `crossRepo` data. `settings.md` remains the human and agent instruction file, and legacy fenced settings in `settings.md` are still accepted as a fallback when JSON is absent.

## Outputs

The resolver returns one management context for the target repository:

- `topology`: `internal` or `shared`
- `repo_name`
- `target_repo`
- `management_root`
- `onboarding_root`
- `settings_path`
- `path_settings_path`: sibling machine-readable settings path when `settings.json` exists, otherwise empty in JSON output
- `task_root`
- `docs_root`
- `system_root`
- `sources_path`
- `tools_path`
- `storage`: storage mode, default, and storage rule data
- `pathRules`: include/exclude eligibility rules by source path and file type
- `crossRepo`: allowed adjacent repositories for internal cross-repo discovery

## Resolution Rules

1. If `onboarding_root` is supplied, treat it as a compatibility override and infer the management root and settings path from that root.
2. If `requested_topology` is `internal`, use `<target-repo>/ar-management/`.
3. If `requested_topology` is `shared`, use the supplied or inferred shared `ar-management/` root.
4. If no topology override is supplied, inspect the active shared root and resolved settings. A repository is shared-managed only when the shared root contains repository-specific evidence such as `onboarding/<repo_name>/` or a scoped `pathRules` entry for `path: <repo_name>` or `path: <repo_name>/<subtree>`.
5. If no shared selection applies, default to internal topology and use `<target-repo>/ar-management/`.

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

The helper uses only the Python standard library, including the built-in JSON parser for `settings.json`. If the executable bit is unavailable, invoke it with the machine's Python 3 interpreter.

## Consumers

- `AGENTS.md` Gate 1 uses this skill to resolve task root, onboarding root, settings, storage, `pathRules`, and cross-repo allowances.
- `C-02-onboarding-drift-detection` consumes the resolved context and remains responsible only for drift classification and trust reporting.
- `C-03-repo-bootstrap`, `C-04-discovery`, `C-05-create-or-update-onboarding-files`, and task workflows use the resolved roots instead of rebuilding topology rules.

## Boundaries

1. C-08 owns topology detection, management-root resolution, JSON-first settings parsing with legacy Markdown fallback, storage semantics, `pathRules`, and cross-repo allowance parsing.
2. Other skills may import or call the C-08 helper, but they must not keep parallel resolver implementations.
3. The top `AGENTS.md` topology explanation remains fallback guidance for humans and agents if the helper cannot run.
4. C-08 resolves where management context lives; it does not create missing scaffolding. Use `C-00-initialize-management-root` for scaffold creation.
