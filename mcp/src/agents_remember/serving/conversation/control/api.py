"""Registered conversation control routes (260718-CHATS-L3).

The authoritative human control surface for structured Chats: exact-turn
interrupt, the source-aware operation queue with cockpit-only withdrawal and
bounded recovery, typed attachment staging/rebind/submit, read-only effective
policy, and evidence-bound telemetry. Every wire resolves the caller through
the L0 authorization dependency, compares ``expectedBridgeEpoch`` against the
live submission authority, and maps every typed refusal to the serving status
idiom — raw 500s are never a routine refusal path (L0 reviewer obligation O4).
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agents_remember.errors import (
    AuthorityError,
    ConversationCompositionError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.conversation.active.factories import SessionResolutionError
from agents_remember.serving.conversation.control import (
    attachments,
    operations,
    policy,
    queue_projection,
    telemetry,
    withdrawals,
)
from agents_remember.serving.conversation.control.capabilities import (
    control_capabilities_for,
)
from agents_remember.serving.conversation.control.policy import ConversationPolicyProjection
from agents_remember.serving.conversation.control.refs import ControlRefError
from agents_remember.serving.conversation.control.service import (
    ControlOperationError,
    ControlRequest,
    OperationRejectedError,
    conversation_control_service,
)
from agents_remember.serving.conversation.dependencies import (
    get_conversation_runtime,
    resolve_conversation_authorization,
)
from agents_remember.serving.conversation.models import (
    AttachmentOperationProjection,
    ConversationSubmitRequest,
    ConversationTelemetry,
    InterruptOperation,
    NonEmptyText,
    OperationQueueProjection,
    PendingWithdrawalRecoveryList,
    WireModel,
    WithdrawalOperationProjection,
    WithdrawalRecovery,
    WithdrawQueueRequest,
)
from agents_remember.serving.conversation.response_contract import (
    CONTROL_RESPONSES,
    INTERRUPT_OUTCOME_RESPONSES,
    WITHDRAW_OUTCOME_RESPONSES,
    ConversationSubmitted,
    StagedAttachments,
    WithdrawQueueAnswer,
)
from agents_remember.serving.harness_control_models import MAX_SUBMIT_ASSET_BYTES
from agents_remember.serving.response_contract import StatusRefusal

router = APIRouter(
    prefix="/api/terminal/{ar_session_id}",
    tags=["structured-conversation-control"],
)

_TYPED_ERRORS = (
    AuthorityError,
    ConversationCompositionError,
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
    ControlRefError,
    ControlOperationError,
    SessionResolutionError,
)

EpochQuery = Annotated[str, Query(alias="expectedBridgeEpoch", min_length=1)]


class InterruptBody(WireModel):
    turn_id: NonEmptyText
    request_id: NonEmptyText


class WithdrawStatusBody(WireModel):
    operation_ref: NonEmptyText
    withdraw_request_id: NonEmptyText


class RecoveryFetchBody(WireModel):
    recovery_ref: NonEmptyText


class RecoveryAckBody(WireModel):
    recovery_ref: NonEmptyText
    disposition: Literal["replace-current-draft", "keep-current-draft"]


class RebindBody(WireModel):
    recovery_asset_ref: NonEmptyText
    request_id: NonEmptyText


def _error(status_code: int, slug: str, detail: str, **extra: object) -> JSONResponse:
    return JSONResponse(
        content={"status": slug, "detail": detail, **extra},
        status_code=status_code,
    )


def _map_typed_error(error: Exception) -> JSONResponse:
    if isinstance(error, HarnessBridgeEpochMismatchError):
        return _error(
            409,
            "bridge-epoch-mismatch",
            str(error),
            expectedBridgeEpoch=error.expected,
            actualBridgeEpoch=error.actual,
        )
    if isinstance(error, AuthorityError):
        return _error(403, "authorization-failed", str(error))
    if isinstance(error, ConversationCompositionError):
        return _error(503, "composition-unavailable", str(error))
    if isinstance(error, (ControlRefError, ControlOperationError, SessionResolutionError)):
        return _error(error.http_status, error.status_slug, str(error))
    if isinstance(error, HarnessControlError):
        return _error(503, "control-unavailable", str(error))
    raise error


def _dump(model: WireModel) -> dict[str, object]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


# Every route below chooses its status from the operation it built, and routes every typed
# refusal through ``_map_typed_error`` -- which is why one shared ``responses`` table covers
# all 17 of them, and why the success model alone would be a half-declaration.
@router.post(
    "/conversation/interrupt",
    response_model=InterruptOperation,
    responses={**CONTROL_RESPONSES, **INTERRUPT_OUTCOME_RESPONSES},
)
async def conversation_interrupt(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: InterruptBody,
    request: Request,
) -> JSONResponse:
    """Request one exact-turn interrupt; acknowledgement, never settlement."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        operation = await operations.interrupt(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            turn_id=body.turn_id,
            request_id=body.request_id,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content=_dump(operation), status_code=operations.interrupt_http_status(operation)
    )


@router.post(
    "/conversation/interrupt-status",
    response_model=InterruptOperation,
    responses={**CONTROL_RESPONSES, **INTERRUPT_OUTCOME_RESPONSES},
)
async def conversation_interrupt_status(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: InterruptBody,
    request: Request,
) -> JSONResponse:
    """Read one interrupt operation's current acknowledgement/settlement."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        operation = await operations.interrupt_status(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            turn_id=body.turn_id,
            request_id=body.request_id,
            reconcile=False,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content=_dump(operation), status_code=operations.interrupt_http_status(operation)
    )


@router.post(
    "/conversation/interrupt-reconcile",
    response_model=InterruptOperation,
    responses={**CONTROL_RESPONSES, **INTERRUPT_OUTCOME_RESPONSES},
)
async def conversation_interrupt_reconcile(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: InterruptBody,
    request: Request,
) -> JSONResponse:
    """Actively re-drive a lost interrupt acknowledgement and settle."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        operation = await operations.interrupt_status(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            turn_id=body.turn_id,
            request_id=body.request_id,
            reconcile=True,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content=_dump(operation), status_code=operations.interrupt_http_status(operation)
    )


@router.get(
    "/operation-queue",
    response_model=OperationQueueProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_operation_queue(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
) -> JSONResponse:
    """The complete retained live queue; bodies never cross this wire."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await queue_projection.operation_queue(
            conversation_control_service(runtime),
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


# Two success shapes: ``withdraw_http_status`` reads which one was built to pick 200 vs the
# refusal status, so a failed withdrawal is still this route's own answer, not an error body.
@router.post(
    "/operation-queue/withdraw",
    response_model=WithdrawQueueAnswer,
    responses={**CONTROL_RESPONSES, **WITHDRAW_OUTCOME_RESPONSES},
)
async def conversation_withdraw(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: WithdrawQueueRequest,
    request: Request,
) -> JSONResponse:
    """One authorized atomic cockpit-only withdrawal with bounded recovery."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        response = await withdrawals.withdraw(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            operation_ref=body.operation_ref,
            withdrawal_ref=body.withdrawal_ref,
            withdraw_request_id=body.withdraw_request_id,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content=_dump(response), status_code=withdrawals.withdraw_http_status(response)
    )


@router.post(
    "/operation-queue/withdraw-status",
    response_model=WithdrawalOperationProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_withdraw_status(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: WithdrawStatusBody,
    request: Request,
) -> JSONResponse:
    """Read one withdrawal operation; recovery bodies never cross this wire."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await withdrawals.withdraw_status(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            operation_ref=body.operation_ref,
            withdraw_request_id=body.withdraw_request_id,
            reconcile=False,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


@router.post(
    "/operation-queue/withdraw-reconcile",
    response_model=WithdrawalOperationProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_withdraw_reconcile(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: WithdrawStatusBody,
    request: Request,
) -> JSONResponse:
    """Actively re-drive a lost withdrawal response with the same request id."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await withdrawals.withdraw_status(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            operation_ref=body.operation_ref,
            withdraw_request_id=body.withdraw_request_id,
            reconcile=True,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


@router.get(
    "/operation-queue/pending-withdrawal-recoveries",
    response_model=PendingWithdrawalRecoveryList,
    responses=CONTROL_RESPONSES,
)
async def conversation_pending_recoveries(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
) -> JSONResponse:
    """The opaque pending-recovery discovery list; content never crosses."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await withdrawals.pending_recoveries(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            )
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


@router.post(
    "/operation-queue/withdraw-recovery",
    response_model=WithdrawalRecovery,
    responses=CONTROL_RESPONSES,
)
async def conversation_fetch_recovery(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: RecoveryFetchBody,
    request: Request,
) -> JSONResponse:
    """The authenticated exact recovery fetch; only while unacknowledged."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        recovery = await withdrawals.fetch_recovery(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            recovery_ref=body.recovery_ref,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(recovery))


@router.post(
    "/operation-queue/withdraw-recovery-ack",
    response_model=WithdrawalOperationProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_ack_recovery(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: RecoveryAckBody,
    request: Request,
) -> JSONResponse:
    """Record the draft disposition and permit raw-content disposal."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await withdrawals.acknowledge_recovery(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            recovery_ref=body.recovery_ref,
            disposition=body.disposition,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


class StageAttachmentsForm(BaseModel):
    """The multipart body of one attachment-staging request: its id, its metadata, its bytes.

    One model because the three parts are one upload -- the metadata array is positionally matched
    against the assets, and both are only meaningful under the request id that makes the staging
    idempotent.
    """

    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    metadata: str | None = None
    assets: list[UploadFile] | None = None


@router.post(
    "/conversation/attachments",
    response_model=StagedAttachments,
    responses=CONTROL_RESPONSES,
)
async def conversation_stage_attachments(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
    form: Annotated[StageAttachmentsForm, Form()],
) -> JSONResponse:
    """Stage and bind typed caller uploads; idempotent under the same content."""

    request_id = form.request_id
    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        service = conversation_control_service(runtime)
        entry = await service.resolve_entry(ar_session_id)
        epoch = await service.verify_epoch(entry, expected_bridge_epoch)
        snapshot = await service.live_snapshot(entry)
        identity = service.build_identity(entry, bridge_epoch=epoch, snapshot=snapshot)
        capabilities = control_capabilities_for(identity.harness_id, snapshot).attachments
        uploads = await _parse_uploads(form.assets or [], form.metadata)
        answer = await attachments.stage(
            ControlRequest(
                service=service,
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            request_id=request_id,
            kind_capabilities={
                "image": capabilities.image,
                "file": capabilities.file,
                "resource": capabilities.resource,
            },
            uploads=uploads,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content={
            "operation": _dump(answer.operation),
            "receipts": [_dump(receipt) for receipt in answer.receipts],
        }
    )


@router.post(
    "/conversation/attachments/rebind",
    response_model=StagedAttachments,
    responses=CONTROL_RESPONSES,
)
async def conversation_rebind_attachment(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    body: RebindBody,
    request: Request,
) -> JSONResponse:
    """Exchange one authorized recovery asset for a new one-use staged asset."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        answer = await attachments.rebind(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            recovery_asset_ref=body.recovery_asset_ref,
            request_id=body.request_id,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(
        content={
            "operation": _dump(answer.operation),
            "receipts": [_dump(receipt) for receipt in answer.receipts],
        }
    )


@router.get(
    "/conversation/attachments/{request_id}/status",
    response_model=AttachmentOperationProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_attachment_status(
    ar_session_id: str,
    request_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
) -> JSONResponse:
    """Read one attachment operation's semantic lifecycle projection."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await attachments.attachment_status(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            request_id=request_id,
            reconcile=False,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


@router.post(
    "/conversation/attachments/{request_id}/reconcile",
    response_model=AttachmentOperationProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_attachment_reconcile(
    ar_session_id: str,
    request_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
) -> JSONResponse:
    """Advance one attachment operation from the retained timeline."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await attachments.attachment_status(
            ControlRequest(
                service=conversation_control_service(runtime),
                authorization=authorization,
                ar_session_id=ar_session_id,
                expected_bridge_epoch=expected_bridge_epoch,
            ),
            request_id=request_id,
            reconcile=True,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


# One body shape across three statuses: ``acceptance`` picks 200 / 202 (unknown) / 422
# (rejected or unsupported), so all three are the SAME success model. The 422 entry must still
# UNION in ``CONTROL_RESPONSES[422]``: this is a ``{**a, **b}`` dict merge, so a bare
# ``{422: ConversationSubmitted}`` would delete the shared refusal rather than join it, and
# ``_map_typed_error`` reaches this route's 422 too (``CapabilityRefusedError`` and
# ``OperationRejectedError`` both carry ``http_status = 422``).
@router.post(
    "/conversation/submit",
    response_model=ConversationSubmitted,
    responses={
        **CONTROL_RESPONSES,
        202: {"model": ConversationSubmitted, "description": "Accepted, acceptance unknown"},
        422: {
            "model": ConversationSubmitted | StatusRefusal,
            "description": "Rejected or unsupported, or a typed refusal of the request as posed",
        },
    },
)
async def conversation_submit(
    ar_session_id: str,
    body: ConversationSubmitRequest,
    request: Request,
) -> JSONResponse:
    """The typed composer submit; disposition ``next`` over the AR FIFO."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        answer = await attachments.submit(
            conversation_control_service(runtime),
            authorization,
            ar_session_id,
            body=body,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    status_code = 200
    if answer.acceptance == "unknown":
        status_code = 202
    elif answer.acceptance in {"rejected", "unsupported"}:
        status_code = 422
    content: dict[str, object] = {
        "requestId": answer.request_id,
        "acceptance": answer.acceptance,
        "submittedAt": answer.submitted_at,
        "bridgeEpoch": answer.bridge_epoch,
    }
    if answer.operation_ref is not None:
        content["operationRef"] = answer.operation_ref
    if answer.attachment is not None:
        content["attachment"] = _dump(answer.attachment)
    if answer.detail is not None:
        content["detail"] = answer.detail
    return JSONResponse(content=content, status_code=status_code)


@router.get(
    "/conversation/policy",
    response_model=ConversationPolicyProjection,
    responses=CONTROL_RESPONSES,
)
async def conversation_policy(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
) -> JSONResponse:
    """The read-only effective policy evidence; no mutation surface exists."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await policy.conversation_policy(
            conversation_control_service(runtime),
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


@router.get(
    "/conversation/telemetry",
    response_model=ConversationTelemetry,
    responses=CONTROL_RESPONSES,
)
async def conversation_telemetry(
    ar_session_id: str,
    expected_bridge_epoch: EpochQuery,
    request: Request,
) -> JSONResponse:
    """The evidence-bound telemetry projection; missing data stays absent."""

    try:
        runtime = get_conversation_runtime(request)
        authorization = resolve_conversation_authorization(request)
        projection = await telemetry.conversation_telemetry(
            conversation_control_service(runtime),
            authorization,
            ar_session_id,
            expected_bridge_epoch=expected_bridge_epoch,
        )
    except _TYPED_ERRORS as exc:
        return _map_typed_error(exc)
    return JSONResponse(content=_dump(projection))


async def _parse_uploads(
    assets: list[UploadFile],
    metadata: str | None,
) -> list[attachments.StagedUpload]:
    metas = _parse_metadata_array(metadata)
    if len(metas) not in {0, len(assets)}:
        raise OperationRejectedError("attachment metadata must describe every uploaded file")
    uploads: list[attachments.StagedUpload] = []
    for position, upload in enumerate(assets):
        meta = metas[position] if metas else {}
        uploads.append(await _upload_for(upload, meta, position=position))
    return uploads


def _parse_metadata_array(metadata: str | None) -> list[dict[str, object]]:
    """The optional parallel metadata array; every entry must be an object."""

    if metadata is None:
        return []
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise OperationRejectedError("attachment metadata must be a JSON array") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise OperationRejectedError("attachment metadata must be a JSON array of objects")
    return parsed


async def _upload_for(
    upload: UploadFile, meta: dict[str, object], *, position: int
) -> attachments.StagedUpload:
    """One byte-bounded upload with its declared kind, name, and alt."""

    kind = meta.get("kind")
    if kind not in {"image", "file", "resource"}:
        raise OperationRejectedError("each staged asset requires its attachment kind")
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        name = upload.filename or f"asset-{position + 1}"
    alt = meta.get("alt")
    if alt is not None and (not isinstance(alt, str) or not alt):
        raise OperationRejectedError("asset alt text must be non-empty text")
    data = await upload.read(MAX_SUBMIT_ASSET_BYTES + 1)
    return attachments.StagedUpload(
        kind=cast(Literal["image", "file", "resource"], kind),
        name=name,
        mime_type=upload.content_type or "",
        alt=alt,
        data=data,
    )


__all__ = ["router"]
