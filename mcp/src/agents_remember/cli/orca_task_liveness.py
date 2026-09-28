"""Refresh and reconcile saved Orca execution outcomes without redispatching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from agents_remember.cli.orca_runtime import (
    OrcaRuntimeFailure,
)
from agents_remember.cli.orca_runtime import (
    runtime_call as _runtime_call,
)
from agents_remember.cli.orca_task_receipts import (
    _archive_receipt,
    _execute_prepared_launch,
    _now_iso,
    _public_execution,
    _read_receipt,
    _write_receipt,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import OrcaDispatchRequest


def _reconcile_prior_execution(
    config: McpRuntimeConfig,
    path: Path,
    request: OrcaDispatchRequest,
    request_digest: str,
) -> JSONResponse | None:
    current = _read_receipt(path)
    if current is None:
        return None
    if current.get("requestId") == str(request.request_id):
        if current.get("requestDigest") != request_digest:
            raise HTTPException(
                status_code=409,
                detail="This request id is already bound to different AR task or agent-selection content.",
            )
        if current.get("status") in {"starting", "unknown"}:
            return _execute_prepared_launch(config, path, current)
        return JSONResponse(_refresh_execution(config, path, current))
    status = _refresh_execution(config, path, current)
    if status["status"] not in {"completed", "failed", "stopped", "rejected"}:
        raise HTTPException(
            status_code=409,
            detail="This AR role selection already has a live or unresolved Orca session. Refresh or revive it before starting another.",
        )
    _archive_receipt(path, current)
    return None


def _refresh_execution(
    config: McpRuntimeConfig,
    path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    if receipt.get("status") in {"completed", "failed", "stopped", "rejected"}:
        return _public_execution(receipt)
    reference = receipt.get("execution")
    if not isinstance(reference, dict):
        return _public_execution(receipt)
    try:
        if reference.get("kind") == "terminal":
            _refresh_terminal_execution(config, receipt)
        else:
            _refresh_structured_session(config, receipt)
    except (KeyError, OrcaRuntimeFailure, ValueError) as error:
        if getattr(error, "code", None) == "terminal_gone":
            receipt.update(
                status="stopped",
                detail="Orca reports terminal_gone for this saved execution; the native terminal is no longer present.",
                terminalObservedAt=_now_iso(),
                updatedAt=_now_iso(),
            )
        else:
            receipt.update(
                status="unknown",
                detail=f"Live refresh is inconclusive ({getattr(error, 'code', 'runtime-read')}); the saved native execution reference remains attached to this AR selection.",
                updatedAt=_now_iso(),
            )
    _write_receipt(path, receipt)
    return _public_execution(receipt)


def _refresh_terminal_execution(config: McpRuntimeConfig, receipt: dict[str, Any]) -> None:
    reference = receipt.get("execution")
    handle = reference.get("handle") if isinstance(reference, dict) else None
    if not isinstance(handle, str) or not handle:
        receipt.update(
            status="unknown",
            detail="The saved terminal reference is incomplete; its live state cannot be verified.",
            updatedAt=_now_iso(),
        )
        return
    terminal_result = _runtime_call(config, "terminal-show", {"handle": handle})
    terminal = terminal_result.get("terminal")
    agent_result = _runtime_call(config, "terminal-status", {"handle": handle})
    agent_status = agent_result.get("agentStatus")
    if not isinstance(terminal, dict) or not isinstance(agent_status, dict):
        receipt.update(
            status="unknown",
            detail="Orca returned incomplete terminal and agent status evidence; the saved session may still exist.",
            updatedAt=_now_iso(),
        )
        return
    if terminal.get("handle") not in {None, handle} or agent_status.get("handle") not in {
        None,
        handle,
    }:
        receipt.update(
            status="unknown",
            detail="Orca's terminal status did not match the saved execution handle; the live state is unknown.",
            updatedAt=_now_iso(),
        )
        return
    connected = terminal.get("connected") is True
    running = agent_status.get("isRunningAgent")
    if connected and running is True:
        receipt.update(
            status="running",
            detail="Orca confirms this exact native terminal is connected and its agent status is running.",
            updatedAt=_now_iso(),
        )
    elif connected and running is False:
        receipt.update(
            status="stopped",
            detail="Orca confirms the saved terminal is connected but its agent is no longer running.",
            terminalObservedAt=_now_iso(),
            updatedAt=_now_iso(),
        )
    else:
        receipt.update(
            status="unknown",
            detail="Orca's agent status is not corroborated by a connected terminal; live state remains unknown.",
            updatedAt=_now_iso(),
        )


def _refresh_structured_session(config: McpRuntimeConfig, receipt: dict[str, Any]) -> None:
    reference = receipt["execution"]
    session_id = reference["sessionId"]
    status = _runtime_call(config, "agent-history", {"sessionId": session_id})
    state = status.get("status")
    turn_state = status.get("turnState")
    outcome = status.get("turnOutcome")
    if status.get("lastAssistantMessage"):
        receipt["result"] = {"summary": status["lastAssistantMessage"]}
    if state in {"working", "attention"} or turn_state == "running":
        receipt.update(
            status="running",
            detail="The Orca structured session is active; continue or inspect it in native Chats.",
            updatedAt=_now_iso(),
        )
        return
    if turn_state == "completed" and outcome in {"success", "failure", "cancellation"}:
        terminal_status = "completed" if outcome == "success" else "failed"
        receipt.update(
            status=terminal_status,
            detail="Orca observed the native agent turn complete; AR review and acceptance remain pending.",
            terminalObservedAt=_now_iso(),
            updatedAt=_now_iso(),
        )
        return
    if turn_state in {"interrupted", "unverifiable"}:
        candidates = _runtime_call(config, "restart-resumable", {})
        sessions = candidates.get("sessions", [])
        exact = next(
            (
                row
                for row in sessions
                if isinstance(row, dict)
                and row.get("sessionId") == session_id
                and row.get("workspaceId") == reference.get("worktreeId")
                and row.get("agent") == receipt.get("agent", {}).get("id")
            ),
            None,
        )
        receipt.update(
            status="interrupted",
            canRevive=exact is not None,
            detail=(
                "Orca offers this exact structured session for native resume."
                if exact is not None
                else "The session was interrupted, but Orca has no exact validated resume offer."
            ),
            updatedAt=_now_iso(),
        )
        return
    receipt.update(
        status="running",
        detail="The native structured session is retained; its turn outcome is not yet terminal.",
        updatedAt=_now_iso(),
    )
