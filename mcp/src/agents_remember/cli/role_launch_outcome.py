"""Publish a host launch outcome, including recoverable empty step-up seats."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from agents_remember.cli import role_launch_receipts as receipts
from agents_remember.cli.paseo_launch import LaunchOutcome


def publish_launch_outcome(
    path: Path, receipt: dict[str, Any], launch_call: dict[str, Any], outcome: LaunchOutcome
) -> JSONResponse:
    if outcome.kind == "created":
        receipt["execution"] = outcome.execution
        receipt["agent"] = outcome.applied
        if outcome.workspace_preparation in {"created", "found", "opened"}:
            receipt.setdefault("preparation", {})["workspace"] = outcome.workspace_preparation
        receipts._observe_launch_choices(receipt, outcome.applied)
        if outcome.warning:
            receipt["warning"] = " ".join(filter(None, (receipt.get("warning"), outcome.warning)))
        agent_call = launch_call.get("agent")
        held = receipt.get("heldSeat") is True and (
            not isinstance(agent_call, dict) or "prompt" not in agent_call
        )
        receipt.update(
            status="held" if held else "running",
            hostAgentExists=True,
            detail=(
                "The Paseo runtime created the role agent without a first message; the seat is held and takes its first turn with the first message addressed to it."
                if held
                else "The Paseo runtime created the role agent; AR acceptance remains with its owner."
            ),
            updatedAt=receipts._now_iso(),
        )
        if not held:
            receipt.pop("replayRequest", None)
        receipts._write_receipt(path, receipt)
        return JSONResponse(receipts._public_execution(receipt))
    if outcome.kind == "refused":
        return _refused_outcome(path, receipt, launch_call, outcome)
    receipt.update(
        status="unknown",
        detail=f"The launch has no usable answer ({outcome.code}): {outcome.message} Retry this same request to repeat the saved call under the same agent id.",
        updatedAt=receipts._now_iso(),
    )
    receipts._write_receipt(path, receipt)
    return JSONResponse(receipts._public_execution(receipt), status_code=202)


def _refused_outcome(
    path: Path, receipt: dict[str, Any], launch_call: dict[str, Any], outcome: LaunchOutcome
) -> JSONResponse:
    if outcome.lost_agent_id:
        receipt["pendingArchiveAgentId"] = outcome.lost_agent_id
    elif not outcome.predecessor_settled:
        receipt["pendingArchiveAgentId"] = launch_call["archiveAgentId"]
    retry_step_up = (
        bool(receipt.get("stepUp")) and not outcome.lost_agent_id and outcome.predecessor_settled
    )
    detail = (
        "The agent of this launch never got its first message and the Paseo runtime cannot open its session again, so this launch is closed. "
        + f"{receipts._lost_agent_advice(receipt, outcome.lost_agent_id)} {outcome.message}"
        if outcome.lost_agent_id
        else f"The Paseo runtime refused the launch; no agent exists under the minted agent id. {outcome.message}"
    )
    if retry_step_up:
        detail += " The step-up seat is empty; repeat this same call to start it."
    receipt.update(
        status="rejected",
        hostAgentExists=outcome.lost_agent_id is not None,
        detail=detail,
        updatedAt=receipts._now_iso(),
    )
    if not retry_step_up:
        receipt.pop("replayRequest", None)
    receipts._write_receipt(path, receipt)
    return JSONResponse(receipts._public_execution(receipt), status_code=502)
