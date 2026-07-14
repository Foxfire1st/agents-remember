"""Inbox-rooted delivery through exact-session harness protocol adapters."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.controlplane.operator_inbox_records import (
    AdapterDeliveryState,
    InboxDeliveryState,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import HarnessControlError
from agents_remember.observer.events import now_iso
from agents_remember.serving.dispatch_brief import (
    DISPATCH_BRIEF_KIND,
    DispatchBriefGate,
    with_prompt_keywords,
)
from agents_remember.serving.harness_control_client import (
    reconcile_control_prompt,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_models import ReconciliationResult, SubmissionReceipt
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import TerminalPaster


@dataclass(frozen=True)
class InboxDeliveryResult:
    """Outcome of trying to push one durable inbox row into a hosted session."""

    state: str
    session_id: str | None = None
    detail: str | None = None


def deliver_inbox_entry(
    *,
    store: OperatorInboxStore,
    catalog: TerminalCatalog,
    host: TerminalHost,
    paster: TerminalPaster,
    entry: OperatorInboxEntry,
    submit: bool = True,
    current: dict[str, OperatorInboxEntry] | None = None,
    redelivery_floor_seconds: float | None = None,
    delivery_at: str | None = None,
    dispatch_gate: DispatchBriefGate | None = None,
) -> OperatorInboxEntry:
    """Deliver a pre-existing durable row and record adapter evidence without consuming it.

    ``paster`` remains in the public composition signature for callers shared with ordinary
    terminal plumbing, but harness delivery never invokes it and has no raw-input fallback.
    """

    del paster  # compatibility composition parameter; protocol delivery never uses terminal input
    timestamp = delivery_at or now_iso()
    target = _target_session(catalog, entry)
    if target is None:
        return _record(
            store,
            entry,
            timestamp,
            target=None,
            delivery_state="no-hosted-session",
            adapter_state=None,
            detail="no running hosted session matched the inbox address",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    if not host.has_session(target.tmux_name):
        return _record(
            store,
            entry,
            timestamp,
            target=target,
            delivery_state="no-hosted-session",
            adapter_state=None,
            detail="catalog row exists but tmux session is not running",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    if target.kind != "harness" or target.control_endpoint is None:
        return _record(
            store,
            entry,
            timestamp,
            target=target,
            delivery_state="unconfirmed",
            adapter_state="unsupported",
            detail="legacy or ordinary terminal session has no protocol delivery adapter",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    if not submit:
        return _record(
            store,
            entry,
            timestamp,
            target=target,
            delivery_state="unconfirmed",
            adapter_state="rejected",
            detail="durable inbox delivery requires a committed adapter submission",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    if entry.messageKind == DISPATCH_BRIEF_KIND:
        gate_detail = (dispatch_gate or DispatchBriefGate()).check(
            catalog,
            host,
            target,
            recovery=entry.attemptCount > 0,
        )
        if gate_detail is not None:
            return _record(
                store,
                entry,
                timestamp,
                target=target,
                delivery_state="unconfirmed",
                adapter_state="rejected",
                detail=gate_detail,
                current=current,
                redelivery_floor_seconds=redelivery_floor_seconds,
            )

    if entry.adapterRequestId is not None:
        return _redelivery(
            store,
            entry,
            target,
            timestamp,
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )

    text = _push_text(entry)
    if entry.messageKind == DISPATCH_BRIEF_KIND:
        text = with_prompt_keywords(target, text)
    try:
        receipt = submit_control_prompt(
            target,
            text,
            source="durable",
            request_id=entry.id,
            submitted_at=timestamp,
        )
    except HarnessControlError as exc:
        reconciliation = _try_reconcile(target, entry.id)
        return _record_reconciliation(
            store,
            entry,
            target,
            timestamp,
            reconciliation,
            fallback_detail=f"ambiguous adapter transport: {exc}",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    return _record_receipt(
        store,
        entry,
        target,
        timestamp,
        receipt,
        current=current,
        redelivery_floor_seconds=redelivery_floor_seconds,
    )


def _redelivery(
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
    target: TerminalCatalogEntry,
    timestamp: str,
    *,
    current: dict[str, OperatorInboxEntry] | None,
    redelivery_floor_seconds: float | None,
) -> OperatorInboxEntry:
    if entry.adapterDeliveryState in {"accepted", "queued", "completed"}:
        return _record(
            store,
            entry,
            timestamp,
            target=target,
            delivery_state="delivered",
            adapter_state=entry.adapterDeliveryState,
            detail=f"adapter-{entry.adapterDeliveryState}: already correlated",
            current=current,
            redelivery_floor_seconds=redelivery_floor_seconds,
        )
    request_id = entry.adapterRequestId
    assert request_id is not None  # this helper is entered only for an already-correlated row
    reconciliation = _try_reconcile(target, request_id)
    return _record_reconciliation(
        store,
        entry,
        target,
        timestamp,
        reconciliation,
        fallback_detail="adapter request remains ambiguous; not resubmitted",
        current=current,
        redelivery_floor_seconds=redelivery_floor_seconds,
    )


def _try_reconcile(target: TerminalCatalogEntry, request_id: str) -> ReconciliationResult | None:
    try:
        return reconcile_control_prompt(target, request_id)
    except HarnessControlError:
        return None


def _record_receipt(
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
    target: TerminalCatalogEntry,
    timestamp: str,
    receipt: SubmissionReceipt,
    *,
    current: dict[str, OperatorInboxEntry] | None,
    redelivery_floor_seconds: float | None,
) -> OperatorInboxEntry:
    adapter_state: AdapterDeliveryState = (
        "accepted" if receipt.acceptance == "immediate" else receipt.acceptance
    )
    delivery_state: InboxDeliveryState = (
        "delivered" if adapter_state in {"accepted", "queued"} else "unconfirmed"
    )
    detail = f"adapter-{adapter_state}"
    if receipt.detail:
        detail += f": {receipt.detail}"
    return _record(
        store,
        entry,
        timestamp,
        target=target,
        delivery_state=delivery_state,
        adapter_state=adapter_state,
        detail=detail,
        request_id=receipt.request_id,
        vendor_correlation_id=receipt.vendor_correlation_id,
        accepted_at=receipt.accepted_at,
        current=current,
        redelivery_floor_seconds=redelivery_floor_seconds,
    )


def _record_reconciliation(
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
    target: TerminalCatalogEntry,
    timestamp: str,
    reconciliation: ReconciliationResult | None,
    *,
    fallback_detail: str,
    current: dict[str, OperatorInboxEntry] | None,
    redelivery_floor_seconds: float | None,
) -> OperatorInboxEntry:
    if reconciliation is None or reconciliation.state == "unresolved":
        state: AdapterDeliveryState = "unknown"
        detail = fallback_detail
    elif reconciliation.state == "accepted":
        state = "accepted"
        detail = reconciliation.detail or "adapter reconciliation accepted the request"
    else:
        state = reconciliation.state
        detail = reconciliation.detail or f"adapter reconciliation {reconciliation.state}"
    return _record(
        store,
        entry,
        timestamp,
        target=target,
        delivery_state="delivered" if state == "accepted" else "unconfirmed",
        adapter_state=state,
        detail=detail,
        request_id=(
            reconciliation.request_id
            if reconciliation is not None
            else entry.adapterRequestId or entry.id
        ),
        vendor_correlation_id=(
            reconciliation.vendor_correlation_id if reconciliation is not None else None
        ),
        current=current,
        redelivery_floor_seconds=redelivery_floor_seconds,
    )


def _record(
    store: OperatorInboxStore,
    entry: OperatorInboxEntry,
    timestamp: str,
    *,
    target: TerminalCatalogEntry | None,
    delivery_state: InboxDeliveryState,
    adapter_state: AdapterDeliveryState | None,
    detail: str,
    current: dict[str, OperatorInboxEntry] | None,
    redelivery_floor_seconds: float | None,
    request_id: str | None = None,
    vendor_correlation_id: str | None = None,
    accepted_at: str | None = None,
) -> OperatorInboxEntry:
    return store.record_delivery(
        entry.id,
        now=timestamp,
        delivery_state=delivery_state,
        delivered_to_session=target.id if target is not None else None,
        delivery_detail=detail,
        adapter_delivery_state=adapter_state,
        adapter_request_id=entry.adapterRequestId or request_id,
        adapter_vendor_correlation_id=vendor_correlation_id,
        adapter_accepted_at=accepted_at,
        adapter_delivery_detail=detail,
        current=current,
        redelivery_floor_seconds=redelivery_floor_seconds,
    )


def _target_session(
    catalog: TerminalCatalog,
    entry: OperatorInboxEntry,
) -> TerminalCatalogEntry | None:
    if entry.messageKind == DISPATCH_BRIEF_KIND:
        if entry.agentId is None:
            return None
        target = catalog.get(entry.agentId)
        return target if target is not None and target.status == "running" else None
    if entry.agentId:
        target = catalog.get(entry.agentId)
        if target is not None and target.status == "running":
            return target
    if entry.lifecycleId:
        return next(
            (
                target
                for target in catalog.list()
                if target.status == "running" and target.lifecycle_id == entry.lifecycleId
            ),
            None,
        )
    return None


def _push_text(entry: OperatorInboxEntry) -> str:
    sender = entry.senderRole or "operator"
    if entry.senderAgentId:
        sender = f"{sender}:{entry.senderAgentId}"
    parts = [
        f"[Agents Remember inbox:{entry.messageKind}]",
        f"from: {sender}",
        f"entry: {entry.id}",
        f"ack: reply in this chat -- entry {entry.id} is the mechanical ack target",
    ]
    if entry.artifactPath:
        parts.append(f"artifact: {entry.artifactPath}")
    parts.extend(["", entry.ask, "", entry.response])
    return "\n".join(parts)
