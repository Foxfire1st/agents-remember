"""Shared payload-builder primitives for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.models.tokens import finalize_payload_tokens
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS

TRANSPORT = "stdio"
PUBLIC_TOOLS = (
    "ping",
    "server_info",
    "context_packet",
    "runtime_install",
    "resolve_context",
    "drift_check",
    "memory_quality_check",
    "route_index_refresh",
    "memory_init",
    "skills_install",
    "provider_status",
    "provider_diagnostics",
    "grepai_search",
    "grepai_trace",
    "cgc_symbol_search",
    "cgc_callers",
    "cgc_callees",
    "cgc_dependencies",
    "cgc_complexity",
    "provider_watchers",
    "cgc_visualize",
    "worktree_start",
    "worktree_attach",
    "worktree_status",
    "worktree_closeout_preview",
    "worktree_closeout_apply",
    "direct_closeout_preview",
    "direct_closeout_apply",
    "worktree_integrate",
    "worktree_cleanup",
    "memory_baseline_status",
    "memory_baseline_adopt",
    "memory_carryover_plan",
    "memory_carryover_apply",
    "codex_benchmark_prepare",
    "codex_benchmark_run",
)
RESERVED_TOOLS: tuple[str, ...] = ()


def _tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = PUBLIC_TOOL_RESPONSE_MODELS[tool_name]
    dumped = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
    return finalize_payload_tokens(dumped)
