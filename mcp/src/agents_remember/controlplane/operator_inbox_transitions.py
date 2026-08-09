"""What one inbox row's next snapshot says: the policy over the operator inbox log.

The store next door owns the FILE -- the advisory lock, the append, the physical
removal of rows both processes must be able to perform. These six functions own the
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
one delivery attempt did, what a re-firing condition refreshes, which rung the ladder is
climbing to -- and the store has no use for any of them. ``RedeliveryFloor`` moved DOWN
to this module from ``serving/inbox_delivery.py`` in the same change: it is the schedule
a control-plane row is measured against, so a rank-12 package was declaring a rank-3
concept, and the transition that reads it lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agents_remember.controlplane.inbox_backoff import is_ladder_resolved, next_attempt_at
from agents_remember.controlplane.operator_inbox_records import (
    AdapterDeliveryState,
    InboxDeliveryState,
    InboxOwner,
    InboxSubject,
    OperatorInboxEntry,
    state_signal_landed,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore

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
    was pasted into, the human-readable detail, and the adapter's receipt for the same attempt.
    ``delivered`` is not terminal (pasted != perceived); only a consume ends the schedule."""

    delivery_state: InboxDeliveryState
    delivered_to_session: str | None = None
    detail: str | None = None
    adapter: AdapterReceipt = AdapterReceipt()


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
class RungAdvance:
    """The ladder rung to stamp and, when that rung re-addresses, the owner the row moves to.

    One value because the ladder decides both together (``escalation_ladder.next_step``
    returns the rung and its addressee as one step) and stamping one without the other
    would leave a row escalated to nobody.
    """

    rung: int
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
        "ownerRole": owner.role,
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

    R1/R3: every attempt -- including a confirmed ``delivered`` paste -- bumps
    ``attemptCount``, stamps ``lastAttemptAt``, and schedules ``nextAttemptAt`` from the
    backoff ladder. 'delivered' is never terminal (pasted != perceived), so only
    ``consume`` clears the redelivery schedule; a still-pending entry always carries a
    durable next-attempt row L2 can sweep, restart-proof.
    """
    entry = _require_entry(store, entry_id, floor.current)
    delivery_state = attempt.delivery_state
    adapter = attempt.adapter
    attempt_count = entry.attemptCount + 1
    update: dict[str, object] = {
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
        "attemptCount": attempt_count,
        "lastAttemptAt": now,
    }
    landed = state_signal_landed(entry.model_copy(update=update))
    update["nextAttemptAt"] = (
        None
        if landed
        else (
            next_attempt_at(
                now=datetime.fromisoformat(now),
                attempt_count=attempt_count,
                redelivery_floor_seconds=floor.seconds,
            )
            if entry.state == "pending"
            else entry.nextAttemptAt
        )
    )
    delivered = entry.model_copy(update=update)
    store.append(delivered)
    return delivered


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


def mark_escalated(
    store: OperatorInboxStore,
    entry_id: str,
    *,
    now: str,
    current: InboxFold = None,
) -> OperatorInboxEntry:
    """Stamp ``escalatedAt`` once the ladder (HFX2-L4) escalates an unacked row.

    HFX2-L2 only reserved the field -- it never called this itself; redelivery keeps
    running until either ack or this mark, per R3.
    """
    entry = _require_entry(store, entry_id, current)
    escalated = entry.model_copy(update={"ts": now, "escalatedAt": now})
    store.append(escalated)
    return escalated


def advance_rung(
    store: OperatorInboxStore,
    entry_id: str,
    advance: RungAdvance,
    *,
    now: str,
    current: InboxFold = None,
) -> OperatorInboxEntry:
    """Stamp the ladder's next rung (260707-HFX2-L4, R1/R2): re-anchors ``escalatedAt`` to
    ``now`` so the NEXT rung's SLA is measured from this transition, not the row's original
    creation. Distinct from :func:`mark_escalated` (HFX2-L2's reserved "this row is now
    escalatable" stamp, rung-agnostic) -- the ladder is the only caller of this function.

    Ruled invariant (developer, 2026-07-09): the ladder climbs by MUTATING this one row --
    with ``readdress_to`` the row itself moves to the next addressee (skip-level owner,
    then the developer attention queue). It never mints a sibling row; one root cause is one
    row for its whole ladder life. (The escalation storm that took the host down was every
    rung transition posting a new pending row whose own rungs posted more rows.)
    """
    entry = _require_entry(store, entry_id, current)
    update: dict[str, object] = {
        "ts": now,
        "rung": advance.rung,
        "escalatedAt": now,
        "rungTransitionAt": now,
    }
    if advance.readdress_to is not None:
        update.update(_readdress_fields(advance.readdress_to))
    advanced = entry.model_copy(update=update)
    store.append(advanced)
    return advanced


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
    if renewal.subject.leaf_key is not None:
        update["leafKey"] = renewal.subject.leaf_key
    if renewal.subject.seat_role is not None:
        update["seatRole"] = renewal.subject.seat_role
    if renewal.subject.agent_id is not None:
        update["subjectAgentId"] = renewal.subject.agent_id
    if renewal.readdress_to is not None:
        update.update(_readdress_fields(renewal.readdress_to))
    renewed = entry.model_copy(update=update)
    store.append(renewed)
    return renewed


def mark_ladder_resolved(
    store: OperatorInboxStore,
    entry_id: str,
    *,
    now: str,
    reason: str,
    current: InboxFold = None,
) -> tuple[OperatorInboxEntry, bool]:
    """Terminally resolve a ladder-complete row without treating it as an ack."""
    entry = _require_entry(store, entry_id, current)
    if is_ladder_resolved(entry):
        return entry, False
    resolved = entry.model_copy(
        update={
            "ts": now,
            "state": "ladder-resolved",
            "ladderResolvedAt": now,
            "ladderResolvedReason": reason,
            "nextAttemptAt": None,
        }
    )
    store.append(resolved)
    return resolved, True
