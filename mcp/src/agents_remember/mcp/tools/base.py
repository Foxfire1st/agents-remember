"""Shared payload-builder primitives for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.models.tokens import finalize_payload_tokens
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import ambient

TRANSPORT = "stdio"
PUBLIC_TOOLS = (
    "ping",
    "server_info",
    "context_packet",
    "read_ar_files",
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
    "worktree_sync",
    "worktree_closeout_preview",
    "worktree_closeout_apply",
    "worktree_integrate",
    "worktree_cleanup",
    "worktree_abandon",
    "memory_baseline_status",
    "memory_baseline_adopt",
    "memory_carryover_plan",
    "memory_carryover_apply",
    "codex_benchmark_prepare",
    "codex_benchmark_run",
    "lifecycle_start",
    "lifecycle_block",
    "lifecycle_resume",
    "lifecycle_end",
    "switch_lifecycle",
    "lifecycle_phase",
    "task_doc",
    "gate_create",
    "gate_decide",
    "gate_wait",
    "gate_list",
    "operator_inbox_post",
    "operator_inbox_poll",
    "operator_inbox_consume",
)
RESERVED_TOOLS: tuple[str, ...] = ()


def _tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = PUBLIC_TOOL_RESPONSE_MODELS[tool_name]
    dumped = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
    finalized = finalize_payload_tokens(dumped)
    # The choke point: tag every tool call onto the active lifecycle by
    # construction. A lifecycle-less call is dropped, never misattributed.
    amb = ambient()
    if amb is not None:
        amb.emit_tool(tool_name, finalized)
    return finalized
