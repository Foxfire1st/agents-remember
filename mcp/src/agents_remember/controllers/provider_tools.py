"""Controllers for provider-facing MCP tools."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.controllers._guards import require_repo
from agents_remember.mcp.config import (
    DEFAULT_DOCKER_CONTROL_SECONDS,
    ConfigError,
    McpRuntimeConfig,
    require_provider_launch_authority,
)
from agents_remember.providers import lifecycle_service
from agents_remember.providers.current_state import write_current_provider_state
from agents_remember.providers.identity import stable_provider_id
from agents_remember.providers.settings import (
    lifecycle_settings_from_config,
    write_lifecycle_settings,
)
from agents_remember.providers.status import (
    provider_diagnostics_packet,
    provider_status_packet,
)


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
                _provider_watchers_once(config, "stop", dry_run=dry_run),
                _provider_watchers_once(config, "start", dry_run=dry_run),
            ],
        }
    if action == "invalidate-indexes":
        return _provider_invalidate_indexes(config, dry_run=dry_run)
    effective_dry_run = False if action == "status" else dry_run
    return _provider_watchers_once(config, action, dry_run=effective_dry_run)


@dataclass(frozen=True)
class WorktreeProviderTarget:
    """A worktree's isolated provider stack, located from its persisted state file."""

    repo_id: str
    task_name: str
    group: str
    settings_path: Path


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
    provider = providers.get("providers", {}).get("grepai-memory") if isinstance(providers, dict) else None
    if not isinstance(provider, dict):
        raise ValueError(f"worktree grepai settings missing grepai-memory provider: {settings_path}")
    return provider


def grepai_search_tool(
    config: McpRuntimeConfig,
    *,
    query: str,
    repo_ids: list[str] | None = None,
    all_repos: bool = True,
    limit: int = 10,
    output_format: str = "json",
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    target = _resolve_worktree_target(
        config,
        repo_id=repo_ids[0] if repo_ids and len(repo_ids) == 1 else None,
        worktree=worktree,
    )
    selection = _grepai_project_selection(
        config,
        repo_ids=repo_ids,
        all_repos=all_repos,
        allow_multiple=True,
        provider_settings=_load_worktree_grepai_provider(target.settings_path) if target else None,
    )
    native_args = [
        "search",
        _required_text(query, "query"),
        "--workspace",
        selection.workspace,
        "--limit",
        str(_positive_int(limit, "limit")),
        _grepai_output_flag(output_format),
    ]
    for project_id in selection.project_ids:
        native_args.extend(["--project", project_id])
    return _provider_operation_result(
        config,
        operation="grepai_search",
        dry_run=dry_run,
        timeout=timeout,
        launch_capable=True,
        launch_capable_provider="grepai-memory",
        settings_path_override=_worktree_settings_override(target),
        run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
            service_config,
            action="run",
            native_args=native_args,
        ),
    )


def grepai_trace_tool(
    config: McpRuntimeConfig,
    *,
    trace_action: str,
    symbol: str,
    repo_ids: list[str] | None = None,
    all_repos: bool = True,
    depth: int | None = None,
    output_format: str = "json",
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    action = _grepai_trace_action(trace_action)
    if depth is not None and action != "graph":
        raise ValueError("grepai_trace depth is only supported for trace_action='graph'")
    target = _resolve_worktree_target(
        config,
        repo_id=repo_ids[0] if repo_ids and len(repo_ids) == 1 else None,
        worktree=worktree,
    )
    selection = _grepai_project_selection(
        config,
        repo_ids=repo_ids,
        all_repos=all_repos,
        allow_multiple=False,
        provider_settings=_load_worktree_grepai_provider(target.settings_path) if target else None,
    )
    native_args = [
        "trace",
        action,
        _required_text(symbol, "symbol"),
        "--workspace",
        selection.workspace,
        _grepai_output_flag(output_format),
    ]
    if depth is not None:
        native_args.extend(["--depth", str(_positive_int(depth, "depth"))])
    for project_id in selection.project_ids:
        native_args.extend(["--project", project_id])
    return _provider_operation_result(
        config,
        operation="grepai_trace",
        dry_run=dry_run,
        timeout=timeout,
        launch_capable=True,
        launch_capable_provider="grepai-memory",
        settings_path_override=_worktree_settings_override(target),
        run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
            service_config,
            action="run",
            native_args=native_args,
        ),
    )


def cgc_symbol_search_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    name: str,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_symbol_search",
        repo_id=repo_id,
        native_args=["find", "name", _required_text(name, "name")],
        dry_run=dry_run,
        timeout=timeout,
        worktree=worktree,
    )


def cgc_callers_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    file: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    native_args = ["analyze", "callers", _required_text(function, "function")]
    if file:
        native_args.extend(["--file", _required_text(file, "file")])
    return _cgc_run_tool(
        config,
        operation="cgc_callers",
        repo_id=repo_id,
        native_args=native_args,
        dry_run=dry_run,
        timeout=timeout,
        worktree=worktree,
    )


def cgc_callees_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_callees",
        repo_id=repo_id,
        native_args=["analyze", "calls", _required_text(function, "function")],
        dry_run=dry_run,
        timeout=timeout,
        worktree=worktree,
    )


def cgc_dependencies_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    module: str,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_dependencies",
        repo_id=repo_id,
        native_args=["analyze", "deps", _required_text(module, "module")],
        dry_run=dry_run,
        timeout=timeout,
        worktree=worktree,
    )


def cgc_complexity_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    native_args = ["analyze", "complexity"]
    if function:
        native_args.append(_required_text(function, "function"))
    return _cgc_run_tool(
        config,
        operation="cgc_complexity",
        repo_id=repo_id,
        native_args=native_args,
        dry_run=dry_run,
        timeout=timeout,
        worktree=worktree,
    )


def cgc_visualize_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    port: int = 8000,
    context: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    require_repo(config, repo_id)
    target = _resolve_worktree_target(config, repo_id=repo_id, worktree=worktree)
    return _provider_operation_result(
        config,
        operation="cgc_visualize",
        dry_run=dry_run,
        timeout=timeout,
        launch_capable=True,
        launch_capable_provider="codegraphcontext-code",
        settings_path_override=_worktree_settings_override(target),
        run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
            service_config,
            action="visualize",
            repo_id=repo_id,
            port=port,
            context=context,
        ),
    )


def _cgc_run_tool(
    config: McpRuntimeConfig,
    *,
    operation: str,
    repo_id: str,
    native_args: list[str],
    dry_run: bool,
    timeout: int | None,
    worktree: str | None = None,
) -> dict[str, Any]:
    require_repo(config, repo_id)
    target = _resolve_worktree_target(config, repo_id=repo_id, worktree=worktree)
    return _provider_operation_result(
        config,
        operation=operation,
        dry_run=dry_run,
        timeout=timeout,
        launch_capable=True,
        launch_capable_provider="codegraphcontext-code",
        settings_path_override=_worktree_settings_override(target),
        run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
            service_config,
            action="run",
            repo_id=repo_id,
            native_args=native_args,
        ),
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
    repo_ids: list[str] | None,
    all_repos: bool,
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
    selected_repo_ids = _canonical_repo_ids(config, _normalized_repo_ids(repo_ids))
    if not selected_repo_ids:
        return _grepai_workspace_selection(workspace, all_repos)

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


def _canonical_repo_ids(
    config: McpRuntimeConfig, repo_ids: tuple[str, ...]
) -> tuple[str, ...]:
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


def _provider_watchers_once(
    config: McpRuntimeConfig, action: str, *, dry_run: bool
) -> dict[str, Any]:
    payload = _provider_operation_result(
        config,
        operation="provider_watchers",
        dry_run=dry_run,
        timeout=None,
        run=lambda service_config: lifecycle_service.run_watchers_lifecycle(
            service_config,
            action=action,
        ),
    )
    if action == "status" and payload.get("provider") == "watchers":
        current_state = write_current_provider_state(config, payload)
        payload["currentStateFile"] = current_state["path"]
        payload["currentState"] = current_state["state"]
        payload["state"] = current_state["state"]["state"]
    return payload


def _provider_invalidate_indexes(config: McpRuntimeConfig, *, dry_run: bool) -> dict[str, Any]:
    """Delete and rebuild every provider index from scratch (full re-embed + full graph re-index).

    This is the destructive path formerly called 'refresh'. It force-rebuilds the grepai
    embeddings and the CGC graph for all enabled repos, so it is slow and CPU-heavy. Use
    'restart' instead when the goal is only to bring a sleeping watcher back up to date.
    """
    steps: list[dict[str, Any]] = []
    if "grepai-memory" in config.providers:
        steps.append(
            _provider_operation_result(
                config,
                operation="provider_watchers",
                dry_run=dry_run,
                timeout=None,
                run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
                    service_config,
                    action="refresh",
                ),
            )
        )
    if "codegraphcontext-code" in config.providers:
        steps.append(
            _provider_operation_result(
                config,
                operation="provider_watchers",
                dry_run=dry_run,
                timeout=None,
                run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
                    service_config,
                    action="refresh-all",
                ),
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


def _provider_operation_result(
    config: McpRuntimeConfig,
    *,
    operation: str,
    dry_run: bool = False,
    timeout: int | None = None,
    run: ProviderLifecycleRunner,
    settings_path_override: Path | None = None,
    launch_capable: bool = False,
    launch_capable_provider: str | None = None,
) -> dict[str, Any]:
    if launch_capable:
        # Containment R1 (260707-HFX-L1): query/run ops spin one-shot runner
        # containers, and a worktree's persisted settings file is stamped
        # enabled:true forever — neither is launch authority. The live on-disk
        # providers map gates both; with an override the worktree's own stack
        # settings still drive the run, but only under an armed authority.
        # Review note: the SPECIFIC provider must be armed, not just any.
        live = require_provider_launch_authority(config, operation=operation)
        if launch_capable_provider and launch_capable_provider not in live.providers:
            raise ConfigError(
                f"{operation} refused: provider {launch_capable_provider!r} is not enabled "
                f"in the on-disk authority settings ({config.config_path}) (containment R1)"
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
            dry_run=dry_run,
            timeout=timeout or DEFAULT_DOCKER_CONTROL_SECONDS,
        )
        data = run(service_config)
        payload = {**data, "operation": operation, "ok": bool(data.get("ok"))}
        if not owns_settings:
            payload["worktreeScoped"] = True
        return payload
    finally:
        if owns_settings:
            settings_path.unlink(missing_ok=True)
