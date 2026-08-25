"""Memory, drift, route-index, baseline, and carryover payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.application.memory_quality.controller import (
    poll_memory_quality_request,
    run_memory_quality_request,
    start_memory_quality_request,
)
from agents_remember.application.memory_tools import (
    DEFAULT_CARRYOVER_MESSAGES,
    DEFAULT_CITATION_OPERATION_SCOPE,
    DEFAULT_MEMORY_BRANCHES,
    CarryoverCommitMessages,
    CarryoverSelection,
    CitationOperationScope,
    MemoryBranches,
    citation_fix_tool,
    drift_check_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_carryover_apply_tool,
    memory_carryover_plan_tool,
    memory_init_tool,
    route_index_refresh_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.tool_reports import write_tool_report
from agents_remember.models.memory import (
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
    MemoryQualitySyncRequest,
)

from .base import _tool_payload


def drift_check_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    detail_limit: int = 50,
    contract_path: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "drift_check",
        drift_check_tool(
            config,
            repo_id=repo_id,
            detail_limit=detail_limit,
            contract_path=contract_path,
        ),
    )


def memory_quality_check_payload(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_quality_check",
        run_memory_quality_request(config, request),
    )


def memory_quality_check_start_payload(
    config: McpRuntimeConfig,
    request: MemoryQualityStartRequest,
) -> dict[str, Any]:
    """Start one bounded request; a successful admission carries its run id."""

    return _tool_payload(
        "memory_quality_check",
        start_memory_quality_request(config, request),
    )


def memory_quality_check_poll_payload(
    config: McpRuntimeConfig,
    request: MemoryQualityPollRequest,
) -> dict[str, Any]:
    """Poll one started check through the repository recorded on its run."""

    return _tool_payload(
        "memory_quality_check",
        poll_memory_quality_request(config, request),
    )


def citation_fix_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    contract_path: str,
    operation_scope: CitationOperationScope = DEFAULT_CITATION_OPERATION_SCOPE,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "citation_fix",
        citation_fix_tool(
            config,
            repo_id=repo_id,
            contract_path=contract_path,
            dry_run=dry_run,
            operation_scope=operation_scope,
        ),
    )


def route_index_refresh_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = False,
    contract_path: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "route_index_refresh",
        route_index_refresh_tool(
            config,
            repo_id=repo_id,
            dry_run=dry_run,
            contract_path=contract_path,
        ),
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
    branches: MemoryBranches = DEFAULT_MEMORY_BRANCHES,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "memory_baseline_adopt",
        memory_baseline_adopt_tool(
            config,
            repo_id=repo_id,
            accept_drift=accept_drift,
            branches=branches,
            dry_run=dry_run,
        ),
    )


MAX_INLINE_CARRYOVER_PATHS = 25


def _capped_paths(paths: list[str]) -> list[str]:
    if len(paths) <= MAX_INLINE_CARRYOVER_PATHS:
        return paths
    overflow = len(paths) - MAX_INLINE_CARRYOVER_PATHS
    return [*paths[:MAX_INLINE_CARRYOVER_PATHS], f"... (+{overflow} more in report)"]


def compact_carryover_payload(full: dict[str, Any], report_path: str) -> dict[str, Any]:
    """Keep the decisions, file the evidence.

    Every candidate record repeats two onboarding paths derivable from
    ``source_path`` plus the memory roots, and the same evidence/reason strings
    per entry; apply additionally duplicates every candidate verbatim in
    ``carried``. The response keeps per-decision ``source_path`` lists (the
    facts the model acts on), capped per decision so giant carryovers stay
    under budget; the report keeps the full records."""
    compact = {key: value for key, value in full.items() if key not in {"candidates", "carried"}}
    decisions: dict[str, list[str]] = {}
    for candidate in full.get("candidates", []):
        decisions.setdefault(str(candidate.get("decision")), []).append(
            str(candidate.get("source_path"))
        )
    compact["decisions"] = {decision: _capped_paths(paths) for decision, paths in decisions.items()}
    if "carried" in full:
        compact["carriedPaths"] = _capped_paths(
            [str(candidate.get("source_path")) for candidate in full["carried"]]
        )
    compact["reportPath"] = report_path
    return compact


def memory_carryover_plan_payload(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
) -> dict[str, Any]:
    full = memory_carryover_plan_tool(config, selection)
    report_path = write_tool_report(
        config.coordination_root, "memory_carryover_plan", full, label="plan"
    )
    return _tool_payload(
        "memory_carryover_plan",
        compact_carryover_payload(full, report_path.as_posix()),
    )


def memory_carryover_apply_payload(
    config: McpRuntimeConfig,
    selection: CarryoverSelection,
    *,
    intent_note: str,
    include_review_required: list[str] | None = None,
    messages: CarryoverCommitMessages = DEFAULT_CARRYOVER_MESSAGES,
) -> dict[str, Any]:
    full = memory_carryover_apply_tool(
        config,
        selection,
        intent_note=intent_note,
        include_review_required=include_review_required,
        messages=messages,
    )
    report_path = write_tool_report(
        config.coordination_root, "memory_carryover_apply", full, label="apply"
    )
    return _tool_payload(
        "memory_carryover_apply",
        compact_carryover_payload(full, report_path.as_posix()),
    )
