"""Controllers for provider-facing MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.providers import lifecycle_service
from agents_remember.providers.current_state import write_current_provider_state
from agents_remember.providers.integrity import check_provider_runner_integrity
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
    if action not in {"status", "start", "stop", "restart", "refresh", "shutdown-all"}:
        raise ValueError("action must be status, start, stop, restart, refresh, or shutdown-all")
    if action == "restart":
        return {
            "ok": True,
            "operation": "provider_watchers",
            "action": "restart",
            "steps": [
                _provider_watchers_once(config, "stop", dry_run=dry_run),
                _provider_watchers_once(config, "start", dry_run=dry_run),
            ],
        }
    if action == "refresh":
        return _provider_refresh(config, dry_run=dry_run)
    effective_dry_run = False if action == "status" else dry_run
    return _provider_watchers_once(config, action, dry_run=effective_dry_run)


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
) -> dict[str, Any]:
    selection = _grepai_project_selection(
        config,
        repo_ids=repo_ids,
        all_repos=all_repos,
        allow_multiple=True,
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
) -> dict[str, Any]:
    action = _grepai_trace_action(trace_action)
    if depth is not None and action != "graph":
        raise ValueError("grepai_trace depth is only supported for trace_action='graph'")
    selection = _grepai_project_selection(
        config,
        repo_ids=repo_ids,
        all_repos=all_repos,
        allow_multiple=False,
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
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_symbol_search",
        repo_id=repo_id,
        native_args=["find", "name", _required_text(name, "name")],
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_callers_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    file: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
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
    )


def cgc_callees_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    dry_run: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_callees",
        repo_id=repo_id,
        native_args=["analyze", "calls", _required_text(function, "function")],
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_dependencies_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    module: str,
    dry_run: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_dependencies",
        repo_id=repo_id,
        native_args=["analyze", "dependencies", _required_text(module, "module")],
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_complexity_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
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
    )


def cgc_visualize_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    port: int = 8000,
    context: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    _repo(config, repo_id)
    return _provider_operation_result(
        config,
        operation="cgc_visualize",
        dry_run=dry_run,
        timeout=timeout,
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
) -> dict[str, Any]:
    _repo(config, repo_id)
    return _provider_operation_result(
        config,
        operation=operation,
        dry_run=dry_run,
        timeout=timeout,
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
) -> GrepaiProjectSelection:
    provider_settings = _grepai_provider_settings(config)
    workspace = _required_text(
        str(provider_settings.get("workspace", "agents-remember-memory")),
        "grepai workspace",
    )
    project_by_repo = _grepai_project_ids_by_repo(config, provider_settings)
    selected_repo_ids = _normalized_repo_ids(repo_ids)
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
    return {
        project_id: project_id
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


def _repo(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    try:
        return config.repositories[repo_id]
    except KeyError as error:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ValueError(
            f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}"
        ) from error


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


def _provider_refresh(config: McpRuntimeConfig, *, dry_run: bool) -> dict[str, Any]:
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
        "ok": all(step.get("ok") for step in steps),
        "operation": "provider_watchers",
        "action": "refresh",
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
) -> dict[str, Any]:
    integrity = check_provider_runner_integrity(config)
    if integrity.get("ok") is False:
        return _provider_integrity_block_payload(operation, integrity)

    settings_path = write_lifecycle_settings(config)
    try:
        service_config = lifecycle_service.ProviderLifecycleServiceConfig(
            coordination_root=config.coordination_root,
            settings_path=settings_path,
            dry_run=dry_run,
            timeout=timeout or config.timeout_caps.get("providerSeconds", 120),
        )
        data = run(service_config)
        return {"operation": operation, "ok": bool(data.get("ok")), **data}
    finally:
        settings_path.unlink(missing_ok=True)


def _provider_integrity_block_payload(operation: str, integrity: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": operation,
        "ok": False,
        "state": "runnerIntegrityFailed",
        "error": "provider runner integrity check failed; run runtime_install before provider operations",
        "integrity": integrity,
        "recoveryActions": [
            {
                "action": "runtime_install",
                "reason": "provider runner files changed or were not recorded since install",
            }
        ],
    }
