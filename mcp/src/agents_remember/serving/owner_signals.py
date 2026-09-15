"""Owner-addressed durable signal posting (one row per root cause)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from agents_remember.controlplane import operator_inbox_transitions as inbox_transitions
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxDeliveryState,
    InboxMessage,
    InboxMessageKind,
    InboxOwner,
    InboxPoster,
    InboxRouting,
    InboxSubject,
    OperatorInboxEntry,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_transitions import InboxRenewal, RedeliveryFloor
from agents_remember.controlplane.signal_routing import RoutedOwner
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.ulid import new_ulid
from agents_remember.serving._agent_notifier_evaluation import _seat_liveness_ask_identity
from agents_remember.serving.agent_notifier_models import AgentNotifierContext
from agents_remember.serving.agent_notifier_models import SweepState as _SweepState
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import (
    DEFAULT_DELIVERY_ADMISSION,
    DeliveryAdmission,
    InboxDeliveryLog,
    deliver_inbox_entry,
)


@dataclass(frozen=True)
class OwnerSignal:
    """One owner-addressed signal: what is being said, and about which seat.

    The message and its subject are inseparable here -- coalescing looks up an existing row by
    (ask, kind, subject), and renewal rewrites the subject from the same value, so a message
    carrying someone else's subject silently renews the wrong row. What the subject means
    differs by kind: a state signal is identified by the exact seat it reports on, every other
    kind by the task document and role it names.
    """

    message_kind: InboxMessageKind
    ask: str
    response: str
    task_document_ref: TaskDocumentRef | None = None
    seat_role: str | None = None
    subject_agent_id: str | None = None


@dataclass(frozen=True)
class OwnerSignalOptions:
    """The delivery context for one owner signal: when, against which sweep fold, and under
    which admission policy. The three travel together because one signal attempt is one
    sweep action against one store snapshot.

    ``after_persist`` is the optional step that must be durable before anything reaches the
    wire. It runs once the row itself is durable -- appended or renewed, and remembered in the
    sweep -- and strictly before the first delivery attempt, so a failure inside it leaves that
    one pending row undelivered for the next sweep to retry. Owner signals whose marker is
    written after delivery pass nothing.
    """

    now: datetime
    sweep: _SweepState | None = None
    admission: DeliveryAdmission = DEFAULT_DELIVERY_ADMISSION
    after_persist: Callable[[], None] | None = None


def _coalesces_on_source(row: OperatorInboxEntry, *, signal: OwnerSignal) -> bool:
    """Whether one pending row is the same logical signal as the one being posted.

    A state signal is identified by the exact subject seat it reports on: R08 may rewrite that
    seat's task document, role, and owner address for the same evidence before a retry, while a
    different replacement seat that ends the same turn can never renew this seat's row. Every
    other kind keeps the structural key -- which occupant a row concerns is correlation there.
    """

    if signal.message_kind == "state-signal":
        return row.subjectAgentId == signal.subject_agent_id
    return (
        row.subjectTaskDocumentRef == signal.task_document_ref and row.seatRole == signal.seat_role
    )


def _find_coalescible(
    entries: dict[str, OperatorInboxEntry],
    *,
    signal: OwnerSignal,
) -> OperatorInboxEntry | None:
    """The ruled coalescing lookup (developer, 2026-07-09): an agent-notifier-authored condition that
    is still pending under the SAME ask identity is the row to renew -- matched on content, not
    address, so a row the rebind machinery has re-addressed still coalesces with its re-firing root
    condition, and a legacy-prefix row still coalesces with a new-prefix re-fire."""
    for row in entries.values():
        if (
            row.state == "pending"
            # Legacy rows created before the rename window carry "supervisor"; both are the
            # same relay-authored condition and must coalesce until the window closes.
            and row.createdBy in {"supervisor", "agent-notifier"}
            and row.messageKind == signal.message_kind
            # The ask prefix was renamed too; both prefixes are one signal identity, so a
            # new-format re-fire renews a legacy-format pending row (one row per root cause).
            and _seat_liveness_ask_identity(row.ask) == _seat_liveness_ask_identity(signal.ask)
            and _coalesces_on_source(row, signal=signal)
        ):
            return row
    return None


def _post_owner_signal(
    ctx: AgentNotifierContext,
    owner: RoutedOwner,
    signal: OwnerSignal,
    options: OwnerSignalOptions,
) -> InboxDeliveryState:
    """Emit one owner-addressed signal row, then attempt hosted delivery.

    Ruled invariant (developer, 2026-07-09): one row per root cause. A condition that re-fires
    while its row is still pending RENEWS that row (bumped date, refreshed detail) instead of
    appending a duplicate -- the storm that took the host down was this function minting a new
    pending row per re-fire, each of which then escalated into more rows.
    """
    sweep = options.sweep
    now = options.now
    entries = sweep.inbox_current if sweep is not None else ctx.inbox_store.current()
    subject = InboxSubject(
        task_document_ref=signal.task_document_ref,
        seat_role=signal.seat_role,
        agent_id=signal.subject_agent_id,
    )
    existing = _find_coalescible(entries, signal=signal)
    if existing is not None:
        entry = inbox_transitions.renew(
            ctx.inbox_store,
            existing.id,
            InboxRenewal(
                response=signal.response,
                subject=subject,
                readdress_to=InboxOwner(
                    role=owner.role,
                    task_document_ref=owner.task_document_ref,
                    agent_id=owner.agent_id,
                    lifecycle_id=owner.lifecycle_id,
                ),
            ),
            now=now.isoformat(),
            current=entries,
        )
    else:
        entry = create_operator_inbox_entry(
            InboxMessage(
                ask=signal.ask,
                response=signal.response,
                message_kind=signal.message_kind,
                subject=subject,
            ),
            entry_id=new_ulid(),
            now=now.isoformat(),
            routing=InboxRouting(
                address=InboxAddress(
                    task_document_ref=owner.task_document_ref,
                    lifecycle_id=owner.lifecycle_id,
                    agent_id=owner.agent_id,
                    recipient_role=owner.role,
                ),
                owner=InboxOwner(
                    role=owner.role,
                    task_document_ref=owner.task_document_ref,
                    agent_id=owner.agent_id,
                    lifecycle_id=owner.lifecycle_id,
                ),
            ),
            poster=InboxPoster(
                created_by="agent-notifier", created_via="cli", sender_role="system"
            ),
        )
        ctx.inbox_store.append(entry)
    if sweep is not None:
        sweep.remember(entry)
    # The row is durable and visible to this sweep before the marker callback runs, and delivery
    # starts only after that callback returns: a crash or a failed marker write here cannot leave
    # a landed row without its marker, and cannot lose the wake -- the pending row is the
    # recovery authority the next sweep renews.
    if options.after_persist is not None:
        options.after_persist()
    delivered = deliver_inbox_entry(
        InboxDeliveryLog(
            store=ctx.inbox_store,
            entry=entry,
            at=now.isoformat(),
            floor=RedeliveryFloor(
                current=sweep.inbox_current if sweep is not None else None,
                seconds=ctx.redeliver_rate_limit_seconds,
            ),
        ),
        sessions=HostedSessionRuntime(catalog=ctx.catalog, host=ctx.host),
        paster=ctx.paster,
        admission=options.admission,
    )
    if sweep is not None:
        sweep.remember(delivered)
    return delivered.deliveryState


__all__ = ["OwnerSignal", "OwnerSignalOptions", "_find_coalescible", "_post_owner_signal"]
