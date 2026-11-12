"""The role-start tool answer projected from its durable execution receipt."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from agents_remember.application.agent_binding import exclusion_keeps_copy_out
from agents_remember.cli.dashboard_address import chat_address_for, dashboard_address_for
from agents_remember.cli.paseo_role_caller import _host_unreachable
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_preparation import ROLE_START_TOOL
from agents_remember.cli.role_launch_receipts import _public_execution, _read_receipt, _receipt_path
from agents_remember.cli.role_message_delivery import _Refused
from agents_remember.cli.role_step_up import step_up_note
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import TASKLESS_ROLES
from agents_remember.models.role_launcher import RoleDispatchRequest


def started_answer(config: McpRuntimeConfig, request: RoleDispatchRequest) -> dict[str, Any]:
    """The tool's answer, read from the receipt the start path wrote for this request."""
    try:
        receipt = _read_receipt(
            _receipt_path(
                config,
                request,
                request.request_id if request.role in TASKLESS_ROLES else None,
            )
        )
    except HTTPException as error:
        raise _launch_refused(str(error.detail)) from error
    if receipt is None or receipt.get("requestId") != str(request.request_id):
        raise _launch_refused("The start was answered but its receipt cannot be read back.")
    execution = str(receipt.get("status"))
    agent_id = _started_agent_id(receipt)
    launch = (
        "rejected"
        if execution == "rejected"
        else "unknown"
        if execution in {"starting", "unknown"}
        else "held"
        if execution == "held"
        else "running"
    )
    if execution == "stopped" and isinstance(agent_id, str):
        _refuse_agent_that_is_gone(config, agent_id, execution)
    report = receipt.get("report")
    artifact = receipt.get("handoverArtifact")
    next_action = {
        "running": None,
        "held": "The seat is held and takes its first turn with the first message addressed to it; no new start is needed.",
        "rejected": "The launch is closed and agentId names no agent that can be used. Resolve the reason in detail, then start again with a new request id.",
        "unknown": f"Call {ROLE_START_TOOL} again with the same request id and selection: it reconciles the same agent and never creates a second.",
    }[launch]
    if launch == "rejected" and _public_execution(receipt).get("canRetry") is True:
        next_action = (
            f"Resolve the reason in detail, then call {ROLE_START_TOOL} again with the "
            "same request id and selection, including the same step-up reason and overrides; "
            "it repeats the saved call for this empty seat."
        )
    result = {
        "ok": launch == "running",
        "status": launch,
        "detail": str(receipt.get("detail") or "")
        + _projects_preparation_note(receipt)
        + step_up_note(receipt),
        **({"stepUp": receipt["stepUp"]} if receipt.get("stepUp") else {}),
        "requestId": str(request.request_id),
        "role": request.role,
        "agentId": agent_id,
        "secondCopyLoaded": second_copy_loaded(receipt),
        "parentAgentId": receipt.get("parentAgentId"),
        "reportPath": report.get("path") if isinstance(report, dict) else None,
        "handoverArtifactPath": artifact.get("path") if isinstance(artifact, dict) else None,
        "handoverFilePath": (
            receipt["handoverFile"].get("path")
            if isinstance(receipt.get("handoverFile"), dict)
            else None
        ),
        "executionStatus": execution,
        **({"preparation": receipt["preparation"]} if "preparation" in receipt else {}),
        "choices": launch_choice_metadata(receipt.get("choices", {})),
        **(
            {"taskDocumentFiles": receipt["taskDocumentFiles"]}
            if receipt.get("taskDocumentFiles")
            else {}
        ),
        **({"warning": receipt["warning"]} if receipt.get("warning") else {}),
        **({"nextAction": next_action} if next_action else {}),
    }
    if request.role == "bootstrap":
        # MIK-R196 rule 1: the one call answers with the seat's identity and the address at
        # which the user continues the setup, not only with a path inside the host. The address
        # is answered only when something serves it: the dashboard owner is asked first, and a
        # dashboard that does not come up is reported rather than published (L188-B24, 7c1153ee).
        result.update(dashboard_address_for(config))
        result.update(chat_address_for(config, agent_id))
        if launch == "running":
            address = result.get("dashboardAddress")
            result["nextAction"] = (
                f"Open {address} and continue the setup with this seat in the chat it lists; "
                "the call started no task and no other role."
                if address
                else (
                    "The dashboard could not be started, so this answer names no address to open: "
                    f"{result.get('dashboardAddressDetail')}. Start the dashboard, then open this "
                    "answer's seat in the chat the dashboard lists."
                )
            )
    return result


def _launch_refused(detail: str) -> _Refused:
    return _Refused(
        "launch-refused",
        detail,
        "Resolve the reason named in detail; repeat the same request id only for the same "
        "selection and override.",
    )


def _projects_preparation_note(receipt: dict[str, Any]) -> str:
    preparation = receipt.get("preparation")
    if isinstance(preparation, dict) and preparation.get("workspace") == "opened":
        return " Projects workspace was opened; the host does not report whether it was created."
    return ""


def _started_agent_id(receipt: dict[str, Any]) -> str | None:
    """The agent a saved receipt's execution names, as _public_execution reports it."""

    reference = receipt.get("execution")
    if isinstance(reference, dict):
        agent_id = reference.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    agent_id = receipt.get("agentId")
    return agent_id if isinstance(agent_id, str) and agent_id else None


def second_copy_loaded(receipt: dict[str, Any]) -> bool:
    """Whether the started agent carries the product's tools a second time.

    It follows from what the launch handed the host (MIK-R132 rule 5), never from the
    harness's name: the copy is kept out only by a record of the mode the host reads. A
    receipt without a record, and one with a record of an earlier build, which handed the
    host nothing, read as loaded. An agent that was given no task server carries no second
    copy. The record is read from the receipt, so a repeat answered from its receipt makes
    no new call.
    """

    replay = receipt.get("replayRequest")
    applied = replay.get("serverExclusion") if isinstance(replay, dict) else None
    if applied is None:
        # The receipt drops replayRequest after the launch; the record survives at its top.
        applied = receipt.get("serverExclusion")
    tool_server = receipt.get("toolServer")
    carries_task_server = not isinstance(tool_server, dict) or bool(tool_server.get("applied"))
    return carries_task_server and not exclusion_keeps_copy_out(applied)


def _refuse_agent_that_is_gone(config: McpRuntimeConfig, agent_id: str, execution: str) -> None:
    """Refuse the repeat of a request whose agent is archived or unknown to the host.

    A ``stopped`` execution can also be one whose last turn was cancelled; that agent is live and
    the repeat answers ``running`` for it. Which of the two it is, the host says: one read.
    """

    reading = read_agent(config, agent_id)
    if not reading.reachable:
        raise _host_unreachable(
            f"{reading.unreachable_reason} Whether agent {agent_id} of this request still "
            "exists could not be read."
        )
    if reading.found and not reading.archived:
        return
    state = "is archived" if reading.found else "is unknown to the host"
    raise _Refused(
        "launch-refused",
        f"Agent {agent_id} of this request {state}; a repeat of the request id does not bring "
        "it back.",
        "Start again with a new request id.",
        agentId=agent_id,
        executionStatus=execution,
    )
