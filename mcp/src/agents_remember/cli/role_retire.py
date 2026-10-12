"""Retire the agent in a leaf seat, by the agent that started it (MIK-R100 rule 8).

The retirement finds its caller by the stored role binding that ``role_start`` trusts --
the binding of the tool server this call runs on -- and admits the agent whose id the
launch record names as the parent of the seat. It archives the agent on the host without
giving it a turn, and writes the retirement into the launch record with the caller and
the reason. After it, a message to that agent is refused as for an archived agent, and
the address by role no longer finds it.

The retirement of seats under the earlier launch mechanism (the structural
``retire_child`` path through plane identity) keeps working as it does; this module is
only the native receipt-tracked path, chosen when the call names an agent id.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.application.role_launch_context import LEAF_ROLES
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.cli.paseo_status import read_agent
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.structural.agent import RetireChildRequest


class _RetireTerminal(Exception):
    """A terminal answer met before archiving: a refusal, or an idempotent repeat."""

    def __init__(self, answer: dict[str, Any]) -> None:
        super().__init__(str(answer.get("detail")))
        self.answer = answer


@dataclass(frozen=True, slots=True)
class _RetireSeat:
    """One seat asked to retire: its record, its role, and who called."""

    path: Path
    receipt: dict[str, Any]
    seat_role: str
    caller: AgentBinding


def retire_native_seat(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retire one receipt-tracked leaf-seat agent, or say why it was not retired."""

    try:
        caller = _retire_caller(request, environment)
        seat = _retire_seat(config, request, caller)
        return _archive_retiree(config, seat, request)
    except _RetireTerminal as terminal:
        return terminal.answer


def _retire_caller(
    request: RetireChildRequest, environment: Mapping[str, str] | None
) -> AgentBinding:
    """The caller from the tool server's binding; a server without one retires none."""

    try:
        return _native_caller(environment)
    except ValueError as error:
        raise _RetireTerminal(
            _refusal(
                None,
                str(request.role),
                "caller-has-no-binding",
                f"This tool server was not started by an AR role launch: {error}",
            )
        ) from error


def _retire_seat(
    config: McpRuntimeConfig, request: RetireChildRequest, caller: AgentBinding
) -> _RetireSeat:
    """The seat of the named agent, checked but not yet archived (rule 8)."""

    agent_id = request.agent_id or ""
    if not _is_minted_id(agent_id):
        raise _RetireTerminal(
            _refusal(
                None,
                str(request.role),
                "unknown-agent",
                f"{agent_id!r} is not an agent id: AR agent ids are UUIDs. Nothing was archived.",
            )
        )
    located = _locate_seat(config, agent_id, str(request.role))
    path, receipt, archived_record = located
    if archived_record:
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(receipt),
                _role_of(receipt, str(request.role)),
                "unknown-agent",
                f"Agent {agent_id} holds no seat of this leaf selection anymore. "
                "Nothing was archived.",
            )
        )
    seat_role = _role_of(receipt, str(request.role))
    if seat_role not in LEAF_ROLES:
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(receipt),
                seat_role,
                "not-a-leaf-seat",
                f"Agent {agent_id} is a {seat_role}, not a leaf seat; native retirement retires "
                "worker, reviewer and curator seats. Nothing was archived.",
            )
        )
    starter = receipt.get("parentAgentId")
    if starter != caller.agent_id:
        owner = f"agent {starter}" if isinstance(starter, str) and starter else "the launcher"
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(receipt),
                seat_role,
                "not-starter",
                f"Agent {caller.agent_id} did not start agent {agent_id} and cannot retire it; "
                f"only {owner} can. Nothing was archived.",
            )
        )
    if isinstance(receipt.get("retiredBy"), dict):
        retired = receipt["retiredBy"]
        raise _RetireTerminal(
            {
                "ok": True,
                "operation": "retire_child",
                "status": "already-retired",
                "role": seat_role,
                **({"taskDocumentRef": _task_ref_of(receipt)} if _task_ref_of(receipt) else {}),
                "detail": (
                    f"Agent {agent_id} was already retired by {retired.get('caller')} for "
                    f"{retired.get('reason')!r}; nothing changed."
                ),
            }
        )
    return _RetireSeat(path, receipt, seat_role, caller)


def _archive_retiree(
    config: McpRuntimeConfig, seat: _RetireSeat, request: RetireChildRequest
) -> dict[str, Any]:
    """Archive the checked seat's agent on the host and record it (rule 8)."""

    agent_id = request.agent_id or ""
    reading = read_agent(config, agent_id)
    if not reading.reachable:
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(seat.receipt),
                seat.seat_role,
                "host-unreachable",
                f"The Paseo runtime cannot be reached: {reading.unreachable_reason} "
                "Nothing was archived.",
            )
        )
    if not reading.found or reading.archived:
        recorded = _record_retirement(
            seat.path,
            seat.caller,
            agent_id,
            request.reason,
        )
        if recorded != "recorded":
            raise _RetireTerminal(
                {
                    "ok": True,
                    "operation": "retire_child",
                    "status": "already-retired",
                    "role": seat.seat_role,
                    **(
                        {"taskDocumentRef": _task_ref_of(seat.receipt)}
                        if _task_ref_of(seat.receipt)
                        else {}
                    ),
                    "detail": (
                        f"Agent {agent_id} was already archived or gone on the host; "
                        f"the launch record could not be written ({recorded})."
                    ),
                }
            )
        return {
            "ok": True,
            "operation": "retire_child",
            "status": "already-retired",
            "role": seat.seat_role,
            **(
                {"taskDocumentRef": _task_ref_of(seat.receipt)}
                if _task_ref_of(seat.receipt)
                else {}
            ),
            "detail": (
                f"Agent {agent_id} was already archived or gone on the host; the record says so."
            ),
        }
    if (reading.turn_active or reading.lifecycle == "running") and not request.interrupt_turn:
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(seat.receipt),
                seat.seat_role,
                "agent-in-turn",
                f"Agent {agent_id} is in a turn; it is not retired without the word that the "
                "turn may be interrupted. Nothing was archived.",
            )
        )
    try:
        reply = bridge_call(config, "agent-archive", {"agentId": agent_id})
    except PaseoBridgeFailure as error:
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(seat.receipt),
                seat.seat_role,
                "host-unreachable",
                f"The Paseo runtime cannot be reached: {error.code}: {error} Nothing was archived.",
            )
        ) from error
    if not isinstance(reply, dict) or reply.get("archived") is not True:
        raise _RetireTerminal(
            _refusal(
                _task_ref_of(seat.receipt),
                seat.seat_role,
                "host-unreachable",
                "The Paseo bridge returned an unreadable archive answer. Nothing was recorded.",
            )
        )
    recorded = _record_retirement(seat.path, seat.caller, agent_id, request.reason)
    if recorded != "recorded":
        intent = {
            "caller": f"{seat.caller.role} agent {seat.caller.agent_id}",
            "callerAgentId": seat.caller.agent_id,
            "reason": request.reason,
        }
        raise _RetireTerminal(
            {
                "ok": False,
                "operation": "retire_child",
                "status": "retire-record-failed",
                "role": seat.seat_role,
                **(
                    {"taskDocumentRef": _task_ref_of(seat.receipt)}
                    if _task_ref_of(seat.receipt)
                    else {}
                ),
                "detail": (
                    f"Agent {agent_id} was archived on the host, but the launch record "
                    f"could not be written ({recorded}); the record does not name the "
                    "caller and reason. Retry the identical call to converge; it records "
                    f"{intent['caller']} for {request.reason!r}."
                ),
                "retireIntent": intent,
            }
        )
    return {
        "ok": True,
        "operation": "retire_child",
        "status": "retired",
        "role": seat.seat_role,
        **({"taskDocumentRef": _task_ref_of(seat.receipt)} if _task_ref_of(seat.receipt) else {}),
        "detail": (
            f"Agent {agent_id} was archived on the host without a turn by "
            f"{seat.caller.role} agent {seat.caller.agent_id}; the launch record names "
            "the caller and the reason."
        ),
    }


def _native_caller(environment: Mapping[str, str] | None) -> AgentBinding:
    binding = read_agent_binding(environment)
    if binding is None:
        raise ValueError("no role binding names the caller")
    return binding


def _is_minted_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _task_ref_of(receipt: dict[str, Any]) -> Any:
    selection = receipt.get("selection")
    return selection.get("taskDocumentRef") if isinstance(selection, dict) else None


def _role_of(receipt: dict[str, Any], fallback: str) -> str:
    role = receipt.get("role")
    return role if isinstance(role, str) and role else fallback


def _scan_seat_records(
    config: McpRuntimeConfig, agent_id: str
) -> tuple[list[tuple[Path, dict[str, Any]]], list[tuple[Path, dict[str, Any]]]]:
    """All launch records naming one agent: current ones, then history ones."""

    from agents_remember.cli.role_launch_receipts import (  # noqa: PLC0415
        EXECUTIONS_DIRECTORY,
        _read_receipt,
    )

    roots = [config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY]
    roots.extend(
        sorted(
            (config.coordination_root / "tasks").glob(f"**/notes/reports/{EXECUTIONS_DIRECTORY}")
        )
    )
    currents: list[tuple[Path, dict[str, Any]]] = []
    histories: list[tuple[Path, dict[str, Any]]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            if "message-bindings" in path.relative_to(root).parts:
                continue
            try:
                receipt = _read_receipt(path)
            except ValueError:
                continue
            if receipt is None or receipt.get("agentId") != agent_id:
                continue
            if path.parent.name == "history":
                histories.append((path, receipt))
            else:
                currents.append((path, receipt))
    return currents, histories


def _locate_seat(
    config: McpRuntimeConfig, agent_id: str, role: str
) -> tuple[Path, dict[str, Any], bool]:
    """The current receipt recording one agent, or its archived record with ``True``.

    Every matching record is enumerated first: an agent recorded twice, in any
    mix of current and archived records, is refused as ambiguous, with no host
    call and no disk write. A sole current record wins over no history; a sole
    archived record reports the seat gone.
    """

    currents, histories = _scan_seat_records(config, agent_id)
    if len(currents) + len(histories) > 1:
        where = [f"current {path}" for path, _receipt in currents]
        where += [f"archived {path}" for path, _receipt in histories]
        raise _RetireTerminal(
            _refusal(
                None,
                role,
                "ambiguous-agent",
                f"Agent {agent_id} is recorded by {len(where)} launch records "
                f"({', '.join(str(item) for item in where)}); retirement "
                "refuses an ambiguous target. Nothing was archived.",
            )
        )
    if currents:
        path, receipt = currents[0]
        return path, receipt, False
    if histories:
        path, receipt = histories[0]
        return path, receipt, True
    raise _RetireTerminal(
        _refusal(
            None,
            role,
            "unknown-agent",
            f"No launch record of this AR line knows agent {agent_id}. Nothing was archived.",
        )
    )


def _record_retirement(
    path: Path,
    caller: AgentBinding,
    agent_id: str,
    reason: str,
) -> str:
    """Write one retirement into its launch record; say what the write did.

    Returns ``recorded`` when the receipt names the caller and reason,
    ``receipt-unreadable`` when the record could not be read back, and
    ``write-failed`` when the updated record could not be written. Callers
    report a partial failure truthfully instead of claiming the record.
    """

    from agents_remember.cli.role_held_messages import (  # noqa: PLC0415
        end_waiting_as,
        record_lines,
        recount_waiting_for,
    )
    from agents_remember.cli.role_launch_receipts import (  # noqa: PLC0415
        _read_receipt,
        _write_receipt,
    )

    now = datetime.now(UTC).isoformat()
    try:
        current = _read_receipt(path)
    except ValueError:
        return "receipt-unreadable"
    if current is None:
        return "receipt-unreadable"
    current.update(
        status="stopped",
        detail=f"Retired by {caller.role} agent {caller.agent_id}: {reason}",
        retiredBy={
            "caller": f"{caller.role} agent {caller.agent_id}",
            "callerAgentId": caller.agent_id,
            "reason": reason,
            "at": now,
        },
        retireReason=reason,
        updatedAt=now,
    )
    workspace = current.get("workspace")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    if isinstance(folder, str) and folder:
        try:
            ended = end_waiting_as(folder, agent_id)
        except ValueError:
            ended = []
        if ended:
            record_lines(current, ended, "not-delivered-archived-or-gone")
    recount_waiting_for(current)
    try:
        _write_receipt(path, current)
    except OSError:
        return "write-failed"
    return "recorded"


def _refusal(task_ref: Any, role: str, status: str, detail: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": "retire_child",
        "status": status,
        "role": role,
        **({"taskDocumentRef": task_ref} if task_ref else {}),
        "detail": detail,
    }
