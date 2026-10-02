"""AR's role-aware launcher seam for role agents in the Paseo runtime.

AR resolves the role hierarchy, the role's folder, the task capsule and the report scope, chooses
the agent id and records the launch. Paseo owns the agent session. Receipts retain only that
execution reference; they never update AR task, review, curation, or Git state.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from agents_remember.application.orca_task_context import (
    LEAF_ROLES,
    TASKLESS_ROLES,
    OrcaRoleContext,
    resolve_orca_role_context,
    selection_binding,
)
from agents_remember.cli.orca_handover_artifacts import first_message, write_handover_artifact
from agents_remember.cli.orca_runtime import OrcaRuntimeFailure
from agents_remember.cli.orca_runtime import (
    configured_frame_url as _configured_frame_url,
)
from agents_remember.cli.orca_runtime import (
    configured_pairing_code as _configured_pairing_code,
)
from agents_remember.cli.orca_runtime import (
    digest as _digest,
)
from agents_remember.cli.orca_task_liveness import (
    _reconcile_prior_execution,
    _recorded_agent_id,
    _refresh_execution,
    _revive_agent,
)
from agents_remember.cli.orca_task_preparation import (
    PreparedOrcaRoleHandover,
    _launcher_catalog,
    _verify_leaf_revival_scope,
    prepare_orca_role_handover,
)
from agents_remember.cli.orca_task_receipts import (
    _create_receipt,
    _execute_prepared_launch,
    _migrate_taskless_legacy_receipt,
    _now_iso,
    _public_execution,
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _replaced_agent_id,
    _request_digest,
    _taskless_execution_receipts,
    _verify_message_binding_projection,
    _write_message_binding_projection,
)
from agents_remember.cli.paseo_bridge import (
    BRIDGE_TIMEOUT,
    RUNTIME_NOT_CONFIGURED,
    PaseoBridgeFailure,
    require_bridge_runtime,
)
from agents_remember.cli.paseo_catalog import provider_accepts_tool_servers
from agents_remember.cli.paseo_launch import (
    RoleLaunch,
    StartingAgent,
    applied_to_agent,
    build_launch_call,
    mint_agent_id,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import (
    OrcaDispatchRequest,
    OrcaLauncherOptionsRequest,
    OrcaResultRequest,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError

_DISPATCH_LOCK = threading.Lock()
# How a failed bridge call answers a launcher route; every other bridge failure is a bad gateway.
_BRIDGE_FAILURE_STATUS = {RUNTIME_NOT_CONFIGURED: 503, BRIDGE_TIMEOUT: 504}


@dataclass(frozen=True, slots=True)
class _PreparedRoleStart:
    config: McpRuntimeConfig
    request: OrcaDispatchRequest
    context: OrcaRoleContext
    binding: dict[str, Any]
    request_digest: str
    receipt_path: Path
    role_handover: PreparedOrcaRoleHandover
    message_binding_projection: dict[str, str]
    # The agent of the closed execution this start replaces on a task-bound selection.
    replaces_agent_id: str | None = None
    # The role agent that asked for this start; none for a start from the launcher.
    started_by: StartingAgent | None = None


def register_orca_task_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    app.add_api_route("/api/orca/frame", orca_frame, methods=["GET"])
    app.add_api_route(
        "/api/orca/launcher/options",
        _bind_options_endpoint(config),
        methods=["POST"],
    )
    app.add_api_route("/api/orca/dispatch", _bind_dispatch_endpoint(config), methods=["POST"])
    app.add_api_route("/api/orca/result", _bind_result_endpoint(config), methods=["POST"])


def orca_frame() -> JSONResponse:
    frame_url = _configured_frame_url() if _configured_pairing_code() else None
    return JSONResponse(
        {"available": frame_url is not None, "frameUrl": frame_url},
        status_code=200 if frame_url else 503,
    )


def _bind_options_endpoint(config: McpRuntimeConfig):
    def endpoint(request: OrcaLauncherOptionsRequest) -> JSONResponse:
        return _orca_options_endpoint(config, request)

    return endpoint


def _bind_dispatch_endpoint(config: McpRuntimeConfig):
    def endpoint(request: OrcaDispatchRequest) -> JSONResponse:
        return _orca_dispatch_endpoint(config, request)

    return endpoint


def _bind_result_endpoint(config: McpRuntimeConfig):
    def endpoint(request: OrcaResultRequest) -> JSONResponse:
        return _orca_result_endpoint(config, request)

    return endpoint


def _orca_options_endpoint(
    config: McpRuntimeConfig, request: OrcaLauncherOptionsRequest
) -> JSONResponse:
    _acquire_dispatch_lock()
    try:
        context = resolve_orca_role_context(config, request)
        response = _launcher_catalog(config, context, request)
        if request.role in TASKLESS_ROLES:
            _migrate_taskless_legacy_receipt(config, request)
            response["executions"] = [
                _public_execution(receipt)
                for _path, receipt in _taskless_execution_receipts(config, request)
            ]
        else:
            receipt_path = _receipt_path(config, request)
            receipt = _read_receipt(receipt_path)
            response["execution"] = (
                _refresh_execution(config, receipt_path, receipt) if receipt else None
            )
        return JSONResponse(response)
    except PaseoBridgeFailure as error:
        raise _bridge_http_error(error) from error
    except (OSError, ValueError, TaskDocumentRefError, OrcaRuntimeFailure) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        _DISPATCH_LOCK.release()


def _orca_dispatch_endpoint(
    config: McpRuntimeConfig,
    request: OrcaDispatchRequest,
    *,
    started_by: StartingAgent | None = None,
) -> JSONResponse:
    """Start or revive one role execution; the launcher's route and the role-start tool end here.

    ``started_by`` is the role agent on whose behalf the start runs. The preparation, the launch,
    the receipt, the reconciliation of a repeated request and the lock are the same either way.
    """

    _require_paseo_runtime(config)
    _acquire_dispatch_lock()
    try:
        if request.action == "revive":
            return _revive_execution(config, request)
        return _start_execution(config, request, started_by)
    except (
        OSError,
        ValueError,
        TaskDocumentRefError,
        OrcaRuntimeFailure,
        PaseoBridgeFailure,
    ) as error:
        # Nothing was recorded for this request. The launcher shows the reason of a 409; a bridge
        # failure reaches this point only while the selection is validated, before a receipt.
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        _DISPATCH_LOCK.release()


def _orca_result_endpoint(config: McpRuntimeConfig, request: OrcaResultRequest) -> JSONResponse:
    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise _bridge_http_error(error) from error
    # The lock is held for the receipt and the one bridge call of the refresh, nothing longer.
    _acquire_dispatch_lock()
    try:
        if request.role in TASKLESS_ROLES:
            if request.request_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="Taskless Orca results require the exact saved requestId.",
                )
            _migrate_taskless_legacy_receipt(config, request)
            path = _receipt_path(config, request, request.request_id)
        else:
            path = _receipt_path(config, request)
        receipt = _read_receipt(path)
        if receipt is None:
            raise HTTPException(
                status_code=404, detail="No Orca execution is recorded for this AR selection."
            )
        if not _receipt_address_matches(receipt, request):
            raise HTTPException(
                status_code=409,
                detail="The recorded Orca execution belongs to another AR selection.",
            )
        return JSONResponse(_refresh_execution(config, path, receipt))
    except (OSError, ValueError, OrcaRuntimeFailure) as error:
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
    request: OrcaDispatchRequest,
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


def _reserve_message_binding_projection(
    config: McpRuntimeConfig,
    path: Path,
    request: OrcaDispatchRequest,
    request_digest: str,
    prepared: dict[str, Any],
) -> tuple[dict[str, str], str | None, JSONResponse | None]:
    """Settle the receipt already at this address and write the message-binding file.

    Returns the binding reference, the agent of a closed execution this request replaces, and the
    response when the request is answered by the existing receipt instead of a new launch.
    """

    binding, reference = _prepared_message_binding_projection(prepared, request.request_id)
    current = _read_receipt(path)
    _verify_prior_message_binding_projection(config, current, request, binding, reference)
    replaces_agent_id = (
        _replaced_agent_id(current)
        if current and current.get("requestId") != str(request.request_id)
        else None
    )
    prior = _reconcile_prior_execution(config, path, request, request_digest)
    if prior is not None:
        return reference, None, prior
    _write_message_binding_projection(config, request.request_id, binding, reference)
    return reference, replaces_agent_id, None


def _start_execution(
    config: McpRuntimeConfig,
    request: OrcaDispatchRequest,
    started_by: StartingAgent | None = None,
) -> JSONResponse:
    context = resolve_orca_role_context(config, request)
    binding = selection_binding(request)
    request_digest = _request_digest(context, request, started_by.agent_id if started_by else None)
    if request.role in TASKLESS_ROLES:
        _migrate_taskless_legacy_receipt(config, request)
    path = _receipt_path(
        config, request, request.request_id if request.role in TASKLESS_ROLES else None
    )
    if request.role in TASKLESS_ROLES:
        prior = _reconcile_prior_execution(config, path, request, request_digest)
        if prior is not None:
            return prior
    role_handover = prepare_orca_role_handover(
        config,
        context,
        agent_override=request.agent_override,
        request_id=request.request_id,
        started_by=started_by,
    )
    prepared = role_handover.handover
    prompt = prepared["prompt"]
    projection_reference, replaces_agent_id, prior = _reserve_message_binding_projection(
        config, path, request, request_digest, prepared
    )
    if prior is not None:
        return prior
    start = _PreparedRoleStart(
        config=config,
        request=request,
        context=context,
        binding=binding,
        request_digest=request_digest,
        receipt_path=path,
        role_handover=role_handover,
        message_binding_projection=projection_reference,
        replaces_agent_id=replaces_agent_id,
        started_by=started_by,
    )
    return _launch_prepared_role_session(start, prompt=prompt)


def _launch_prepared_role_session(start: _PreparedRoleStart, *, prompt: str) -> JSONResponse:
    request = start.request
    role_handover = start.role_handover
    workspace = role_handover.workspace
    provider = role_handover.agent_id
    session_options = role_handover.session_options
    prepared = role_handover.handover
    # The agent id is chosen here: before the receipt is written and before the runtime is called.
    agent_id = mint_agent_id()
    # The compiled first message is stored once; what the agent receives names that file first.
    artifact = write_handover_artifact(prepared["taskReportPath"], prompt)
    launch_call = build_launch_call(
        RoleLaunch(
            agent_id=agent_id,
            request_id=request.request_id,
            context=start.context,
            folder=workspace["path"],
            provider=provider,
            session_options=session_options,
            prompt=first_message(artifact, prompt),
            report_path=prepared["taskReportPath"],
            handover_artifact=artifact,
            settings_file=start.config.config_path,
            accepts_tool_servers=provider_accepts_tool_servers(start.config, provider),
            replaces_agent_id=start.replaces_agent_id,
            parent_agent_id=start.started_by.agent_id if start.started_by else None,
        )
    )
    receipt: dict[str, Any] = {
        "schema": "ar-orca-native-execution/v1",
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
            }
        ),
        "capsuleDigest": prepared["capsuleDigest"],
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
        "requestedAgentOverride": (
            request.agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
            if request.agent_override
            else None
        ),
        "execution": {},
        "handoverArtifact": artifact,
        # What the launch call gives the agent: its tool server, or why none, and the note.
        **applied_to_agent(launch_call),
        "replayRequest": launch_call,
        "detail": "The Paseo runtime is creating the AR role agent.",
    }
    if not _create_receipt(start.receipt_path, receipt):
        # Another process created a receipt at this address since it was last read here.
        prior = _reconcile_prior_execution(
            start.config, start.receipt_path, request, start.request_digest
        )
        if prior is not None:
            return prior
        raise HTTPException(
            status_code=409,
            detail="Another launch of this AR role selection started at the same moment; refresh and start again.",
        )
    return _execute_prepared_launch(start.config, start.receipt_path, receipt)


def _revive_execution(config: McpRuntimeConfig, request: OrcaDispatchRequest) -> JSONResponse:
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
        _verify_leaf_revival_scope(resolve_orca_role_context(config, request), receipt)
    return _revive_agent(config, path, receipt, agent_id)


def _require_paseo_runtime(config: McpRuntimeConfig) -> None:
    """Refuse a launch before anything is written when the settings name no Paseo runtime."""

    try:
        require_bridge_runtime(config)
    except PaseoBridgeFailure as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _bridge_http_error(error: PaseoBridgeFailure) -> HTTPException:
    """A failed bridge call as a route error whose text names the state (the launcher shows it)."""

    return HTTPException(status_code=_BRIDGE_FAILURE_STATUS.get(error.code, 502), detail=str(error))


def _acquire_dispatch_lock() -> None:
    if not _DISPATCH_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="An AR-to-Orca launch or result check is already in progress."
        )
