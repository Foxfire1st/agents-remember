"""Append-only gate store, co-located with the observer event substrate.

Gate snapshots live in ``<observer_root>/lifecycles/<lifecycle-id>/gates.jsonl``
beside that lifecycle's ``events.jsonl``; lifecycle-less gates go to
``<observer_root>/workspace/gates.jsonl``. Append-only and history-preserving:
:meth:`current` folds the log by gate id (last snapshot wins) into the live gate
set, so a gate's whole history stays on disk and the current state is a pure
read. One writer per file in practice (a lifecycle is owned by one live
session), the same single-writer assumption the event store makes.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.controlplane.records import GateRecord


class GateStore:
    """Resolve per-lifecycle / workspace gate log paths and append snapshots."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self, lifecycle_id: str | None) -> Path:
        """The gate log a record routes to (workspace when lifecycle-less)."""
        if lifecycle_id:
            return self._root / "lifecycles" / lifecycle_id / "gates.jsonl"
        return self._root / "workspace" / "gates.jsonl"

    def append(self, record: GateRecord) -> None:
        """Append one snapshot to its log, creating parent dirs on first write."""
        path = self.log_path(record.lifecycleId)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json(by_alias=True, exclude_none=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self, lifecycle_id: str | None) -> list[GateRecord]:
        """Read a gate log back as validated snapshots (empty when absent)."""
        path = self.log_path(lifecycle_id)
        if not path.exists():
            return []
        return [
            GateRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def current(self, lifecycle_id: str | None) -> dict[str, GateRecord]:
        """Fold the log by gate id, last-wins -- the live gate set."""
        latest: dict[str, GateRecord] = {}
        for record in self.read(lifecycle_id):
            latest[record.id] = record
        return latest
