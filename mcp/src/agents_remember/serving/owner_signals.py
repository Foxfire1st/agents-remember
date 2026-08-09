"""Owner-addressed durable signal posting (one row per root cause)."""

from __future__ import annotations

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
    (ask, kind, leaf, role), and renewal rewrites the subject from the same value, so a message
    carrying someone else's subject silently renews the wrong row.
    """

    message_kind: InboxMessageKind
    ask: str
    response: str
    leaf_key: str | None = None
    seat_role: str | None = None
    subject_agent_id: str | None = None


@dataclass(frozen=True)
class OwnerSignalOptions:
    """The delivery context for one owner signal: when, against which sweep fold, and under
    which admission policy. The three travel together because one signal attempt is one
    sweep action against one store snapshot."""

    now: datetime
    sweep: _SweepState | None = None
    admission: DeliveryAdmission = DEFAULT_DELIVERY_ADMISSION


def _find_coalescible(
    entries: dict[str, OperatorInboxEntry],
    *,
    ask: str,
    message_kind: InboxMessageKind,
    leaf_key: str | None,
    seat_role: str | None,
) -> OperatorInboxEntry | None:
    """The ruled coalescing lookup (developer, 2026-07-09): an agent-notifier-authored condition that
    is still pending under the SAME ask identity is the row to renew -- matched on content, not
    address, so a row the ladder has re-addressed still coalesces with its re-firing root
    condition, and a legacy-prefix row still coalesces with a new-prefix re-fire."""
    for row in entries.values():
        if (
            row.state == "pending"
            # Legacy rows created before the rename window carry "supervisor"; both are the
            # same relay-authored condition and must coalesce until the window closes.
            and row.createdBy in {"supervisor", "agent-notifier"}
            and row.messageKind == message_kind
            # The ask prefix was renamed too; both prefixes are one signal identity, so a
            # new-format re-fire renews a legacy-format pending row (one row per root cause).
            and _seat_liveness_ask_identity(row.ask) == _seat_liveness_ask_identity(ask)
            and row.leafKey == leaf_key
            and row.seatRole == seat_role
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
    pending row per re-fire, each of which the ladder then escalated into more rows.
    """
    sweep = options.sweep
    now = options.now
    entries = sweep.inbox_current if sweep is not None else ctx.inbox_store.current()
    subject = InboxSubject(
        leaf_key=signal.leaf_key, seat_role=signal.seat_role, agent_id=signal.subject_agent_id
    )
    existing = _find_coalescible(
        entries,
        ask=signal.ask,
        message_kind=signal.message_kind,
        leaf_key=signal.leaf_key,
        seat_role=signal.seat_role,
    )
    if existing is not None:
        entry = inbox_transitions.renew(
            ctx.inbox_store,
            existing.id,
            InboxRenewal(
                response=signal.response,
                subject=subject,
                readdress_to=InboxOwner(
                    role=owner.role, agent_id=owner.agent_id, lifecycle_id=owner.lifecycle_id
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
                    lifecycle_id=owner.lifecycle_id,
                    agent_id=owner.agent_id,
                    recipient_role=owner.role,
                ),
                owner=InboxOwner(
                    role=owner.role, agent_id=owner.agent_id, lifecycle_id=owner.lifecycle_id
                ),
            ),
            poster=InboxPoster(
                created_by="agent-notifier", created_via="cli", sender_role="system"
            ),
        )
        ctx.inbox_store.append(entry)
    if sweep is not None:
        sweep.remember(entry)
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
