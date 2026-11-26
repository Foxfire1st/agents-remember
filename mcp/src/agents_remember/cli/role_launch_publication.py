"""Publish a prepared launch's files and initial receipt under its enclosure authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.cli import role_launch_enclosure
from agents_remember.cli.paseo_launch import (
    RoleLaunch,
    applied_to_agent,
    build_launch_call,
    mint_agent_id,
)
from agents_remember.cli.role_handover_artifacts import first_message
from agents_remember.cli.role_handover_publication import publish_handover_files
from agents_remember.cli.role_held_store import GENERATION
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_first_run import recall_first_run_report
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA, _now_iso, digest
from agents_remember.cli.role_launch_request_rules import missing_copies_receipt_warning
from agents_remember.cli.role_opening_offload import (
    preflight_opening_message_artifact,
    prepare_direct_opening_message,
    resolve_role_message_limit,
    selection_repo_id,
    write_opening_message_artifact,
)
from agents_remember.cli.role_step_up_runtime import successor_prompt

if TYPE_CHECKING:
    from agents_remember.cli.role_launch_routes import _PreparedRoleStart


@dataclass(frozen=True, slots=True)
class LaunchPublication:
    prompt: str
    instruction: str | None
    acp_instruction: dict[str, Any]
    accepts_tool_servers: bool
    host_denied_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PublishedHandover:
    prompt: str
    artifact: dict[str, Any]
    handover: dict[str, Any]
    opening_artifact: dict[str, Any] | None = None


def publish_prepared_launch(
    start: _PreparedRoleStart,
    preparation: LaunchPublication,
    install_receipt: Callable[[Path, dict[str, Any]], bool],
) -> tuple[dict[str, Any], bool]:
    """Publish launch files before the receipt, then return without contacting the host."""
    workspace = dict(start.role_handover.workspace)
    options = start.role_handover.session_options
    session_options = {**options, "choices": launch_choice_metadata(options.get("choices", {}))}
    # The terminal seat census starts only after this reservation is released.
    with role_launch_enclosure.publication_scope(start.config.coordination_root, workspace):
        files = _publish_files(start, preparation)
        call = _prepared_launch_call(start, preparation, files, session_options)
        receipt = _launch_receipt(start, preparation, files, session_options, call)
        created = install_receipt(start.receipt_path, receipt)
    return receipt, created


def _publish_files(start: _PreparedRoleStart, preparation: LaunchPublication) -> _PublishedHandover:
    prepared = start.role_handover.handover
    # A takeover's sidecar is an in-group write as well as the main handover files.
    prompt = successor_prompt(start, preparation.prompt)
    opening_artifact: dict[str, Any] | None = None
    if start.opening_message and not start.held:
        limit = resolve_role_message_limit(start.config, repo_id=selection_repo_id(start.binding))
        bounded_opening, opening_artifact = prepare_direct_opening_message(
            prepared["taskReportPath"], start.opening_message, limit=limit
        )
        if opening_artifact is not None:
            preflight_opening_message_artifact(opening_artifact)
        prompt = f"{prompt}\n{bounded_opening}"
    if preparation.instruction is not None:
        prompt = f"{prompt}\n\n{preparation.instruction}"
    # The message's artifact and the complete hand-over are written before the message that
    # names them is sent; both targets are checked first, so a refusal creates neither.
    artifact, handover_file = publish_handover_files(
        prepared["taskReportPath"],
        prompt,
        prepared["handoverFileContent"],
        expected=prepared["handoverFile"],
        request_id=str(start.request.request_id),
    )
    if opening_artifact is not None:
        write_opening_message_artifact(opening_artifact)

    return _PublishedHandover(
        first_message(artifact, prompt, handover_file),
        artifact,
        handover_file,
        opening_artifact,
    )


def _prepared_launch_call(
    start: _PreparedRoleStart,
    prepared_launch: LaunchPublication,
    files: _PublishedHandover,
    session_options: dict[str, Any],
) -> dict[str, Any]:
    request = start.request
    prepared = start.role_handover.handover
    return build_launch_call(
        RoleLaunch(
            agent_id=mint_agent_id(),
            request_id=request.request_id,
            context=start.context,
            folder=start.role_handover.workspace["path"],
            provider=start.role_handover.agent_id,
            session_options=session_options,
            prompt=files.prompt,
            report_path=prepared["taskReportPath"],
            handover_artifact=files.artifact,
            settings_file=start.config.config_path,
            accepts_tool_servers=prepared_launch.accepts_tool_servers,
            host_denied_tools=prepared_launch.host_denied_tools,
            replaces_agent_id=start.replaces_agent_id,
            parent_agent_id=start.started_by.agent_id if start.started_by else None,
            held=start.held,
        )
    )


def _launch_receipt(
    start: _PreparedRoleStart,
    preparation: LaunchPublication,
    files: _PublishedHandover,
    session_options: dict[str, Any],
    launch_call: dict[str, Any],
) -> dict[str, Any]:
    request = start.request
    prepared = start.role_handover.handover
    provider = start.role_handover.agent_id
    workspace = dict(start.role_handover.workspace)
    agent_id = launch_call["agent"]["agentId"]
    return {
        "schema": RECEIPT_SCHEMA,
        "requestId": str(request.request_id),
        "role": request.role,
        "selection": start.binding,
        "requestDigest": start.request_digest,
        "bindingDigest": digest(
            {
                "requestDigest": start.request_digest,
                "requestId": str(request.request_id),
                "taskDocumentDigest": prepared["taskDocumentDigest"],
                "capsuleDigest": prepared["capsuleDigest"],
                "capsuleOperation": prepared["capsuleOperation"],
                "workspace": workspace,
                "agent": provider,
                "sessionOptions": session_options,
                "arMcpContext": prepared.get("arMcpContext"),
                "stepUpArtifact": prepared.get("stepUpArtifact"),
            }
        ),
        "capsuleDigest": prepared["capsuleDigest"],
        "instructionComposition": prepared.get("instructionComposition"),
        "taskDocumentFiles": prepared.get("taskDocumentFiles"),
        "predecessorReports": prepared.get("predecessorReports"),
        **missing_copies_receipt_warning(
            prepared.get("taskDocumentFiles"), session_options.get("stepUpWarning")
        ),
        # MIK-R216 rule 3: the launch's own answer about this seat's provider, beside the
        # warnings — what was added, or that nothing was and why.
        "acpInstruction": preparation.acp_instruction,
        "choices": session_options.get("choices", {}),
        "capsuleOperation": prepared["capsuleOperation"],
        "taskDocumentDigest": prepared["taskDocumentDigest"],
        "messageBindingProjection": start.message_binding_projection,
        "report": {
            "path": prepared["taskReportPath"],
            "canonicalPath": prepared["canonicalTaskReportPath"],
        },
        **(
            {"arMcpContext": prepared["arMcpContext"]}
            if isinstance(prepared.get("arMcpContext"), dict)
            else {}
        ),
        "agentId": agent_id,
        GENERATION: 0,
        # The role agent that started this one; a start from the launcher has no parent.
        **({"parentAgentId": start.started_by.agent_id} if start.started_by else {}),
        "status": "starting",
        "createdAt": _now_iso(),
        "workspace": workspace,
        "agent": {"id": provider, **session_options},
        "sessionOptions": session_options,
        "requestedAgentOverride": (
            request.agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
            if request.agent_override
            else None
        ),
        "stepUp": session_options.get("stepUp"),
        "requestedStepUp": request.step_up,
        "execution": {},
        "preparation": {
            "enclosure": workspace.get("enclosurePreparation", "not-applicable"),
            **({"briefsProblem": problem} if (problem := workspace.get("briefsProblem")) else {}),
            **({"firstRun": report} if (report := recall_first_run_report()) is not None else {}),
        },
        "handoverArtifact": files.artifact,
        "handoverFile": {**files.handover, "content": prepared["handoverFileContent"]},
        "stepUpArtifact": prepared.get("stepUpArtifact"),
        **(
            {"openingMessageArtifact": files.opening_artifact}
            if files.opening_artifact is not None
            else {}
        ),
        # A held seat keeps its opening handover for its first message (MIK-R100 rules 2/4).
        **(
            {
                "heldSeat": True,
                "heldOpeningPrompt": files.prompt,
            }
            if start.held
            else {}
        ),
        # What the launch call gives the agent: its tool server, or why none, and the note.
        **applied_to_agent(launch_call),
        "replayRequest": launch_call,
        "detail": "The Paseo runtime is creating the AR role agent.",
    }
