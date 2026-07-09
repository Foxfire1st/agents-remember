"""Append-only operator inbox store for external chat polling."""

from __future__ import annotations

import os
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
    AgentRole,
    InboxDeliveryState,
    OperatorInboxEntry,
    OperatorInboxVia,
    consume_operator_inbox_entry,
    require_inbox_address,
)


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
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json(by_alias=True, exclude_none=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self) -> list[OperatorInboxEntry]:
        """Read the inbox log back as validated snapshots (empty when absent)."""
        path = self.log_path()
        if not path.exists():
            return []
        return [
            OperatorInboxEntry.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def current(self) -> dict[str, OperatorInboxEntry]:
        """Fold the inbox by entry id, last-wins."""
        latest: dict[str, OperatorInboxEntry] = {}
        for record in self.read():
            latest[record.id] = record
        return latest

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
        *,
        now: str,
        delivery_state: InboxDeliveryState,
        delivered_to_session: str | None = None,
        delivery_detail: str | None = None,
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
        attempt_count = entry.attemptCount + 1
        delivered = entry.model_copy(
            update={
                "ts": now,
                "deliveryState": delivery_state,
                "deliveredAt": now if delivery_state == "delivered" else entry.deliveredAt,
                "deliveredToSession": delivered_to_session,
                "deliveryDetail": delivery_detail,
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
        current: dict[str, OperatorInboxEntry] | None = None,
    ) -> OperatorInboxEntry:
        """Stamp the ladder's next rung (260707-HFX2-L4, R1/R2): re-anchors ``escalatedAt`` to
        ``now`` so the NEXT rung's SLA is measured from this transition, not the row's original
        creation. Distinct from :meth:`mark_escalated` (HFX2-L2's reserved "this row is now
        escalatable" stamp, rung-agnostic) -- the ladder is the only caller of this method.
        """
        entry = self._entry_from_current(entry_id, current)
        if entry is None:
            raise KeyError(f"no operator inbox entry {entry_id!r}")
        advanced = entry.model_copy(update={"ts": now, "rung": rung, "escalatedAt": now})
        self.append(advanced)
        return advanced

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
        current = self.current().get(entry_id)
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
        self.append(consumed)
        return consumed, True

    def delete(self, entry_id: str) -> bool:
        """Physically remove one inbox entry id from the shared inbox log."""
        records = self.read()
        kept = [record for record in records if record.id != entry_id]
        if len(kept) == len(records):
            return False
        self._replace(kept)
        return True

    def delete_by_gate(self, gate_id: str) -> int:
        """Physically remove pending or historical entries tied to one gate."""
        records = self.read()
        kept = [record for record in records if record.gateId != gate_id]
        if len(kept) == len(records):
            return 0
        self._replace(kept)
        return len(records) - len(kept)

    def compact(self, *, now: datetime) -> int:
        """Prune consumed or expired interaction entries from the inbox log."""
        records = self.read()
        if not records:
            return 0
        keep_ids = inbox_keep_ids(records, now=now)
        kept = [record for record in records if record.id in keep_ids]
        if len(kept) == len(records):
            return 0
        self._replace(kept)
        return len(records) - len(kept)

    def _replace(self, records: list[OperatorInboxEntry]) -> None:
        path = self.log_path()
        if not records:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(
            "\n".join(
                record.model_dump_json(by_alias=True, exclude_none=True)
                for record in records
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _entry_from_current(
        self,
        entry_id: str,
        current: dict[str, OperatorInboxEntry] | None,
    ) -> OperatorInboxEntry | None:
        entries = self.current() if current is None else current
        return entries.get(entry_id)
