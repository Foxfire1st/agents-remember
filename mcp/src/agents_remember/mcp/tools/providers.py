"""Provider status/diagnostics and CGC/GrepAI query payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.application.provider_tools import (
    ALL_INDEXED_REPOS,
    WORKSPACE_QUERY_SCOPE,
    GrepaiRepoScope,
    GrepaiSearchQuery,
    GrepaiTraceQuery,
    ProviderQueryScope,
    cgc_callees_tool,
    cgc_callers_tool,
    cgc_complexity_tool,
    cgc_dependencies_tool,
    cgc_symbol_search_tool,
    cgc_visualize_tool,
    grepai_search_tool,
    grepai_trace_tool,
    provider_diagnostics_tool,
    provider_status_tool,
    provider_watchers_tool,
    summarize_provider_watchers_result,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.tool_reports import write_tool_report

from .base import _tool_payload


def provider_status_payload(config: McpRuntimeConfig, *, detail_limit: int = 20) -> dict[str, Any]:
    return _tool_payload(
        "provider_status",
        provider_status_tool(config, detail_limit=detail_limit),
    )


def provider_diagnostics_payload(
    config: McpRuntimeConfig,
    *,
    detail_limit: int = 20,
) -> dict[str, Any]:
    full = provider_diagnostics_tool(config, detail_limit=detail_limit)
    report_path = write_tool_report(
        config.coordination_root, "provider_diagnostics", full, label="diagnostics"
    )
    return _tool_payload(
        "provider_diagnostics",
        compact_diagnostics_payload(full, report_path.as_posix()),
    )


def compact_diagnostics_payload(full: dict[str, Any], report_path: str) -> dict[str, Any]:
    """Keep the diagnosis, file the evidence.

    ``rawStatus`` trees and the ``currentState`` body (a verbatim copy of the
    already-on-disk current.json) were thousands of tokens per call; the report
    carries them, the response carries the path."""
    compact = {
        key: value for key, value in full.items() if key not in {"rawStatus", "currentState"}
    }
    compact["items"] = [
        {key: value for key, value in item.items() if key != "rawStatus"}
        for item in full.get("items", [])
        if isinstance(item, dict)
    ]
    compact["reportPath"] = report_path
    return compact


def provider_watchers_payload(
    config: McpRuntimeConfig,
    *,
    action: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    full = provider_watchers_tool(config, action=action, dry_run=dry_run)
    report_path = write_tool_report(
        config.coordination_root, "provider_watchers", full, label=action
    )
    summarized = summarize_provider_watchers_result(full)
    return _tool_payload(
        "provider_watchers",
        compact_watchers_payload(summarized, report_path.as_posix()),
    )


def compact_watchers_payload(full: dict[str, Any], report_path: str) -> dict[str, Any]:
    """Per-provider outcomes inline; raw provider payloads in the report."""
    compact = {
        key: value for key, value in full.items() if key not in {"steps", "results", "currentState"}
    }
    if "steps" in full:
        compact["steps"] = [_compact_watcher_step(step) for step in full["steps"]]
    elif "results" in full:
        compact["results"] = _compact_watcher_results(full.get("results"))
    compact["reportPath"] = report_path
    return compact


def _compact_watcher_step(step: Any) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {"ok": None}
    compact = {
        key: step[key]
        for key in ("provider", "action", "ok", "partial", "dryRun", "state", "currentStateFile")
        if key in step
    }
    compact["results"] = _compact_watcher_results(step.get("results"))
    return compact


def _compact_watcher_results(results: Any) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    return [
        {
            key: result[key]
            for key in ("provider", "action", "ok", "repoId", "containerName", "workspace")
            if key in result
        }
        for result in results
        if isinstance(result, dict)
    ]


def grepai_search_payload(
    config: McpRuntimeConfig,
    query: GrepaiSearchQuery,
    *,
    repos: GrepaiRepoScope = ALL_INDEXED_REPOS,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "grepai_search",
        grepai_search_tool(config, query=query, repos=repos, scope=scope),
    )


def grepai_trace_payload(
    config: McpRuntimeConfig,
    trace: GrepaiTraceQuery,
    *,
    repos: GrepaiRepoScope = ALL_INDEXED_REPOS,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "grepai_trace",
        grepai_trace_tool(config, trace=trace, repos=repos, scope=scope),
    )


def cgc_symbol_search_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    name: str,
    *,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_symbol_search",
        cgc_symbol_search_tool(config, repo_id=repo_id, name=name, scope=scope),
    )


def cgc_callers_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    function: str,
    *,
    file: str | None = None,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_callers",
        cgc_callers_tool(config, repo_id=repo_id, function=function, file=file, scope=scope),
    )


def cgc_callees_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    function: str,
    *,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_callees",
        cgc_callees_tool(config, repo_id=repo_id, function=function, scope=scope),
    )


def cgc_dependencies_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    module: str,
    *,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_dependencies",
        cgc_dependencies_tool(config, repo_id=repo_id, module=module, scope=scope),
    )


def cgc_complexity_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    function: str | None = None,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_complexity",
        cgc_complexity_tool(config, repo_id=repo_id, function=function, scope=scope),
    )


def cgc_visualize_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    port: int = 8000,
    context: str | None = None,
    scope: ProviderQueryScope = WORKSPACE_QUERY_SCOPE,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_visualize",
        cgc_visualize_tool(config, repo_id=repo_id, port=port, context=context, scope=scope),
    )
