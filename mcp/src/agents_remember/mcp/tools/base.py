"""Shared payload-builder primitives for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.models.tokens import finalize_payload_tokens
from agents_remember.models.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import ambient

from .next_step import next_step_for

TRANSPORT = "stdio"
PUBLIC_TOOLS = (
    "ping",
    "server_info",
    "context_packet",
    "read_ar_files",
    "attach_terminal_session_to_leaf",
    "spawn_agent_session",
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
    model = TOOL_RESPONSE_MODELS[tool_name]
    dumped = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
    finalized = finalize_payload_tokens(dumped)
    # The choke point: tag every tool call onto the active lifecycle by
    # construction. A lifecycle-less call is dropped, never misattributed.
    amb = ambient()
    if amb is not None:
        amb.emit_tool(tool_name, finalized)
        # Leaf-28 auto-dismiss: a NOTIFY-AND-CONTINUE turn end parks the lifecycle in
        # awaiting-developer; the next AR tool call resumes it to running so the
        # notification is a stop, not a stall. The tool-name guard is mandatory --
        # lifecycle_turn_end_notification flows through here in the SAME call that set
        # the state, so without it the notification would self-dismiss.
        if (
            amb.current is not None
            and amb.current.state == "awaiting-developer"
            and tool_name != "lifecycle_turn_end_notification"
        ):
            amb.resume_from_await()
        # Task 27: attach the engine-computed next step for the active lifecycle.
        # next_step_for is exception-safe, so it never raises into the tool path.
        next_step = next_step_for(amb, tool_name)
        if next_step is not None:
            finalized["nextStep"] = next_step
    return finalized
