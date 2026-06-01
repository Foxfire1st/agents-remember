"""Provider status/diagnostics and CGC/GrepAI query payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.provider_tools import (
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
)
from agents_remember.providers.lifecycle.log_capture import summarize_command_logs

from ..config import McpRuntimeConfig
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
    return _tool_payload(
        "provider_diagnostics",
        provider_diagnostics_tool(config, detail_limit=detail_limit),
    )


def provider_watchers_payload(
    config: McpRuntimeConfig,
    *,
    action: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "provider_watchers",
        summarize_command_logs(provider_watchers_tool(config, action=action, dry_run=dry_run)),
    )


def grepai_search_payload(
    config: McpRuntimeConfig,
    query: str,
    *,
    repo_ids: list[str] | None = None,
    all_repos: bool = True,
    limit: int = 10,
    output_format: str = "json",
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "grepai_search",
        grepai_search_tool(
            config,
            query=query,
            repo_ids=repo_ids,
            all_repos=all_repos,
            limit=limit,
            output_format=output_format,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def grepai_trace_payload(
    config: McpRuntimeConfig,
    trace_action: str,
    symbol: str,
    *,
    repo_ids: list[str] | None = None,
    all_repos: bool = True,
    depth: int | None = None,
    output_format: str = "json",
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "grepai_trace",
        grepai_trace_tool(
            config,
            trace_action=trace_action,
            symbol=symbol,
            repo_ids=repo_ids,
            all_repos=all_repos,
            depth=depth,
            output_format=output_format,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def cgc_symbol_search_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    name: str,
    *,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_symbol_search",
        cgc_symbol_search_tool(
            config,
            repo_id=repo_id,
            name=name,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def cgc_callers_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    function: str,
    *,
    file: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_callers",
        cgc_callers_tool(
            config,
            repo_id=repo_id,
            function=function,
            file=file,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def cgc_callees_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    function: str,
    *,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_callees",
        cgc_callees_tool(
            config,
            repo_id=repo_id,
            function=function,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def cgc_dependencies_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    module: str,
    *,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_dependencies",
        cgc_dependencies_tool(
            config,
            repo_id=repo_id,
            module=module,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def cgc_complexity_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    function: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_complexity",
        cgc_complexity_tool(
            config,
            repo_id=repo_id,
            function=function,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )


def cgc_visualize_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    port: int = 8000,
    context: str | None = None,
    dry_run: bool = False,
    timeout: int | None = None,
    worktree: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "cgc_visualize",
        cgc_visualize_tool(
            config,
            repo_id=repo_id,
            port=port,
            context=context,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        ),
    )
