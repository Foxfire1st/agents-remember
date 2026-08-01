"""Shared payload-builder primitives for Agents Remember MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agents_remember.kernel.agentic_settings import DEFAULT_SUPERVISOR_STALE_CUTOFF_SECONDS
from agents_remember.models.base import ResponseEnvelope
from agents_remember.models.tokens import finalize_payload_tokens
from agents_remember.models.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.observer.ambient import AmbientLifecycle, ambient
from agents_remember.serving.supervisor_heartbeat import supervisor_staleness_banner

from .next_step import next_step_for

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


def _supervisor_banner(amb: AmbientLifecycle) -> str | None:
    """The stale-supervisor one-liner for this seat's observer root, or ``None``.

    260707-HFX2-L2 R5: "the watcher must be code AND watched" (#15) -- a stale supervisor
    surfaces a fail-loud banner at ANY seat's next AR call, opportunistically, off the
    heartbeat row it writes on every sweep. Exception-safe (an unreadable heartbeat file
    degrades to "never ticked", never blocks the tool response).
    """
    try:
        return supervisor_staleness_banner(
            amb.root,
            now=datetime.now(UTC),
            stale_cutoff_seconds=DEFAULT_SUPERVISOR_STALE_CUTOFF_SECONDS,
        )
    except Exception:
        return None


def _attach_lifecycle_tail(
    response: ResponseEnvelope, amb: AmbientLifecycle, tool_name: str
) -> None:
    """Set the two lifecycle-wide envelope fields on ``response``, before it is dumped.

    Both are declared on the envelope (``models.base``), so setting them here means the
    single ``model_dump`` in ``_tool_payload`` serializes them and ``finalize_payload_tokens``
    counts them. They used to be written into the dumped dict afterwards, which put the
    emitted object outside its own model (``supervisorBanner`` was declared nowhere, so a
    stale supervisor made every response fail its own ``model_validate``) and left the
    advertised token count short by the whole ``nextStep`` object.

    ``None`` is assigned rather than skipped: ``exclude_none=True`` drops it, so a
    lifecycle-less or live-supervisor response is byte-identical to before.
    """
    # Leaf-28 auto-dismiss: a NOTIFY-AND-CONTINUE turn end parks the lifecycle in
    # awaiting-developer; the next AR tool call resumes it to running so the
    # notification is a stop, not a stall. The tool-name guard is mandatory --
    # lifecycle_turn_end_notification flows through here in the SAME call that set
    # the state, so without it the notification would self-dismiss. It runs BEFORE
    # next_step_for, which reads the state it just moved.
    if (
        amb.current is not None
        and amb.current.state == "awaiting-developer"
        and tool_name != "lifecycle_turn_end_notification"
    ):
        amb.resume_from_await()
    # Task 27: attach the engine-computed next step for the active lifecycle.
    # next_step_for is exception-safe, so it never raises into the tool path.
    response.nextStep = next_step_for(amb, tool_name)
    response.supervisorBanner = _supervisor_banner(amb)


def _tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = TOOL_RESPONSE_MODELS[tool_name]
    response = model.model_validate(payload)
    # The choke point: tag every tool call onto the active lifecycle by
    # construction. A lifecycle-less call is dropped, never misattributed.
    amb = ambient()
    if amb is not None:
        _attach_lifecycle_tail(response, amb, tool_name)
    # One dump, then one token pass over it: everything the caller receives is inside the
    # count, because everything the caller receives is a field of the model dumped here.
    finalized = finalize_payload_tokens(response.model_dump(mode="json", exclude_none=True))
    if amb is not None:
        # Observed LAST, off the final payload, so the `tokens` recorded against the
        # lifecycle is the count the caller was actually served -- hint included. Emitting
        # before the tail was attached recorded the same short count the wire advertised.
        amb.emit_tool(tool_name, finalized)
    return finalized
