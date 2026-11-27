"""Deferred held-seat reservation, predecessor settlement, and opening answers (BUG-R58@v2).

Held seats are reserved on disk without creating a host agent until the first
message addressed to them opens the seat. Keeping reservation, predecessor-archive
settlement, retried-ID lookup, and opened-answer construction in one cohesive module
keeps ``role_launch_receipts`` and ``paseo_role_tools`` within the file-size budget.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from agents_remember.cli import paseo_launch
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_launch import LaunchOutcome
from agents_remember.cli.role_held_store import settle_held_store
from agents_remember.cli.role_instructions import instruction_metadata, launch_choice_metadata
from agents_remember.cli.role_retirement_receipt import finish_replacing_predecessor
from agents_remember.cli.role_step_up_decisions import record_step_up
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig


def is_deferred_held_receipt(receipt: Mapping[str, Any]) -> bool:
    """Whether ``receipt`` records a held seat whose host agent is deferred until open."""

    agent_id = receipt.get("agentId")
    return (
        receipt.get("status") == "held"
        and receipt.get("hostAgentExists") is False
        and isinstance(agent_id, str)
        and bool(agent_id)
    )


def retried_outcome_agent_id(outcome: LaunchOutcome, current: dict[str, Any]) -> str | None:
    """Return a retried attempt's agent ID when confirmation retry minted one."""

    candidates = [
        outcome.execution.get("agentId") if isinstance(outcome.execution, dict) else None,
        (
            outcome.effective_launch_call.get("agent", {}).get("agentId")
            if isinstance(outcome.effective_launch_call, dict)
            and isinstance(outcome.effective_launch_call.get("agent"), dict)
            else None
        ),
        outcome.lost_agent_id,
    ]
    recorded = current.get("agentId")
    for candidate in candidates:
        if isinstance(candidate, str) and candidate and candidate != recorded:
            return candidate
    return None


def opened_held_seat_answer(
    drain: dict[str, Any], fallback_agent_id: str, line: str
) -> dict[str, Any]:
    """Build the ``role_message`` answer naming the live agent and its confirmation (L58-R8)."""

    execution = drain.get("execution")
    live_agent_id = (
        execution["agentId"]
        if isinstance(execution, dict)
        and isinstance(execution.get("agentId"), str)
        and execution["agentId"]
        else fallback_agent_id
    )
    result: dict[str, Any] = {
        "ok": True,
        "status": "accepted",
        "detail": str(drain.get("detail") or "The held seat opened."),
        "recipientAgentId": live_agent_id,
        "senderLine": line,
        "taken": "started",
        "openedHeldSeat": True,
    }
    confirmation = drain.get("bindingConfirmation")
    if isinstance(confirmation, dict):
        result["bindingConfirmation"] = confirmation
    return result


def apply_leaf_holder(
    public: dict[str, Any], receipt: dict[str, Any], status: Any, holder: str | None
) -> None:
    """Name the leaf seat's holder while it holds the seat, including deferred held seats."""

    if holder is None and is_deferred_held_receipt(receipt):
        holder = str(receipt["agentId"])
        public["agentId"] = holder
    if holder is not None and status not in {
        "completed",
        "failed",
        "stopped",
        "rejected",
        "interrupted",
    }:
        public["holderAgentId"] = holder


def apply_leaf_closing(public: dict[str, Any], receipt: dict[str, Any]) -> None:
    """Unsettled archive status also prevents a different request moving an in-flight receipt."""

    own = receipt["leafArchive"].get("outcomes", {}).get(receipt.get("agentId"), {})
    if own.get("state") in {"archived", "gone"}:
        public["status"] = "rejected"
    public.update(startRefused=True, canStart=False, canRevive=False, canRetry=False)
    public.pop("retryPayload", None)


def public_instruction_fields(public: dict[str, Any]) -> dict[str, Any]:
    """Normalize public instruction and choice metadata on a launch execution view."""

    if isinstance(public.get("choices"), dict):
        public["choices"] = launch_choice_metadata(public["choices"])
    if isinstance(public.get("sessionOptions"), dict):
        public["sessionOptions"] = {
            **public["sessionOptions"],
            "choices": launch_choice_metadata(public["sessionOptions"].get("choices", {})),
        }
    if isinstance(public.get("instructionComposition"), dict):
        public["instructionComposition"] = instruction_metadata(public["instructionComposition"])
    return public


def _archive_predecessor(config: McpRuntimeConfig, predecessor_id: str) -> tuple[bool, str, str]:
    """Archive a replaced live predecessor before reserving a deferred held seat."""

    try:
        reply = paseo_launch.bridge_call(config, "agent-archive", {"agentId": predecessor_id})
        settled = reply.get("archived") is True or reply.get("found") is False
        return (
            settled,
            "paseo_archive_unconfirmed",
            f"The archive of predecessor agent {predecessor_id} was not confirmed.",
        )
    except PaseoBridgeFailure as error:
        return False, error.code, str(error)


def _publish_unsettled_predecessor(
    config: McpRuntimeConfig,
    path: Path,
    current: dict[str, Any],
    launch_call: dict[str, Any],
    outcome: LaunchOutcome,
) -> JSONResponse:
    """Keep predecessor archive debt and reserve nothing when archive or settlement fails (L58-R5)."""

    from agents_remember.cli import role_launch_receipts as receipts  # noqa: PLC0415
    from agents_remember.cli.role_launch_outcome import publish_launch_outcome  # noqa: PLC0415

    current["pendingArchiveAgentId"] = str(launch_call["archiveAgentId"])
    if receipts._leaf_is_closing(current):
        receipts._write_receipt(path, current)
        return receipts._refuse_closing_launch(config, path, current, creation_returned=False)
    response = publish_launch_outcome(path, current, launch_call, outcome)
    record_step_up(config, current)
    return response


def defer_held_launch(
    config: McpRuntimeConfig,
    path: Path,
    current: dict[str, Any],
    launch_call: dict[str, Any],
    prior: Any,
) -> JSONResponse:
    """Publish a held seat without creating a host agent; the agent is created on open."""

    from agents_remember.cli import role_launch_receipts as receipts  # noqa: PLC0415

    saved_call = dict(launch_call)
    predecessor_id = launch_call.get("archiveAgentId")
    archive_ok = True
    archive_code = "paseo_archive_unconfirmed"
    archive_message = ""
    if isinstance(predecessor_id, str) and predecessor_id:
        archive_ok, archive_code, archive_message = _archive_predecessor(config, predecessor_id)
        if archive_ok:
            saved_call.pop("archiveAgentId", None)
    with exclusive_file_lock(path, "role execution receipt"):
        current = receipts._same_request_receipt(path, current)
        if isinstance(predecessor_id, str) and predecessor_id:
            if not archive_ok:
                return _publish_unsettled_predecessor(
                    config,
                    path,
                    current,
                    launch_call,
                    LaunchOutcome(
                        kind="no-answer",
                        code=archive_code,
                        message=archive_message,
                        predecessor_settled=False,
                    ),
                )
            settled = finish_replacing_predecessor(
                path,
                current,
                launch_call,
                LaunchOutcome("created", predecessor_settled=True),
            )
            if not settled.predecessor_settled:
                return _publish_unsettled_predecessor(config, path, current, launch_call, settled)
            if prior is not None:
                settle_held_store(prior, host_state="archived")
            if current.get("pendingArchiveAgentId") == predecessor_id:
                current.pop("pendingArchiveAgentId", None)
        if receipts._leaf_is_closing(current):
            receipts._write_receipt(path, current)
            return receipts._refuse_closing_launch(config, path, current, creation_returned=False)
        current.update(
            status="held",
            hostAgentExists=False,
            execution={},
            replayRequest=saved_call,
            detail=(
                "The role seat is held without a host agent; the Paseo runtime will create it "
                "and take its first turn with the first message addressed to it."
            ),
            updatedAt=receipts._now_iso(),
        )
        receipts._write_receipt(path, current)
        record_step_up(config, current)
        return JSONResponse(receipts._public_execution(current))
