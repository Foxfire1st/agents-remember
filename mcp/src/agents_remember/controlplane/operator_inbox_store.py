"""Append-only operator inbox store for external chat polling.

THE DECLARED LOCK EXCEPTION of leaf 260731-EFA-L5. Every other control-plane log was given a
single compaction owner so that no two processes read-modify-write it. This one cannot be:
both long-lived processes must physically REMOVE rows, not merely append them. The MCP deletes
the inbox rows tied to a cancelled gate at the moment it cancels the gate
(``application/gate_tools.py`` -> :meth:`delete_by_gate`), and the dashboard's supervisor sweep must
resolve and compact under one continuously held lock (:meth:`reconcile_and_compact`) so that a
consume which won the lock stays terminal. Moving either to the other process means moving the
decision it implements. So the advisory lock this store already had is kept, and it is the only
place in the control plane where locking -- rather than ownership -- is the mechanism. Its
filesystem is verified rather than assumed; see ``controlplane/durable_store.py``.

The six stamp/refresh transitions -- what one row's next snapshot SAYS -- live next door in
``controlplane/operator_inbox_transitions.py`` and call back through :meth:`current` and
:meth:`append`, so this module remains the only one that knows the inbox log has a lock.
Nothing else moved. All five operations that read this log and then write it --
:meth:`consume`, :meth:`delete`, :meth:`delete_by_gate`, :meth:`compact`,
:meth:`reconcile_and_compact` -- hold the lock across both halves, which is the property
the exception above exists for. ``durable_store.py`` enumerates the four that replace the
file; :meth:`consume` appends its result instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.durable_store import (
    OPERATOR_INBOX_OWNERSHIP,
    append_line,
    exclusive_access,
    read_log_text,
    rewrite_lines,
)
from agents_remember.controlplane.inbox_backoff import (
    DEFAULT_RATE_LIMIT_SECONDS,
    redeliverable,
)
from agents_remember.controlplane.interaction_retention import inbox_keep_ids
from agents_remember.controlplane.operator_inbox_records import (
    AgentRole,
    OperatorInboxEntry,
    OperatorInboxVia,
    consume_operator_inbox_entry,
    fold_operator_inbox_entries,
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
        OPERATOR_INBOX_OWNERSHIP.check_declared_writer()
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
        append_line(self.log_path(), record.model_dump_json(by_alias=True, exclude_none=True))

    def _read_unlocked(self) -> list[OperatorInboxEntry]:
        """STRICT, unchanged: an inbox row that cannot be parsed is an ack nobody can account
        for, and :meth:`consume` decides on this fold. The tolerant policy belongs to reads that
        only render."""
        return [
            OperatorInboxEntry.model_validate_json(line)
            for line in read_log_text(self.log_path()).splitlines()
            if line.strip()
        ]

    def _replace_unlocked(self, records: list[OperatorInboxEntry]) -> None:
        rewrite_lines(
            self.log_path(),
            [record.model_dump_json(by_alias=True, exclude_none=True) for record in records],
            OPERATOR_INBOX_OWNERSHIP,
        )

    @contextmanager
    def _exclusive_access(self) -> Iterator[None]:
        """Serialize append and physical compaction across dashboard and MCP processes."""
        with exclusive_access(self.log_path(), OPERATOR_INBOX_OWNERSHIP):
            yield
