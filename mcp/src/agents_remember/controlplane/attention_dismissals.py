"""Lifecycle-scoped attention acknowledgements: ``ar-attention-dismissal/v1``.

Attention-queue items are derived from lifecycle/control-plane state on every
projection pass. A dismissal therefore records only the live acknowledgement
needed to hide one current occurrence until a newer signal arrives or the source
leaves the live set. This file is deliberately compacted in place: attention
items are disposable UI facts, not an audit trail.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

ATTENTION_DISMISSAL_SCHEMA = "ar-attention-dismissal/v1"


class AttentionDismissalRecord(BaseModel):
    """One ``ar-attention-dismissal/v1`` snapshot: the operator dismissed an item.

    ``itemId`` is the stable :class:`AttentionItem` id (the fold key); ``kind`` /
    ``lifecycleId`` / ``gateId`` are recorded for provenance and so the serving
    layer can pair a ``gate-open`` dismissal with the gate cancel it also performs.
    ``dismissedAt`` is the server wall-clock the reducer compares against the item's
    triggering-signal timestamp.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=ATTENTION_DISMISSAL_SCHEMA, alias="schema")
    itemId: str
    dismissedAt: str
    kind: str | None = None
    lifecycleId: str | None = None
    gateId: str | None = None


class AttentionDismissalStore:
    """Compact current acknowledgement set under the observer root."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self) -> Path:
        return self._root / "workspace" / "attention-dismissals.jsonl"

    def dismiss(self, record: AttentionDismissalRecord) -> None:
        """Upsert one current acknowledgement, replacing any previous same-item row."""
        records = self.current()
        records[record.itemId] = record
        self._replace(list(records.values()))

    def read(self) -> list[AttentionDismissalRecord]:
        """Read current acknowledgement rows (empty when absent)."""
        path = self.log_path()
        if not path.exists():
            return []
        return [
            AttentionDismissalRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def current(self) -> dict[str, AttentionDismissalRecord]:
        """Fold by ``itemId``; legacy duplicate rows collapse to the newest row read."""
        latest: dict[str, AttentionDismissalRecord] = {}
        for record in self.read():
            latest[record.itemId] = record
        return latest

    def prune_lifecycles(self, live_lifecycle_ids: set[str]) -> int:
        """Drop acknowledgements for missing/non-live lifecycles and compact duplicates."""
        records = self.read()
        kept = [
            record
            for record in self.current().values()
            if _keep_current_record(record, live_lifecycle_ids)
        ]
        if len(kept) == len(records):
            return 0
        self._replace(kept)
        return len(records) - len(kept)

    def _replace(self, records: list[AttentionDismissalRecord]) -> None:
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


def _keep_current_record(record: AttentionDismissalRecord, live_lifecycle_ids: set[str]) -> bool:
    if record.lifecycleId is not None:
        return record.lifecycleId in live_lifecycle_ids
    return record.kind == "actionable-drift"
