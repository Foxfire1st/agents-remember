"""Shared protocol adapter primitives for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.application.tool_response import complete_tool_response

TRANSPORT = "stdio"
PUBLIC_TOOLS = (
    "ping",
    "server_info",
    "context_packet",
    "read_ar_files",
    "attach_terminal_session_to_leaf",
    "spawn_agent_session",
    "hosted_session_readiness",
    "session_retire",
    "session_rename",
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
    "lifecycle_resume",
    "lifecycle_turn_end_notification",
    "lifecycle_end",
    "switch_lifecycle",
    "lifecycle_phase",
    "lifecycle_finalize_task",
    "task_doc",
    "task_reopen",
    "lifecycle_gate",
    "gate_decide",
    "gate_list",
    "operator_inbox_post",
    "operator_inbox_poll",
    "operator_inbox_consume",
    "orchestration_nudge_manager",
)
RESERVED_TOOLS: tuple[str, ...] = ()


def _tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Convert one application result into its protocol-ready response."""
    return complete_tool_response(tool_name, payload)
