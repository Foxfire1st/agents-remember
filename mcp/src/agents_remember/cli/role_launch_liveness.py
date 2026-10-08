"""Refresh a saved execution from its agent's state, and reconcile a new request with it.

Every refresh reads the agent once and applies the first matching row of the status table
(``paseo_status``, PNT-R07). The read changes nothing in the runtime; the receipt is rewritten
only when the row changes what it holds, and never when the host cannot be reached.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from agents_remember.cli.paseo_launch import PASEO_AGENT_KIND
from agents_remember.cli.paseo_status import AgentReading, read_agent, resume_agent, status_row
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


def _reconcile_prior_execution(
    config: McpRuntimeConfig,
    path: Path,
    request: RoleDispatchRequest,
    request_digest: str,
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
