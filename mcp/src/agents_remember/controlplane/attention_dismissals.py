"""Lifecycle-scoped attention acknowledgements: ``ar-attention-dismissal/v1``.

Attention-queue items are derived from lifecycle/control-plane state on every
projection pass. A dismissal therefore records only the live acknowledgement
needed to hide one current occurrence until a newer signal arrives or the source
leaves the live set. This file is deliberately compacted in place: attention
items are disposable UI facts, not an audit trail.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, ValidationError

from agents_remember.controlplane.durable_store import (
    ATTENTION_DISMISSAL_OWNERSHIP,
    DurableRecord,
    exclusive_access,
    read_log_text,
    rewrite_lines,
)

ATTENTION_DISMISSAL_SCHEMA = "ar-attention-dismissal/v1"


class AttentionDismissalRecord(DurableRecord):
    """One ``ar-attention-dismissal/v1`` snapshot: the operator dismissed an item.

    ``itemId`` is the stable :class:`AttentionItem` id (the fold key); ``kind`` /
    ``lifecycleId`` / ``gateId`` are recorded for provenance and so the serving
    layer can pair a ``gate-open`` dismissal with the gate cancel it also performs.
    ``dismissedAt`` is the server wall-clock the reducer compares against the item's
    triggering-signal timestamp.
    """

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
        """Upsert one current acknowledgement, replacing any previous same-item row.

        A whole-file read-modify-write rather than an append -- this store keeps only the live
        acknowledgement set. Because there is no append, this IS the store's write entry point,
        so it carries the declared-writer check the other five stores put on ``append``: the
        ``writers=("dashboard",)`` claim is checkable here rather than merely asserted in
        ``ATTENTION_DISMISSAL_OWNERSHIP``. Advisory, as everywhere -- it is silent in any process
        that declared no role, and the lock below is what makes the rewrite safe.

        The rewrite itself is driven by the TOLERANT :meth:`read`, so an unparseable row is
        dropped permanently rather than for one tick. That is deliberate and is only acceptable
        because this log carries no authority; see the read-policy section of
        ``controlplane/durable_store.py`` for what would have to change if it ever did.
        """
        ATTENTION_DISMISSAL_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.log_path(), ATTENTION_DISMISSAL_OWNERSHIP):
            records = self.current()
            records[record.itemId] = record
            self._replace(list(records.values()))

    def read(self) -> list[AttentionDismissalRecord]:
        """Read current acknowledgement rows (empty when absent).

        260707-HFX2-L12 F12: a torn/legacy line is skipped, not raised — this store is read on the
        dashboard projection/ASGI path, and a single malformed row must not 500 the endpoint or
        freeze a tick (these are disposable UI facts, not an audit trail)."""
        records: list[AttentionDismissalRecord] = []
        for line in read_log_text(self.log_path()).splitlines():
            if not line.strip():
                continue
            try:
                records.append(AttentionDismissalRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def current(self) -> dict[str, AttentionDismissalRecord]:
        """Fold by ``itemId``; legacy duplicate rows collapse to the newest row read."""
        latest: dict[str, AttentionDismissalRecord] = {}
        for record in self.read():
            latest[record.itemId] = record
        return latest

    def prune_lifecycles(self, live_lifecycle_ids: set[str]) -> int:
        """Drop acknowledgements for missing/non-live lifecycles and compact duplicates.

        The store's second write entry point, so it carries the declared-writer check too: both
        of this log's writes are whole-file rewrites, and guarding only one of them would leave
        half the ``writers`` claim unchecked.
        """
        ATTENTION_DISMISSAL_OWNERSHIP.check_declared_writer()
        with exclusive_access(self.log_path(), ATTENTION_DISMISSAL_OWNERSHIP):
            return self._prune_locked(live_lifecycle_ids)

    def _prune_locked(self, live_lifecycle_ids: set[str]) -> int:
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
        """Rewrite the log. Its lock -- held by the caller across the read too -- is what makes
        this safe, and it is the ONLY thing checked: ``rewrite_lines`` verifies the lock is held
        and nothing else. No owner check happens here or anywhere below here;
        ``ATTENTION_DISMISSAL_OWNERSHIP`` is passed so a refusal can name the store. The writer
        check this store does make is on its two public write entry points, not here."""
        rewrite_lines(
            self.log_path(),
            [record.model_dump_json(by_alias=True, exclude_none=True) for record in records],
            ATTENTION_DISMISSAL_OWNERSHIP,
        )


def _keep_current_record(record: AttentionDismissalRecord, live_lifecycle_ids: set[str]) -> bool:
    if record.lifecycleId is not None:
        return record.lifecycleId in live_lifecycle_ids
    return record.kind == "actionable-drift"
