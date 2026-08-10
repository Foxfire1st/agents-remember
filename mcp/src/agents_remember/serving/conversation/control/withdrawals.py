"""Authoritative cockpit-only withdrawal and bounded recovery (260718-CHATS-L3, R3).

The authorized withdrawalRef/operationRef pair is verified against the live
queue row; the L2E substrate then linearizes the queued-to-dispatching race
with exactly one winner, ``cockpit_only`` untouched. A successful withdrawal
retains the exact body — the substrate's pre-tombstone recovery payload,
cross-checked against this authority's own submit journal — in a bounded,
authorization-bound, expiring recovery record. Fresh tabs discover only opaque
pending-recovery identities; the exact text and asset exchange refs cross only
through an authenticated fetch while the record is unacknowledged; status and
reconcile never carry recovery bodies; acknowledgement permits raw-content
disposal; lease expiry deletes recoverable bytes. Lost responses reconcile
through the same withdrawRequestId and never mint a replacement; the substrate
replay is idempotent and carries no recovery, so the journal is the recovery
source of last resort (without it the recovery is honestly empty, never
fabricated).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlClientError,
    HarnessControlError,
)
from agents_remember.models.conversations.control_wire import (
    OperationTimelineItem,
    WithdrawalResult,
)
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
)
from agents_remember.models.conversations.primitives import (
    OperationFingerprint,
)
from agents_remember.models.conversations.telemetry import (
    operation_fingerprint,
)
from agents_remember.models.conversations.withdrawals import (
    AttachmentRecoveryRef,
    FailedWithdrawalResponse,
    PendingWithdrawalRecoveryList,
    PendingWithdrawalRecoveryProjection,
    WithdrawalOperationProjection,
    WithdrawalRecovery,
    WithdrawnQueueResponse,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.conversation.control import attachments, recovery_assembly
from agents_remember.serving.conversation.control.refs import (
    OperationIdentity,
    RefBinding,
    RefTarget,
    decode_ref,
    mint_ref,
    ref_identity,
)
from agents_remember.serving.conversation.control.service import (
    MAX_RECOVERIES_PER_CHANNEL,
    MAX_WITHDRAWALS_PER_CHANNEL,
    RECOVERY_TTL_SECONDS,
    ControlChannel,
    ControlRequest,
    ControlScope,
    ControlUnavailableError,
    ConversationControlService,
    OperationConflictError,
    OperationNotFoundError,
    iso_expired,
    iso_seconds_after,
)

WithdrawalPhase = Literal["requested", "linearizing", "settled", "unknown"]
WithdrawalOutcome = Literal[
    "pending",
    "withdrawn",
    "already-dispatching",
    "delivery-unknown",
    "epoch-mismatch",
    "not-found",
    "request-conflict",
]
RecoveryState = Literal["none", "recovery-unacknowledged", "acknowledged", "expired"]
FailedOutcome = Literal[
    "already-dispatching", "delivery-unknown", "epoch-mismatch", "not-found", "request-conflict"
]

_LIVE_ROW_STATES = {"queued", "dispatching", "unknown"}


@dataclass
class RecoveryRecord:
    recovery_ref: str
    withdraw_request_id: str
    operation: OperationIdentity
    revision: int
    state: RecoveryState
    text: str | None
    content_digest: str
    submitted_draft_revision: int | None
    attachment_refs: tuple[AttachmentRecoveryRef, ...]
    expires_at: str


@dataclass
class WithdrawalRecord:
    withdraw_request_id: str
    fingerprint: OperationFingerprint
    revision: int
    bridge_epoch: str
    operation: OperationIdentity
    phase: WithdrawalPhase
    outcome: WithdrawalOutcome
    detail: str | None
    withdrawn_at: str | None
    recovery_state: RecoveryState
    recovery_ref: str | None
    recovery_expires_at: str | None
    response: WithdrawnQueueResponse | FailedWithdrawalResponse


async def withdraw(
    request: ControlRequest,
    *,
    operation_ref: str,
    withdrawal_ref: str,
    withdraw_request_id: str,
) -> WithdrawnQueueResponse | FailedWithdrawalResponse:
    """One authorized atomic cockpit-only withdrawal with bounded recovery."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    scope = request.resolved(epoch)
    identity = _verify_refs(scope, operation_ref, withdrawal_ref)
    channel = service.channel(ar_session_id, epoch)
    sweep_recoveries(service, channel)
    fingerprint = operation_fingerprint(
        "withdraw",
        authorization,
        {
            "arSessionId": ar_session_id,
            "bridgeEpoch": epoch,
            "kind": identity.kind,
            "operationId": identity.operation_id,
            "sequence": identity.sequence,
            "withdrawRequestId": withdraw_request_id,
        },
    )
    existing = channel.withdrawals.get(withdraw_request_id)
    if existing is not None:
        record = _as_withdrawal(existing)
        if record.fingerprint != fingerprint:
            raise OperationConflictError(
                f"withdraw request id {withdraw_request_id!r} already belongs to its first tuple"
            )
        channel.withdrawals.move_to_end(withdraw_request_id)
        return _replay_response(record)
    async with service.session_lock(ar_session_id):
        recheck = channel.withdrawals.get(withdraw_request_id)
        if recheck is not None:
            record = _as_withdrawal(recheck)
            if record.fingerprint != fingerprint:
                raise OperationConflictError(
                    f"withdraw request id {withdraw_request_id!r} already belongs to its first tuple"
                )
            return _replay_response(record)
        record = await _drive_withdrawal(
            scope,
            entry,
            channel,
            WithdrawalTicket(
                epoch=epoch,
                identity=identity,
                operation_ref=operation_ref,
                fingerprint=fingerprint,
                withdraw_request_id=withdraw_request_id,
            ),
        )
        _store_withdrawal(channel, record)
        return record.response


async def withdraw_status(
    request: ControlRequest,
    *,
    operation_ref: str,
    withdraw_request_id: str,
    reconcile: bool,
) -> WithdrawalOperationProjection:
    """Read (and, for reconcile, actively re-drive) one withdrawal operation."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    sweep_recoveries(service, channel)
    stored = channel.withdrawals.get(withdraw_request_id)
    if stored is None:
        raise OperationNotFoundError(
            f"withdrawal {withdraw_request_id!r} is not retained by this control authority"
        )
    record = _as_withdrawal(stored)
    payload = decode_ref(
        service.secret,
        operation_ref,
        "operation-ref",
        RefBinding(authorization, ar_session_id, epoch),
    )
    if ref_identity(payload) != record.operation:
        raise OperationConflictError("operation ref does not name this withdrawal's operation")
    channel.withdrawals.move_to_end(withdraw_request_id)
    if reconcile and record.phase == "unknown":
        async with service.session_lock(ar_session_id):
            await _redrive_withdrawal(service, authorization, entry, channel, record)
    return _withdrawal_projection(record)


async def pending_recoveries(
    request: ControlRequest,
) -> PendingWithdrawalRecoveryList:
    """The opaque pending-recovery discovery list; never content of any kind."""

    service = request.service
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    sweep_recoveries(service, channel)
    items: list[PendingWithdrawalRecoveryProjection] = []
    key_parts: list[str] = []
    for record in channel.recoveries.values():
        recovery = _as_recovery(record)
        if recovery.state != "recovery-unacknowledged":
            continue
        items.append(
            PendingWithdrawalRecoveryProjection(
                recovery_ref=recovery.recovery_ref,
                operation_ref=_mint_operation_ref(request.resolved(epoch), recovery.operation),
                withdraw_request_id=recovery.withdraw_request_id,
                revision=recovery.revision,
                recovery_expires_at=recovery.expires_at,
            )
        )
        key_parts.append(f"{recovery.recovery_ref}|{recovery.revision}")
    pending_key = ";".join(key_parts)
    if pending_key != channel.pending_key:
        channel.pending_key = pending_key
        channel.pending_revision += 1
    return PendingWithdrawalRecoveryList(
        bridge_epoch=epoch,
        revision=max(1, channel.pending_revision),
        items=tuple(items),
    )


async def fetch_recovery(
    request: ControlRequest,
    *,
    recovery_ref: str,
) -> WithdrawalRecovery:
    """The authenticated exact recovery fetch; only while unacknowledged."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    sweep_recoveries(service, channel)
    decode_ref(
        service.secret,
        recovery_ref,
        "recovery-ref",
        RefBinding(authorization, ar_session_id, epoch),
    )
    recovery = _find_recovery(channel, recovery_ref)
    if recovery.state != "recovery-unacknowledged" or recovery.text is None:
        raise OperationNotFoundError("the withdrawal recovery is not retained for fetching")
    return WithdrawalRecovery(
        recovery_ref=recovery.recovery_ref,
        text=recovery.text,
        content_digest=recovery.content_digest,
        submitted_draft_revision=recovery.submitted_draft_revision,
        attachments=recovery.attachment_refs,
    )


async def acknowledge_recovery(
    request: ControlRequest,
    *,
    recovery_ref: str,
    disposition: str,
) -> WithdrawalOperationProjection:
    """Record the draft disposition, advance the revision, and permit disposal."""

    service = request.service
    authorization = request.authorization
    ar_session_id = request.ar_session_id
    expected_bridge_epoch = request.expected_bridge_epoch

    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    sweep_recoveries(service, channel)
    decode_ref(
        service.secret,
        recovery_ref,
        "recovery-ref",
        RefBinding(authorization, ar_session_id, epoch),
    )
    recovery = _find_recovery(channel, recovery_ref)
    if recovery.state != "recovery-unacknowledged":
        raise OperationNotFoundError("the withdrawal recovery is not awaiting acknowledgement")
    recovery.state = "acknowledged"
    recovery.revision += 1
    recovery.text = None
    withdrawal = channel.withdrawals.get(recovery.withdraw_request_id)
    if withdrawal is None:
        raise OperationNotFoundError("the recovery's withdrawal operation is not retained")
    record = _as_withdrawal(withdrawal)
    record.recovery_state = "acknowledged"
    record.revision += 1
    if disposition == "keep-current-draft":
        attachments.delete_recoverable(channel, record.operation.operation_id)
    return _withdrawal_projection(record)


@dataclass(frozen=True)
class WithdrawalTicket:
    """The exact operation being withdrawn, and the request that is withdrawing it.

    Every record this module builds -- settled, failed or unknown -- is stamped with the same five
    facts: the epoch the withdrawal is bound to, the operation identity, the caller's opaque
    operation ref, the fingerprint that makes the request idempotent, and its withdraw request id.
    Passing them as one ticket is what keeps a failure record from being stamped with a different
    operation's identity than the attempt it describes.
    """

    epoch: str
    identity: OperationIdentity
    operation_ref: str
    fingerprint: OperationFingerprint
    withdraw_request_id: str


async def _drive_withdrawal(
    scope: ControlScope,
    entry: TerminalCatalogEntry,
    channel: ControlChannel,
    ticket: WithdrawalTicket,
) -> WithdrawalRecord:
    service, epoch, identity = scope.service, ticket.epoch, ticket.identity
    row = await _live_row(service, entry, epoch, identity)
    if row is None or row.source != "cockpit":
        return _settled_failure(
            ticket,
            outcome="not-found",
            detail="the operation is not a retained cockpit queue row",
        )
    if row.state != "queued":
        outcome = "already-dispatching" if row.state == "dispatching" else "delivery-unknown"
        return _settled_failure(
            ticket,
            outcome=outcome,
            detail=f"the queue row is already {row.state}",
        )
    try:
        result = await asyncio.to_thread(
            service.control_plane.withdraw_submission,
            entry,
            expected_bridge_epoch=epoch,
            request_id=identity.operation_id,
        )
    except HarnessBridgeEpochMismatchError:
        return _settled_failure(
            ticket,
            outcome="epoch-mismatch",
            detail="the bridge epoch flipped while the withdrawal crossed",
        )
    except HarnessControlClientError as exc:
        if not exc.may_have_sent:
            raise ControlUnavailableError(str(exc)) from exc
        return _unknown_withdrawal(ticket, str(exc))
    except HarnessControlError as exc:
        raise ControlUnavailableError(str(exc)) from exc
    return _apply_withdrawal_result(scope, channel, ticket, result)


def _apply_withdrawal_result(
    scope: ControlScope,
    channel: ControlChannel,
    ticket: WithdrawalTicket,
    result: WithdrawalResult,
) -> WithdrawalRecord:
    failure = _failure_for_result(ticket, result)
    if failure is not None:
        return failure
    # outcome == "withdrawn": the true transition, or the substrate's idempotent
    # replay of one (a replay carries no recovery payload — the journal is then
    # the only content source, and without it the recovery is honestly empty).
    return _build_withdrawn_record(scope, channel, ticket, result)


def _failure_for_result(
    ticket: WithdrawalTicket,
    result: WithdrawalResult,
) -> WithdrawalRecord | None:
    """Map any non-withdrawn substrate outcome to its failure record, or None."""

    if result.outcome == "not-found":
        return _settled_failure(
            ticket,
            outcome="not-found",
            detail=result.detail or "submission is not retained for this cockpit authority",
        )
    if result.outcome != "not-withdrawable":
        return None
    outcome: FailedOutcome = "not-found"
    if result.state == "dispatching":
        outcome = "already-dispatching"
    elif result.state == "unknown":
        outcome = "delivery-unknown"
    return _settled_failure(
        ticket,
        outcome=outcome,
        detail=result.detail or f"submission is already {result.state}",
    )


def _build_withdrawn_record(
    scope: ControlScope,
    channel: ControlChannel,
    ticket: WithdrawalTicket,
    result: WithdrawalResult,
) -> WithdrawalRecord:
    service = scope.service
    epoch, identity = ticket.epoch, ticket.identity
    fingerprint, withdraw_request_id = ticket.fingerprint, ticket.withdraw_request_id
    operation_ref = ticket.operation_ref
    recovery = result.recovery
    journal = channel.journal.get(identity.operation_id)
    text = recovery_assembly.recovery_text(recovery, journal)
    substrate_assets = recovery.assets if recovery is not None else ()
    digest = recovery_assembly.recovery_digest(text, substrate_assets, journal)
    withdrawn_at = result.withdrawn_at or service.clock()
    expires_at = iso_seconds_after(withdrawn_at, RECOVERY_TTL_SECONDS)
    recovery_ref = mint_ref(
        service.secret,
        "recovery-ref",
        RefBinding(scope.authorization, scope.ar_session_id, epoch),
        RefTarget(identity=identity, withdraw_request_id=withdraw_request_id),
    )
    attachment_refs = recovery_assembly.recover_attachment_refs(
        scope, channel, identity, withdraw_request_id, expires_at
    )
    recovery_state: RecoveryState = (
        "recovery-unacknowledged" if (text is not None or attachment_refs) else "none"
    )
    record = WithdrawalRecord(
        withdraw_request_id=withdraw_request_id,
        fingerprint=fingerprint,
        revision=1,
        bridge_epoch=epoch,
        operation=identity,
        phase="settled",
        outcome="withdrawn",
        detail=result.detail,
        withdrawn_at=withdrawn_at,
        recovery_state=recovery_state,
        recovery_ref=recovery_ref if recovery_state != "none" else None,
        recovery_expires_at=expires_at if recovery_state != "none" else None,
        response=WithdrawnQueueResponse(
            withdraw_request_id=withdraw_request_id,
            request_fingerprint=fingerprint,
            revision=1,
            operation_ref=operation_ref,
            withdrawn_at=withdrawn_at,
            recovery=recovery_assembly.recovery_payload(
                recovery_ref, text, digest, journal, attachment_refs
            ),
        ),
    )
    if recovery_state == "recovery-unacknowledged":
        _store_recovery(
            channel,
            RecoveryRecord(
                recovery_ref=recovery_ref,
                withdraw_request_id=withdraw_request_id,
                operation=identity,
                revision=1,
                state="recovery-unacknowledged",
                text=text,
                content_digest=digest,
                submitted_draft_revision=journal.draft_revision if journal is not None else None,
                attachment_refs=attachment_refs,
                expires_at=expires_at,
            ),
        )
    return record


def _settled_failure(
    ticket: WithdrawalTicket,
    *,
    outcome: FailedOutcome,
    detail: str,
) -> WithdrawalRecord:
    epoch, identity = ticket.epoch, ticket.identity
    fingerprint, withdraw_request_id = ticket.fingerprint, ticket.withdraw_request_id
    operation_ref = ticket.operation_ref
    response = FailedWithdrawalResponse(
        withdraw_request_id=withdraw_request_id,
        request_fingerprint=fingerprint,
        revision=1,
        outcome=outcome,
        operation_ref=operation_ref,
        detail=detail,
    )
    return WithdrawalRecord(
        withdraw_request_id=withdraw_request_id,
        fingerprint=fingerprint,
        revision=1,
        bridge_epoch=epoch,
        operation=identity,
        phase="settled",
        outcome=outcome,
        detail=detail,
        withdrawn_at=None,
        recovery_state="none",
        recovery_ref=None,
        recovery_expires_at=None,
        response=response,
    )


def _unknown_withdrawal(ticket: WithdrawalTicket, detail: str) -> WithdrawalRecord:
    epoch, identity = ticket.epoch, ticket.identity
    fingerprint, withdraw_request_id = ticket.fingerprint, ticket.withdraw_request_id
    operation_ref = ticket.operation_ref
    response = FailedWithdrawalResponse(
        withdraw_request_id=withdraw_request_id,
        request_fingerprint=fingerprint,
        revision=1,
        outcome="delivery-unknown",
        operation_ref=operation_ref,
        detail=f"withdrawal response was lost after request bytes were sent: {detail}",
    )
    return WithdrawalRecord(
        withdraw_request_id=withdraw_request_id,
        fingerprint=fingerprint,
        revision=1,
        bridge_epoch=epoch,
        operation=identity,
        phase="unknown",
        outcome="delivery-unknown",
        detail=detail,
        withdrawn_at=None,
        recovery_state="none",
        recovery_ref=None,
        recovery_expires_at=None,
        response=response,
    )


async def _redrive_withdrawal(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    entry: TerminalCatalogEntry,
    channel: ControlChannel,
    record: WithdrawalRecord,
) -> None:
    """Reconcile a lost withdrawal response; the substrate replay is idempotent."""

    try:
        result = await asyncio.to_thread(
            service.control_plane.withdraw_submission,
            entry,
            expected_bridge_epoch=record.bridge_epoch,
            request_id=record.operation.operation_id,
        )
    except HarnessBridgeEpochMismatchError:
        raise
    except HarnessControlClientError as exc:
        if not exc.may_have_sent:
            raise ControlUnavailableError(str(exc)) from exc
        return
    except HarnessControlError as exc:
        record.detail = str(exc)
        return
    scope = ControlScope(
        service=service,
        authorization=authorization,
        ar_session_id=entry.id,
        epoch=record.bridge_epoch,
    )
    fresh = _apply_withdrawal_result(
        scope,
        channel,
        WithdrawalTicket(
            epoch=record.bridge_epoch,
            identity=record.operation,
            operation_ref=_mint_operation_ref(scope, record.operation),
            fingerprint=record.fingerprint,
            withdraw_request_id=record.withdraw_request_id,
        ),
        result,
    )
    record.phase = fresh.phase
    record.outcome = fresh.outcome
    record.detail = fresh.detail
    record.withdrawn_at = fresh.withdrawn_at
    record.recovery_state = fresh.recovery_state
    record.recovery_ref = fresh.recovery_ref
    record.recovery_expires_at = fresh.recovery_expires_at
    record.response = fresh.response
    record.revision += 1


async def _live_row(
    service: ConversationControlService,
    entry: TerminalCatalogEntry,
    epoch: str,
    identity: OperationIdentity,
) -> OperationTimelineItem | None:
    items = await service.read_full_timeline(entry, expected_bridge_epoch=epoch)
    return next(
        (
            item
            for item in items
            if item.kind == identity.kind
            and item.operation_id == identity.operation_id
            and item.sequence == identity.sequence
            and item.state in _LIVE_ROW_STATES
        ),
        None,
    )


def sweep_recoveries(service: ConversationControlService, channel: ControlChannel) -> None:
    """Expire unacknowledged recoveries past their lease and reclaim content (D3)."""

    now = service.clock()
    for stored in channel.recoveries.values():
        recovery = _as_recovery(stored)
        if recovery.state == "recovery-unacknowledged" and iso_expired(
            recovery.expires_at, now=now
        ):
            recovery.state = "expired"
            recovery.revision += 1
            recovery.text = None
            attachments.delete_recoverable(channel, recovery.operation.operation_id)
            withdrawal = channel.withdrawals.get(recovery.withdraw_request_id)
            if withdrawal is not None:
                record = _as_withdrawal(withdrawal)
                if record.recovery_state == "recovery-unacknowledged":
                    record.recovery_state = "expired"
                    record.revision += 1


def _store_withdrawal(channel: ControlChannel, record: WithdrawalRecord) -> None:
    channel.withdrawals[record.withdraw_request_id] = record
    while len(channel.withdrawals) > MAX_WITHDRAWALS_PER_CHANNEL:
        victim = next(
            (
                key
                for key, value in channel.withdrawals.items()
                if _as_withdrawal(value).recovery_state in {"none", "acknowledged", "expired"}
            ),
            None,
        )
        channel.withdrawals.pop(victim if victim is not None else next(iter(channel.withdrawals)))


def _store_recovery(channel: ControlChannel, record: RecoveryRecord) -> None:
    channel.recoveries[record.withdraw_request_id] = record
    while len(channel.recoveries) > MAX_RECOVERIES_PER_CHANNEL:
        # Reclaim under pressure: expire the oldest unacknowledged lease with
        # full disposal rather than failing the completed withdrawal (D1/D3).
        victim_key, victim_record = next(iter(channel.recoveries.items()))
        victim = _as_recovery(victim_record)
        if victim.state == "recovery-unacknowledged":
            victim.state = "expired"
            victim.text = None
            attachments.delete_recoverable(channel, victim.operation.operation_id)
        channel.recoveries.pop(victim_key)


def _find_recovery(channel: ControlChannel, recovery_ref: str) -> RecoveryRecord:
    for stored in channel.recoveries.values():
        recovery = _as_recovery(stored)
        if recovery.recovery_ref == recovery_ref:
            return recovery
    raise OperationNotFoundError("the withdrawal recovery is not retained")


def _replay_response(
    record: WithdrawalRecord,
) -> WithdrawnQueueResponse | FailedWithdrawalResponse:
    """Idempotent replay: same outcome/revision; bodies stay disposed post-ack."""

    response = record.response
    if isinstance(response, WithdrawnQueueResponse) and record.recovery_state in {
        "acknowledged",
        "expired",
    }:
        return WithdrawnQueueResponse(
            withdraw_request_id=response.withdraw_request_id,
            request_fingerprint=response.request_fingerprint,
            revision=response.revision,
            operation_ref=response.operation_ref,
            withdrawn_at=response.withdrawn_at,
            recovery=WithdrawalRecovery(
                recovery_ref=response.recovery.recovery_ref,
                text="",
                content_digest=response.recovery.content_digest,
                submitted_draft_revision=response.recovery.submitted_draft_revision,
                attachments=(),
            ),
        )
    return response


def _withdrawal_projection(record: WithdrawalRecord) -> WithdrawalOperationProjection:
    return WithdrawalOperationProjection(
        withdraw_request_id=record.withdraw_request_id,
        request_fingerprint=record.fingerprint,
        operation_ref=record.response.operation_ref,
        revision=record.revision,
        phase=record.phase,
        outcome=record.outcome,
        recovery_state=record.recovery_state,
        recovery_expires_at=record.recovery_expires_at,
    )


def _mint_operation_ref(scope: ControlScope, identity: OperationIdentity) -> str:
    return mint_ref(
        scope.service.secret,
        "operation-ref",
        RefBinding(scope.authorization, scope.ar_session_id, scope.epoch),
        RefTarget(identity=identity),
    )


def _verify_refs(
    scope: ControlScope,
    operation_ref: str,
    withdrawal_ref: str,
) -> OperationIdentity:
    service = scope.service
    binding = RefBinding(scope.authorization, scope.ar_session_id, scope.epoch)
    operation_payload = decode_ref(service.secret, operation_ref, "operation-ref", binding)
    withdrawal_payload = decode_ref(service.secret, withdrawal_ref, "withdrawal-ref", binding)
    operation_identity = ref_identity(operation_payload)
    if ref_identity(withdrawal_payload) != operation_identity:
        raise OperationConflictError("withdrawal ref does not name the operation ref's row")
    return operation_identity


def _as_withdrawal(stored: object) -> WithdrawalRecord:
    assert isinstance(stored, WithdrawalRecord)
    return stored


def _as_recovery(stored: object) -> RecoveryRecord:
    assert isinstance(stored, RecoveryRecord)
    return stored


def withdraw_http_status(response: WithdrawnQueueResponse | FailedWithdrawalResponse) -> int:
    if isinstance(response, WithdrawnQueueResponse):
        return 200
    return {
        "already-dispatching": 409,
        "delivery-unknown": 202,
        "epoch-mismatch": 409,
        "not-found": 404,
        "request-conflict": 409,
    }[response.outcome]


__all__ = [
    "acknowledge_recovery",
    "fetch_recovery",
    "pending_recoveries",
    "sweep_recoveries",
    "withdraw",
    "withdraw_http_status",
    "withdraw_status",
]
