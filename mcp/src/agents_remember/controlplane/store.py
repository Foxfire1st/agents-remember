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

import os
from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.interaction_retention import gate_keep_ids
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

    def find(self, gate_id: str) -> GateRecord | None:
        """Resolve one gate id across the workspace log and every lifecycle log.

        The seam-decide path: a deciding seat holds only the gate id (packet-carried);
        lifecycle ids stay server-side. Last-wins fold per log, first hit returned
        (gate ids are ULIDs — collisions across logs do not occur in practice).
        """
        hit = self.current(None).get(gate_id)
        if hit is not None:
            return hit
        lifecycles_dir = self._root / "lifecycles"
        if not lifecycles_dir.is_dir():
            return None
        for log in sorted(lifecycles_dir.glob("*/gates.jsonl")):
            hit = self.current(log.parent.name).get(gate_id)
            if hit is not None:
                return hit
        return None

    def current(self, lifecycle_id: str | None) -> dict[str, GateRecord]:
        """Fold the log by gate id, last-wins -- the live gate set."""
        latest: dict[str, GateRecord] = {}
        for record in self.read(lifecycle_id):
            latest[record.id] = record
        return latest

    def delete(self, gate_id: str, lifecycle_id: str | None) -> bool:
        """Physically remove one gate id from its log."""
        records = self.read(lifecycle_id)
        kept = [record for record in records if record.id != gate_id]
        if len(kept) == len(records):
            return False
        self._replace(lifecycle_id, kept)
        return True

    def compact(self, lifecycle_id: str | None, *, now: datetime) -> int:
        """Prune expired or consumed interaction records from one gate log."""
        records = self.read(lifecycle_id)
        if not records:
            return 0
        keep_ids = gate_keep_ids(records, now=now)
        kept = [record for record in records if record.id in keep_ids]
        if len(kept) == len(records):
            return 0
        self._replace(lifecycle_id, kept)
        return len(records) - len(kept)

    def lifecycle_ids(self) -> list[str | None]:
        """Lifecycle ids with gate logs, plus workspace when present."""
        ids: list[str | None] = []
        lifecycles = self._root / "lifecycles"
        if lifecycles.is_dir():
            ids.extend(
                entry.name
                for entry in sorted(lifecycles.iterdir())
                if entry.is_dir() and (entry / "gates.jsonl").is_file()
            )
        if self.log_path(None).is_file():
            ids.append(None)
        return ids

    def _replace(self, lifecycle_id: str | None, records: list[GateRecord]) -> None:
        path = self.log_path(lifecycle_id)
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
