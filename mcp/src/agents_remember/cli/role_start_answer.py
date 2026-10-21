"""The role-start tool answer projected from its durable execution receipt."""

from __future__ import annotations

from typing import Any

from agents_remember.cli import paseo_role_tools as tools
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_receipts import _public_execution
from agents_remember.cli.role_step_up import step_up_note
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest


def started_answer(config: McpRuntimeConfig, request: RoleDispatchRequest) -> dict[str, Any]:
    """The tool's answer, read from the receipt the start path wrote for this request."""
    try:
        receipt = tools._read_receipt(
            tools._receipt_path(
                config,
                request,
                request.request_id if request.role in tools.TASKLESS_ROLES else None,
            )
        )
    except tools.HTTPException as error:
        raise tools._launch_refused(str(error.detail)) from error
    if receipt is None or receipt.get("requestId") != str(request.request_id):
        raise tools._launch_refused("The start was answered but its receipt cannot be read back.")
    execution = str(receipt.get("status"))
    agent_id = tools._started_agent_id(receipt)
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
        tools._refuse_agent_that_is_gone(config, agent_id, execution)
    report = receipt.get("report")
    artifact = receipt.get("handoverArtifact")
    next_action = {
        "running": None,
        "held": "The seat is held and takes its first turn with the first message addressed to it; no new start is needed.",
        "rejected": "The launch is closed and agentId names no agent that can be used. Resolve the reason in detail, then start again with a new request id.",
        "unknown": f"Call {tools.ROLE_START_TOOL} again with the same request id and selection: it reconciles the same agent and never creates a second.",
    }[launch]
    if launch == "rejected" and _public_execution(receipt).get("canRetry") is True:
        next_action = (
            f"Resolve the reason in detail, then call {tools.ROLE_START_TOOL} again with the "
            "same request id and selection, including the same step-up reason and overrides; "
            "it repeats the saved call for this empty seat."
        )
    result = {
        "ok": launch == "running",
        "status": launch,
        "detail": str(receipt.get("detail") or "")
        + tools._projects_preparation_note(receipt)
        + step_up_note(receipt),
        **({"stepUp": receipt["stepUp"]} if receipt.get("stepUp") else {}),
        "requestId": str(request.request_id),
        "role": request.role,
        "agentId": agent_id,
        "secondCopyLoaded": tools.second_copy_loaded(receipt),
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
    return result
