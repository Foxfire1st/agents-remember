"""Inbox-rooted delivery through exact-session harness protocol adapters."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.operator_inbox_records import (
    AdapterDeliveryState,
    InboxDeliveryState,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.controlplane.operator_inbox_transitions import (
    AdapterReceipt,
    DeliveryAttempt,
    RedeliveryFloor,
)
from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    SubmissionReceipt,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
    seat_at_turn_boundary,
)
from agents_remember.observer.events import now_iso
from agents_remember.serving.dispatch_brief import (
    DISPATCH_BRIEF_KIND,
    DispatchBriefGate,
    with_prompt_keywords,
)
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    reconcile_control_prompt,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_models import (
    ReconciliationResult,
)
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.ports import TerminalCatalogPort
from agents_remember.serving.terminal_paste import TerminalPaster


@dataclass(frozen=True)
class InboxDeliveryResult:
    """Outcome of trying to push one durable inbox row into a hosted session."""

    state: str
    session_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class _DeliveryOutcome:
    """What one delivery attempt amounted to, in exactly the fields ``_record`` writes.

    A refusal, an adapter receipt and a reconciliation all reduce to this triple, which is why it
    is one value: the three fields are always decided together and are meaningless apart (a
    delivery state with someone else's detail is a lie in the durable record).
    """

    delivery_state: InboxDeliveryState
    adapter_state: AdapterDeliveryState | None
    detail: str


@dataclass(frozen=True)
class _AdapterCorrelation:
    """How the adapter identifies the submission this attempt produced, if it produced one."""

    request_id: str | None = None
    vendor_correlation_id: str | None = None
    accepted_at: str | None = None


@dataclass(frozen=True)
class InboxDeliveryLog:
    """One durable row's delivery journal: which row, where attempts are written, and when.

    Every recorder in this module writes through the same journal, so it travels as one value and
    each recorder supplies only what is genuinely different about its own outcome.
    """

    store: OperatorInboxStore
    entry: OperatorInboxEntry
    at: str = field(default_factory=now_iso)
    floor: RedeliveryFloor = field(default_factory=RedeliveryFloor)


@dataclass(frozen=True)
class DeliveryAdmission:
    """Whether this push is allowed to reach the wire at all.

    Both checks settle before any adapter call: ``submit`` is the caller's commitment to a real
    adapter submission, ``dispatch_gate`` is the exact-once gate a durable brief must pass, and
    ``boundary`` is the availability gate state signals hold behind until the target seat is at
    a turn boundary (turn-ended / awaiting-input / ready-idle).
    """

    submit: bool = True
    dispatch_gate: DispatchBriefGate | None = None
    boundary: bool = False


_NO_ADAPTER_CORRELATION = _AdapterCorrelation()
DEFAULT_DELIVERY_ADMISSION = DeliveryAdmission()
"""The ordinary committed push: a real adapter submission with the default brief gate."""


def _delivery_refusal(
    entry: OperatorInboxEntry,
    *,
    sessions: HostedSessionRuntime,
    target: TerminalCatalogEntry,
    admission: DeliveryAdmission,
) -> _DeliveryOutcome | None:
    """The refusal to durably record for an addressed target, or ``None`` to go submit.

    Every check here is settled before any adapter call is made, so a dead pane, a legacy session
    with no bridge, an uncommitted caller or a closed dispatch-brief gate never reaches the wire.
    """

    if not sessions.host.has_session(target.tmux_name):
        return _DeliveryOutcome(
            "no-hosted-session", None, "catalog row exists but tmux session is not running"
        )
    if target.kind != "harness" or target.control_endpoint is None:
        return _DeliveryOutcome(
            "unconfirmed",
            "unsupported",
            "legacy or ordinary terminal session has no protocol delivery adapter",
        )
    if not admission.submit:
        return _DeliveryOutcome(
            "unconfirmed",
            "rejected",
            "durable inbox delivery requires a committed adapter submission",
        )
    if entry.messageKind == DISPATCH_BRIEF_KIND:
        gate_detail = (admission.dispatch_gate or DispatchBriefGate()).check(
            sessions.catalog,
            sessions.host,
            target,
            recovery=entry.attemptCount > 0,
        )
        if gate_detail is not None:
            return _DeliveryOutcome("unconfirmed", "rejected", gate_detail)
    # Fail-closed availability gate: state-signal rows are gated BY ROW KIND regardless of
    # which caller drives the delivery (first post, redelivery, or a boundary drain) --
    # a mid-turn push would make acceptance terminal without the N1 gate, which is exactly
    # what landed-terminality must never mean. Other kinds use the caller's admission flag.
    if (entry.messageKind == "state-signal" or admission.boundary) and not seat_at_turn_boundary(
        target
    ):
        reason = (
            "availability gate: state-signal rows push only at a turn boundary"
            if entry.messageKind == "state-signal"
            else "availability gate: target seat is not at a turn boundary"
        )
        return _DeliveryOutcome(
            "queued",
            "queued",
            reason,
        )
    return None


def deliver_inbox_entry(
    log: InboxDeliveryLog,
    *,
    sessions: HostedSessionRuntime,
    paster: TerminalPaster,
    admission: DeliveryAdmission = DEFAULT_DELIVERY_ADMISSION,
) -> OperatorInboxEntry:
    """Deliver a pre-existing durable row and record adapter evidence without consuming it.

    ``paster`` remains in the public composition signature for callers shared with ordinary
    terminal plumbing, but harness delivery never invokes it and has no raw-input fallback.

    Landing (N16) is decided HERE, where the target's boundary state is still live: a
    correlated adapter ``accepted`` receipt while ``seat_at_turn_boundary(target)`` holds
    writes the formal ``landed`` terminal state. ``acceptance=queued`` from a busy adapter is
    never a landing, and acceptance outside a boundary leaves the row on its redelivery
    schedule so the next boundary can drain it.
    """

    del paster  # compatibility composition parameter; protocol delivery never uses terminal input
    entry = log.entry
    target = _target_session(sessions.catalog, entry)
    if target is None:
        return _record(
            log,
            None,
            _DeliveryOutcome(
                "no-hosted-session",
                None,
                "no running hosted session matched the inbox address",
            ),
        )
    refusal = _delivery_refusal(entry, sessions=sessions, target=target, admission=admission)
    if refusal is not None:
        return _record(log, target, refusal, landed=False)

    at_boundary = seat_at_turn_boundary(target)
    if entry.adapterRequestId is not None:
        return _redelivery(log, target, at_boundary=at_boundary)

    text = _push_text(entry)
    if entry.messageKind == DISPATCH_BRIEF_KIND:
        text = with_prompt_keywords(target, text)
    try:
        receipt = submit_control_prompt(
            target,
            text,
            ControlSubmission(source="durable", request_id=entry.id, submitted_at=log.at),
        )
    except HarnessControlError as exc:
        reconciliation = _try_reconcile(target, entry.id)
        return _record_reconciliation(
            log,
            target,
            reconciliation,
            fallback_detail=f"ambiguous adapter transport: {exc}",
            at_boundary=at_boundary,
        )
    return _record_receipt(log, target, receipt, at_boundary=at_boundary)


def _redelivery(
    log: InboxDeliveryLog, target: TerminalCatalogEntry, *, at_boundary: bool
) -> OperatorInboxEntry:
    entry = log.entry
    if entry.adapterDeliveryState in {"accepted", "queued", "completed"}:
        return _record(
            log,
            target,
            _DeliveryOutcome(
                "delivered",
                entry.adapterDeliveryState,
                f"adapter-{entry.adapterDeliveryState}: already correlated",
            ),
            landed=at_boundary and entry.adapterDeliveryState == "accepted",
        )
    request_id = entry.adapterRequestId
    assert request_id is not None  # this helper is entered only for an already-correlated row
    return _record_reconciliation(
        log,
        target,
        _try_reconcile(target, request_id),
        fallback_detail="adapter request remains ambiguous; not resubmitted",
        at_boundary=at_boundary,
    )


def _try_reconcile(target: TerminalCatalogEntry, request_id: str) -> ReconciliationResult | None:
    try:
        return reconcile_control_prompt(target, request_id)
    except HarnessControlError:
        return None


def _record_receipt(
    log: InboxDeliveryLog,
    target: TerminalCatalogEntry,
    receipt: SubmissionReceipt,
    *,
    at_boundary: bool,
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
        log,
        target,
        _DeliveryOutcome(delivery_state, adapter_state, detail),
        _AdapterCorrelation(
            request_id=receipt.request_id,
            vendor_correlation_id=receipt.vendor_correlation_id,
            accepted_at=receipt.accepted_at,
        ),
        landed=at_boundary and adapter_state == "accepted",
    )


def _record_reconciliation(
    log: InboxDeliveryLog,
    target: TerminalCatalogEntry,
    reconciliation: ReconciliationResult | None,
    *,
    fallback_detail: str,
    at_boundary: bool,
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
        log,
        target,
        _DeliveryOutcome("delivered" if state == "accepted" else "unconfirmed", state, detail),
        _AdapterCorrelation(
            request_id=(
                reconciliation.request_id
                if reconciliation is not None
                else log.entry.adapterRequestId or log.entry.id
            ),
            vendor_correlation_id=(
                reconciliation.vendor_correlation_id if reconciliation is not None else None
            ),
        ),
        landed=at_boundary and state == "accepted",
    )


def _record(
    log: InboxDeliveryLog,
    target: TerminalCatalogEntry | None,
    outcome: _DeliveryOutcome,
    correlation: _AdapterCorrelation = _NO_ADAPTER_CORRELATION,
    *,
    landed: bool = False,
) -> OperatorInboxEntry:
    return inbox_transitions.record_delivery(
        log.store,
        log.entry.id,
        DeliveryAttempt(
            delivery_state=outcome.delivery_state,
            delivered_to_session=target.id if target is not None else None,
            detail=outcome.detail,
            landed=landed,
            adapter=AdapterReceipt(
                delivery_state=outcome.adapter_state,
                request_id=log.entry.adapterRequestId or correlation.request_id,
                vendor_correlation_id=correlation.vendor_correlation_id,
                accepted_at=correlation.accepted_at,
                detail=outcome.detail,
            ),
        ),
        now=log.at,
        floor=log.floor,
    )


def _target_session(
    catalog: TerminalCatalogPort,
    entry: OperatorInboxEntry,
) -> TerminalCatalogEntry | None:
    return target_session_for_entry(catalog, entry)


def target_session_for_entry(
    catalog: TerminalCatalogPort,
    entry: OperatorInboxEntry,
) -> TerminalCatalogEntry | None:
    """The running catalog session a durable row is addressed to, by exact agent id first."""
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
