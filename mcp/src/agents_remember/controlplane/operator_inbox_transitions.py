"""What one inbox row's next snapshot says: the policy over the operator inbox log.

The store next door owns the FILE -- the advisory lock, the append, the physical
removal of rows both processes must be able to perform. These functions own the
DECISION: given the row as it stands, what does the next snapshot of it say. They were
methods on ``OperatorInboxStore`` and are here because they are a second job: every one
of them reads the current fold, computes a ``model_copy`` update, and hands the result
back to the store to persist. None of them touches a path, a handle or a lock.

THE LOCK CONTRACT IS THE STORE'S AND IS UNCHANGED BY THIS MOVE. Every read here goes
through :meth:`OperatorInboxStore.current` and every write through
:meth:`OperatorInboxStore.append`, which are exactly the calls these bodies made when
they were methods -- so the log's ``flock`` is still taken unconditionally on both
sides, ``rewrite_lines``' ``require_lock_held`` is still reached only from inside the
store, and the store is still the only module that knows the inbox has a lock at all.
Read-then-append was never one atomic section (the append-only fold is what resolves a
race, per ``operator_inbox_records.fold_operator_inbox_entries``), and it still is not.
Every operation that reads the log and then writes it -- ``consume``, ``delete``,
``delete_by_gate``, ``compact``, ``reconcile_and_compact`` -- holds the lock across both
halves and stayed on the store for that reason. Only these six pure ``model_copy``
transitions could move.

Each function takes the already-folded snapshot its caller had -- directly as ``current``,
or inside ``RedeliveryFloor`` where the fold and the schedule floor travel together -- so a
sweep that folded once can drive many transitions without re-reading the log per row.

The argument records below travel with the transitions rather than with the store,
because each of them describes an INPUT to a decision -- what the adapter reported, what
one delivery attempt did, what a re-firing condition refreshes, and why a row expires --
and the store has no use for any of them. ``RedeliveryFloor`` moved DOWN to this module
from ``serving/inbox_delivery.py`` in the same change: it is the schedule a control-plane
row is measured against, so a rank-12 package was declaring a rank-3 concept, and the
transition that reads it lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agents_remember.controlplane.operator_inbox_records import (
    AdapterDeliveryState,
    InboxDeliveryState,
    InboxOwner,
    InboxSubject,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.inbox_backoff import (
    next_attempt_at,
)

InboxFold = dict[str, OperatorInboxEntry] | None


@dataclass(frozen=True)
class AdapterReceipt:
    """What the vendor adapter reported about one delivery attempt: the state it returned, the
    request it acknowledged, the vendor's own correlation id, when it accepted the payload, and
    any detail. One receipt per attempt -- the fields are never sourced independently."""

    delivery_state: AdapterDeliveryState | None = None
    request_id: str | None = None
    vendor_correlation_id: str | None = None
    accepted_at: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class DeliveryAttempt:
    """One attempt to put a pending row in front of its addressee: the outcome, the session it
    was pasted into, the human-readable detail, the adapter's receipt for the same attempt,
    and whether the target seat was at a turn boundary when the attempt happened.

    ``landed`` is the N16 gate: a correlated ``accepted`` receipt at a turn boundary writes the
    formal ``landed`` terminal state. ``delivered`` outside a boundary is not terminal, and a
    busy adapter's ``queued`` acceptance is never a landing."""

    delivery_state: InboxDeliveryState
    delivered_to_session: str | None = None
    detail: str | None = None
    adapter: AdapterReceipt = AdapterReceipt()
    landed: bool = False


@dataclass(frozen=True)
class InboxRenewal:
    """What a re-firing condition refreshes on the one row it already has: the response text,
    the subject the row now concerns, and -- when the routed owner has moved on -- the owner to
    readdress it to. Passing ``readdress_to`` IS the readdress; there is no owner without one."""

    response: str | None = None
    subject: InboxSubject = field(default_factory=InboxSubject)
    readdress_to: InboxOwner | None = None


@dataclass(frozen=True)
class RedeliveryFloor:
    """The rate limit on re-recording a delivery, and the row snapshot it is measured against.

    The floor is meaningless without ``current``: scheduling the next attempt needs the rows
    this attempt's timing is compared against. They arrive together from the sweep that owns
    both, which is why they are one value rather than two parameters.
    """

    current: dict[str, OperatorInboxEntry] | None = None
    seconds: float | None = None


@dataclass(frozen=True)
class AdapterCompletion:
    """The terminal evidence a vendor adapter reported for one row: the vendor's own
    correlation id and whatever detail came with the terminal result. Separate from
    :class:`AdapterReceipt`, which describes an ATTEMPT; this describes the end of one."""

    vendor_correlation_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ExpiryOptions:
    """Why a row expires, and whether its terminal marker moves to an inspection mailbox."""

    reason: str
    readdress_to: InboxOwner | None = None


# "The caller folded nothing and applies no floor" -- what a one-off transition passes. A
# module-level singleton rather than a call in the parameter default, which ruff B008 refuses
# because a default evaluated at definition time reads as if it were evaluated per call.
_NO_REDELIVERY_FLOOR = RedeliveryFloor()


def _readdress_fields(owner: InboxOwner) -> dict[str, object]:
    """Move a row's delivery address onto ``owner`` and record it as the routed owner."""
    return {
        "recipientRole": owner.role,
        "agentId": owner.agent_id,
        "lifecycleId": owner.lifecycle_id,
        "taskDocumentRef": owner.task_document_ref,
        "ownerRole": owner.role,
        "ownerTaskDocumentRef": owner.task_document_ref,
        "ownerAgentId": owner.agent_id,
        "ownerLifecycleId": owner.lifecycle_id,
    }


def _require_entry(
    store: OperatorInboxStore, entry_id: str, current: InboxFold
) -> OperatorInboxEntry:
    """The row ``entry_id`` names, from the supplied fold or a fresh one.

    Raises rather than returning ``None``: every caller here was already raising on a
    missing row, and a transition applied to a row nobody can find is a lost decision.
    """
    entries = store.current() if current is None else current
    entry = entries.get(entry_id)
    if entry is None:
        raise KeyError(f"no operator inbox entry {entry_id!r}")
    return entry


def record_delivery(
    store: OperatorInboxStore,
    entry_id: str,
    attempt: DeliveryAttempt,
    *,
    now: str,
    floor: RedeliveryFloor = _NO_REDELIVERY_FLOOR,
) -> OperatorInboxEntry:
    """Append a delivery-status snapshot for one pending entry.

    Every attempt bumps ``attemptCount`` and stamps ``lastAttemptAt``. A correlated adapter
    acceptance while the target was at a turn boundary (``attempt.landed``) writes the formal
    ``landed`` terminal state and clears the redelivery schedule (N16); everything else keeps
    the durable ``nextAttemptAt`` backoff row, restart-proof. The landed write is a
    lock-held latest-fold transition: a concurrent terminal write (explicit supersession,
    another expiry) wins and the stale landing appends nothing (F1).
    """
    entry = _require_entry(store, entry_id, floor.current)
    adapter = attempt.adapter
    landed = attempt.landed and adapter.delivery_state == "accepted" and entry.state == "pending"
    if landed:

        def _land_latest(latest: OperatorInboxEntry) -> OperatorInboxEntry | None:
            if latest.state != "pending":
                return None
            update = _delivery_evidence_update(latest, attempt, now=now)
            update.update(
                {
                    "state": "landed",
                    "terminalAt": now,
                    "terminalReason": "adapter-accepted-at-turn-boundary",
                    "nextAttemptAt": None,
                }
            )
            return latest.model_copy(update=update)

        return store.transition(entry_id, _land_latest)[0]
    update = _delivery_evidence_update(entry, attempt, now=now)
    update["nextAttemptAt"] = (
        next_attempt_at(
            now=datetime.fromisoformat(now),
            attempt_count=entry.attemptCount + 1,
            redelivery_floor_seconds=floor.seconds,
        )
        if entry.state == "pending"
        else entry.nextAttemptAt
    )
    delivered = entry.model_copy(update=update)
    store.append(delivered)
    return delivered


def _delivery_evidence_update(
    entry: OperatorInboxEntry,
    attempt: DeliveryAttempt,
    *,
    now: str,
) -> dict[str, object]:
    """The delivery-evidence half of one attempt snapshot, computed against ``entry``."""
    adapter = attempt.adapter
    delivery_state = attempt.delivery_state
    return {
        "ts": now,
        "deliveryState": delivery_state,
        "deliveredAt": now if delivery_state == "delivered" else entry.deliveredAt,
        "deliveredToSession": attempt.delivered_to_session,
        "deliveryDetail": attempt.detail,
        "adapterDeliveryState": adapter.delivery_state or entry.adapterDeliveryState,
        "adapterRequestId": adapter.request_id or entry.adapterRequestId,
        "adapterVendorCorrelationId": (
            adapter.vendor_correlation_id or entry.adapterVendorCorrelationId
        ),
        "adapterAcceptedAt": adapter.accepted_at or entry.adapterAcceptedAt,
        "adapterDeliveryDetail": (
            adapter.detail if adapter.detail is not None else entry.adapterDeliveryDetail
        ),
        "attemptCount": entry.attemptCount + 1,
        "lastAttemptAt": now,
    }


def mark_landed(
    store: OperatorInboxStore,
    entry_id: str,
    *,
    now: str,
    reason: str,
) -> tuple[OperatorInboxEntry, bool]:
    """Fold a legacy by-rule landing into the formal ``landed`` state (N13 migration)."""

    def _apply(latest: OperatorInboxEntry) -> OperatorInboxEntry | None:
        if latest.state != "pending":
            return None
        return latest.model_copy(
            update={
                "ts": now,
                "state": "landed",
                "terminalAt": now,
                "terminalReason": reason,
                "nextAttemptAt": None,
            }
        )

    return store.transition(entry_id, _apply)


def mark_superseded(
    store: OperatorInboxStore,
    entry_id: str,
    *,
    now: str,
    reason: str,
    superseded_by: str | None = None,
) -> tuple[OperatorInboxEntry, bool]:
    """Explicitly mark one overtaken command terminal ``superseded`` without a false ack.

    Supersession is always explicit (owner/developer); nothing here infers it from artifacts,
    branches, or task state. The row keeps its delivery evidence; it never lands.
    """

    def _apply(latest: OperatorInboxEntry) -> OperatorInboxEntry | None:
        if latest.state != "pending":
            return None
        return latest.model_copy(
            update={
                "ts": now,
                "state": "superseded",
                "terminalAt": now,
                "terminalReason": reason,
                "supersededBy": superseded_by,
                "nextAttemptAt": None,
            }
        )

    return store.transition(entry_id, _apply)


def mark_unresolved(
    store: OperatorInboxStore,
    entry_id: str,
    *,
    now: str,
    reason: str,
) -> tuple[OperatorInboxEntry, bool]:
    """Terminally resolve a row whose delivery attempts hit the ceiling (N3).

    Delivery evidence stays intact on the row -- never-accepted vs accepted-but-not-at-a-
    boundary remain distinguishable via ``deliveryState``/``adapterDeliveryState``.
    """

    def _apply(latest: OperatorInboxEntry) -> OperatorInboxEntry | None:
        if latest.state != "pending":
            return None
        return latest.model_copy(
            update={
                "ts": now,
                "state": "unresolved",
                "terminalAt": now,
                "terminalReason": reason,
                "nextAttemptAt": None,
            }
        )

    return store.transition(entry_id, _apply)


def mark_expired(
    store: OperatorInboxStore,
    entry_id: str,
    *,
    now: str,
    options: ExpiryOptions,
) -> tuple[OperatorInboxEntry, bool]:
    """Terminally resolve a row by an expiry clock (rebind grace, retention TTL).

    ``options.readdress_to`` optionally moves the terminal marker onto an inspection mailbox (the N3
    architect mailbox of last resort) so a dead owner chain stays visible instead of vanishing.
    """

    def _apply(latest: OperatorInboxEntry) -> OperatorInboxEntry | None:
        if latest.state != "pending":
            return None
        update: dict[str, object] = {
            "ts": now,
            "state": "expired",
            "terminalAt": now,
            "terminalReason": options.reason,
            "nextAttemptAt": None,
        }
        if options.readdress_to is not None:
            update.update(_readdress_fields(options.readdress_to))
        return latest.model_copy(update=update)

    return store.transition(entry_id, _apply)


def rebind_entry(
    store: OperatorInboxStore,
    entry_id: str,
    owner: InboxOwner,
    *,
    now: str,
    current: InboxFold = None,
) -> tuple[OperatorInboxEntry, bool]:
    """Sweep-time rebind (N14): move a pending row onto its current qualified owner.

    The delivery address and the stamped routed owner move together (one routing decision).
    Per-attempt adapter correlation from the dead seat is cleared -- the replacement never
    saw that submission -- and the attempt clock restarts so the replacement gets the full
    redelivery schedule instead of inheriting the dead seat's attempt count.
    """
    entry = _require_entry(store, entry_id, current)
    if entry.state != "pending":
        return entry, False
    rebound = entry.model_copy(
        update={
            "ts": now,
            **_readdress_fields(owner),
            "deliveryState": "queued",
            "deliveredAt": None,
            "deliveredToSession": None,
            "deliveryDetail": None,
            "adapterDeliveryState": None,
            "adapterRequestId": None,
            "adapterVendorCorrelationId": None,
            "adapterAcceptedAt": None,
            "adapterCompletedAt": None,
            "adapterDeliveryDetail": None,
            "attemptCount": 0,
            "lastAttemptAt": None,
            "nextAttemptAt": None,
        }
    )
    store.append(rebound)
    return rebound, True


def record_adapter_completion(
    store: OperatorInboxStore,
    entry_id: str,
    completion: AdapterCompletion,
    *,
    now: str,
    current: InboxFold = None,
) -> OperatorInboxEntry:
    """Persist terminal adapter evidence without consuming the durable inbox row."""
    entry = _require_entry(store, entry_id, current)
    completed = entry.model_copy(
        update={
            "ts": now,
            "adapterDeliveryState": "completed",
            "adapterVendorCorrelationId": (
                completion.vendor_correlation_id or entry.adapterVendorCorrelationId
            ),
            "adapterCompletedAt": now,
            "adapterDeliveryDetail": completion.detail,
        }
    )
    store.append(completed)
    return completed


def renew(
    store: OperatorInboxStore,
    entry_id: str,
    renewal: InboxRenewal,
    *,
    now: str,
    current: InboxFold = None,
) -> OperatorInboxEntry:
    """Refresh one still-pending row in place: same id, bumped ``ts``, optionally refreshed
    ``response``. The ruled coalescing primitive (developer, 2026-07-09): a condition that
    re-fires updates its ONE existing row's date/detail instead of appending a duplicate --
    there is zero reason to repeat the same message until the system catches fire."""
    entry = _require_entry(store, entry_id, current)
    if entry.state != "pending":
        return entry
    update: dict[str, object] = {"ts": now}
    if renewal.response is not None:
        update["response"] = renewal.response
    if renewal.subject.task_document_ref is not None:
        update["subjectTaskDocumentRef"] = renewal.subject.task_document_ref
    if renewal.subject.seat_role is not None:
        update["seatRole"] = renewal.subject.seat_role
    if renewal.subject.agent_id is not None:
        update["subjectAgentId"] = renewal.subject.agent_id
    if renewal.readdress_to is not None:
        update.update(_readdress_fields(renewal.readdress_to))
    renewed = entry.model_copy(update=update)
    store.append(renewed)
    return renewed
