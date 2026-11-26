"""Retire a role agent within the caller's scope.

The launch receipt identifies the seat and carries the retirement across host
archive retries. Held messages end when the archive is recorded.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.application.role_capsules.refusal_references import with_refusal_reference
from agents_remember.application.seat_retirement_scope import retirement_scope_refusal
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.cli.paseo_status import AgentReading, read_agent
from agents_remember.cli.role_held_store import held_seat_ref, settle_held_store
from agents_remember.cli.role_launch_receipts import read_enumerated_receipt
from agents_remember.cli.role_retirement_receipt import (
    Retirement,
    clear_retirement,
    hold_retirement,
    settle_retirement,
)
from agents_remember.controlplane.held_store_discovery import EXECUTIONS_DIRECTORY
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.models.structural.agent import RetireChildRequest
from agents_remember.serving.projections.host_activity import running_subagents


class _RetireTerminal(Exception):
    """A named refusal or an idempotent repeat."""

    def __init__(self, answer: dict[str, Any]) -> None:
        super().__init__(str(answer.get("detail")))
        self.answer = answer


@dataclass(frozen=True, slots=True)
class _RetireSeat:
    """The unique launch receipt and selection for the named agent."""

    agent_id: str
    path: Path
    receipt: dict[str, Any]
    selection: RoleSelection

    def answer(self, ok: bool, status: str, detail: str, **fields: Any) -> dict[str, Any]:
        return {
            **_answer(ok, self.selection.role, status, detail),
            **self.selection.model_dump(
                mode="json", by_alias=True, exclude_none=True, exclude={"role"}
            ),
            **fields,
        }


def retire_native_seat(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Archive one role agent within the caller's scope and record the retirement."""

    try:
        caller = _retire_caller(request, environment)
        seat = _retire_seat(config, request, caller)
        return _archive_retiree(config, seat, caller, request)
    except _RetireTerminal as terminal:
        return terminal.answer


def _retire_caller(
    request: RetireChildRequest, environment: Mapping[str, str] | None
) -> AgentBinding:
    try:
        binding = read_agent_binding(environment)
        if binding is None:
            raise ValueError("no role binding names the caller")
    except ValueError as error:
        raise _RetireTerminal(
            _answer(
                False,
                str(request.role),
                "caller-has-no-binding",
                f"This tool server was not started by an AR role launch: {error}",
            )
        ) from error
    return binding


def _retire_seat(
    config: McpRuntimeConfig, request: RetireChildRequest, caller: AgentBinding
) -> _RetireSeat:
    agent_id = request.agent_id
    if not _is_minted_id(agent_id):
        raise _RetireTerminal(
            _answer(
                False,
                str(request.role),
                "unknown-agent",
                f"{agent_id!r} is not an agent id: AR agent ids are UUIDs. Nothing was archived.",
            )
        )
    path, receipt = _locate_seat(config, agent_id, str(request.role))
    selection = RoleSelection.model_validate(receipt["selection"])
    seat = _RetireSeat(agent_id, path, receipt, selection)
    refusal = retirement_scope_refusal(config, caller, agent_id, selection)
    if refusal is not None:
        raise _RetireTerminal(
            seat.answer(
                False,
                "outside-scope",
                with_refusal_reference(refusal, "retire-child-outside-scope"),
            )
        )
    _require_resolved_launch(seat)
    archive = receipt.get("leafArchive")
    if isinstance(receipt.get("retiredBy"), dict) or (
        isinstance(archive, dict) and archive.get("closing") is True
    ):
        ref = held_seat_ref(path, receipt)
        if ref.store_path is not None and ref.store_path.exists():
            try:
                settle_held_store(ref)
            except (ValueError, OSError) as error:
                raise _RetireTerminal(
                    seat.answer(False, "retire-record-failed", str(error))
                ) from error
        raise _RetireTerminal(_already_retired(seat, receipt))
    return seat


def _require_resolved_launch(seat: _RetireSeat) -> None:
    """A reserved ID can still be created by the original start or its replay."""

    receipt = seat.receipt
    status = str(receipt.get("status"))
    if status in {"starting", "unknown"} or (
        status == "rejected" and receipt.get("stepUp") and receipt.get("replayRequest")
    ):
        raise _RetireTerminal(
            seat.answer(
                False,
                "launch-unresolved",
                f"Launch request {receipt.get('requestId')} can still create agent {seat.agent_id}. "
                "Nothing was archived. Let the launch settle or have its starter retry that "
                "exact request before calling retire_child again.",
            )
        )


def _already_retired(seat: _RetireSeat, receipt: dict[str, Any]) -> dict[str, Any]:
    retired = receipt.get("retiredBy")
    detail = (
        f"Agent {seat.agent_id} was already retired by {retired.get('caller')} for "
        f"{retired.get('reason')!r}. Nothing changed."
        if isinstance(retired, dict)
        else f"Agent {seat.agent_id} is already closing under its task's archive. Nothing changed."
    )
    return seat.answer(True, "already-retired", detail)


def _archive_retiree(
    config: McpRuntimeConfig,
    seat: _RetireSeat,
    caller: AgentBinding,
    request: RetireChildRequest,
) -> dict[str, Any]:
    _require_no_open_children(config, seat)
    reading = _read_host(config, seat, seat.agent_id)
    _require_interruptible(config, seat, reading, request)
    caller_name = f"{caller.role} agent {caller.agent_id}"
    intent = Retirement(
        caller_name, caller.agent_id, request.reason, f"Retired by {caller_name}: {request.reason}"
    )
    try:
        held = hold_retirement(seat.path, seat.receipt, intent)
    except OSError as error:
        return seat.answer(
            False, "retire-record-failed", f"Could not save the retirement before archive: {error}"
        )
    if held.state == "already-retired":
        return _already_retired(seat, held.receipt)
    if held.state != "held":
        return _retirement_changed(seat, held.state)
    if reading.found and not reading.archived:
        _archive_on_host(config, seat, intent)
    return _finish_retirement(seat, intent, held.receipt)


def _archive_on_host(config: McpRuntimeConfig, seat: _RetireSeat, intent: Retirement) -> None:
    try:
        reply = bridge_call(config, "agent-archive", {"agentId": seat.agent_id})
    except PaseoBridgeFailure as error:
        if error.code == "paseo_archive_refused":
            raise _RetireTerminal(_clear_refused_archive(seat, intent, str(error))) from error
        raise _RetireTerminal(
            seat.answer(
                False,
                "host-unreachable",
                f"The Paseo archive could not be confirmed: {error.code}: {error}. "
                "Retry the identical retirement to reconcile the receipt.",
            )
        ) from error
    if reply.get("archived") is True or reply.get("found") is False:
        return
    raise _RetireTerminal(
        seat.answer(
            False,
            "host-unreachable",
            "The archive reply did not confirm the host state. Retry the identical retirement.",
        )
    )


def _clear_refused_archive(seat: _RetireSeat, intent: Retirement, reason: str) -> dict[str, Any]:
    try:
        updated = clear_retirement(seat.path, seat.receipt, intent)
    except OSError as error:
        return seat.answer(
            False,
            "retire-record-failed",
            f"The host refused the archive and its intent could not be cleared: {error}. "
            "Retry the identical retirement to reconcile the receipt.",
        )
    if updated.state == "already-retired":
        return _already_retired(seat, updated.receipt)
    if updated.state != "cleared":
        return _retirement_changed(seat, updated.state)
    return seat.answer(
        False,
        "archive-refused",
        f"Paseo refused the archive of agent {seat.agent_id} ({reason}). "
        "Paseo last reported the agent unarchived. Retry retirement once the refusal is resolved.",
    )


def _finish_retirement(
    seat: _RetireSeat, intent: Retirement, held_receipt: dict[str, Any]
) -> dict[str, Any]:
    try:
        updated = settle_retirement(seat.path, held_receipt, intent)
    except (OSError, ValueError) as error:
        return seat.answer(
            False,
            "retire-record-failed",
            f"The host archive is confirmed, but the retirement could not be recorded: {error}. "
            "Retry the identical retirement to complete the record.",
            retireIntent={
                key: held_receipt["retireIntent"][key]
                for key in ("caller", "callerAgentId", "reason", "at")
            },
        )
    if updated.state == "already-retired":
        return _already_retired(seat, updated.receipt)
    if updated.state != "recorded":
        return _retirement_changed(seat, updated.state)
    return seat.answer(
        True,
        "retired",
        f"Agent {seat.agent_id} was retired by {intent.caller}. The receipt names the reason.",
    )


def _retirement_changed(seat: _RetireSeat, state: str) -> dict[str, Any]:
    return seat.answer(
        False,
        "retire-record-failed",
        f"The retirement receipt changed ({state}). Retry the recorded retirement before "
        "continuing with another request.",
    )


def _require_interruptible(
    config: McpRuntimeConfig,
    seat: _RetireSeat,
    reading: AgentReading,
    request: RetireChildRequest,
) -> None:
    if request.interrupt_turn or not reading.found or reading.archived:
        return
    if reading.turn_active or reading.lifecycle == "running":
        detail = f"Agent {seat.agent_id} is in a turn."
    elif subagents := running_subagents(config, seat.agent_id):
        detail = f"Agent {seat.agent_id} has {subagents} running provider subagent(s)."
    else:
        return
    raise _RetireTerminal(
        seat.answer(
            False,
            "agent-in-turn",
            with_refusal_reference(
                f"{detail} Nothing was archived. Set interrupt_turn=true to interrupt its work.",
                "retire-child-agent-in-turn",
            ),
        )
    )


def _require_no_open_children(config: McpRuntimeConfig, seat: _RetireSeat) -> None:
    open_children: list[dict[str, Any]] = []
    checked: set[str] = set()
    for _path, child in _line_receipts(config):
        if child.get("parentAgentId") != seat.agent_id or isinstance(child.get("retiredBy"), dict):
            continue
        agent_id = child.get("agentId")
        if not isinstance(agent_id, str) or agent_id in checked:
            continue
        checked.add(agent_id)
        reading = _read_host(config, seat, agent_id)
        if reading.found and not reading.archived:
            open_children.append({"agentId": agent_id, "selection": child["selection"]})
    if open_children:
        agent_ids = ", ".join(child["agentId"] for child in open_children)
        raise _RetireTerminal(
            seat.answer(
                False,
                "open-children",
                with_refusal_reference(
                    f"Agent {seat.agent_id} still has open child seats: {agent_ids}. "
                    "Retire those children first or replace the seat. Nothing was archived.",
                    "retire-child-open-children",
                ),
                openChildren=open_children,
            )
        )


def _read_host(config: McpRuntimeConfig, seat: _RetireSeat, agent_id: str) -> AgentReading:
    reading = read_agent(config, agent_id)
    if not reading.reachable:
        raise _RetireTerminal(
            seat.answer(
                False,
                "host-unreachable",
                f"Could not read agent {agent_id} on Paseo: {reading.unreachable_reason}. "
                "Retry when the runtime is reachable. Nothing was archived.",
            )
        )
    return reading


def _is_minted_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _line_receipts(config: McpRuntimeConfig) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Visit both current and historical receipts, ignoring message projections and attachments."""

    roots = [config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY]
    roots.extend(
        sorted(
            (config.coordination_root / "tasks").glob(f"**/notes/reports/{EXECUTIONS_DIRECTORY}")
        )
    )
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            if "message-bindings" in path.relative_to(root).parts:
                continue
            receipt = read_enumerated_receipt(path)
            if receipt is not None:
                yield path, receipt


def _locate_seat(config: McpRuntimeConfig, agent_id: str, role: str) -> tuple[Path, dict[str, Any]]:
    """Resolve exactly one receipt before contacting the host or writing to disk."""

    matches = [
        (path, receipt)
        for path, receipt in _line_receipts(config)
        if receipt.get("agentId") == agent_id
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        where = ", ".join(
            f"{'archived' if path.parent.name == 'history' else 'current'} {path}"
            for path, _receipt in matches
        )
        raise _RetireTerminal(
            _answer(
                False,
                role,
                "ambiguous-agent",
                f"Agent {agent_id} is recorded by {len(matches)} launch records ({where}). "
                "Retirement requires a unique record. Nothing was archived.",
            )
        )
    raise _RetireTerminal(
        _answer(
            False,
            role,
            "unknown-agent",
            f"No launch record of this AR line knows agent {agent_id}. Nothing was archived.",
        )
    )


def _answer(ok: bool, role: str, status: str, detail: str) -> dict[str, Any]:
    return {"ok": ok, "operation": "retire_child", "status": status, "role": role, "detail": detail}
