"""AR's role-aware launcher seam for native Orca sessions.

AR resolves the role hierarchy, paired leaf workspace, task capsule, and report scope. Orca owns
the actual agent session and its runtime identity. Receipts retain only that execution reference;
they never update AR task, review, curation, or Git state.
"""

from __future__ import annotations

import json
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from agents_remember.application.orca_task_context import (
    LEAF_ROLES,
    TASKLESS_ROLES,
    OrcaRoleContext,
    resolve_orca_role_context,
    selection_binding,
)
from agents_remember.cli.orca_handover_artifacts import (
    build_role_handover_artifact,
    read_role_handover_artifact,
    write_role_handover_artifact,
)
from agents_remember.cli.orca_runtime import (
    OrcaRuntimeFailure,
)
from agents_remember.cli.orca_runtime import (
    digest as _digest,
)
from agents_remember.cli.orca_runtime import (
    require_pairing as _require_pairing,
)
from agents_remember.cli.orca_runtime import (
    runtime_call as _runtime_call,
)
from agents_remember.cli.orca_task_liveness import (
    _reconcile_prior_execution,
    _refresh_execution,
)
from agents_remember.cli.orca_task_preparation import (
    PreparedOrcaRoleHandover,
    _launcher_catalog,
    _verify_leaf_revival_scope,
    prepare_orca_role_handover,
)
from agents_remember.cli.orca_task_receipts import (
    _execute_prepared_launch,
    _migrate_taskless_legacy_receipt,
    _now_iso,
    _public_execution,
    _read_receipt,
    _receipt_address_matches,
    _receipt_path,
    _request_digest,
    _taskless_execution_receipts,
    _verify_message_binding_projection,
    _write_message_binding_projection,
    _write_receipt,
)
from agents_remember.cli.paseo_frame import frame_descriptor, request_dashboard_origin
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.orca_launcher import (
    OrcaDispatchRequest,
    OrcaLauncherOptionsRequest,
    OrcaResultRequest,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError

_DISPATCH_LOCK = threading.Lock()


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


@dataclass(frozen=True, slots=True)
class NativeRoleSessionPreparation:
    execution: dict[str, Any]
    request_id: uuid.UUID
    handover_reference: dict[str, str]
    handover_artifact: dict[str, Any]


def _json_response_payload(response: JSONResponse) -> Any:
    body = response.body
    if isinstance(body, memoryview):
        body = body.tobytes()
    return json.loads(body)


def register_orca_task_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    app.add_api_route("/api/orca/frame", _bind_frame_endpoint(config), methods=["GET"])
    app.add_api_route(
        "/api/orca/launcher/options",
        _bind_options_endpoint(config),
        methods=["POST"],
    )
    app.add_api_route("/api/orca/dispatch", _bind_dispatch_endpoint(config), methods=["POST"])
    app.add_api_route("/api/orca/result", _bind_result_endpoint(config), methods=["POST"])


def orca_frame(config: McpRuntimeConfig, request: Request) -> JSONResponse:
    """Where this dashboard origin frames the Paseo web UI, or why it cannot (always HTTP 200)."""
    return JSONResponse(frame_descriptor(config, request_dashboard_origin(request)))


def _bind_frame_endpoint(config: McpRuntimeConfig):
    def endpoint(request: Request) -> JSONResponse:
        return orca_frame(config, request)

    return endpoint


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
    _require_pairing()
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
    except (OSError, ValueError, TaskDocumentRefError, OrcaRuntimeFailure) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        _DISPATCH_LOCK.release()


def _orca_dispatch_endpoint(config: McpRuntimeConfig, request: OrcaDispatchRequest) -> JSONResponse:
    _require_pairing()
    _acquire_dispatch_lock()
    try:
        if request.action == "revive":
            return _revive_execution(config, request)
        return _start_execution(config, request)
    except (OSError, ValueError, TaskDocumentRefError, OrcaRuntimeFailure) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        _DISPATCH_LOCK.release()


def _orca_result_endpoint(config: McpRuntimeConfig, request: OrcaResultRequest) -> JSONResponse:
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
) -> tuple[dict[str, str], JSONResponse | None]:
    binding, reference = _prepared_message_binding_projection(prepared, request.request_id)
    _verify_prior_message_binding_projection(
        config, _read_receipt(path), request, binding, reference
    )
    prior = _reconcile_prior_execution(config, path, request, request_digest)
    if prior is not None:
        return reference, prior
    _write_message_binding_projection(config, request.request_id, binding, reference)
    return reference, None


def _start_execution(config: McpRuntimeConfig, request: OrcaDispatchRequest) -> JSONResponse:
    _require_pairing()
    context = resolve_orca_role_context(config, request)
    binding = selection_binding(request)
    request_digest = _request_digest(context, request)
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
    )
    prepared = role_handover.handover
    prompt = prepared["prompt"]
    projection_reference, prior = _reserve_message_binding_projection(
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
    )
    return _launch_prepared_role_session(start, prompt=prompt)


def prepare_idle_native_role_session(
    config: McpRuntimeConfig, request: OrcaDispatchRequest
) -> NativeRoleSessionPreparation:
    """Start an idle native leaf session and persist its complete AR role handover."""

    if request.role not in LEAF_ROLES:
        raise ValueError(
            "Native task preparation is available only for Worker, Reviewer, and Curator roles."
        )
    _acquire_dispatch_lock()
    try:
        context = resolve_orca_role_context(config, request)
        binding = selection_binding(context)
        path = _receipt_path(config, request)
        role_handover = prepare_orca_role_handover(
            config,
            context,
            agent_override=request.agent_override,
            request_id=request.request_id,
            entry_mode="native-orca-task",
        )
        prepared = role_handover.handover
        request_digest = _digest(
            {
                "selection": binding,
                "agentOverride": (
                    request.agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
                    if request.agent_override
                    else None
                ),
                "taskDocumentDigest": prepared["taskDocumentDigest"],
                "capsuleDigest": prepared["capsuleDigest"],
                "capsuleOperation": prepared["capsuleOperation"],
                "workspace": role_handover.workspace,
                "agent": role_handover.agent_id,
                "sessionOptions": role_handover.session_options,
                "arMcpContext": prepared.get("arMcpContext"),
            }
        )
        message_binding_projection, prior = _reserve_message_binding_projection(
            config, path, request, request_digest, prepared
        )
        if prior is not None:
            receipt = _read_receipt(path)
            reference = receipt.get("handoverProjection") if receipt else None
            if (
                receipt is None
                or receipt.get("nativeSessionPurpose") != "idle-native-task"
                or not isinstance(reference, dict)
                or not isinstance(reference.get("path"), str)
                or not isinstance(reference.get("sha256"), str)
            ):
                raise ValueError(
                    "An existing role session has no prepared native handover; use its native Orca identity without attaching a new task."
                )
            report = receipt.get("report")
            report_path = report.get("path") if isinstance(report, dict) else None
            if not isinstance(report_path, str):
                raise ValueError(
                    "The existing native role session has no canonical task report path."
                )
            artifact = read_role_handover_artifact(report_path, reference)
            return NativeRoleSessionPreparation(
                execution=_json_response_payload(prior),
                request_id=uuid.UUID(str(receipt["requestId"])),
                handover_reference=reference,
                handover_artifact=artifact,
            )
        artifact = build_role_handover_artifact(role_handover)
        handover_reference = write_role_handover_artifact(prepared["taskReportPath"], artifact)
        start = _PreparedRoleStart(
            config=config,
            request=request,
            context=context,
            binding=binding,
            request_digest=request_digest,
            receipt_path=path,
            role_handover=role_handover,
            message_binding_projection=message_binding_projection,
        )
        response = _launch_prepared_role_session(
            start,
            prompt=None,
            handover_projection=handover_reference,
        )
        return NativeRoleSessionPreparation(
            execution=_json_response_payload(response),
            request_id=request.request_id,
            handover_reference=handover_reference,
            handover_artifact=artifact,
        )
    finally:
        _DISPATCH_LOCK.release()


def _launch_prepared_role_session(
    start: _PreparedRoleStart,
    *,
    prompt: str | None,
    handover_projection: dict[str, str] | None = None,
) -> JSONResponse:
    request = start.request
    role_handover = start.role_handover
    workspace = role_handover.workspace
    agent_id = role_handover.agent_id
    session_options = role_handover.session_options
    agent_args = shlex.join(role_handover.agent_arg_tokens)
    prepared = role_handover.handover
    operation_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
    launch_request: dict[str, Any] = {
        "operationId": operation_id,
        "agent": agent_id,
        "target": {"kind": "existing", "worktree": workspace["selector"]},
        "launchSource": "agents-remember-role-launcher",
    }
    if prompt is not None:
        launch_request["prompt"] = {"text": prompt, "delivery": "submit"}
    if session_options:
        launch_request["sessionOptions"] = session_options
    if agent_args:
        launch_request["agentArgs"] = agent_args
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
                "agent": agent_id,
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
        **({"handoverProjection": handover_projection} if handover_projection else {}),
        **({"nativeSessionPurpose": "idle-native-task"} if prompt is None else {}),
        "operationId": operation_id,
        "status": "starting",
        "createdAt": _now_iso(),
        "workspace": workspace,
        "agent": {"id": agent_id, **session_options},
        "requestedAgentOverride": (
            request.agent_override.model_dump(mode="json", by_alias=True, exclude_none=True)
            if request.agent_override
            else None
        ),
        "execution": {},
        "replayRequest": launch_request,
        "detail": (
            "Orca is starting an idle native role session; no native Task or Dispatch has started."
            if prompt is None
            else "Orca is starting the native AR role session."
        ),
    }
    _write_receipt(start.receipt_path, receipt)
    return _execute_prepared_launch(start.config, start.receipt_path, receipt)


def _revive_execution(config: McpRuntimeConfig, request: OrcaDispatchRequest) -> JSONResponse:
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
    reference = receipt.get("execution")
    if not isinstance(reference, dict) or reference.get("kind") != "structured":
        raise HTTPException(
            status_code=409,
            detail="This Orca session has no native structured-session revive path.",
        )
    if request.role in LEAF_ROLES:
        context = resolve_orca_role_context(config, request)
        _verify_leaf_revival_scope(config, context, receipt)
    status = _refresh_execution(config, path, receipt)
    if status.get("canRevive") is not True:
        raise HTTPException(
            status_code=409,
            detail="Orca has no validated interrupted-session offer for this exact AR workspace and agent.",
        )
    session_id = reference.get("sessionId")
    if not isinstance(session_id, str):
        raise HTTPException(
            status_code=409, detail="The saved structured session identity is incomplete."
        )
    resumed = _runtime_call(config, "restart-continue", {"sessionId": session_id})
    resumed_rows = resumed.get("resumed", [])
    continued_rows = resumed.get("continued", [])
    resumed_row = next(
        (
            row
            for row in resumed_rows
            if isinstance(row, dict) and row.get("sessionId") == session_id
        ),
        None,
    )
    continued_row = next(
        (
            row
            for row in continued_rows
            if isinstance(row, dict) and row.get("sessionId") == session_id
        ),
        None,
    )
    if resumed_row is None or resumed_row.get("outcome") != "resumed":
        raise HTTPException(
            status_code=409,
            detail="Orca refused to resume this session; no new session was launched.",
        )
    if continued_row is None:
        receipt.update(
            status="unknown",
            detail="Orca resumed the exact session but returned no continuation outcome; inspect native Chats before acting again.",
            revivedAt=_now_iso(),
            updatedAt=_now_iso(),
        )
    elif continued_row.get("outcome") == "refused":
        receipt.update(
            status="running",
            detail="Orca resumed the exact session, but its continuation was refused; continue it in native Chats.",
            revivedAt=_now_iso(),
            updatedAt=_now_iso(),
        )
    else:
        receipt.update(
            status="running",
            detail=f"Orca resumed the exact session; continuation state is {continued_row.get('outcome', 'unknown')}.",
            revivedAt=_now_iso(),
            updatedAt=_now_iso(),
        )
    continuation_reason = continued_row.get("reason") if isinstance(continued_row, dict) else None
    receipt["resumeResult"] = {
        "resumed": resumed_row.get("outcome"),
        "continuation": continued_row.get("outcome") if continued_row else "unknown",
        **({"reason": continuation_reason[:300]} if isinstance(continuation_reason, str) else {}),
    }
    _write_receipt(path, receipt)
    return JSONResponse(_public_execution(receipt))


def _acquire_dispatch_lock() -> None:
    if not _DISPATCH_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="An AR-to-Orca launch or result check is already in progress."
        )
