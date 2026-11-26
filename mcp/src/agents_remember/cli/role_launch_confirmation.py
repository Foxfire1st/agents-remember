"""Three-step seat launch confirmation, single retry, and held-seat re-confirmation (L51-R2).

Eager harnesses start ``agents-remember-task`` when Paseo creates the seat, before the first
message is sent. This module confirms that a live bound server carrying the seat's exact
``(agentId, requestId)`` is registered under that seat's harness process (with no foreign or
unreadable bound server under the same harness) before sending the opening message with
``skipToolServerWait=True``.

When confirmation fails, the module archives the failed seat, retries once under the same
``requestId`` with a freshly minted ``agentId`` and a distinct ``idempotencyKey``, and refuses
if the retry also fails, leaving zero live seats.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from agents_remember.application.agent_binding import (
    AGENT_ID_VARIABLE,
    REQUEST_ID_VARIABLE,
    TOOL_SERVER_NAME,
)
from agents_remember.cli.paseo_bridge import (
    AGENT_WITHOUT_MESSAGE_LOST,
    BRIDGE_REFUSED,
    PaseoBridgeFailure,
)
from agents_remember.cli.paseo_seat_policy import provider_starts_tool_server_at_create
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.server_bindings import (
    ProcessAncestor,
    ServerBindingRecord,
    harness_root_pids,
    live_server_bindings,
)

if TYPE_CHECKING:
    from agents_remember.cli.paseo_launch import LaunchExecutionPolicy, LaunchOutcome

BINDING_CONFIRM_WAIT_S: float = 5.0
BINDING_CONFIRM_POLL_S: float = 0.1
BINDING_CONFIRMATION_FAILED_CODE = "binding_confirmation_failed"
_TEXT_LIMIT = 800

BridgeCall = Callable[[McpRuntimeConfig, str, dict[str, Any]], dict[str, Any]]
_SnapshotStatus = Literal["confirmed", "failed", "pending"]


@dataclass(frozen=True, slots=True)
class BindingConfirmationResult:
    """Outcome of polling ``runtime/server-bindings`` for one seat's bound MCP server."""

    confirmed: bool
    reason: str | None = None
    detail: str | None = None
    wait_ms: int = 0
    ancestry_checked: bool = False
    ancestry_skipped_reason: str | None = None
    server_pid: int | None = None
    harness_root_pids: tuple[int, ...] = ()
    foreign_agent_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfirmedLaunchRequest:
    """Prepared workspace and bridge inputs for one confirmed launch execution."""

    workspace_id: str
    bridge: BridgeCall
    predecessor_settled: bool
    workspace_preparation: str | None
    execution: LaunchExecutionPolicy


@dataclass(slots=True)
class _RetryState:
    archived_agent_ids: list[str]
    prior_failure: BindingConfirmationResult | None = None
    retried: bool = False


@dataclass(frozen=True, slots=True)
class _AttemptContext:
    config: McpRuntimeConfig
    workspace_id: str
    bridge: BridgeCall
    request_id: str
    has_prompt: bool
    predecessor_settled: bool
    workspace_preparation: str | None
    on_retry_agent: Callable[[str], Any] | None


def confirmation_skip_reason(call: dict[str, Any]) -> str | None:
    """Return why Step 2 confirmation is skipped for ``call``, or ``None`` when required."""
    agent = call.get("agent")
    if not isinstance(agent, dict):
        return "no-task-tool-server"
    if agent.get("existingOnly") is True:
        return "existing-only-reconcile"
    servers = agent.get("mcpServers")
    if not isinstance(servers, dict) or TOOL_SERVER_NAME not in servers:
        return "no-task-tool-server"
    provider = agent.get("provider")
    if not isinstance(provider, str) or not provider_starts_tool_server_at_create(provider):
        return "provider-starts-server-after-create"
    return None


def _same_process(first: ProcessAncestor, second: ProcessAncestor) -> bool:
    return first.pid == second.pid and (
        first.processStart is None
        or second.processStart is None
        or first.processStart == second.processStart
    )


def _under_roots(record: ServerBindingRecord, roots: list[ProcessAncestor]) -> bool:
    lineage = (
        ProcessAncestor(pid=record.parentPid, processStart=record.parentProcessStart),
        *record.ancestors,
    )
    return any(_same_process(member, root) for member in lineage for root in roots)


def _matches_seat(record: ServerBindingRecord, agent_id: str, request_id: str) -> bool:
    return (
        record.bindingState == "bound"
        and record.binding is not None
        and record.binding.agentId == agent_id
        and record.binding.requestId == request_id
    )


def _failed(
    reason: str,
    detail: str,
    elapsed_ms: int,
    *,
    root_pids: tuple[int, ...] = (),
    foreign_ids: tuple[str, ...] = (),
) -> tuple[_SnapshotStatus, BindingConfirmationResult]:
    return (
        "failed",
        BindingConfirmationResult(
            confirmed=False,
            reason=reason,
            detail=detail,
            wait_ms=elapsed_ms,
            ancestry_checked=True,
            harness_root_pids=root_pids,
            foreign_agent_ids=foreign_ids,
        ),
    )


def _confirmed(
    pid: int,
    elapsed_ms: int,
    *,
    ancestry_checked: bool,
    skipped_reason: str | None = None,
    root_pids: tuple[int, ...] = (),
) -> tuple[_SnapshotStatus, BindingConfirmationResult]:
    return (
        "confirmed",
        BindingConfirmationResult(
            confirmed=True,
            wait_ms=elapsed_ms,
            ancestry_checked=ancestry_checked,
            ancestry_skipped_reason=skipped_reason,
            server_pid=pid,
            harness_root_pids=root_pids,
        ),
    )


def _evaluate_under_roots(
    records: list[ServerBindingRecord],
    roots: list[ProcessAncestor],
    agent_id: str,
    request_id: str,
    elapsed_ms: int,
) -> tuple[_SnapshotStatus, BindingConfirmationResult | None]:
    root_pids = tuple(root.pid for root in roots)
    under = [
        r for r in records if r.bindingState in {"bound", "unreadable"} and _under_roots(r, roots)
    ]
    unreadable = [r for r in under if r.bindingState == "unreadable"]
    if unreadable:
        reasons = ", ".join(r.unreadableReason or "unreadable binding" for r in unreadable)
        return _failed(
            "unreadable-binding",
            f"Seat {agent_id}'s harness runs a tool server with an unreadable binding ({reasons}).",
            elapsed_ms,
            root_pids=root_pids,
        )
    own = [r for r in under if _matches_seat(r, agent_id, request_id)]
    foreign = [
        r for r in under if r.bindingState == "bound" and not _matches_seat(r, agent_id, request_id)
    ]
    if foreign:
        foreign_ids = tuple(r.binding.agentId for r in foreign if r.binding is not None)
        reason = "multiple-bound-servers" if own else "foreign-binding"
        detail = (
            f"Multiple bound tool servers run under seat {agent_id}'s harness (foreign bindings: {', '.join(foreign_ids)})."
            if own
            else f"Seat {agent_id}'s harness runs a tool server bound to another seat ({', '.join(foreign_ids)})."
        )
        return _failed(reason, detail, elapsed_ms, root_pids=root_pids, foreign_ids=foreign_ids)
    if own:
        return _confirmed(own[0].pid, elapsed_ms, ancestry_checked=True, root_pids=root_pids)
    return ("pending", None)


def _evaluate_without_roots(
    records: list[ServerBindingRecord],
    matching: list[ServerBindingRecord],
    agent_id: str,
    request_id: str,
    elapsed_ms: int,
) -> tuple[_SnapshotStatus, BindingConfirmationResult | None]:
    if not matching:
        return ("pending", None)
    for match in matching:
        if match.parentPid <= 0:
            continue
        parent = ProcessAncestor(pid=match.parentPid, processStart=match.parentProcessStart)
        siblings = [
            r
            for r in records
            if r.bindingState in {"bound", "unreadable"}
            and r.parentPid > 0
            and not _matches_seat(r, agent_id, request_id)
            and _same_process(
                ProcessAncestor(pid=r.parentPid, processStart=r.parentProcessStart), parent
            )
        ]
        unreadable = [r for r in siblings if r.bindingState == "unreadable"]
        if unreadable:
            reasons = ", ".join(r.unreadableReason or "unreadable binding" for r in unreadable)
            return _failed(
                "unreadable-binding",
                f"Seat {agent_id}'s parent process {match.parentPid} hosts an unreadable binding ({reasons}).",
                elapsed_ms,
            )
        foreign = [r for r in siblings if r.bindingState == "bound"]
        if foreign:
            foreign_ids = tuple(r.binding.agentId for r in foreign if r.binding is not None)
            return _failed(
                "multiple-bound-servers",
                f"Multiple bound tool servers share parent process {match.parentPid} with seat {agent_id} (foreign bindings: {', '.join(foreign_ids)}).",
                elapsed_ms,
                foreign_ids=foreign_ids,
            )
    return _confirmed(
        matching[0].pid,
        elapsed_ms,
        ancestry_checked=False,
        skipped_reason="harness-root-not-found",
    )


def _evaluate_snapshot(
    records: list[ServerBindingRecord],
    roots: list[ProcessAncestor] | None,
    agent_id: str,
    request_id: str,
    elapsed_ms: int,
) -> tuple[_SnapshotStatus, BindingConfirmationResult | None]:
    matching = [r for r in records if _matches_seat(r, agent_id, request_id)]
    if roots is None:
        if not matching:
            return ("pending", None)
        return _confirmed(
            matching[0].pid, elapsed_ms, ancestry_checked=False, skipped_reason="proc-unavailable"
        )
    if roots:
        return _evaluate_under_roots(records, roots, agent_id, request_id, elapsed_ms)
    return _evaluate_without_roots(records, matching, agent_id, request_id, elapsed_ms)


def _timeout_result(
    agent_id: str, elapsed_ms: int, roots: list[ProcessAncestor] | None
) -> BindingConfirmationResult:
    if roots is None:
        checked, skipped, root_pids = False, "proc-unavailable", ()
    elif roots:
        checked, skipped, root_pids = True, None, tuple(r.pid for r in roots)
    else:
        checked, skipped, root_pids = False, "harness-root-not-found", ()
    return BindingConfirmationResult(
        confirmed=False,
        reason="binding-not-registered",
        detail=f"No bound {TOOL_SERVER_NAME} server registered for seat {agent_id} within {elapsed_ms} ms.",
        wait_ms=elapsed_ms,
        ancestry_checked=checked,
        ancestry_skipped_reason=skipped,
        harness_root_pids=root_pids,
    )


def confirm_seat_binding(
    config: McpRuntimeConfig,
    agent_id: str,
    request_id: str,
    *,
    wait_s: float | None = None,
    poll_s: float | None = None,
) -> BindingConfirmationResult:
    """Poll live server binding records until ``(agent_id, request_id)`` is confirmed or fails."""
    budget = BINDING_CONFIRM_WAIT_S if wait_s is None else wait_s
    interval = BINDING_CONFIRM_POLL_S if poll_s is None else poll_s
    started = time.monotonic()
    deadline = started + max(0.0, budget)
    last_roots: list[ProcessAncestor] | None = None
    while True:
        records = live_server_bindings(config)
        roots = harness_root_pids(agent_id)
        last_roots = roots
        elapsed_ms = round((time.monotonic() - started) * 1000)
        state, result = _evaluate_snapshot(records, roots, agent_id, request_id, elapsed_ms)
        if state != "pending" and result is not None:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _timeout_result(agent_id, elapsed_ms, last_roots)
        time.sleep(min(interval, remaining))


def _call_request_id(call: dict[str, Any]) -> str:
    agent = call.get("agent")
    if isinstance(agent, dict):
        env = ((agent.get("mcpServers") or {}).get(TOOL_SERVER_NAME) or {}).get("env")
        if (
            isinstance(env, dict)
            and isinstance(env.get(REQUEST_ID_VARIABLE), str)
            and env[REQUEST_ID_VARIABLE]
        ):
            return env[REQUEST_ID_VARIABLE]
        labels = agent.get("labels")
        if (
            isinstance(labels, dict)
            and isinstance(labels.get("ar.request-id"), str)
            and labels["ar.request-id"]
        ):
            return labels["ar.request-id"]
    raise ValueError("The saved launch call carries no request id for binding confirmation.")


def _retry_launch_call(call: dict[str, Any], new_agent_id: str) -> dict[str, Any]:
    """Clone ``call`` for a single retry under the same ``requestId`` with a fresh agent id."""
    retried = copy.deepcopy(call)
    retried.pop("archiveAgentId", None)
    agent = retried["agent"]
    agent["agentId"] = new_agent_id
    base_key = str(agent["idempotencyKey"])
    agent["idempotencyKey"] = (
        f"{base_key}{':open-retry-1' if base_key.endswith(':retry-1') else ':retry-1'}"
    )
    env = ((agent.get("mcpServers") or {}).get(TOOL_SERVER_NAME) or {}).get("env")
    if isinstance(env, dict):
        env[AGENT_ID_VARIABLE] = new_agent_id
    return retried


def _confirmation_record(
    result: BindingConfirmationResult,
    *,
    retried: bool,
    archived_agent_ids: list[str],
    prior_failure: BindingConfirmationResult | None,
) -> dict[str, Any]:
    total_wait_ms = result.wait_ms + (prior_failure.wait_ms if prior_failure is not None else 0)
    record: dict[str, Any] = {
        "status": "confirmed" if result.confirmed else "failed",
        "waitMs": total_wait_ms,
        "ancestryChecked": result.ancestry_checked,
        "retried": retried,
        "archivedAgentIds": list(archived_agent_ids),
        "harnessRootPids": list(result.harness_root_pids),
    }
    if result.ancestry_skipped_reason:
        record["ancestrySkippedReason"] = result.ancestry_skipped_reason
    if len(archived_agent_ids) == 1:
        record["archivedAgentId"] = archived_agent_ids[0]
    if not result.confirmed and result.reason:
        record["reason"] = result.reason
    if prior_failure is not None and prior_failure.reason:
        record["priorFailureReason"] = prior_failure.reason
    if result.server_pid is not None:
        record["serverPid"] = result.server_pid
    if result.foreign_agent_ids:
        record["foreignAgentIds"] = list(result.foreign_agent_ids)
    return record


def _refusal_message(
    result: BindingConfirmationResult,
    archived_agent_ids: list[str],
    *,
    archive_error: str | None = None,
) -> str:
    reason = result.reason or "binding-not-registered"
    detail = result.detail or f"Binding confirmation failed ({reason})."
    agents = ", ".join(archived_agent_ids) if archived_agent_ids else "none"
    if archive_error is not None:
        return (
            f"Seat identity confirmation failed ({reason}): {detail} "
            f"Failed to archive seat ({archive_error}); archive it in Paseo before retrying."
        )[:_TEXT_LIMIT]
    return (
        f"Seat identity confirmation failed after retry ({reason}): {detail} "
        f"Both attempted seats ({agents}) were archived, leaving zero live seats. "
        "Inspect the harness MCP configuration and start the role again."
    )[:_TEXT_LIMIT]


def _archive_attempt(
    ctx: _AttemptContext,
    agent_id: str,
    result: BindingConfirmationResult,
    state: _RetryState,
) -> LaunchOutcome | None:
    """Archive ``agent_id``; return a refusal ``LaunchOutcome`` only if archiving itself fails."""
    from agents_remember.cli.paseo_launch import LaunchOutcome  # noqa: PLC0415

    try:
        ctx.bridge(ctx.config, "agent-archive", {"agentId": agent_id})
    except PaseoBridgeFailure as error:
        return LaunchOutcome(
            kind="refused",
            code=BINDING_CONFIRMATION_FAILED_CODE,
            message=_refusal_message(result, state.archived_agent_ids, archive_error=str(error)),
            predecessor_settled=ctx.predecessor_settled,
            lost_agent_id=agent_id,
            binding_confirmation=_confirmation_record(
                result,
                retried=state.retried,
                archived_agent_ids=state.archived_agent_ids,
                prior_failure=state.prior_failure,
            ),
        )
    state.archived_agent_ids.append(agent_id)
    return None


def _bridge_failure_outcome(
    error: PaseoBridgeFailure,
    *,
    agent_id: str,
    predecessor_settled: bool,
    binding_confirmation: dict[str, Any] | None = None,
    effective_launch_call: dict[str, Any] | None = None,
) -> LaunchOutcome:
    from agents_remember.cli.paseo_launch import (  # noqa: PLC0415
        NATIVE_ANTIGRAVITY_REPLAY_MISSING,
        LaunchOutcome,
        native_antigravity_role_start_refusal,
    )

    if error.code == NATIVE_ANTIGRAVITY_REPLAY_MISSING:
        return LaunchOutcome(
            kind="refused",
            code=error.code,
            message=str(native_antigravity_role_start_refusal()),
            predecessor_settled=predecessor_settled,
        )
    lost = error.code == AGENT_WITHOUT_MESSAGE_LOST
    return LaunchOutcome(
        kind="refused" if lost or error.code == BRIDGE_REFUSED else "no-answer",
        code=error.code,
        message=str(error)[:_TEXT_LIMIT],
        predecessor_settled=predecessor_settled,
        lost_agent_id=agent_id if lost else None,
        binding_confirmation=binding_confirmation,
        effective_launch_call=effective_launch_call,
    )


def _create_confirm_and_send(
    ctx: _AttemptContext,
    attempt_call: dict[str, Any],
    state: _RetryState,
) -> tuple[LaunchOutcome | None, BindingConfirmationResult | None]:
    from agents_remember.cli.paseo_launch import (  # noqa: PLC0415
        _created_outcome,
        agent_create_input,
    )

    current_agent_id = str(attempt_call["agent"]["agentId"])
    create_only = agent_create_input(attempt_call, ctx.workspace_id)
    create_only.pop("prompt", None)
    try:
        base_outcome = _created_outcome(
            ctx.bridge(ctx.config, "agent-create", create_only), current_agent_id, ctx.workspace_id
        )
    except PaseoBridgeFailure as error:
        return (
            _bridge_failure_outcome(
                error,
                agent_id=current_agent_id,
                predecessor_settled=ctx.predecessor_settled,
                effective_launch_call=attempt_call if state.retried else None,
            ),
            None,
        )
    result = confirm_seat_binding(ctx.config, current_agent_id, ctx.request_id)
    if not result.confirmed:
        return (None, result)
    confirmation = _confirmation_record(
        result,
        retried=state.retried,
        archived_agent_ids=state.archived_agent_ids,
        prior_failure=state.prior_failure,
    )
    if ctx.has_prompt:
        send_input = {
            **agent_create_input(attempt_call, ctx.workspace_id),
            "skipToolServerWait": True,
        }
        try:
            base_outcome = _created_outcome(
                ctx.bridge(ctx.config, "agent-create", send_input),
                current_agent_id,
                ctx.workspace_id,
            )
        except PaseoBridgeFailure as error:
            return (
                _bridge_failure_outcome(
                    error,
                    agent_id=current_agent_id,
                    predecessor_settled=ctx.predecessor_settled,
                    binding_confirmation=confirmation,
                    effective_launch_call=attempt_call if state.retried else None,
                ),
                None,
            )
    return (
        replace(
            base_outcome,
            predecessor_settled=ctx.predecessor_settled,
            workspace_preparation=ctx.workspace_preparation,
            binding_confirmation=confirmation,
            effective_launch_call=attempt_call if state.retried else None,
        ),
        result,
    )


def _retry_after_failure(
    ctx: _AttemptContext,
    call: dict[str, Any],
    failed_agent_id: str,
    first_result: BindingConfirmationResult,
) -> LaunchOutcome:
    """Archive attempt 1, retry once under the same requestId, and archive attempt 2 on refusal."""
    from agents_remember.cli.paseo_launch import LaunchOutcome, mint_agent_id  # noqa: PLC0415

    state = _RetryState(archived_agent_ids=[])
    if refusal := _archive_attempt(ctx, failed_agent_id, first_result, state):
        return refusal
    retry_agent_id = mint_agent_id()
    retry_call = _retry_launch_call(call, retry_agent_id)
    if ctx.on_retry_agent is not None:
        ctx.on_retry_agent(retry_agent_id)
    state.prior_failure = first_result
    state.retried = True
    outcome, second_result = _create_confirm_and_send(ctx, retry_call, state)
    if outcome is not None:
        return outcome
    assert second_result is not None
    if refusal := _archive_attempt(ctx, retry_agent_id, second_result, state):
        return refusal
    return LaunchOutcome(
        kind="refused",
        code=BINDING_CONFIRMATION_FAILED_CODE,
        message=_refusal_message(second_result, state.archived_agent_ids),
        predecessor_settled=ctx.predecessor_settled,
        lost_agent_id=None,
        binding_confirmation=_confirmation_record(
            second_result,
            retried=True,
            archived_agent_ids=state.archived_agent_ids,
            prior_failure=first_result,
        ),
    )


def execute_confirmed_launch(
    config: McpRuntimeConfig,
    call: dict[str, Any],
    request: ConfirmedLaunchRequest,
) -> LaunchOutcome:
    """Run agent creation or held-seat opening with L51-R2 binding confirmation and single retry."""
    from agents_remember.cli.paseo_launch import (  # noqa: PLC0415
        _created_outcome,
        agent_create_input,
    )

    agent = call["agent"]
    agent_id = str(agent["agentId"])
    provider = str(agent.get("provider") or "")
    skip_reason = confirmation_skip_reason(call)
    if skip_reason is not None:
        created = request.bridge(
            config, "agent-create", agent_create_input(call, request.workspace_id)
        )
        return replace(
            _created_outcome(created, agent_id, request.workspace_id),
            predecessor_settled=request.predecessor_settled,
            workspace_preparation=request.workspace_preparation,
            binding_confirmation={"status": "skipped", "reason": skip_reason, "provider": provider},
        )
    has_prompt = isinstance(agent.get("prompt"), str) and bool(agent.get("prompt"))
    ctx = _AttemptContext(
        config=config,
        workspace_id=request.workspace_id,
        bridge=request.bridge,
        request_id=_call_request_id(call),
        has_prompt=True if request.execution.mode == "open-held" else has_prompt,
        predecessor_settled=request.predecessor_settled,
        workspace_preparation=request.workspace_preparation,
        on_retry_agent=request.execution.on_retry_agent,
    )
    if request.execution.mode == "open-held":
        first_result = confirm_seat_binding(config, agent_id, ctx.request_id)
        if first_result.confirmed:
            send_input = {
                **agent_create_input(call, request.workspace_id),
                "skipToolServerWait": True,
            }
            sent = request.bridge(config, "agent-create", send_input)
            return replace(
                _created_outcome(sent, agent_id, request.workspace_id),
                predecessor_settled=request.predecessor_settled,
                workspace_preparation=request.workspace_preparation,
                binding_confirmation=_confirmation_record(
                    first_result, retried=False, archived_agent_ids=[], prior_failure=None
                ),
            )
        return _retry_after_failure(ctx, call, agent_id, first_result)
    outcome, first_result = _create_confirm_and_send(
        ctx, copy.deepcopy(call), _RetryState(archived_agent_ids=[])
    )
    if outcome is not None:
        return outcome
    assert first_result is not None
    return _retry_after_failure(ctx, call, agent_id, first_result)


def held_open_outcome_extras(outcome: LaunchOutcome) -> dict[str, Any]:
    """Optional confirmation and retry fields carried from ``_open_held_seat`` to receipt apply."""
    extras: dict[str, Any] = {}
    if outcome.binding_confirmation is not None:
        extras["bindingConfirmation"] = outcome.binding_confirmation
    if outcome.effective_launch_call is not None:
        extras["effectiveLaunchCall"] = outcome.effective_launch_call
    return extras


def is_held_seat_lost(outcome: LaunchOutcome) -> bool:
    """Whether a held-seat opening failure left no usable live seat."""
    return outcome.kind == "refused" and (
        bool(outcome.lost_agent_id) or outcome.code == BINDING_CONFIRMATION_FAILED_CODE
    )


def held_seat_lost_detail(outcome: LaunchOutcome) -> str:
    """Operator-legible refusal detail for a lost or unconfirmed held seat."""
    if outcome.code == BINDING_CONFIRMATION_FAILED_CODE and not outcome.lost_agent_id:
        return str(outcome.message)
    return (
        "The held seat's agent exists but can never take its first message; "
        f"its waiting messages end as not delivered. {outcome.message}"
    )


def held_seat_lost_settlement(
    outcome: LaunchOutcome,
) -> tuple[str, dict[str, Any], tuple[str, ...], dict[str, Any]]:
    """Build ``(detail, receipt_updates, remove_fields, result_extras)`` for a lost held seat."""
    detail = held_seat_lost_detail(outcome)
    extras = held_open_outcome_extras(outcome)
    host_exists = outcome.lost_agent_id is not None
    updates: dict[str, Any] = {"detail": detail, "hostAgentExists": host_exists}
    if "bindingConfirmation" in extras:
        updates["bindingConfirmation"] = extras["bindingConfirmation"]
    return (
        detail,
        updates,
        () if host_exists else ("replayRequest",),
        {"hostAgentExists": host_exists, **extras},
    )


def apply_held_open_confirmation(current: dict[str, Any], result: dict[str, Any]) -> None:
    """Apply confirmation and retried-seat fields from a held-seat drain onto ``current``."""
    from agents_remember.cli.paseo_launch import applied_to_agent  # noqa: PLC0415

    outcome = result.get("outcome")
    if outcome == "opened" and isinstance(effective := result.get("effectiveLaunchCall"), dict):
        agent = effective.get("agent")
        if isinstance(agent, dict) and isinstance(agent.get("agentId"), str):
            current["agentId"] = agent["agentId"]
        current.update(applied_to_agent(effective))
        current["replayRequest"] = {
            **effective,
            "agent": {k: v for k, v in (effective.get("agent") or {}).items() if k != "prompt"},
        }
    elif outcome == "seat-lost" and "hostAgentExists" in result:
        current["hostAgentExists"] = bool(result["hostAgentExists"])
        if not result["hostAgentExists"]:
            current.pop("replayRequest", None)
    if isinstance(confirmation := result.get("bindingConfirmation"), dict):
        current["bindingConfirmation"] = confirmation
