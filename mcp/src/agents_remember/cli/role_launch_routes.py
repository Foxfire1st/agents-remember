"""AR's role-aware launcher seam for role agents in the Paseo runtime.

AR resolves the role hierarchy, the role's folder, the task capsule and the report scope, chooses
the agent id and records the launch. Paseo owns the agent session. Receipts retain only that
execution reference; they never update AR task, review, curation, or Git state.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from agents_remember.application.master_architect_record import (
    MANAGER_START,
    master_architect_path,
    write_record,
)
from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    resolve_role_launch_context,
)
from agents_remember.cli import role_launch_progress
from agents_remember.cli.investigator_receipts import (
    investigator_listing,
    investigator_start_capacity,
)
from agents_remember.cli.paseo_bridge import (
    PaseoBridgeFailure,
    require_bridge_runtime,
)
from agents_remember.cli.paseo_catalog import (
    launcher_catalog_question,
    provider_accepts_tool_servers,
)
from agents_remember.cli.paseo_catalog_snapshot import register_saved_catalog_route
from agents_remember.cli.paseo_frame import frame_answer
from agents_remember.cli.paseo_launch import (
    RoleLaunch,
    StartingAgent,
    applied_to_agent,
    build_launch_call,
    mint_agent_id,
)
from agents_remember.cli.paseo_status import read_agent
from agents_remember.cli.paseo_warmup import register_warmup_route
from agents_remember.cli.role_answers import alias_response
from agents_remember.cli.role_document_chat_tree import register_document_chat_tree_route
from agents_remember.cli.role_document_chats import register_document_chat_route
from agents_remember.cli.role_handover_artifacts import (
    first_message,
)
from agents_remember.cli.role_handover_publication import publish_handover_files
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_admission import admit, prepare_first_run_for
from agents_remember.cli.role_launch_first_run import (
    _FIRST_RUN_REPORT,
    recall_first_run_report,
    refuse_after_preparation,
)
from agents_remember.cli.role_launch_liveness import (
    HostUnreachableRefusal,
    _reconcile_prior_execution,
    _recorded_agent_id,
    _refresh_execution,
    _Replacement,
    _revive_agent,
)
from agents_remember.cli.role_launch_preparation import (
    PreparedRoleHandover,
    _launcher_catalog,
    _verify_leaf_revival_scope,
    prepare_role_handover,
)
from agents_remember.cli.role_launch_receipts import (
    EXECUTIONS_DIRECTORY,
    RECEIPT_SCHEMA,
    _archived_receipt_for_selection,
    _create_receipt,
    _discard_message_binding_projection,
    _execute_prepared_launch,
    _migrate_taskless_legacy_receipt,
    _now_iso,
    _place_message_binding_projection,
    _public_execution,
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _refuse_reused_request_id,
    _replaced_agent_id,
    _request_digest,
    _taskless_execution_receipts,
    _verify_message_binding_projection,
)
from agents_remember.cli.role_launch_receipts import (
    digest as _digest,
)
from agents_remember.cli.role_launch_request_rules import (
    bridge_http_error,
    missing_copies_receipt_warning,
    with_caller_harness,
)
from agents_remember.cli.role_report import register_role_report_route
from agents_remember.cli.role_start_execution import start_execution
from agents_remember.cli.role_step_up_runtime import (
    bind_step_up_predecessor,
    successor_prompt,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleStartSetCall
from agents_remember.models.role_identity import LEAF_ROLES, TASKLESS_ROLES, canonical_role
from agents_remember.models.role_launcher import (
    RoleAgentOverride,
    RoleDispatchRequest,
    RoleLauncherOptionsRequest,
    RoleResultRequest,
    RoleSelection,
)
from agents_remember.serving.paseo.paseo_daemon_config import provider_denied_tools
from agents_remember.tasks.document_refs import TaskDocumentRefError

_DISPATCH_LOCK = threading.Lock()
__all__ = ["_refuse_reused_request_id", "_request_digest", "prepare_role_handover"]


# How a failed bridge call answers a launcher route; every other bridge failure is a bad gateway.
#: The role a Manager's start records as its master's architect (MIK-R189 rule 7 step 3).
ARCHITECT_ROLE = "architect"


@dataclass(frozen=True, slots=True)
class _PreparedRoleStart:
    config: McpRuntimeConfig
    request: RoleDispatchRequest
    context: RoleLaunchContext
    binding: dict[str, Any]
    request_digest: str
    receipt_path: Path
    role_handover: PreparedRoleHandover
    message_binding_projection: dict[str, str]
    # The agent of the closed execution this start replaces on a task-bound selection.
    replaces_agent_id: str | None = None
    # The role agent that asked for this start; none for a start from the launcher.
    started_by: StartingAgent | None = None
    # Whether this start created the request's message-binding file (it may then take it back).
    created_message_binding: bool = False
    # A held seat (MIK-R100 rule 2): created without a first message, opened later.
    held: bool = False
    # First work carried with the start (MIK-R100 rule 3): appended to the opening
    # handover when the seat opens at once. Never used with a held seat.
    opening_message: str | None = None


def register_role_launch_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    register_saved_catalog_route(app, config)
    app.add_api_route("/api/role-launch/frame", _bind_frame_endpoint(config), methods=["GET"])
    app.add_api_route(
        "/api/role-launch/options",
        _bind_options_endpoint(config),
        methods=["POST"],
    )
    app.add_api_route(
        "/api/role-launch/dispatch", _bind_dispatch_endpoint(config), methods=["POST"]
    )
    app.add_api_route("/api/role-launch/result", _bind_result_endpoint(config), methods=["POST"])
    app.add_api_route(
        "/api/role-launch/progress/{request_id}", role_launch_progress.read, methods=["GET"]
    )
    register_document_chat_route(app, config)
    register_document_chat_tree_route(app, config)
    register_warmup_route(app)
    register_role_report_route(app, config)


def role_launch_frame(config: McpRuntimeConfig, request: Request) -> JSONResponse:
    """Where this dashboard origin frames the Paseo web UI, or why it cannot (always HTTP 200)."""
    return JSONResponse(frame_answer(config, request))


def _bind_frame_endpoint(config: McpRuntimeConfig):
    def endpoint(request: Request) -> JSONResponse:
        return role_launch_frame(config, request)

    return endpoint


def _bind_options_endpoint(config: McpRuntimeConfig):
    def endpoint(request: RoleLauncherOptionsRequest) -> JSONResponse:
        return alias_response(request, _role_launch_options_endpoint(config, request))

    return endpoint


def _bind_dispatch_endpoint(config: McpRuntimeConfig):
    def endpoint(request: RoleDispatchRequest) -> JSONResponse:
        return alias_response(request, _role_launch_dispatch_endpoint(config, request))

    return endpoint


def _bind_result_endpoint(config: McpRuntimeConfig):
    def endpoint(request: RoleResultRequest) -> JSONResponse:
        return alias_response(request, _role_launch_result_endpoint(config, request))

    return endpoint


@launcher_catalog_question()
def _role_launch_options_endpoint(
    config: McpRuntimeConfig, request: RoleLauncherOptionsRequest
) -> JSONResponse:
    try:
        context = resolve_role_launch_context(config, request)
        # The catalog asks Paseo before the launch lock is taken: the
        # lock is held for the receipts and the one bridge call of the refresh, nothing longer.
        response = _launcher_catalog(config, context, request)
        _acquire_dispatch_lock()
        try:
            if request.role in TASKLESS_ROLES:
                _migrate_taskless_legacy_receipt(config, request)
                if request.role == "investigator":
                    listing = investigator_listing(config, request)
                    response["executions"] = [
                        _public_execution(receipt) for _, receipt in listing.records
                    ]
                    response["omittedCount"] = listing.omitted_count
                else:
                    response["executions"] = [
                        _public_execution(receipt)
                        for _, receipt in _taskless_execution_receipts(config, request)
                    ]
            else:
                receipt_path = _receipt_path(config, request)
                receipt = _read_receipt(receipt_path)
                response["execution"] = (
                    _refresh_execution(config, receipt_path, receipt) if receipt else None
                )
        finally:
            _DISPATCH_LOCK.release()
        return JSONResponse(response)
    except LaunchLockBusy as busy:
        return busy.read_answer()
    except PaseoBridgeFailure as error:
        raise bridge_http_error(error) from error
    except (OSError, ValueError, TaskDocumentRefError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@launcher_catalog_question()
def _role_launch_dispatch_endpoint(
    config: McpRuntimeConfig,
    request: RoleDispatchRequest,
    *,
    started_by: StartingAgent | None = None,
    lock_wait_seconds: float = 0.0,
) -> JSONResponse:
    """Start or revive one role execution; the launcher's route and the role-start tool end here.

    ``started_by`` is the role agent on whose behalf the start runs. The preparation, the launch,
    the receipt, the reconciliation of a repeated request and the lock are the same either way.
    The launcher's route never waits for the lock; the role-start tool waits ``lock_wait_seconds``
    for it, because one agent issues its starts side by side.

    Admission and preparation share the dispatch lock: a queued request sees the binding or
    receipt its predecessor published, rather than preparing from a stale pre-lock decision.
    Invalid selections still refuse before any preparation writes.
    """

    _acquire_dispatch_lock(lock_wait_seconds)
    role_launch_progress.begin(request.request_id)
    first_run_token = _FIRST_RUN_REPORT.set(None)
    try:
        admission = admit(config, request, started_by)
        prepare_first_run_for(config, request, admission)
        request = with_caller_harness(config, request)
        if request.action == "revive":
            return _revive_execution(config, request)
        with investigator_start_capacity(config, request):
            return _start_execution(config, request, started_by)
    except HostUnreachableRefusal as refusal:
        return JSONResponse({"detail": refusal.detail, "hostUnreachable": True}, status_code=409)
    except (HTTPException, OSError, ValueError, TaskDocumentRefError, PaseoBridgeFailure) as error:
        # The receipt/retry owners keep their existing behavior; the refusal also names
        # preparation effects already made by this call before its context is cleared.
        refuse_after_preparation(error)
    finally:
        _FIRST_RUN_REPORT.reset(first_run_token)
        role_launch_progress.finish()
        _DISPATCH_LOCK.release()


def _role_launch_result_endpoint(
    config: McpRuntimeConfig, request: RoleResultRequest
) -> JSONResponse:
    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise bridge_http_error(error) from error
    # The lock is held for the receipt and the one bridge call of the refresh, nothing longer.
    try:
        _acquire_dispatch_lock()
    except LaunchLockBusy as busy:
        return busy.read_answer()
    try:
        if request.role in TASKLESS_ROLES:
            if request.request_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="Taskless role results require the exact saved requestId.",
                )
            _migrate_taskless_legacy_receipt(config, request)
            path = _receipt_path(config, request, request.request_id)
        else:
            path = _receipt_path(config, request)
        receipt = _read_receipt(path)
        if receipt is None:
            raise HTTPException(
                status_code=404, detail="No role execution is recorded for this AR selection."
            )
        if not _receipt_address_matches(receipt, request):
            raise HTTPException(
                status_code=409,
                detail="The recorded role execution belongs to another AR selection.",
            )
        return JSONResponse(_refresh_execution(config, path, receipt))
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        _DISPATCH_LOCK.release()


def _prepared_message_binding_projection(
    prepared: dict[str, Any], request_id: uuid.UUID
) -> tuple[dict[str, Any], dict[str, str]]:
    projection = prepared.get("messageBindingProjection")
    if not isinstance(projection, dict) or projection.get("requestId") != str(request_id):
        raise ValueError("The prepared native message binding has no matching request identity.")
    binding = projection.get("binding")
    path = projection.get("path")
    sha256 = projection.get("sha256")
    if (
        not isinstance(binding, dict)
        or not isinstance(path, str)
        or not path
        or not isinstance(sha256, str)
        or not sha256
    ):
        raise ValueError("The prepared native message binding is incomplete.")
    return binding, {"path": path, "sha256": sha256}


def _verify_prior_message_binding_projection(
    config: McpRuntimeConfig,
    receipt: dict[str, Any] | None,
    request: RoleDispatchRequest,
    binding: dict[str, Any],
    reference: dict[str, str],
) -> None:
    if not receipt or receipt.get("requestId") != str(request.request_id):
        return
    if "messageBindingProjection" not in receipt:
        return
    prior_reference = receipt.get("messageBindingProjection")
    if not isinstance(prior_reference, dict) or prior_reference != reference:
        raise ValueError("This request ID is already bound to different native message data.")
    _verify_message_binding_projection(config, request.request_id, binding, reference)


@dataclass(frozen=True, slots=True)
class _BindingReservation:
    """Settle the receipt at this address and write the message-binding file."""

    path: Path
    request: RoleDispatchRequest
    request_digest: str
    prepared: dict[str, Any]
    started_by: StartingAgent | None = None


def _reserve_message_binding_projection(
    config: McpRuntimeConfig, reservation: _BindingReservation
) -> tuple[dict[str, str], str | None, bool, JSONResponse | None]:
    """Settle the receipt already at this address and write the message-binding file.

    Returns the binding reference, the agent of a closed execution this request replaces, whether
    this call created the binding file, and the response when the request is answered by the
    existing receipt instead of a new launch.
    """

    path = reservation.path
    request = reservation.request
    request_digest = reservation.request_digest
    prepared = reservation.prepared
    binding, reference = _prepared_message_binding_projection(prepared, request.request_id)
    current = _read_receipt(path)
    _verify_prior_message_binding_projection(config, current, request, binding, reference)
    if current is None:
        # A closed receipt may already be in the history (a launch that ended, or lost, between
        # archiving it and creating its own); its agent is archived by this launch.
        current = _archived_receipt_for_selection(path, request)
        replaces_agent_id = _replaced_agent_id(current) if current else None
    elif current.get("requestId") != str(request.request_id):
        replaces_agent_id = _replaced_agent_id(current)
    else:
        replaces_agent_id = None
    prior = _reconcile_prior_execution(
        config,
        path,
        request,
        request_digest,
        _Replacement(
            reservation.started_by,
            request.replaces_agent_id,
            request.replaces_interrupt_turn,
            str(request.request_id),
            bool(prepared.get("stepUp")),
        ),
    )
    if prior is not None:
        return reference, None, False, prior
    if current and replaces_agent_id:
        bind_step_up_predecessor(prepared, current)
        prepared["predecessorReports"] = {
            key: current[key] for key in ("requestId", "workspace", "report") if key in current
        }
    created = _place_message_binding_projection(config, request.request_id, binding, reference)
    return reference, replaces_agent_id, created, None


def _take_back_message_binding(start: _PreparedRoleStart) -> None:
    """Remove the message-binding file of a start that lost the creation of its receipt.

    Only a file this start created is removed, and only while no receipt at the address carries
    the request id: a file that was there before belongs to another execution of that id (the
    winner's, or an archived one), and a winner with the same id names the file in its receipt.
    """

    if not start.created_message_binding:
        return
    try:
        if _receipt_carries_request(start.receipt_path, start.request):
            return
    except HTTPException:
        return  # the receipt cannot be read, so nothing proves the file is this start's alone
    _discard_message_binding_projection(start.config, start.request.request_id)


def _receipt_carries_request(path: Path, request: RoleDispatchRequest) -> bool:
    current = _read_receipt(path)
    return current is not None and current.get("requestId") == str(request.request_id)


def _refuse_another_starter(
    path: Path, request: RoleDispatchRequest, started_by: StartingAgent | None
) -> None:
    """Refuse the repeat of a request id by a role agent that did not start its execution.

    The receipt records the agent that started the execution (``parentAgentId``); a start from
    the launcher records none. The launcher repeats and retries every execution: its retry of an
    agent-started one replays the stored call, which names the stored parent. A role agent
    repeats only what it started itself.
    """

    if started_by is None:
        return
    current = _read_receipt(path)
    if current is None or current.get("requestId") != str(request.request_id):
        return
    recorded = current.get("parentAgentId")
    if recorded == started_by.agent_id:
        return
    owner = f"agent {recorded}" if isinstance(recorded, str) and recorded else "the launcher"
    raise HTTPException(
        status_code=409,
        detail=(
            f"This request id belongs to an execution that {owner} started; agent "
            f"{started_by.agent_id} did not start it and cannot repeat it."
        ),
    )


def remember_master_architect(
    config: McpRuntimeConfig,
    request: RoleSelection,
    started_by: StartingAgent | None,
) -> dict[str, str] | None:
    """Write the master-to-architect record a Manager start makes (MIK-R189 rule 7 step 3).

    The master comes from the Manager's own reference. The record names the starter when the
    starter is a hosted architect; when it is an Orchestrator, it names that Orchestrator's
    parent, which the Orchestrator's launch recorded as ``parentAgentId`` and which this writer
    checks is an architect before recording it. Any other starter writes nothing -- an
    Orchestrator's own id is never recorded -- and neither does a start with no starter at all
    (the launcher's).

    The record has a second writer, ``explainer_control`` action ``claim``, and the last write
    wins; an architect a Manager's start did not record takes the master over with it.
    """

    if request.role != "manager" or started_by is None:
        return None
    master_ref = request.master_document_ref
    if master_ref is None:
        return None
    architect = _architect_behind(config, started_by)
    if architect is None:
        return None
    master = PurePosixPath(master_ref.path).parts[0]
    return write_record(
        master_architect_path(config.coordination_root, master_ref.repository, master),
        architect_agent_id=architect,
        act=MANAGER_START,
    )


def _record_manager_start(start: _PreparedRoleStart) -> None:
    """Record the architect behind one Manager start; a failed write never fails the seat.

    The record is an address the explainer reads and the architect claims; a start that could not
    write it leaves the master unclaimed, which ``claim`` answers, while a start failed over it
    would cost the developer the seat itself.
    """

    try:
        remember_master_architect(start.config, start.request, start.started_by)
    except (OSError, ValueError):
        return


def _architect_behind(config: McpRuntimeConfig, started_by: StartingAgent) -> str | None:
    """The architect one starter stands for, or ``None`` for a starter that stands for none."""

    role = canonical_role(started_by.role)
    if role == ARCHITECT_ROLE:
        return started_by.agent_id
    if role != "orchestrator":
        return None
    parent = _orchestrator_parent(config, started_by)
    if parent is None:
        return None
    receipt = _architect_receipt(config, parent)
    if receipt is None or canonical_role(str(receipt.get("role") or "")) != ARCHITECT_ROLE:
        return None
    return parent


def _orchestrator_parent(config: McpRuntimeConfig, started_by: StartingAgent) -> str | None:
    """The agent that started one Orchestrator, read from the Orchestrator's own launch record."""

    try:
        path = _receipt_path(
            config,
            RoleSelection(role="orchestrator", sprintDocumentRef=started_by.sprint_ref),
        )
    except (ValueError, TaskDocumentRefError):
        return None
    receipt = _readable_receipt(path)
    parent = None if receipt is None else receipt.get("parentAgentId")
    return parent if isinstance(parent, str) and parent else None


def _architect_receipt(config: McpRuntimeConfig, agent_id: str) -> dict[str, Any] | None:
    """The architect's own launch record, read to prove the parent a starter names is one."""

    folder = (
        config.coordination_root
        / "notes"
        / "reports"
        / EXECUTIONS_DIRECTORY
        / "architect"
        / "sessions"
    )
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        receipt = _readable_receipt(path)
        if receipt is not None and receipt.get("agentId") == agent_id:
            return receipt
    return None


def _readable_receipt(path: Path) -> dict[str, Any] | None:
    try:
        return _read_receipt(path)
    except (HTTPException, OSError, ValueError):
        return None


def _start_execution(
    config: McpRuntimeConfig,
    request: RoleDispatchRequest,
    started_by: StartingAgent | None = None,
    *,
    held: bool = False,
    opening_message: str | None = None,
) -> JSONResponse:
    return start_execution(config, request, started_by, held=held, opening_message=opening_message)


def _launch_prepared_role_session(start: _PreparedRoleStart, *, prompt: str) -> JSONResponse:
    request = start.request
    role_handover = start.role_handover
    workspace = role_handover.workspace
    provider = role_handover.agent_id
    session_options = {
        **role_handover.session_options,
        "choices": launch_choice_metadata(role_handover.session_options.get("choices", {})),
    }
    prepared = role_handover.handover
    # The agent id is chosen here: before the receipt is written and before the runtime is called.
    agent_id = mint_agent_id()
    # The compiled first message is stored once; what the agent receives names that file first.
    # A successor names the earlier agent's report paths, so that it takes over from what is
    # written (MIK-R100 rule 9).
    prompt = successor_prompt(start, prompt)
    if start.opening_message and not start.held:
        # First work carried with the start (MIK-R100 rule 3): the seat opens at once
        # with its handover and that message.
        prompt = f"{prompt}\n{start.opening_message}"
    # The message's artifact and the complete hand-over are written before the message that
    # names them is sent; both targets are checked first, so a refusal creates neither.
    artifact, handover_file = publish_handover_files(
        prepared["taskReportPath"],
        prompt,
        prepared["handoverFileContent"],
        expected=prepared["handoverFile"],
        request_id=str(request.request_id),
    )
    launch_call = build_launch_call(
        RoleLaunch(
            agent_id=agent_id,
            request_id=request.request_id,
            context=start.context,
            folder=workspace["path"],
            provider=provider,
            session_options=session_options,
            prompt=first_message(artifact, prompt, handover_file),
            report_path=prepared["taskReportPath"],
            handover_artifact=artifact,
            settings_file=start.config.config_path,
            accepts_tool_servers=provider_accepts_tool_servers(start.config, provider),
            host_denied_tools=_host_denied_tools(start.config, provider),
            replaces_agent_id=start.replaces_agent_id,
            parent_agent_id=start.started_by.agent_id if start.started_by else None,
            held=start.held,
        )
    )
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "requestId": str(request.request_id),
        "role": request.role,
        "selection": start.binding,
        "requestDigest": start.request_digest,
        "bindingDigest": _digest(
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
            **({"firstRun": report} if (report := recall_first_run_report()) is not None else {}),
        },
        "handoverArtifact": artifact,
        "handoverFile": {**handover_file, "content": prepared["handoverFileContent"]},
        "stepUpArtifact": prepared.get("stepUpArtifact"),
        # A held seat keeps its opening handover for its first message (MIK-R100 rules 2/4).
        **(
            {
                "heldSeat": True,
                "heldOpeningPrompt": first_message(artifact, prompt, handover_file),
            }
            if start.held
            else {}
        ),
        # What the launch call gives the agent: its tool server, or why none, and the note.
        **applied_to_agent(launch_call),
        "replayRequest": launch_call,
        "detail": "The Paseo runtime is creating the AR role agent.",
    }
    if not _create_receipt(start.receipt_path, receipt):
        # Another process created a receipt at this address since it was last read here.
        try:
            _refuse_another_starter(start.receipt_path, request, start.started_by)
            prior = _reconcile_prior_execution(
                start.config,
                start.receipt_path,
                request,
                start.request_digest,
                _Replacement(
                    start.started_by,
                    request.replaces_agent_id,
                    request.replaces_interrupt_turn,
                    str(request.request_id),
                ),
            )
        except HTTPException:
            _take_back_message_binding(start)
            raise
        if prior is not None:
            return prior
        _take_back_message_binding(start)
        raise HTTPException(
            status_code=409,
            detail="Another launch of this AR role selection started at the same moment; refresh and start again.",
        )
    # The seat's receipt is this start's now; a Manager's start also names its master's architect.
    _record_manager_start(start)
    return _execute_prepared_launch(start.config, start.receipt_path, receipt)


def _revive_execution(config: McpRuntimeConfig, request: RoleDispatchRequest) -> JSONResponse:
    """Resume the recorded agent's closed session without a message; never create an agent."""

    if request.role in TASKLESS_ROLES:
        _migrate_taskless_legacy_receipt(config, request)
    path = _receipt_path(
        config, request, request.request_id if request.role in TASKLESS_ROLES else None
    )
    receipt = _read_receipt(path)
    if receipt is None or not _receipt_address_matches(receipt, request):
        raise HTTPException(
            status_code=404, detail="No matching AR role execution is recorded to revive."
        )
    agent_id = _recorded_agent_id(receipt)
    if agent_id is None:
        raise HTTPException(
            status_code=409,
            detail="This execution has no agent in the Paseo runtime, so there is nothing to revive.",
        )
    if request.role in LEAF_ROLES:
        _verify_leaf_revival_scope(config, resolve_role_launch_context(config, request), receipt)
    return _revive_agent(config, path, receipt, agent_id)


def _require_paseo_runtime(config: McpRuntimeConfig) -> None:
    """Refuse a launch before anything is written when the settings name no Paseo runtime."""

    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _host_denied_tools(config: McpRuntimeConfig, provider: str) -> tuple[str, ...]:
    """The tool rules the host's own configuration denies in the options of this provider."""

    runtime = config.paseo_runtime
    return provider_denied_tools(runtime.home, provider) if runtime is not None else ()


class LaunchLockBusy(HTTPException):
    """Another launch or result check of this backend process holds the launch lock."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409, detail="A role launch or result check is already in progress."
        )

    def read_answer(self) -> JSONResponse:
        """The answer of a route that only reads: the refusal, marked as a launch in progress.

        A launch holds the lock for as long as it runs, which includes the time it gives the
        agent's tool server. The launcher takes the mark for "read again shortly", not for an
        error of the selection it shows.
        """

        return JSONResponse(
            {"detail": self.detail, "launchInProgress": True}, status_code=self.status_code
        )


def _acquire_dispatch_lock(wait_seconds: float = 0.0) -> None:
    """Take the launch lock of this process, waiting at most ``wait_seconds`` for it."""

    acquired = (
        _DISPATCH_LOCK.acquire(timeout=wait_seconds)
        if wait_seconds > 0
        else _DISPATCH_LOCK.acquire(blocking=False)
    )
    if not acquired:
        raise LaunchLockBusy


# The seats one set call prepares, in start order: the Worker opens at once, the
# Reviewer and the Curator are held until their first message (MIK-R100 rules 1/2).
SET_SEAT_ROLES: tuple[str, ...] = ("worker", "reviewer", "curator")


def seat_request_id(set_request_id: uuid.UUID, role: str) -> uuid.UUID:
    """The deterministic seat request id of one set: a repeat reconciles the same agent."""

    return uuid.uuid5(set_request_id, f"ar-leaf-set-seat:{role}")


def dispatch_leaf_set(
    config: McpRuntimeConfig,
    call: RoleStartSetCall,
    started_by: StartingAgent | None,
    *,
    reviewer_opening: str | None = None,
    lock_wait_seconds: float = 60.0,
) -> dict[str, Any]:
    """Start a leaf's Worker, Reviewer and Curator in one call (MIK-R100 rule 1).

    The three have the calling Manager as their parent and the same leaf as their
    selection. The Worker opens at once; the Reviewer opens at once with its handover
    and the carried first message when one is given (rule 3), else held like the
    Curator (rule 2). If one seat cannot be created the call names it, leaves the
    others as they are, and a repeat with the same set request id completes the set.
    """

    _require_paseo_runtime(config)
    _acquire_dispatch_lock(lock_wait_seconds)
    try:
        seats = {
            role: _start_set_seat(config, call, role, started_by, reviewer_opening)
            for role in SET_SEAT_ROLES
        }
    finally:
        _DISPATCH_LOCK.release()
    failed = sorted(role for role, seat in seats.items() if not seat.get("agentId"))
    ok = not failed
    return {
        "ok": ok,
        "status": "completed" if ok else "partial",
        "detail": (
            "The set prepared the leaf's Worker, Reviewer and Curator as siblings."
            if ok
            else f"The set left {', '.join(failed)} without an agent; the other seats stand. "
            "Repeat the call with the same set request id to complete the set."
        ),
        "requestId": str(call.request_id),
        "seats": seats,
        **(
            {}
            if ok
            else {
                "nextAction": "Repeat role_start_set with the same request_id and leaf; "
                "seats that have an agent reconcile it and create no fourth."
            }
        ),
    }


def _start_set_seat(
    config: McpRuntimeConfig,
    call: RoleStartSetCall,
    role: str,
    started_by: StartingAgent | None,
    reviewer_opening: str | None,
) -> dict[str, Any]:
    """Start one seat of a set; a seat that fails names itself and stops no other seat."""

    seat_id = seat_request_id(call.request_id, role)
    override = getattr(call, role)
    agent_override = (
        RoleAgentOverride(
            agentId=override.agent,
            modelId=override.model,
            effortId=override.effort,
            featureValues=dict(override.feature_values) or None,
        )
        if override.agent or override.model or override.effort or override.feature_values
        else None
    )
    try:
        request = RoleDispatchRequest.model_validate(
            {
                "role": role,
                "sprintDocumentRef": call.sprint_document_ref,
                "masterDocumentRef": call.master_document_ref,
                "taskDocumentRef": call.task_document_ref,
                "requestId": seat_id,
                "agentOverride": (
                    agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if agent_override
                    else None
                ),
                "replacesAgentId": (call.replaces or {}).get(role),
                "replacesInterruptTurn": call.replaces_interrupt_turn,
            }
        )
    except (ValidationError, ValueError) as error:
        return {
            "requestId": str(seat_id),
            "status": "refused",
            "agentId": None,
            "held": False,
            "opened": False,
            "hasTurn": False,
            "detail": f"The seat request is not valid: {error}",
        }
    opens_at_once = role == "worker" or (role == "reviewer" and bool(reviewer_opening))
    try:
        response = _start_execution(
            config,
            request,
            started_by,
            held=not opens_at_once,
            opening_message=reviewer_opening if role == "reviewer" and opens_at_once else None,
        )
    except HostUnreachableRefusal as refusal:
        return {
            "requestId": str(seat_id),
            "status": "host-unreachable",
            "agentId": None,
            "held": False,
            "opened": False,
            "hasTurn": False,
            "detail": str(refusal.detail),
        }
    except HTTPException as error:
        return {
            "requestId": str(seat_id),
            "status": "refused",
            "agentId": None,
            "held": False,
            "opened": False,
            "hasTurn": False,
            "detail": str(error.detail),
        }
    except (OSError, ValueError, PaseoBridgeFailure) as error:
        return {
            "requestId": str(seat_id),
            "status": "error",
            "agentId": None,
            "held": False,
            "opened": False,
            "hasTurn": False,
            "detail": str(error),
        }
    try:
        answer = json.loads(bytes(response.body))
    except ValueError:
        answer = {}
    agent_id = _answer_agent_id(answer)
    status = str(answer.get("status") or "unknown")
    held = status == "held"
    return {
        "requestId": str(seat_id),
        "status": status,
        "agentId": agent_id if isinstance(agent_id, str) else None,
        "held": held,
        "opened": isinstance(agent_id, str) and not held,
        "hasTurn": _seat_has_turn(config, agent_id),
        "detail": str(answer.get("detail") or ""),
    }


def _answer_agent_id(answer: dict[str, Any]) -> str | None:
    """The agent a public execution answer names, top level or under execution."""

    agent_id = answer.get("agentId")
    if isinstance(agent_id, str) and agent_id:
        return agent_id
    execution = answer.get("execution")
    if isinstance(execution, dict):
        agent_id = execution.get("agentId")
        if isinstance(agent_id, str) and agent_id:
            return agent_id
    return None


def _seat_has_turn(config: McpRuntimeConfig, agent_id: Any) -> bool:
    """Whether the host records any turn for the seat's agent (rule 1 answer)."""

    if not isinstance(agent_id, str) or not agent_id:
        return False
    try:
        reading = read_agent(config, agent_id)
    except Exception:
        return False
    return reading.reachable and reading.last_turn in {"replied", "unreplied"}
