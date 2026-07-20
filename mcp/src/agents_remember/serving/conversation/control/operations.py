"""Exact-turn interrupt operation ledger (260718-CHATS-L3, R1).

One idempotent interrupt authority per (session, epoch) channel, keyed by
authenticated caller + ``(bridgeEpoch, turnId, requestId)`` with an immutable
request fingerprint and a monotonic semantic revision. Acknowledgement is the
native interrupt write's answer (``requested -> accepted | unknown |
rejected``); settlement is the correlated terminal native event
(``pending -> interrupted | already-settled | failed``). Acknowledgement is
never settlement: an ``accepted`` interrupt settles only when the exact turn's
terminal evidence crosses the landed evidence/completion surface.

The L2E substrate owns the epoch-guarded native write and its replay-once
cache; this ledger serializes every same-session interrupt above that cache
(L2E precision note 4: the pair cache is not a concurrency lock). Replaying an
identical tuple returns the stored projection and causes no second native
write; reusing a request id with a different tuple is ``request-conflict``;
lost responses stay reconcilable through ``interrupt-status`` /
``interrupt-reconcile`` with the same id — the browser never mints a
replacement. For pi (no native turn identity) the caller's ``turnId`` names
the exact active AR operation id, which the substrate guards pre-write.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from agents_remember.errors import HarnessControlClientError, HarnessControlError
from agents_remember.serving.conversation.control.capabilities import (
    control_capabilities_for,
)
from agents_remember.serving.conversation.control.service import (
    MAX_INTERRUPTS_PER_CHANNEL,
    CapabilityRefusedError,
    ControlChannel,
    ControlUnavailableError,
    ConversationControlService,
    OperationConflictError,
    OperationNotFoundError,
)
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
    InterruptOperation,
    OperationFingerprint,
    operation_fingerprint,
)
from agents_remember.serving.harness_control_client import (
    interrupt_control,
    read_control_evidence,
)
from agents_remember.serving.harness_control_models import InterruptResult
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

Acknowledgement = Literal["requested", "accepted", "unknown", "rejected"]
Settlement = Literal["pending", "interrupted", "already-settled", "failed"]

_CODEX_TERMINAL_STATUSES = {"interrupted", "completed", "failed"}
_PI_ABORTED = "aborted"
_PI_ERROR = "error"


@dataclass
class InterruptRecord:
    request_id: str
    fingerprint: OperationFingerprint
    revision: int
    harness_id: str
    bridge_epoch: str
    turn_id: str
    acknowledgement: Acknowledgement
    settlement: Settlement
    native_correlation_id: str | None
    requested_at: str
    settled_at: str | None
    detail: str | None
    operation_id: str | None
    evidence_floor: int


@dataclass
class InterruptAnswer:
    operation: InterruptOperation


async def interrupt(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    ar_session_id: str,
    *,
    expected_bridge_epoch: str,
    turn_id: str,
    request_id: str,
) -> InterruptOperation:
    """Request one exact-turn interrupt; idempotent under the caller's tuple."""

    entry = service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    snapshot = await service.live_snapshot(entry)
    identity = service.build_identity(entry, bridge_epoch=epoch, snapshot=snapshot)
    capability = control_capabilities_for(identity.harness_id, snapshot).interrupt
    if capability.state != "supported":
        raise CapabilityRefusedError(capability.reason)
    channel = service.channel(ar_session_id, epoch)
    fingerprint = operation_fingerprint(
        "interrupt",
        authorization,
        {
            "arSessionId": ar_session_id,
            "bridgeEpoch": epoch,
            "turnId": turn_id,
            "requestId": request_id,
        },
    )
    existing = channel.interrupts.get(request_id)
    if existing is not None:
        record = _as_record(existing)
        if record.fingerprint != fingerprint:
            raise OperationConflictError(
                f"interrupt request id {request_id!r} already belongs to its first tuple"
            )
        channel.interrupts.move_to_end(request_id)
        return _projection(ar_session_id, epoch, record)
    async with service.session_lock(ar_session_id):
        recheck = channel.interrupts.get(request_id)
        if recheck is not None:
            record = _as_record(recheck)
            if record.fingerprint != fingerprint:
                raise OperationConflictError(
                    f"interrupt request id {request_id!r} already belongs to its first tuple"
                )
            return _projection(ar_session_id, epoch, record)
        record = await _drive_interrupt(
            service,
            entry,
            identity,
            fingerprint,
            turn_id=turn_id,
            request_id=request_id,
            evidence_floor=snapshot.last_event_sequence,
        )
        _store(channel, record)
        return _projection(ar_session_id, epoch, record)


async def interrupt_status(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    ar_session_id: str,
    *,
    expected_bridge_epoch: str,
    turn_id: str,
    request_id: str,
    reconcile: bool,
) -> InterruptOperation:
    """Read (and, for reconcile, actively re-drive) one interrupt operation."""

    entry = service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    stored = channel.interrupts.get(request_id)
    if stored is None:
        raise OperationNotFoundError(
            f"interrupt {request_id!r} is not retained by this control authority"
        )
    record = _as_record(stored)
    fingerprint = operation_fingerprint(
        "interrupt",
        authorization,
        {
            "arSessionId": ar_session_id,
            "bridgeEpoch": epoch,
            "turnId": turn_id,
            "requestId": request_id,
        },
    )
    if record.fingerprint != fingerprint:
        raise OperationConflictError(
            f"interrupt request id {request_id!r} names a different tuple"
        )
    channel.interrupts.move_to_end(request_id)
    if record.settlement == "pending":
        async with service.session_lock(ar_session_id):
            if reconcile and record.acknowledgement == "unknown":
                await _redrive_unknown(service, entry, record)
            if record.settlement == "pending":
                await _observe_settlement(service, entry, record)
    return _projection(ar_session_id, epoch, record)


async def _drive_interrupt(
    service: ConversationControlService,
    entry: TerminalCatalogEntry,
    identity: ActiveConversationRef,
    fingerprint: OperationFingerprint,
    *,
    turn_id: str,
    request_id: str,
    evidence_floor: int,
) -> InterruptRecord:
    now = service.clock()
    record = InterruptRecord(
        request_id=request_id,
        fingerprint=fingerprint,
        revision=1,
        harness_id=identity.harness_id,
        bridge_epoch=identity.bridge_epoch,
        turn_id=turn_id,
        acknowledgement="requested",
        settlement="pending",
        native_correlation_id=None,
        requested_at=now,
        settled_at=None,
        detail=None,
        operation_id=None,
        evidence_floor=evidence_floor,
    )
    turn_kwarg: dict[str, str | None] = (
        {"turn_id": turn_id, "expected_operation_id": None}
        if identity.harness_id == "codex"
        else {"turn_id": None, "expected_operation_id": turn_id}
    )
    try:
        result: InterruptResult = await asyncio.to_thread(
            interrupt_control,
            entry,
            expected_bridge_epoch=identity.bridge_epoch,
            **turn_kwarg,
        )
    except HarnessControlClientError as exc:
        if not exc.may_have_sent:
            raise ControlUnavailableError(str(exc)) from exc
        record.acknowledgement = "unknown"
        record.revision += 1
        record.detail = f"interrupt acknowledgement was lost after request bytes were sent: {exc}"
        return record
    except HarnessControlError as exc:
        record.acknowledgement = "rejected"
        record.settlement = "failed"
        record.settled_at = service.clock()
        record.revision += 1
        record.detail = str(exc)
        return record
    return _apply_interrupt_result(service, record, result)


def _apply_interrupt_result(
    service: ConversationControlService,
    record: InterruptRecord,
    result: InterruptResult,
) -> InterruptRecord:
    record.native_correlation_id = result.vendor_correlation_id
    if result.operation is not None:
        record.operation_id = result.operation.operation_id
    record.revision += 1
    if result.acknowledgement == "accepted":
        record.acknowledgement = "accepted"
        record.detail = result.detail
        return record
    if result.acknowledgement == "unknown":
        record.acknowledgement = "unknown"
        record.detail = result.detail
        return record
    # "rejected" and "unsupported" both settle failed with the native detail.
    record.acknowledgement = "rejected"
    record.settlement = "failed"
    record.settled_at = service.clock()
    record.detail = result.detail or "native interrupt was not accepted"
    return record


async def _redrive_unknown(
    service: ConversationControlService,
    entry: TerminalCatalogEntry,
    record: InterruptRecord,
) -> None:
    """Reconcile a lost acknowledgement; the substrate replays the first write."""

    turn_kwarg: dict[str, str | None] = (
        {"turn_id": record.turn_id, "expected_operation_id": None}
        if record.harness_id == "codex"
        else {"turn_id": None, "expected_operation_id": record.turn_id}
    )
    try:
        result = await asyncio.to_thread(
            interrupt_control,
            entry,
            expected_bridge_epoch=record.bridge_epoch,
            **turn_kwarg,
        )
    except HarnessControlClientError as exc:
        if exc.may_have_sent:
            return
        raise ControlUnavailableError(str(exc)) from exc
    except HarnessControlError as exc:
        # The pair is no longer live (turn moved on): the interrupt never
        # landed; settlement observation decides the honest terminal state.
        record.detail = str(exc)
        return
    _apply_interrupt_result(service, record, result)


async def _observe_settlement(
    service: ConversationControlService,
    entry: TerminalCatalogEntry,
    record: InterruptRecord,
) -> None:
    """Correlate the exact turn's terminal native event before settling."""

    outcome = await (
        _codex_terminal_outcome(service, entry, record)
        if record.harness_id == "codex"
        else _pi_terminal_outcome(service, entry, record)
    )
    if outcome is None:
        return
    settlement, detail = outcome
    record.settlement = settlement
    record.settled_at = service.clock()
    record.detail = detail
    record.revision += 1


async def _codex_terminal_outcome(
    service: ConversationControlService,  # noqa: ARG001 - clock/epoch owner kept for symmetry
    entry: TerminalCatalogEntry,
    record: InterruptRecord,
) -> tuple[Settlement, str] | None:
    after = record.evidence_floor
    while True:
        page = await asyncio.to_thread(
            read_control_evidence,
            entry,
            after_sequence=after,
            expected_bridge_epoch=record.bridge_epoch,
        )
        for frame in page.frames:
            if frame.kind != "completed":
                continue
            turn = frame.raw.get("turn")
            if not isinstance(turn, dict) or turn.get("id") != record.turn_id:
                continue
            status = turn.get("status")
            if status not in _CODEX_TERMINAL_STATUSES:
                continue
            if status == "interrupted":
                return "interrupted", "native turn/completed interrupted the exact turn"
            if status == "completed":
                return "already-settled", "the exact turn completed natively before interruption"
            return "failed", "native turn/completed failed the exact turn"
        if not page.truncated:
            return None
        after = page.frames[-1].sequence


async def _pi_terminal_outcome(
    service: ConversationControlService,
    entry: TerminalCatalogEntry,
    record: InterruptRecord,
) -> tuple[Settlement, str] | None:
    """Settle from the exact operation's settle plus its stopReason evidence."""

    items = await service.read_full_timeline(
        entry, expected_bridge_epoch=record.bridge_epoch
    )
    row = next(
        (item for item in items if item.kind == "prompt" and item.operation_id == record.operation_id),
        None,
    )
    if record.operation_id is None or row is None or row.state in {"queued", "dispatching", "unknown"}:
        return None
    stop_reason = await _pi_stop_reason(entry, record)
    if stop_reason is None:
        return None
    if stop_reason == _PI_ABORTED:
        return "interrupted", "native stopReason aborted settled the exact operation"
    if stop_reason == _PI_ERROR:
        return "failed", "native stopReason error failed the exact operation"
    return "already-settled", "the exact operation settled natively before the abort took effect"


async def _pi_stop_reason(
    entry: TerminalCatalogEntry,
    record: InterruptRecord,
) -> str | None:
    """The latest stopReason evidence after the interrupt's recorded floor."""

    after = record.evidence_floor
    stop_reason: str | None = None
    while True:
        page = await asyncio.to_thread(
            read_control_evidence,
            entry,
            after_sequence=after,
            expected_bridge_epoch=record.bridge_epoch,
        )
        for frame in page.frames:
            if frame.raw.get("type") != "message_end":
                continue
            message = frame.raw.get("message")
            if not isinstance(message, dict):
                continue
            candidate = message.get("stopReason")
            if isinstance(candidate, str) and candidate:
                stop_reason = candidate
        if not page.truncated:
            break
        after = page.frames[-1].sequence
    return stop_reason


def _store(channel: ControlChannel, record: InterruptRecord) -> None:
    channel.interrupts[record.request_id] = record
    while len(channel.interrupts) > MAX_INTERRUPTS_PER_CHANNEL:
        victim = next(
            (
                key
                for key, value in channel.interrupts.items()
                if _as_record(value).settlement != "pending"
            ),
            None,
        )
        channel.interrupts.pop(victim if victim is not None else next(iter(channel.interrupts)))


def _projection(ar_session_id: str, bridge_epoch: str, record: InterruptRecord) -> InterruptOperation:
    del ar_session_id  # the wire model carries no session field; identity is route-scoped
    return InterruptOperation(
        request_id=record.request_id,
        request_fingerprint=record.fingerprint,
        revision=record.revision,
        bridge_epoch=bridge_epoch,
        turn_id=record.turn_id,
        acknowledgement=record.acknowledgement,
        settlement=record.settlement,
        native_correlation_id=record.native_correlation_id,
        requested_at=record.requested_at,
        settled_at=record.settled_at,
        detail=record.detail,
    )


def _as_record(stored: object) -> InterruptRecord:
    assert isinstance(stored, InterruptRecord)
    return stored


def interrupt_http_status(operation: InterruptOperation) -> int:
    """The design's interrupt HTTP mapping for one operation projection."""

    if operation.acknowledgement == "rejected":
        return 422
    if operation.settlement == "failed":
        return 503
    if operation.settlement == "pending":
        return 202
    return 200


__all__ = ["interrupt", "interrupt_http_status", "interrupt_status"]
