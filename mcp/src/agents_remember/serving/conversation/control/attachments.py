"""Typed attachment staging and lifecycle (260718-CHATS-L3, R4).

Bounded ephemeral payload transport for the typed composer: stage binds each
asset to the caller, the exact ``arSessionId + bridgeEpoch``, the caller-minted
request id, and the attachment kind, with fixture-backed MIME/count/byte
limits; submit consumes one-use staged assets through the L2E asset channel
(refs on the wire, digest-verified bytes in the user-private spool); status and
reconcile advance the semantic lifecycle from the authority's retained
timeline; withdrawal marks assets recoverable under the same lease as the text
recovery; rebind atomically exchanges an authorized recovery asset for a new
one-use staged asset under a fresh request id; expiry and acknowledgement
delete recoverable/staged bytes. Accessible alt text with its provenance
(supplied description or truthful filename+MIME fallback) survives staging,
submit, withdrawal recovery, and rebind. Unknown outcomes are retained, never
re-uploaded under a new id; staging is never conversation persistence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal, cast

from agents_remember.models.conversations.attachments import (
    AttachmentOperationProjection,
    AttachmentReceipt,
)
from agents_remember.models.conversations.capabilities import (
    AttachmentCapability,
)
from agents_remember.models.conversations.control_wire import (
    ControlSubmission,
    OperationTimelineItem,
    SubmissionReceipt,
)
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
)
from agents_remember.models.conversations.primitives import (
    OperationFingerprint,
)
from agents_remember.models.conversations.submissions import (
    AssetSubmitBlock,
    ComposerSubmitBlock,
    ConversationSubmitRequest,
)
from agents_remember.models.conversations.telemetry import (
    operation_fingerprint,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.conversation.control.asset_spool import (
    AssetRecord,
    StagedUpload,
    delete_asset_bytes,
    exchange_bytes,
    require_safe_component,
    stage_one,
    upload_identity,
    upload_identity_from_record,
    verify_recoverable_bytes,
    wire_asset,
)
from agents_remember.serving.conversation.control.previews import payload_digest
from agents_remember.serving.conversation.control.refs import (
    OperationIdentity,
    RefBinding,
    RefTarget,
    decode_ref,
    mint_ref,
    ref_identity,
)
from agents_remember.serving.conversation.control.service import (
    MAX_ATTACHMENT_OPS_PER_CHANNEL,
    MAX_JOURNAL_ENTRIES_PER_CHANNEL,
    MAX_SUBMITS_PER_CHANNEL,
    STAGED_ASSET_TTL_SECONDS,
    ControlChannel,
    ControlRequest,
    ControlScope,
    ConversationControlService,
    JournalEntry,
    OperationConflictError,
    OperationNotFoundError,
    OperationRejectedError,
    iso_expired,
    iso_seconds_after,
)

AttachmentPhase = Literal[
    "staging",
    "staged",
    "queued",
    "dispatching",
    "accepted",
    "recoverable",
    "failed",
    "expired",
    "unknown",
]
AttachmentOutcome = Literal[
    "pending", "accepted", "rejected", "withdrawn", "failed", "expired", "unknown"
]

_LIVE_TIMELINE_STATES = {"queued", "dispatching", "unknown"}
_TERMINAL_SUBMIT_ACCEPTANCES = {"rejected", "unsupported"}


@dataclass
class AttachmentOperation:
    request_id: str
    fingerprint: OperationFingerprint
    revision: int
    bridge_epoch: str
    phase: AttachmentPhase
    outcome: AttachmentOutcome
    assets: list[AssetRecord]
    expires_at: str
    operation_ref: str | None = None
    recovery_expires_at: str | None = None
    submit_fingerprint: OperationFingerprint | None = None
    submit_receipt: SubmissionReceipt | None = None
    rebound: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SubmitAnswer:
    request_id: str
    acceptance: str
    submitted_at: str
    bridge_epoch: str
    operation_ref: str | None
    attachment: AttachmentOperationProjection | None
    detail: str | None


@dataclass(frozen=True)
class StageAnswer:
    operation: AttachmentOperationProjection
    receipts: tuple[AttachmentReceipt, ...]


async def stage(
    request: ControlRequest,
    *,
    request_id: str,
    kind_capabilities: dict[str, AttachmentCapability],
    uploads: list[StagedUpload],
) -> StageAnswer:
    """Stage and bind caller uploads; idempotent under the exact same content."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    require_safe_component(request_id, label="requestId")
    if not 1 <= len(uploads) <= 4:
        raise OperationRejectedError("attachment staging requires 1..4 assets per request")
    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    _sweep_expired(service, channel)
    fingerprint = operation_fingerprint(
        "attachment-stage",
        authorization,
        {
            "arSessionId": ar_session_id,
            "bridgeEpoch": epoch,
            "requestId": request_id,
            "assets": [upload_identity(upload) for upload in uploads],
        },
    )
    existing = channel.attachments.get(request_id)
    if existing is not None:
        operation = _as_operation(existing)
        if operation.fingerprint != fingerprint:
            raise OperationConflictError(
                f"attachment request id {request_id!r} already belongs to its first content"
            )
        return StageAnswer(
            operation=_projection(operation),
            receipts=tuple(_receipt(ar_session_id, asset, operation) for asset in operation.assets),
        )
    if len(channel.attachments) >= MAX_ATTACHMENT_OPS_PER_CHANNEL:
        _evict_attachment_operation(channel, request_id)
    assets_root = service.spool_assets_root(entry)
    staged: list[AssetRecord] = []
    now = service.clock()
    expires_at = iso_seconds_after(now, STAGED_ASSET_TTL_SECONDS)
    for position, upload in enumerate(uploads, start=1):
        staged.append(
            stage_one(kind_capabilities, assets_root, request_id, upload, position=position)
        )
    operation = AttachmentOperation(
        request_id=request_id,
        fingerprint=fingerprint,
        revision=1,
        bridge_epoch=epoch,
        phase="staged",
        outcome="pending",
        assets=staged,
        expires_at=expires_at,
    )
    channel.attachments[request_id] = operation
    return StageAnswer(
        operation=_projection(operation),
        receipts=tuple(_receipt(ar_session_id, asset, operation) for asset in operation.assets),
    )


async def submit(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    ar_session_id: str,
    *,
    body: ConversationSubmitRequest,
) -> SubmitAnswer:
    """The typed composer submit; disposition ``next`` over the AR FIFO."""

    require_safe_component(body.request_id, label="requestId")
    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, body.expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    _sweep_expired(service, channel)
    fingerprint = operation_fingerprint(
        "conversation-submit",
        authorization,
        {
            "arSessionId": ar_session_id,
            "bridgeEpoch": epoch,
            "requestId": body.request_id,
            "draftRevision": body.draft_revision,
            "content": [block.model_dump(mode="json", by_alias=True) for block in body.content],
        },
    )
    prior_answer = _replay_prior_submit(channel, body.request_id, fingerprint)
    if prior_answer is not None:
        return prior_answer
    text, operation, asset_records = _compose(channel, body)
    if _staged_submit_conflict(operation, fingerprint):
        raise OperationConflictError(
            f"submit request id {body.request_id!r} already belongs to its first content"
        )
    receipt = await asyncio.to_thread(
        service.control_plane.submit,
        entry,
        text,
        ControlSubmission(
            source="cockpit",
            request_id=body.request_id,
            expected_bridge_epoch=epoch,
            assets=[wire_asset(record) for record in asset_records] or None,
        ),
    )
    if receipt.acceptance not in _TERMINAL_SUBMIT_ACCEPTANCES:
        for record in asset_records:
            record.consumed = True
    content = SubmittedContent(body=body, receipt=receipt, text=text, asset_records=asset_records)
    scope = ControlScope(service, authorization, ar_session_id, epoch)
    answer = await _admit(epoch, content, operation)
    operation_ref = await _mint_queue_ref(scope, entry, body.request_id)
    answer = SubmitAnswer(
        request_id=answer.request_id,
        acceptance=answer.acceptance,
        submitted_at=answer.submitted_at,
        bridge_epoch=answer.bridge_epoch,
        operation_ref=operation_ref,
        attachment=answer.attachment,
        detail=answer.detail,
    )
    if operation is not None:
        operation.submit_fingerprint = fingerprint
        operation.submit_receipt = receipt
        operation.operation_ref = operation_ref
        operation.revision += 1
    _store_submit_artifacts(channel, content, fingerprint, answer)
    return answer


def _staged_submit_conflict(
    operation: AttachmentOperation | None, fingerprint: OperationFingerprint
) -> bool:
    return (
        operation is not None
        and operation.submit_fingerprint is not None
        and operation.submit_fingerprint != fingerprint
    )


def _replay_prior_submit(
    channel: ControlChannel, request_id: str, fingerprint: OperationFingerprint
) -> SubmitAnswer | None:
    """The idempotent replay of an already-answered submit, or None."""

    prior = channel.submits.get(request_id)
    if prior is None:
        return None
    prior_fingerprint, prior_answer = prior
    prior_answer = cast(SubmitAnswer, prior_answer)
    if prior_fingerprint != fingerprint:
        raise OperationConflictError(
            f"submit request id {request_id!r} already belongs to its first content"
        )
    channel.submits.move_to_end(request_id)
    return prior_answer


@dataclass(frozen=True)
class SubmittedContent:
    """What one cockpit submit actually sent, and what the bridge said about it.

    The request body, the sanitized text the daemon holds for recovery, the assets bound to it and
    the receipt that came back describe a single submission. Splitting them let the journal entry
    be written from one submission's text against another's receipt.
    """

    body: ConversationSubmitRequest
    receipt: SubmissionReceipt
    text: str
    asset_records: list[AssetRecord]


def _store_submit_artifacts(
    channel: ControlChannel,
    content: SubmittedContent,
    fingerprint: OperationFingerprint,
    answer: SubmitAnswer,
) -> None:
    """Record the idempotent replay answer and the cockpit submit journal entry."""

    body, receipt = content.body, content.receipt
    text, asset_records = content.text, content.asset_records
    channel.submits[body.request_id] = (fingerprint, answer)
    channel.submits.move_to_end(body.request_id)
    while len(channel.submits) > MAX_SUBMITS_PER_CHANNEL:
        channel.submits.popitem(last=False)
    if receipt.acceptance in _TERMINAL_SUBMIT_ACCEPTANCES:
        return
    channel.journal[body.request_id] = JournalEntry(
        request_id=body.request_id,
        text=text,
        content_digest=payload_digest(text, tuple(record.reference() for record in asset_records)),
        draft_revision=body.draft_revision,
        asset_ids=tuple(record.asset_id for record in asset_records),
        submitted_at=receipt.submitted_at,
    )
    channel.journal.move_to_end(body.request_id)
    while len(channel.journal) > MAX_JOURNAL_ENTRIES_PER_CHANNEL:
        channel.journal.popitem(last=False)


async def attachment_status(
    request: ControlRequest,
    *,
    request_id: str,
    reconcile: bool,
) -> AttachmentOperationProjection:
    """Read (and, for reconcile, advance from the retained timeline) one operation."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    del authorization
    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    _sweep_expired(service, channel)
    stored = channel.attachments.get(request_id)
    if stored is None:
        raise OperationNotFoundError(
            f"attachment request {request_id!r} is not retained by this control authority"
        )
    operation = _as_operation(stored)
    channel.attachments.move_to_end(request_id)
    if reconcile:
        await _advance_from_timeline(service, entry, operation)
    return _projection(operation)


async def rebind(
    request: ControlRequest,
    *,
    recovery_asset_ref: str,
    request_id: str,
) -> StageAnswer:
    """Exchange one authorized recovery asset for a new one-use staged asset."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    require_safe_component(request_id, label="requestId")
    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    payload = decode_ref(
        service.secret,
        recovery_asset_ref,
        "recovery-asset-ref",
        RefBinding(authorization, ar_session_id, epoch),
    )
    identity = ref_identity(payload)
    withdraw_request_id = payload.get("withdrawRequestId")
    asset_id = payload.get("assetId")
    if not isinstance(withdraw_request_id, str) or not isinstance(asset_id, str):
        raise OperationRejectedError("recovery asset reference is incomplete")
    channel = service.channel(ar_session_id, epoch)
    _sweep_expired(service, channel)
    source_operation = _recoverable_operation(channel, identity.operation_id)
    asset = next((item for item in source_operation.assets if item.asset_id == asset_id), None)
    if asset is None:
        raise OperationNotFoundError("the recoverable asset is not retained")
    rebound = source_operation.rebound.get(recovery_asset_ref)
    if rebound is not None:
        return _rebind_replay(channel, ar_session_id, request_id, rebound)
    if asset.consumed:
        raise OperationConflictError("the recovery asset was already exchanged")
    verify_recoverable_bytes(asset)
    assets_root = service.spool_assets_root(entry)
    target_operation = _rebind_target(
        ControlScope(service, authorization, ar_session_id, epoch),
        channel,
        request_id,
        source_operation,
        asset,
    )
    new_record = exchange_bytes(
        assets_root, request_id, asset, position=len(target_operation.assets) + 1
    )
    asset.consumed = True
    source_operation.rebound[recovery_asset_ref] = (request_id, new_record.asset_id)
    source_operation.revision += 1
    target_operation.assets.append(new_record)
    target_operation.revision += 1
    return StageAnswer(
        operation=_projection(target_operation),
        receipts=(_receipt(ar_session_id, new_record, target_operation),),
    )


def _rebind_replay(
    channel: ControlChannel,
    ar_session_id: str,
    request_id: str,
    rebound: tuple[str, str],
) -> StageAnswer:
    """The idempotent replay of an already-executed exchange."""

    target_request_id, rebound_id = rebound
    if target_request_id != request_id:
        raise OperationConflictError("the recovery asset was already exchanged")
    stored = channel.attachments.get(request_id)
    if stored is None:
        raise OperationNotFoundError("the rebind target request is not retained")
    target = _as_operation(stored)
    replay = next((item for item in target.assets if item.asset_id == rebound_id), None)
    if replay is None:
        raise OperationNotFoundError("the rebound asset is not retained")
    return StageAnswer(
        operation=_projection(target),
        receipts=(_receipt(ar_session_id, replay, target),),
    )


def mark_recoverable(
    channel: ControlChannel,
    request_id: str,
    *,
    recovery_expires_at: str,
) -> tuple[AssetRecord, ...]:
    """Transition a withdrawn operation's assets to recoverable under the lease."""

    stored = channel.attachments.get(request_id)
    if stored is None:
        return ()
    operation = _as_operation(stored)
    if operation.phase in {"staged", "queued", "dispatching", "unknown"}:
        operation.phase = "recoverable"
        operation.outcome = "withdrawn"
        operation.recovery_expires_at = recovery_expires_at
        operation.revision += 1
        # The submit's one-use consumption ended with the withdrawal; the
        # assets are recoverable now and rebind mints their next one-use identity.
        for asset in operation.assets:
            asset.consumed = False
    return tuple(asset for asset in operation.assets if not asset.consumed)


def delete_recoverable(channel: ControlChannel, request_id: str) -> None:
    """Delete recoverable staged bytes (ack keep-current-draft / lease expiry)."""

    stored = channel.attachments.get(request_id)
    if stored is None:
        return
    operation = _as_operation(stored)
    if operation.phase != "recoverable":
        return
    delete_asset_bytes(operation.assets)
    operation.phase = "expired"
    operation.outcome = "expired"
    operation.recovery_expires_at = None
    operation.revision += 1


def _compose(
    channel: ControlChannel, body: ConversationSubmitRequest
) -> tuple[str, AttachmentOperation | None, list[AssetRecord]]:
    texts: list[str] = []
    records: list[AssetRecord] = []
    operation = _staged_operation(channel, body.request_id)
    for block in body.content:
        if block.type == "text":
            texts.append(block.text)
            continue
        records.append(_consume_block(operation, block))
    text = "\n\n".join(part for part in texts if part)
    if not text:
        raise OperationRejectedError("submit requires non-empty text content")
    if operation is not None and operation.phase != "staged" and not records:
        raise OperationRejectedError("the staged request already left the staging phase")
    return text, operation, records


def _consume_block(
    operation: AttachmentOperation | None, block: ComposerSubmitBlock
) -> AssetRecord:
    if operation is None:
        raise OperationRejectedError("asset-ref submit requires its staged attachment operation")
    assert isinstance(block, AssetSubmitBlock)
    record = next((item for item in operation.assets if item.asset_id == block.asset_id), None)
    if record is None:
        raise OperationNotFoundError(f"staged asset {block.asset_id!r} is not retained")
    if record.consumed:
        raise OperationConflictError(f"staged asset {block.asset_id!r} was already consumed")
    _require_receipt_match(block, record)
    return record


def _require_receipt_match(block: ComposerSubmitBlock, record: AssetRecord) -> None:
    """An asset-ref block must name its staged receipt exactly, never a tampered copy."""

    assert isinstance(block, AssetSubmitBlock)
    if (
        block.kind != record.kind
        or block.name != record.name
        or block.mime_type != record.mime_type
        or block.sha256 != record.sha256
        or block.alt != record.alt
        or block.alt_provenance != record.alt_provenance
    ):
        raise OperationRejectedError("asset-ref block does not match its staged receipt")


def _staged_operation(channel: ControlChannel, request_id: str) -> AttachmentOperation | None:
    stored = channel.attachments.get(request_id)
    return _as_operation(stored) if stored is not None else None


def _recoverable_operation(channel: ControlChannel, request_id: str) -> AttachmentOperation:
    stored = channel.attachments.get(request_id)
    if stored is None:
        raise OperationNotFoundError("the recoverable attachment operation is not retained")
    operation = _as_operation(stored)
    if operation.phase != "recoverable":
        raise OperationRejectedError("the recoverable attachment operation is not recoverable")
    return operation


async def _admit(
    epoch: str,
    content: SubmittedContent,
    operation: AttachmentOperation | None,
) -> SubmitAnswer:
    body, receipt = content.body, content.receipt
    if operation is not None:
        if receipt.acceptance in _TERMINAL_SUBMIT_ACCEPTANCES:
            operation.phase = "failed"
            operation.outcome = "rejected"
        elif receipt.acceptance == "unknown":
            operation.phase = "unknown"
            operation.outcome = "unknown"
        else:
            operation.phase = "queued" if receipt.acceptance == "queued" else "dispatching"
            operation.outcome = "pending"
        attachment = _projection(operation)
    else:
        attachment = None
    return SubmitAnswer(
        request_id=body.request_id,
        acceptance=receipt.acceptance,
        submitted_at=receipt.submitted_at,
        bridge_epoch=receipt.bridge_epoch or epoch,
        operation_ref=None,
        attachment=attachment,
        detail=receipt.detail,
    )


async def _mint_queue_ref(
    scope: ControlScope,
    entry: TerminalCatalogEntry,
    request_id: str,
) -> str | None:
    service, authorization = scope.service, scope.authorization
    ar_session_id, epoch = scope.ar_session_id, scope.epoch
    items = await service.read_full_timeline(entry, expected_bridge_epoch=epoch)
    row = next(
        (item for item in items if item.kind == "prompt" and item.operation_id == request_id),
        None,
    )
    if row is None:
        return None
    return mint_ref(
        service.secret,
        "operation-ref",
        RefBinding(authorization, ar_session_id, epoch),
        RefTarget(
            identity=OperationIdentity(
                kind=row.kind, operation_id=row.operation_id, sequence=row.sequence
            )
        ),
    )


async def _advance_from_timeline(
    service: ConversationControlService,
    entry: TerminalCatalogEntry,
    operation: AttachmentOperation,
) -> None:
    if operation.phase in {"accepted", "expired", "recoverable"}:
        return
    items = await service.read_full_timeline(entry, expected_bridge_epoch=operation.bridge_epoch)
    row = next(
        (
            item
            for item in items
            if item.kind == "prompt" and item.operation_id == operation.request_id
        ),
        None,
    )
    transition = _timeline_transition(operation, row)
    if transition is None:
        return
    phase, outcome = transition
    operation.phase = phase
    operation.outcome = outcome
    operation.revision += 1
    if phase == "accepted":
        delete_asset_bytes(operation.assets)


def _timeline_transition(
    operation: AttachmentOperation, row: OperationTimelineItem | None
) -> tuple[AttachmentPhase, AttachmentOutcome] | None:
    """Map the retained row's state to the operation's next lifecycle step."""

    if row is not None and row.state in _LIVE_TIMELINE_STATES:
        if row.state == "dispatching" and operation.phase == "queued":
            return "dispatching", "pending"
        return None
    if row is not None and row.state == "withdrawn":
        return None
    if row is not None and row.state in {"rejected", "unsupported"}:
        return "failed", "rejected"
    # Absent or delivered: the retained ledger only evicts terminal records;
    # native acceptance is proven, so staged bytes are deleted (never while unknown).
    if operation.phase in {"queued", "dispatching", "accepted"}:
        return "accepted", "accepted"
    return None


def _rebind_target(
    scope: ControlScope,
    channel: ControlChannel,
    request_id: str,
    source: AttachmentOperation,
    asset: AssetRecord,
) -> AttachmentOperation:
    service, authorization = scope.service, scope.authorization
    ar_session_id, epoch = scope.ar_session_id, scope.epoch
    existing = channel.attachments.get(request_id)
    if existing is not None:
        target = _as_operation(existing)
        if target.phase != "staged":
            raise OperationConflictError("the rebind target request already left staging")
        return target
    if len(channel.attachments) >= MAX_ATTACHMENT_OPS_PER_CHANNEL:
        _evict_attachment_operation(channel, request_id)
    now = service.clock()
    fingerprint = operation_fingerprint(
        "attachment-stage",
        authorization,
        {
            "arSessionId": ar_session_id,
            "bridgeEpoch": epoch,
            "requestId": request_id,
            "assets": [upload_identity_from_record(asset)],
            "rebindOf": source.request_id,
        },
    )
    target = AttachmentOperation(
        request_id=request_id,
        fingerprint=fingerprint,
        revision=0,
        bridge_epoch=epoch,
        phase="staged",
        outcome="pending",
        assets=[],
        expires_at=iso_seconds_after(now, STAGED_ASSET_TTL_SECONDS),
    )
    channel.attachments[request_id] = target
    return target


def _sweep_expired(service: ConversationControlService, channel: ControlChannel) -> None:
    now = service.clock()
    for stored in channel.attachments.values():
        operation = _as_operation(stored)
        if operation.phase == "staged" and iso_expired(operation.expires_at, now=now):
            operation.phase = "expired"
            operation.outcome = "expired"
            operation.revision += 1
            delete_asset_bytes(operation.assets)


def _evict_attachment_operation(channel: ControlChannel, incoming: str) -> None:
    victim = next(
        (
            key
            for key, value in channel.attachments.items()
            if key != incoming and _as_operation(value).phase in {"accepted", "failed", "expired"}
        ),
        None,
    )
    if victim is None:
        raise OperationRejectedError("attachment operation store is full of live operations")
    operation = _as_operation(channel.attachments.pop(victim))
    delete_asset_bytes(operation.assets)


def _delete_operation_bytes(operation: AttachmentOperation) -> None:
    root = operation.assets[0].spool_path.parent if operation.assets else None
    for asset in operation.assets:
        asset.spool_path.unlink(missing_ok=True)
    if root is not None and root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def _receipt(
    ar_session_id: str, asset: AssetRecord, operation: AttachmentOperation
) -> AttachmentReceipt:
    return AttachmentReceipt(
        asset_id=asset.asset_id,
        request_id=operation.request_id,
        request_fingerprint=operation.fingerprint,
        revision=operation.revision,
        ar_session_id=ar_session_id,
        bridge_epoch=operation.bridge_epoch,
        kind=asset.kind,
        name=asset.name,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        alt=asset.alt,
        alt_provenance=asset.alt_provenance,
        expires_at=operation.expires_at,
    )


def _projection(operation: AttachmentOperation) -> AttachmentOperationProjection:
    return AttachmentOperationProjection(
        request_id=operation.request_id,
        request_fingerprint=operation.fingerprint,
        operation_ref=operation.operation_ref,
        revision=operation.revision,
        phase=operation.phase,
        outcome=operation.outcome,
        asset_ids=tuple(asset.asset_id for asset in operation.assets),
        recovery_expires_at=operation.recovery_expires_at,
    )


def _as_operation(stored: object) -> AttachmentOperation:
    assert isinstance(stored, AttachmentOperation)
    return stored


__all__ = [
    "AssetRecord",
    "AttachmentOperation",
    "StageAnswer",
    "StagedUpload",
    "SubmitAnswer",
    "attachment_status",
    "delete_recoverable",
    "mark_recoverable",
    "rebind",
    "stage",
    "submit",
]
