"""Refresh a saved execution from its agent's state, and reconcile a new request with it.

Every refresh reads the agent once and applies the first matching row of the status table
(``paseo_status``, PNT-R07). The read changes nothing in the runtime; the receipt is rewritten
only when the row changes what it holds, and never when the host cannot be reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from agents_remember.application.role_launch_context import LEAF_ROLES
from agents_remember.cli.paseo_launch import PASEO_AGENT_KIND
from agents_remember.cli.paseo_status import AgentReading, read_agent, resume_agent, status_row
from agents_remember.cli.role_held_messages import (
    end_waiting_as,
    record_lines,
    recount_waiting_for,
)
from agents_remember.cli.role_launch_receipts import (
    _archive_receipt,
    _execute_prepared_launch,
    _now_iso,
    _public_execution,
    _read_receipt,
    _request_digest,
    _write_receipt,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest

_CLOSED_STATUSES = frozenset({"completed", "failed", "stopped"})


def execution_is_closed(status: object) -> bool:
    """Whether the existing execution state permits a replacement start."""
    return status in _CLOSED_STATUSES or status == "rejected"


class HostUnreachableRefusal(HTTPException):
    """A request refused because the runtime gave no usable answer for it.

    The dispatch route answers it with ``hostUnreachable: true`` beside the detail.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=409, detail=detail)


# The receipt fields a row may change; a refresh that changes none of them writes nothing.
_ROW_FIELDS = ("status", "detail", "canRevive", "result", "resumeRefused")


@dataclass(frozen=True, slots=True)
class _Replacement:
    """The replacement a start asks for: who asks, and which holder goes first."""

    started_by: Any | None = None
    replaces_agent_id: str | None = None
    interrupt_turn: bool = False
    request_id: str | None = None


def _reconcile_prior_execution(
    config: McpRuntimeConfig,
    path: Path,
    request: RoleDispatchRequest,
    request_digest: str,
    replacement: _Replacement | None = None,
) -> JSONResponse | None:
    current = _read_receipt(path)
    if current is None:
        return None
    if current.get("requestId") == str(request.request_id):
        legacy_digest = (
            _request_digest(request, request, role_name="system-specialist")
            if current.get("role") == "system-specialist" and request.role == "investigator"
            else request_digest
        )
        if current.get("requestDigest") not in {request_digest, legacy_digest}:
            raise HTTPException(
                status_code=409,
                detail="This request id is already bound to different AR task or agent-selection content.",
            )
        if current.get("status") in {"starting", "unknown"}:
            return _execute_prepared_launch(config, path, current)
        return JSONResponse(_refresh_execution(config, path, current))
    if str(current.get("role")) in LEAF_ROLES or str(request.role) in LEAF_ROLES:
        return _reconcile_leaf_occupancy(
            config, path, current, replacement or _Replacement(),
        )
    # The open-execution rule is applied to what the agent is doing now, not to the saved status.
    status = _refresh_execution(config, path, current)
    if status.get("hostUnreachable") is True:
        # The agent could not be read, so nobody knows whether the execution is open: its saved
        # status may be stale, and a new launch would archive an agent that is at work.
        raise HostUnreachableRefusal(
            "The Paseo runtime cannot be reached, so the open-execution rule cannot be applied "
            f"to this AR role selection (request {current.get('requestId')}, last known status "
            f"{status['status']}); nothing was started or archived. "
            f"{status['hostUnreachableReason']}"
        )
    if not execution_is_closed(status["status"]):
        raise HTTPException(
            status_code=409,
            detail=(
                "This AR role selection already has an open execution (request "
                f"{current.get('requestId')}, status {status['status']}). Refresh, retry or "
                "revive it before starting another."
            ),
        )
    _archive_receipt(path, current)
    return None


def _reconcile_leaf_occupancy(
    config: McpRuntimeConfig,
    path: Path,
    current: dict[str, Any],
    replacement: _Replacement,
) -> JSONResponse | None:
    """One seat holds one agent (MIK-R100 rule 9): the occupied-seat contract.

    A leaf seat is held by its recorded agent as long as that agent is not archived,
    whether it is in a turn, idle, held or closed -- a finished turn does not free it.
    A start with the holder's own request id already reconciled above; here a new
    request id for an occupied seat is refused naming the holder, and a start that
    says it replaces the holder retires it first, in the same call, by rule 8. Only
    an archived or vanished holder frees the seat. An unreadable holder refuses as
    the host being unreachable, never as free.
    """

    holder = _recorded_agent_id(current)
    if holder is None:
        if str(current.get("status")) in {"starting", "unknown"}:
            # The seat is held by no agent yet, but a launch of it is still in
            # flight: archiving its receipt would steal the seat mid-launch.
            raise HTTPException(
                status_code=409,
                detail=(
                    "A launch of this leaf seat is still in flight (request "
                    f"{current.get('requestId')}, status {current.get('status')}); "
                    "nothing was started or archived. Start again after that launch settles."
                ),
            )
        _archive_receipt(path, current)
        return None
    reading = read_agent(config, holder)
    if not reading.reachable:
        raise HostUnreachableRefusal(
            "The Paseo runtime cannot be reached, so the holder of this leaf seat "
            f"(agent {holder}) cannot be read; nothing was started or archived. "
            f"{reading.unreachable_reason}"
        )
    if not reading.found or reading.archived:
        _archive_receipt(path, current)
        return None
    replaces_agent_id = replacement.replaces_agent_id
    started_by = replacement.started_by
    if replaces_agent_id is not None:
        if replaces_agent_id != holder:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent {replaces_agent_id} holds no seat of this leaf selection; "
                    f"its holder is agent {holder}. Nothing was archived. Start again "
                    "with the holder's agent id, or without a replacement."
                ),
            )
        starter = current.get("parentAgentId")
        if started_by is None:
            caller = "the launcher"
        elif getattr(started_by, "agent_id", None) == starter:
            caller = f"agent {started_by.agent_id}"
        else:
            owner = f"agent {starter}" if isinstance(starter, str) and starter else "the launcher"
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent {getattr(started_by, 'agent_id', '?')} did not start the holder "
                    f"(agent {holder}) and cannot replace it; only {owner} can. "
                    "Nothing was archived."
                ),
            )
        if (reading.turn_active or reading.lifecycle == "running") and not replacement.interrupt_turn:
            # Rule 8, as in retirement: a holder in a turn is replaced only with
            # the word that its turn may be interrupted.
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Agent {holder} is in a turn; it is not replaced without the word that "
                    "the turn may be interrupted. Nothing was archived. Start again naming "
                    "the interruption, or retire it first with retire_child."
                ),
            )
        current["replaceAuthorizedBy"] = caller
        current["replaceAuthorizedAt"] = _now_iso()
        _record_holder_replacement(current, holder, caller, replacement.request_id)
        _write_receipt(path, current)
        _archive_receipt(path, current)
        return None
    owner = current.get("parentAgentId")
    started = f"agent {owner}" if isinstance(owner, str) and owner else "the launcher"
    raise HTTPException(
        status_code=409,
        detail=(
            f"This leaf seat is held by agent {holder}, started by {started}. A start with "
            f"the holder's own request id ({current.get('requestId')}) reconciles it; a new "
            "request id is refused while the holder is not archived. To replace the holder, "
            "start again with replacesAgentId naming it; a start by hand from the launcher "
            "follows the same rule and offers the replacing start on this line."
        ),
    )


def _record_holder_replacement(
    current: dict[str, Any],
    holder: str,
    caller: str,
    request_id: str | None,
) -> None:
    """Record the holder's retirement by replacement on its receipt (rules 8/9).

    The archived copy then carries who replaced it and why, like a native
    retirement: the host archival that follows needs no turn of its own.
    """

    reason = (
        f"replaced by a new start (request {request_id}) authorized by {caller}"
        if request_id
        else f"replaced by a new start authorized by {caller}"
    )
    now = _now_iso()
    current.update(
        status="stopped",
        detail=f"Replaced by {caller}: {reason}.",
        retiredBy={
            "caller": caller,
            "callerAgentId": None,
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
            ended = end_waiting_as(folder, holder)
        except ValueError:
            ended = []
        if ended:
            record_lines(current, ended, "not-delivered-archived-or-gone")
    recount_waiting_for(current)


def _refresh_execution(
    config: McpRuntimeConfig,
    path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Read the execution's agent once and report the row that applies.

    An execution for which the runtime confirmed no agent has nothing to read and is returned as
    saved: a ``rejected`` launch created none, and a launch that is still unresolved is settled
    only by a retry of its saved call.
    """

    agent_id = _recorded_agent_id(receipt)
    if agent_id is None:
        return _public_execution(receipt)
    return _apply_reading(path, receipt, read_agent(config, agent_id))


def _recorded_agent_id(receipt: dict[str, Any]) -> str | None:
    """The id of the agent the runtime confirmed for this execution, if it confirmed one."""

    reference = receipt.get("execution")
    if isinstance(reference, dict) and reference.get("kind") == PASEO_AGENT_KIND:
        agent_id = reference.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    return None


def _apply_reading(path: Path, receipt: dict[str, Any], reading: AgentReading) -> dict[str, Any]:
    """Write what the matching row says into the receipt and return the public execution."""

    if str(receipt.get("status")) == "held":
        return _apply_held_reading(path, receipt, reading)
    refusal = receipt.get("resumeRefused")
    row = status_row(
        reading,
        str(receipt.get("status")),
        refusal.get("reason") if isinstance(refusal, dict) else None,
    )
    if row.host_unreachable:
        # The saved receipt stays as it is; only this answer says that it is the last known state.
        return {
            **_public_execution(receipt),
            "hostUnreachable": True,
            "hostUnreachableReason": reading.unreachable_reason,
        }
    before = {field: receipt.get(field) for field in _ROW_FIELDS}
    if row.status is not None:
        receipt["status"] = row.status
    if row.detail is not None:
        receipt["detail"] = row.detail
    if row.can_revive is not None:
        receipt["canRevive"] = row.can_revive
    if row.summary is not None:
        receipt["result"] = {"summary": row.summary}
    if reading.found and not reading.session_closed:
        # The session is open again, so an earlier refusal to resume it no longer describes it.
        receipt.pop("resumeRefused", None)
    if before != {field: receipt.get(field) for field in _ROW_FIELDS}:
        now = _now_iso()
        receipt["updatedAt"] = now
        closed_now = before["status"] != receipt["status"] or before["result"] != receipt.get(
            "result"
        )
        if closed_now and receipt["status"] in _CLOSED_STATUSES:
            receipt["terminalObservedAt"] = now
        _write_receipt(path, receipt)
    return _public_execution(receipt)


def _apply_held_reading(
    path: Path, receipt: dict[str, Any], reading: AgentReading
) -> dict[str, Any]:
    """A held seat stays held until its first message opens it (MIK-R100 rules 2/4).

    The host's idle state never rewrites the product's word. Only an archived or
    vanished holder frees the seat.
    """

    if not reading.reachable:
        return {
            **_public_execution(receipt),
            "hostUnreachable": True,
            "hostUnreachableReason": reading.unreachable_reason,
        }
    if not reading.found or reading.archived:
        now = _now_iso()
        receipt.update(
            status="stopped",
            detail="The holder is archived or gone; the seat is free.",
            updatedAt=now,
        )
        _write_receipt(path, receipt)
    return _public_execution(receipt)


def _revive_agent(
    config: McpRuntimeConfig, path: Path, receipt: dict[str, Any], agent_id: str
) -> JSONResponse:
    """Resume the recorded agent's closed session, then report it by the row that applies.

    The agent is read first, so that only a session the table calls revivable is resumed: an open
    session is left alone and answered with the refreshed execution.
    """

    reading = read_agent(config, agent_id)
    status = _apply_reading(path, receipt, reading)
    if status.get("hostUnreachable") is True:
        raise HostUnreachableRefusal(
            "The Paseo runtime cannot be reached, so the agent was not revived and the "
            f"execution is unchanged. {status['hostUnreachableReason']}"
        )
    if status["canRevive"] is not True:
        if reading.found and not reading.archived and not reading.session_closed:
            return JSONResponse(status)
        raise HTTPException(
            status_code=409,
            detail=f"This execution cannot be revived: {status.get('detail')}.",
        )
    outcome = resume_agent(config, agent_id)
    if not outcome.reading.reachable:
        # The read above was answered and its row is written; whether the runtime resumed the
        # session is not known.
        raise HostUnreachableRefusal(
            "The agent's state was read, but the resume was not confirmed; refresh to see the "
            f"agent's state. {outcome.reading.unreachable_reason}"
        )
    if outcome.refusal is not None:
        # The runtime cannot resume this agent. A new agent comes only from a new Start.
        now = _now_iso()
        receipt.update(
            status="failed",
            detail=outcome.refusal,
            canRevive=False,
            resumeRefused={"reason": outcome.refusal, "at": now},
            terminalObservedAt=now,
            updatedAt=now,
        )
        _write_receipt(path, receipt)
        return JSONResponse(_public_execution(receipt))
    if outcome.resumed:
        receipt["revivedAt"] = _now_iso()
        _write_receipt(path, receipt)
    return JSONResponse(_apply_reading(path, receipt, outcome.reading))
