"""Memory, drift, route-index, baseline, and carryover payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.memory_tools import (
    drift_check_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_carryover_apply_tool,
    memory_carryover_plan_tool,
    memory_init_tool,
    memory_quality_check_tool,
    route_index_refresh_tool,
)

from ..config import McpRuntimeConfig
from .base import _tool_payload


def drift_check_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    detail_limit: int = 50,
) -> dict[str, Any]:
    return _tool_payload(
        "drift_check",
        drift_check_tool(config, repo_id=repo_id, detail_limit=detail_limit),
    )


def memory_quality_check_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    checks: list[str] | None = None,
    detail_limit: int = 50,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_quality_check",
        memory_quality_check_tool(
            config,
            repo_id=repo_id,
            checks=checks,
            detail_limit=detail_limit,
        ),
    )


def route_index_refresh_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "route_index_refresh",
        route_index_refresh_tool(config, repo_id=repo_id, dry_run=dry_run),
    )


def memory_init_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = False,
    initialize_git: bool = True,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_init",
        memory_init_tool(
            config,
            repo_id=repo_id,
            dry_run=dry_run,
            initialize_git=initialize_git,
        ),
    )


def memory_baseline_status_payload(config: McpRuntimeConfig, repo_id: str) -> dict[str, Any]:
    return _tool_payload(
        "memory_baseline_status",
        memory_baseline_status_tool(config, repo_id=repo_id),
    )


def memory_baseline_adopt_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    accept_drift: bool = False,
    source_branch: str | None = None,
    work_branch: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_baseline_adopt",
        memory_baseline_adopt_tool(
            config,
            repo_id=repo_id,
            accept_drift=accept_drift,
            source_branch=source_branch,
            work_branch=work_branch,
            dry_run=dry_run,
        ),
    )


def memory_carryover_plan_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    source_memory: str,
    official_code_ref: str,
    source_code_ref: str,
    old_base: str,
    *,
    replace_existing: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_carryover_plan",
        memory_carryover_plan_tool(
            config,
            repo_id=repo_id,
            source_memory=source_memory,
            official_code_ref=official_code_ref,
            source_code_ref=source_code_ref,
            old_base=old_base,
            replace_existing=replace_existing,
        ),
    )


def memory_carryover_apply_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    source_memory: str,
    official_code_ref: str,
    source_code_ref: str,
    old_base: str,
    intent_note: str,
    *,
    replace_existing: bool = False,
    include_review_required: list[str] | None = None,
    memory_commit_message: str = "Carry over landed branch memory",
    ledger_commit_message: str = "Record branch memory carryover",
) -> dict[str, Any]:
    return _tool_payload(
        "memory_carryover_apply",
        memory_carryover_apply_tool(
            config,
            repo_id=repo_id,
            source_memory=source_memory,
            official_code_ref=official_code_ref,
            source_code_ref=source_code_ref,
            old_base=old_base,
            intent_note=intent_note,
            replace_existing=replace_existing,
            include_review_required=include_review_required,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        ),
    )
