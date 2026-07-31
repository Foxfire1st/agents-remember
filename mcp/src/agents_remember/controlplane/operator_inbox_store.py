"""Append-only operator inbox store for external chat polling."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.inbox_backoff import (
    DEFAULT_RATE_LIMIT_SECONDS,
    is_ladder_resolved,
    next_attempt_at,
    redeliverable,
)
from agents_remember.controlplane.interaction_retention import inbox_keep_ids
from agents_remember.controlplane.operator_inbox_records import (
    AdapterDeliveryState,
    AgentRole,
    InboxDeliveryState,
    InboxOwner,
    InboxSubject,
    OperatorInboxEntry,
    OperatorInboxVia,
    consume_operator_inbox_entry,
    fold_operator_inbox_entries,
    require_inbox_address,
)


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


class OperatorInboxStore:
    """Store operator responses in one workspace inbox log and filter by mailbox key."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self) -> Path:
        """The shared inbox log for entries addressable by lifecycle and/or agent id."""
        return self._root / "workspace" / "operator-inbox.jsonl"

    def append(self, record: OperatorInboxEntry) -> None:
        """Append one inbox snapshot, creating parent dirs on first write."""
        with self._exclusive_access():
            self._append_unlocked(record)

    def read(self) -> list[OperatorInboxEntry]:
        """Read the inbox log back as validated snapshots (empty when absent)."""
        with self._exclusive_access():
            return self._read_unlocked()

    def current(self) -> dict[str, OperatorInboxEntry]:
        """Fold the inbox by entry id, with terminal snapshots dominating stale pending ones."""
        return fold_operator_inbox_entries(self.read())

    def list_pending(
        self,
        *,
        lifecycle_id: str | None,
        agent_id: str | None,
        recipient_role: AgentRole | None = None,
    ) -> list[OperatorInboxEntry]:
        """Return pending entries matching all supplied mailbox keys."""
        require_inbox_address(
            lifecycle_id=lifecycle_id,
            agent_id=agent_id,
            recipient_role=recipient_role,
        )
        entries = [
            record
            for record in self.current().values()
            if record.state == "pending"
            and (lifecycle_id is None or record.lifecycleId == lifecycle_id)
            and (agent_id is None or record.agentId == agent_id)
            and (recipient_role is None or record.recipientRole == recipient_role)
        ]
        return sorted(entries, key=lambda record: record.createdAt)

    def record_delivery(
        self,
        entry_id: str,
        attempt: DeliveryAttempt,
        *,
        now: str,
        current: dict[str, OperatorInboxEntry] | None = None,
        redelivery_floor_seconds: float | None = None,
    ) -> OperatorInboxEntry:
        """Append a delivery-status snapshot for one pending entry.

        R1/R3: every attempt -- including a confirmed ``delivered`` paste -- bumps
        ``attemptCount``, stamps ``lastAttemptAt``, and schedules ``nextAttemptAt`` from the
        backoff ladder. 'delivered' is never terminal (pasted != perceived), so only ``consume``
        clears the redelivery schedule; a still-pending entry always carries a durable next-attempt
        row L2 can sweep, restart-proof.
        """
        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
        delivery_state = attempt.delivery_state
        adapter = attempt.adapter
        attempt_count = entry.attemptCount + 1
        delivered = entry.model_copy(
            update={
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
                "nextAttemptAt": (
                    next_attempt_at(
                        now=datetime.fromisoformat(now),
                        attempt_count=attempt_count,
                        redelivery_floor_seconds=redelivery_floor_seconds,
                    )
                    if entry.state == "pending"
                    else entry.nextAttemptAt
                ),
            }
        )
        self.append(delivered)
        return delivered

    def record_adapter_completion(
        self,
        entry_id: str,
        *,
        now: str,
        vendor_correlation_id: str | None = None,
        detail: str | None = None,
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> OperatorInboxEntry:
        """Persist terminal adapter evidence without consuming the durable inbox row."""

        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
        completed = entry.model_copy(
            update={
                "ts": now,
                "adapterDeliveryState": "completed",
                "adapterVendorCorrelationId": (
                    vendor_correlation_id or entry.adapterVendorCorrelationId
                ),
                "adapterCompletedAt": now,
                "adapterDeliveryDetail": detail,
            }
        )
        self.append(completed)
        return completed

    def list_redeliverable(
        self,
        *,
        now: datetime,
        rate_limit_seconds: float | None = None,
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> list[OperatorInboxEntry]:
        """Pending rows past their backoff window and clear of the per-target rate limit (R3).

        The pure selection L2's sweep drives redelivery from; this store never redelivers on its
        own (no in-memory timer -- the sweep is the only caller of ``deliver_inbox_entry`` again).
        """
        entries = self.current() if current is None else current
        pending = [entry for entry in entries.values() if entry.state == "pending"]
        return redeliverable(
            pending,
            now=now,
            rate_limit_seconds=(
                rate_limit_seconds if rate_limit_seconds is not None else DEFAULT_RATE_LIMIT_SECONDS
            ),
        )

    def mark_escalated(
        self,
        entry_id: str,
        *,
        now: str,
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> OperatorInboxEntry:
        """Stamp ``escalatedAt`` once the ladder (HFX2-L4) escalates an unacked row.

        This leaf only reserves the field -- it never calls this itself; redelivery keeps running
        until either ack or this mark, per R3.
        """
        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
        escalated = entry.model_copy(update={"ts": now, "escalatedAt": now})
        self.append(escalated)
        return escalated

    def advance_rung(
        self,
        entry_id: str,
        *,
        rung: int,
        now: str,
        readdress_to: InboxOwner | None = None,
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> OperatorInboxEntry:
        """Stamp the ladder's next rung (260707-HFX2-L4, R1/R2): re-anchors ``escalatedAt`` to
        ``now`` so the NEXT rung's SLA is measured from this transition, not the row's original
        creation. Distinct from :meth:`mark_escalated` (HFX2-L2's reserved "this row is now
        escalatable" stamp, rung-agnostic) -- the ladder is the only caller of this method.

        Ruled invariant (developer, 2026-07-09): the ladder climbs by MUTATING this one row --
        with ``readdress=True`` the row itself moves to the next addressee (skip-level owner,
        then the developer attention queue). It never mints a sibling row; one root cause is one
        row for its whole ladder life. (The escalation storm that took the host down was every
        rung transition posting a new pending row whose own rungs posted more rows.)
        """
        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
        update: dict[str, object] = {
            "ts": now,
            "rung": rung,
            "escalatedAt": now,
            "rungTransitionAt": now,
        }
        if readdress_to is not None:
            update.update(_readdress_fields(readdress_to))
        advanced = entry.model_copy(update=update)
        self.append(advanced)
        return advanced

    def renew(
        self,
        entry_id: str,
        renewal: InboxRenewal,
        *,
        now: str,
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> OperatorInboxEntry:
        """Refresh one still-pending row in place: same id, bumped ``ts``, optionally refreshed
        ``response``. The ruled coalescing primitive (developer, 2026-07-09): a condition that
        re-fires updates its ONE existing row's date/detail instead of appending a duplicate --
        there is zero reason to repeat the same message until the system catches fire."""
        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
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
        self.append(renewed)
        return renewed

    def mark_ladder_resolved(
        self,
        entry_id: str,
        *,
        now: str,
        reason: str,
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> tuple[OperatorInboxEntry, bool]:
        """Terminally resolve a ladder-complete row without treating it as an ack."""
        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
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
        self.append(resolved)
        return resolved, True

    def consume(
        self,
        entry_id: str,
        *,
        now: str,
        consumed_by: str,
        consumed_via: OperatorInboxVia,
    ) -> tuple[OperatorInboxEntry, bool]:
        """Mark an entry consumed. Returns ``(entry, consumed_now)``."""
        with self._exclusive_access():
            current = fold_operator_inbox_entries(self._read_unlocked()).get(entry_id)
            if current is None:
                raise KeyError(f"no operator inbox entry {entry_id!r}")
            if current.state != "pending":
                return current, False
            consumed = consume_operator_inbox_entry(
                current,
                now=now,
                consumed_by=consumed_by,
                consumed_via=consumed_via,
            )
            self._append_unlocked(consumed)
            return consumed, True

    def delete(self, entry_id: str) -> bool:
        """Physically remove one inbox entry id from the shared inbox log."""
        with self._exclusive_access():
            records = self._read_unlocked()
            kept = [record for record in records if record.id != entry_id]
            if len(kept) == len(records):
                return False
            self._replace_unlocked(kept)
            return True

    def delete_by_gate(self, gate_id: str) -> int:
        """Physically remove pending or historical entries tied to one gate."""
        with self._exclusive_access():
            records = self._read_unlocked()
            kept = [record for record in records if record.gateId != gate_id]
            if len(kept) == len(records):
                return 0
            self._replace_unlocked(kept)
            return len(records) - len(kept)

    def compact(self, *, now: datetime) -> int:
        """Prune consumed or expired interaction entries from the inbox log."""
        with self._exclusive_access():
            records = self._read_unlocked()
            if not records:
                return 0
            keep_ids = inbox_keep_ids(records, now=now)
            kept = [record for record in records if record.id in keep_ids]
            if len(kept) == len(records):
                return 0
            self._replace_unlocked(kept)
            return len(records) - len(kept)

    def reconcile_and_compact(
        self,
        *,
        now: datetime,
        reconcile: Callable[[dict[str, OperatorInboxEntry]], Mapping[str, str]],
    ) -> tuple[int, dict[str, OperatorInboxEntry], tuple[OperatorInboxEntry, ...]]:
        """Fold once, terminally resolve a reviewed subset, then compact under one file lock.

        The resolver receives the authoritative folded snapshot while inbox writers are blocked.
        A consume that won the lock first is therefore terminal and cannot be overwritten; stale
        pending snapshots already in the log remain subordinate to the terminal fold.
        """
        with self._exclusive_access():
            records = self._read_unlocked()
            current = fold_operator_inbox_entries(records)
            persisted_ids_before = set(current)
            resolved: list[OperatorInboxEntry] = []
            for entry_id, reason in reconcile(dict(current)).items():
                entry = current.get(entry_id)
                if entry is None or entry.state != "pending":
                    continue
                terminal = entry.model_copy(
                    update={
                        "ts": now.isoformat(),
                        "state": "ladder-resolved",
                        "ladderResolvedAt": now.isoformat(),
                        "ladderResolvedReason": reason,
                        "nextAttemptAt": None,
                    }
                )
                records.append(terminal)
                current[entry_id] = terminal
                resolved.append(terminal)
            keep_ids = inbox_keep_ids(records, now=now, current=current)
            kept_records = [record for record in records if record.id in keep_ids]
            kept_current = {
                entry_id: entry for entry_id, entry in current.items() if entry_id in keep_ids
            }
            removed = len(persisted_ids_before - set(kept_current))
            if removed:
                self._replace_unlocked(kept_records)
            return removed, kept_current, tuple(resolved)

    def _append_unlocked(self, record: OperatorInboxEntry) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json(by_alias=True, exclude_none=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _read_unlocked(self) -> list[OperatorInboxEntry]:
        path = self.log_path()
        if not path.exists():
            return []
        return [
            OperatorInboxEntry.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _replace_unlocked(self, records: list[OperatorInboxEntry]) -> None:
        path = self.log_path()
        if not records:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(
            "\n".join(
                record.model_dump_json(by_alias=True, exclude_none=True) for record in records
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    @contextmanager
    def _exclusive_access(self) -> Iterator[None]:
        """Serialize append and physical compaction across dashboard and MCP processes."""
        lock_path = self.log_path().with_name("operator-inbox.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _entry_from_current(
        self,
        entry_id: str,
        current: dict[str, OperatorInboxEntry] | None,
    ) -> OperatorInboxEntry | None:
        entries = self.current() if current is None else current
        return entries.get(entry_id)
