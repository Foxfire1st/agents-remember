"""Append-only operator inbox store for external chat polling."""

from __future__ import annotations

from pathlib import Path

from agents_remember.controlplane.operator_inbox_records import (
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
    ) -> list[OperatorInboxEntry]:
        """Return pending entries matching all supplied mailbox keys."""
        require_inbox_address(lifecycle_id=lifecycle_id, agent_id=agent_id)
        entries = [
            record
            for record in self.current().values()
            if record.state == "pending"
            and (lifecycle_id is None or record.lifecycleId == lifecycle_id)
            and (agent_id is None or record.agentId == agent_id)
        ]
        return sorted(entries, key=lambda record: record.createdAt)

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
        if current.state == "consumed":
            return current, False
        consumed = consume_operator_inbox_entry(
            current,
            now=now,
            consumed_by=consumed_by,
            consumed_via=consumed_via,
        )
        self.append(consumed)
        return consumed, True
