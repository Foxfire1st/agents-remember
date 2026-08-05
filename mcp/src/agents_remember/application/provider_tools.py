"""Application entry points for provider-facing MCP tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel.authority import require_repo
from agents_remember.mcp.config import (
    DEFAULT_DOCKER_CONTROL_SECONDS,
    ConfigError,
    McpRuntimeConfig,
    require_provider_launch_authority,
)
from agents_remember.providers import lifecycle_service
from agents_remember.providers.identity import stable_provider_id
from agents_remember.providers.lifecycle.log_capture import summarize_command_logs
from agents_remember.providers.settings import (
    lifecycle_settings_from_config,
    write_lifecycle_settings,
)
from agents_remember.providers.status import (
    provider_diagnostics_packet,
    provider_status_packet,
)
from agents_remember.providers.watcher_service import run_configured_watchers


def provider_status_tool(
    config: McpRuntimeConfig,
    *,
    detail_limit: int = 20,
) -> dict[str, Any]:
    return provider_status_packet(config, include_providers=True, detail_limit=detail_limit)


def provider_diagnostics_tool(
    config: McpRuntimeConfig,
    *,
    detail_limit: int = 20,
) -> dict[str, Any]:
    return provider_diagnostics_packet(config, detail_limit=detail_limit)


def provider_watchers_tool(
    config: McpRuntimeConfig,
    *,
    action: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if action == "refresh":
        # 'refresh' silently force-rebuilt every index, which read like a harmless restart.
        # Split into two unmistakable actions so a restart can never wipe an index.
        raise ValueError(
            "'refresh' was renamed to make its effect explicit: use 'restart' to stop and start "
            "the watchers WITHOUT touching their indexes, or 'invalidate-indexes' to delete and "
            "rebuild every index from scratch (slow full re-embed/re-graph)."
        )
    if action not in {"status", "start", "stop", "restart", "invalidate-indexes", "shutdown-all"}:
        raise ValueError(
            "action must be status, start, stop, restart, invalidate-indexes, or shutdown-all"
        )
    if action in {"start", "restart", "invalidate-indexes"}:
        # Containment R1 (260707-HFX-L1): launching (and rebuilding, which
        # launches indexers) runs on the LIVE on-disk authority, never the boot
        # snapshot. Disabled-on-disk refuses loudly; stop/status/shutdown-all
        # stay available — stopping is always legal.
        config = require_provider_launch_authority(config, operation=f"provider_watchers {action}")
    if action == "restart":
        # Stop then start only. 'start' re-attaches the watchers and lets them pick up changes
        # via their incremental scan; it never passes --force, so indexes are preserved.
        return {
            "ok": True,
            "operation": "provider_watchers",
            "action": "restart",
            "steps": [
                run_configured_watchers(config, "stop", dry_run=dry_run),
                run_configured_watchers(config, "start", dry_run=dry_run),
            ],
        }
    if action == "invalidate-indexes":
        return _provider_invalidate_indexes(config, dry_run=dry_run)
    effective_dry_run = False if action == "status" else dry_run
    return run_configured_watchers(config, action, dry_run=effective_dry_run)


def summarize_provider_watchers_result(result: dict[str, Any]) -> dict[str, Any]:
    """Apply provider command-log reporting policy inside the application layer."""
    return summarize_command_logs(result)


@dataclass(frozen=True)
class WorktreeProviderTarget:
    """A worktree's isolated provider stack, located from its persisted state file."""

    repo_id: str
    task_name: str
    group: str
    settings_path: Path


@dataclass(frozen=True)
class ProviderQueryScope:
    """Where a provider query runs and under what execution budget.

    Every CGC/GrepAI query answers the same three questions before it can run: which
    provider stack answers it (``worktree`` names a worktree's isolated stack; omitted,
    the single per-repo stack or the workspace stack is used), whether the provider CLI
    is actually invoked or only planned, and how long the run may take.
    """

    worktree: str | None = None
    dry_run: bool = False
    timeout: int | None = None


@dataclass(frozen=True)
class GrepaiRepoScope:
    """Which indexed repos a GrepAI query spans.

    Either an explicit set of configured repo ids, or -- when none are named -- the
    whole workspace, which ``all_repos`` must permit.
    """

    repo_ids: list[str] | None = None
    all_repos: bool = True


WORKSPACE_QUERY_SCOPE = ProviderQueryScope()
"""The default scope: the workspace provider stack, run for real, on the default timeout."""

ALL_INDEXED_REPOS = GrepaiRepoScope()
"""The default GrepAI corpus: every repo the workspace stack has indexed."""


@dataclass(frozen=True)
class GrepaiSearchQuery:
    """One GrepAI semantic search: what to look for, how many hits, and how they come back."""

    query: str
    limit: int = 10
    output_format: str = "json"


@dataclass(frozen=True)
class GrepaiTraceQuery:
    """One GrepAI relationship trace: which edge kind to follow from which symbol.

    ``depth`` bounds the walk and is only meaningful for ``trace_action='graph'``.
    """

    trace_action: str
    symbol: str
    depth: int | None = None
    output_format: str = "json"


def _worktree_provider_targets(config: McpRuntimeConfig) -> list[WorktreeProviderTarget]:
    """Discover worktree provider stacks from `worktrees/<repo>/<group>/provider-runtime`.

    Only stacks whose persisted lifecycle-settings file still exists on disk are
    returned, so half-torn-down worktrees are not offered as query targets.
    """
    base = config.coordination_root / "worktrees"
    targets: list[WorktreeProviderTarget] = []
    if not base.is_dir():
        return targets
    for state_file in sorted(base.glob("*/*/provider-runtime/provider-state.json")):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        isolated = data.get("isolatedProviderSettings")
        raw_path = isolated.get("path") if isinstance(isolated, dict) else None
        repo_name = data.get("repoName")
        if not isinstance(raw_path, str) or not isinstance(repo_name, str):
            continue
        settings_path = Path(raw_path)
        if not settings_path.is_file():
            continue
        targets.append(
            WorktreeProviderTarget(
                repo_id=repo_name,
                task_name=str(data.get("taskName") or ""),
                group=state_file.parent.parent.name,
                settings_path=settings_path,
            )
        )
    return targets


def _resolve_worktree_target(
    config: McpRuntimeConfig,
    *,
    repo_id: str | None,
    worktree: str | None,
) -> WorktreeProviderTarget | None:
    """Hybrid query routing: explicit worktree wins; else a single per-repo stack is
    the default; otherwise (none, or ambiguous) fall back to / require workspace scope.
    """
    targets = _worktree_provider_targets(config)
    if worktree:
        scope = [t for t in targets if repo_id is None or t.repo_id == repo_id]
        matches = [t for t in scope if worktree in t.task_name or worktree in t.group]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(
            f"worktree {worktree!r} matched {len(matches)} provider stacks"
            + (f" for {repo_id!r}" if repo_id else "")
            + f"; available: {_worktree_listing(scope)}"
        )
    if repo_id is None:
        return None
    per_repo = [t for t in targets if t.repo_id == repo_id]
    if len(per_repo) == 1:
        return per_repo[0]
    if len(per_repo) > 1:
        raise ValueError(
            f"multiple worktree provider stacks for {repo_id!r}; "
            f"pass worktree=<name>: {_worktree_listing(per_repo)}"
        )
    return None


def _worktree_listing(targets: list[WorktreeProviderTarget]) -> str:
    return ", ".join(f"{t.task_name or t.group} [{t.repo_id}]" for t in targets) or "(none)"


def _worktree_settings_override(target: WorktreeProviderTarget | None) -> Path | None:
    return target.settings_path if target else None


def _load_worktree_grepai_provider(settings_path: Path) -> dict[str, Any]:
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    providers = data.get("contextProviders", {})
    provider = (
        providers.get("providers", {}).get("grepai-memory") if isinstance(providers, dict) else None
    )
    if not isinstance(provider, dict):
        raise ValueError(
            f"worktree grepai settings missing grepai-memory provider: {settings_path}"
        )
    return provider


def _grepai_target(
    config: McpRuntimeConfig,
    repos: GrepaiRepoScope,
    scope: ProviderQueryScope,
) -> WorktreeProviderTarget | None:
    return _resolve_worktree_target(
        config,
        repo_id=repos.repo_ids[0] if repos.repo_ids and len(repos.repo_ids) == 1 else None,
        worktree=scope.worktree,
    )


def _grepai_operation(operation: str, native_args: list[str]) -> ProviderOperation:
    return ProviderOperation(
        operation=operation,
        required_provider="grepai-memory",
        run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
            service_config,
            action="run",
            native_args=native_args,
        ),
    )


def grepai_search_tool(
    config: McpRuntimeConfig,
    *,
    query: GrepaiSearchQuery,
    repos: GrepaiRepoScope = ALL_INDEXED_REPOS,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    target = _grepai_target(config, repos, scope)
    selection = _grepai_project_selection(
        config,
        repos=repos,
        allow_multiple=True,
        provider_settings=_load_worktree_grepai_provider(target.settings_path) if target else None,
    )
    native_args = [
        "search",
        _required_text(query.query, "query"),
        "--workspace",
        selection.workspace,
        "--limit",
        str(_positive_int(query.limit, "limit")),
        _grepai_output_flag(query.output_format),
    ]
    for project_id in selection.project_ids:
        native_args.extend(["--project", project_id])
    return _provider_operation_result(
        config,
        _grepai_operation("grepai_search", native_args),
        scope=scope,
        target=target,
    )


def grepai_trace_tool(
    config: McpRuntimeConfig,
    *,
    trace: GrepaiTraceQuery,
    repos: GrepaiRepoScope = ALL_INDEXED_REPOS,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    action = _grepai_trace_action(trace.trace_action)
    if trace.depth is not None and action != "graph":
        raise ValueError("grepai_trace depth is only supported for trace_action='graph'")
    target = _grepai_target(config, repos, scope)
    selection = _grepai_project_selection(
        config,
        repos=repos,
        allow_multiple=False,
        provider_settings=_load_worktree_grepai_provider(target.settings_path) if target else None,
    )
    native_args = [
        "trace",
        action,
        _required_text(trace.symbol, "symbol"),
        "--workspace",
        selection.workspace,
        _grepai_output_flag(trace.output_format),
    ]
    if trace.depth is not None:
        native_args.extend(["--depth", str(_positive_int(trace.depth, "depth"))])
    for project_id in selection.project_ids:
        native_args.extend(["--project", project_id])
    return _provider_operation_result(
        config,
        _grepai_operation("grepai_trace", native_args),
        scope=scope,
        target=target,
    )


def cgc_symbol_search_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    name: str,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_symbol_search",
        repo_id=repo_id,
        native_args=["find", "name", _required_text(name, "name")],
        scope=scope,
    )


def cgc_callers_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    file: str | None = None,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    native_args = ["analyze", "callers", _required_text(function, "function")]
    if file:
        native_args.extend(["--file", _required_text(file, "file")])
    return _cgc_run_tool(
        config,
        operation="cgc_callers",
        repo_id=repo_id,
        native_args=native_args,
        scope=scope,
    )


def cgc_callees_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_callees",
        repo_id=repo_id,
        native_args=["analyze", "calls", _required_text(function, "function")],
        scope=scope,
    )


def cgc_dependencies_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    module: str,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_dependencies",
        repo_id=repo_id,
        native_args=["analyze", "deps", _required_text(module, "module")],
        scope=scope,
    )


def cgc_complexity_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str | None = None,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    native_args = ["analyze", "complexity"]
    if function:
        native_args.append(_required_text(function, "function"))
    return _cgc_run_tool(
        config,
        operation="cgc_complexity",
        repo_id=repo_id,
        native_args=native_args,
        scope=scope,
    )


def cgc_visualize_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    port: int = 8000,
    context: str | None = None,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    require_repo(config, repo_id)
    target = _resolve_worktree_target(config, repo_id=repo_id, worktree=scope.worktree)
    return _provider_operation_result(
        config,
        ProviderOperation(
            operation="cgc_visualize",
            required_provider="codegraphcontext-code",
            run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
                service_config,
                lifecycle_service.CgcLifecycleRequest(
                    action="visualize",
                    repo_id=repo_id,
                    port=port,
                    context=context,
                ),
            ),
        ),
        scope=scope,
        target=target,
    )


def _cgc_run_tool(
    config: McpRuntimeConfig,
    *,
    operation: str,
    repo_id: str,
    native_args: list[str],
    scope: ProviderQueryScope,
) -> dict[str, Any]:
    require_repo(config, repo_id)
    target = _resolve_worktree_target(config, repo_id=repo_id, worktree=scope.worktree)
    return _provider_operation_result(
        config,
        ProviderOperation(
            operation=operation,
            required_provider="codegraphcontext-code",
            run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
                service_config,
                lifecycle_service.CgcLifecycleRequest(
                    action="run",
                    repo_id=repo_id,
                    native_args=tuple(native_args),
                ),
            ),
        ),
        scope=scope,
        target=target,
    )


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True)
class GrepaiProjectSelection:
    workspace: str
    project_ids: tuple[str, ...]


def _grepai_project_selection(
    config: McpRuntimeConfig,
    *,
    repos: GrepaiRepoScope,
    allow_multiple: bool,
    provider_settings: dict[str, Any] | None = None,
) -> GrepaiProjectSelection:
    # provider_settings is the worktree's grepai provider when a worktree target was
    # resolved; otherwise fall back to the workspace-scope provider from config.
    if provider_settings is None:
        provider_settings = _grepai_provider_settings(config)
    workspace = _required_text(
        str(provider_settings.get("workspace", "agents-remember-memory")),
        "grepai workspace",
    )
    project_by_repo = _grepai_project_ids_by_repo(config, provider_settings)
    selected_repo_ids = _canonical_repo_ids(config, _normalized_repo_ids(repos.repo_ids))
    if not selected_repo_ids:
        return _grepai_workspace_selection(workspace, repos.all_repos)

    _validate_grepai_project_selection(
        config,
        selected_repo_ids=selected_repo_ids,
        project_by_repo=project_by_repo,
        allow_multiple=allow_multiple,
    )
    return GrepaiProjectSelection(
        workspace=workspace,
        project_ids=tuple(project_by_repo[repo_id] for repo_id in selected_repo_ids),
    )


def _grepai_workspace_selection(workspace: str, all_repos: bool) -> GrepaiProjectSelection:
    if all_repos:
        return GrepaiProjectSelection(workspace=workspace, project_ids=())
    raise ValueError("repo_ids is required when all_repos is false")


def _validate_grepai_project_selection(
    config: McpRuntimeConfig,
    *,
    selected_repo_ids: tuple[str, ...],
    project_by_repo: dict[str, str],
    allow_multiple: bool,
) -> None:
    if not allow_multiple and len(selected_repo_ids) > 1:
        raise ValueError(
            "grepai_trace supports at most one repo_id because GrepAI trace has one --project flag"
        )
    _raise_unknown_grepai_repo_ids(config, selected_repo_ids)
    _raise_missing_grepai_projects(selected_repo_ids, project_by_repo)


def _raise_unknown_grepai_repo_ids(
    config: McpRuntimeConfig,
    selected_repo_ids: tuple[str, ...],
) -> None:
    unknown = [repo_id for repo_id in selected_repo_ids if repo_id not in config.repositories]
    if unknown:
        raise ValueError(
            "unknown repo_ids for MCP configuration: "
            f"{', '.join(unknown)}; configured repo_ids: {', '.join(config.allowed_repo_ids)}"
        )


def _raise_missing_grepai_projects(
    selected_repo_ids: tuple[str, ...],
    project_by_repo: dict[str, str],
) -> None:
    missing_projects = [repo_id for repo_id in selected_repo_ids if repo_id not in project_by_repo]
    if missing_projects:
        raise ValueError(
            "repo_ids are configured but not indexed by grepai-memory: "
            f"{', '.join(missing_projects)}"
        )


def _grepai_provider_settings(config: McpRuntimeConfig) -> dict[str, Any]:
    settings = lifecycle_settings_from_config(config)
    provider = settings.get("contextProviders", {}).get("providers", {}).get("grepai-memory")
    if not isinstance(provider, dict):
        raise ValueError("grepai-memory provider is not configured")
    return provider


def _grepai_project_ids_by_repo(
    config: McpRuntimeConfig,
    provider_settings: dict[str, Any],
) -> dict[str, str]:
    # Value is the project name the watcher actually uses: the runtime normalizes
    # each root's id with `stable_provider_id` (lowercase/slugify), so a configured
    # repo id like `Cobalt` is indexed under project `cobalt`. Passing the raw
    # config id as `--project` matched nothing and returned an empty result.
    return {
        project_id: stable_provider_id(project_id)
        for project_id in _grepai_provider_project_ids(provider_settings)
        if project_id in config.repositories
    }


def _grepai_provider_project_ids(provider_settings: dict[str, Any]) -> tuple[str, ...]:
    roots = provider_settings.get("roots")
    if not isinstance(roots, list):
        raise ValueError("grepai-memory provider roots are not configured")

    project_ids: list[str] = []
    for root in roots:
        project_id = _grepai_root_project_id(root)
        if project_id is not None:
            project_ids.append(project_id)
    return tuple(project_ids)


def _grepai_root_project_id(root: object) -> str | None:
    if not isinstance(root, dict):
        return None
    project_id = root.get("projectId")
    if isinstance(project_id, str):
        return project_id
    return None


def _normalized_repo_ids(repo_ids: list[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for repo_id in repo_ids or []:
        value = _required_text(repo_id, "repo_id")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return tuple(normalized)


def _canonical_repo_ids(config: McpRuntimeConfig, repo_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve user-supplied repo ids to their configured spelling, case-insensitively.

    MCP-configured repo ids can carry uppercase (e.g. ``Cobalt``). Accept any casing
    and resolve to the configured key so the project filter, validation, and
    ``--project`` selection all agree. Unmatched ids pass through unchanged so the
    unknown-repo error still reports them as the developer typed them.
    """

    by_lower = {repo_id.lower(): repo_id for repo_id in config.repositories}
    return tuple(by_lower.get(repo_id.lower(), repo_id) for repo_id in repo_ids)


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _grepai_output_flag(output_format: str) -> str:
    value = _required_text(output_format, "output_format").lower()
    if value == "json":
        return "--json"
    if value == "toon":
        return "--toon"
    raise ValueError("output_format must be json or toon")


def _grepai_trace_action(trace_action: str) -> str:
    value = _required_text(trace_action, "trace_action")
    if value not in {"callers", "callees", "graph"}:
        raise ValueError("grepai_trace trace_action must be callers, callees, or graph")
    return value


def _provider_invalidate_indexes(config: McpRuntimeConfig, *, dry_run: bool) -> dict[str, Any]:
    """Delete and rebuild every provider index from scratch (full re-embed + full graph re-index).

    This is the destructive path formerly called 'refresh'. It force-rebuilds the grepai
    embeddings and the CGC graph for all enabled repos, so it is slow and CPU-heavy. Use
    'restart' instead when the goal is only to bring a sleeping watcher back up to date.
    """
    steps: list[dict[str, Any]] = []
    scope = ProviderQueryScope(dry_run=dry_run)
    if "grepai-memory" in config.providers:
        steps.append(
            _provider_operation_result(
                config,
                ProviderOperation(
                    operation="provider_watchers",
                    run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
                        service_config,
                        action="refresh",
                    ),
                ),
                scope=scope,
            )
        )
    if "codegraphcontext-code" in config.providers:
        steps.append(
            _provider_operation_result(
                config,
                ProviderOperation(
                    operation="provider_watchers",
                    run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
                        service_config,
                        lifecycle_service.CgcLifecycleRequest(action="refresh-all"),
                    ),
                ),
                scope=scope,
            )
        )
    return {
        "ok": bool(steps) and all(step.get("ok") for step in steps),
        "operation": "provider_watchers",
        "action": "invalidate-indexes",
        "steps": steps,
    }


ProviderLifecycleRunner = Callable[
    [lifecycle_service.ProviderLifecycleServiceConfig],
    dict[str, Any],
]


@dataclass(frozen=True)
class ProviderOperation:
    """One provider-CLI call behind a tool.

    ``operation`` is the tool name the result is stamped with, ``run`` performs the
    call against a resolved lifecycle-service config, and ``required_provider`` names
    the provider whose on-disk launch authority must be armed first. A ``None``
    ``required_provider`` marks an operation that needs no launch authority (stop and
    status are always legal), so no authority reload happens for it.
    """

    operation: str
    run: ProviderLifecycleRunner
    required_provider: str | None = None


def _provider_operation_result(
    config: McpRuntimeConfig,
    operation: ProviderOperation,
    *,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
    target: WorktreeProviderTarget | None = None,
) -> dict[str, Any]:
    settings_path_override = _worktree_settings_override(target)
    if operation.required_provider is not None:
        # Containment R1 (260707-HFX-L1): query/run ops spin one-shot runner
        # containers, and a worktree's persisted settings file is stamped
        # enabled:true forever — neither is launch authority. The live on-disk
        # providers map gates both; with an override the worktree's own stack
        # settings still drive the run, but only under an armed authority.
        # Review note: the SPECIFIC provider must be armed, not just any.
        live = require_provider_launch_authority(config, operation=operation.operation)
        if operation.required_provider not in live.providers:
            raise ConfigError(
                f"{operation.operation} refused: provider {operation.required_provider!r} is not "
                f"enabled in the on-disk authority settings ({config.config_path}) "
                "(containment R1)"
            )
        if settings_path_override is None:
            config = live
    # When a worktree target resolves, run against its already-persisted lifecycle
    # settings (do NOT delete that file). Otherwise write a temp workspace settings
    # file and clean it up.
    if settings_path_override is not None:
        settings_path = settings_path_override
        owns_settings = False
    else:
        settings_path = write_lifecycle_settings(config)
        owns_settings = True
    try:
        service_config = lifecycle_service.ProviderLifecycleServiceConfig(
            coordination_root=config.coordination_root,
            settings_path=settings_path,
            dry_run=scope.dry_run,
            timeout=scope.timeout or DEFAULT_DOCKER_CONTROL_SECONDS,
        )
        data = operation.run(service_config)
        payload = {**data, "operation": operation.operation, "ok": bool(data.get("ok"))}
        if not owns_settings:
            payload["worktreeScoped"] = True
        return payload
    finally:
        if owns_settings:
            settings_path.unlink(missing_ok=True)
