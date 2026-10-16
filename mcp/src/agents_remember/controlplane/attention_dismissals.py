"""Current attention acknowledgements: ``ar-attention-dismissal/v1``.

Attention-queue items are derived from their native source facts on every
projection pass. A dismissal therefore records only the live acknowledgement
needed to hide one current occurrence until its findings change, it recovers or the source
leaves the live set. This file is deliberately compacted in place: attention
items are disposable UI facts, not an audit trail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field, ValidationError, field_validator

from agents_remember.controlplane.durable_store import (
    ATTENTION_DISMISSAL_OWNERSHIP,
    DurableRecord,
    exclusive_access,
    read_log_text,
    rewrite_lines,
)

logger = logging.getLogger(__name__)


ATTENTION_DISMISSAL_SCHEMA = "ar-attention-dismissal/v1"


class AttentionDismissalRecord(DurableRecord):
    """One disposable acknowledgement of a current attention occurrence."""

    schema_version: str = Field(default=ATTENTION_DISMISSAL_SCHEMA, alias="schema")
    itemId: str
    dismissedAt: str
    kind: str
    actionableFingerprint: str | None = None

    @field_validator("kind")
    @classmethod
    def supported_kind(cls, value: str) -> str:
        if value not in {"actionable-drift", "provider-down", "failed-setup", "blocked-start"}:
            raise ValueError("the attention kind is no longer published")
        return value

    @field_validator("dismissedAt")
    @classmethod
    def valid_stamp(cls, value: str) -> str:
        datetime.fromisoformat(value)
        return value


@dataclass
class AttentionObservation:
    """Proof from one actual source read, never from retained projection slots.

    Incomplete enumeration or an unreadable record invalidates the whole kind's
    removal proof. Healthy observations end an occurrence; mere absence of a bad
    signal does not. The read's start protects a click made while it was running.
    """

    kind: str
    observed_before: datetime
    complete: bool = True
    existing: set[str] = field(default_factory=set)
    healthy: set[str] = field(default_factory=set)
    fingerprints: dict[str, str] = field(default_factory=dict)

    def observe(
        self, item_id: str, *, healthy: bool = False, fingerprint: str | None = None
    ) -> None:
        self.existing.add(item_id)
        if healthy:
            self.healthy.add(item_id)
        if fingerprint:
            self.fingerprints[item_id] = fingerprint

    def covers(self, record: AttentionDismissalRecord) -> bool:
        stamp = datetime.fromisoformat(record.dismissedAt)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return self.complete and stamp <= self.observed_before

    def reclaims(self, record: AttentionDismissalRecord) -> bool:
        fingerprint = self.fingerprints.get(record.itemId)
        return self.covers(record) and (
            record.itemId not in self.existing
            or record.itemId in self.healthy
            or (
                fingerprint is not None
                and record.actionableFingerprint is not None
                and fingerprint != record.actionableFingerprint
            )
        )

    def adopt(self, record: AttentionDismissalRecord) -> AttentionDismissalRecord:
        """Preserve an existing acknowledgement through its first complete read."""
        fingerprint = self.fingerprints.get(record.itemId)
        if self.covers(record) and record.actionableFingerprint is None and fingerprint:
            return record.model_copy(update={"actionableFingerprint": fingerprint})
        return record


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

    def reconcile(
        self, observations: list[AttentionObservation]
    ) -> dict[str, AttentionDismissalRecord]:
        """Reclaim only occurrences proved ended/gone by this pass, under the write lock.

        A partial refresh supplies no observation for its unread kinds. A failed
        reader supplies an incomplete one. Neither can remove any row of that kind.
        Duplicate and malformed rows leave on the next required rewrite; an
        unchanged set performs no physical write.
        """
        ATTENTION_DISMISSAL_OWNERSHIP.check_declared_writer()
        by_kind = {observation.kind: observation for observation in observations}
        with exclusive_access(self.log_path(), ATTENTION_DISMISSAL_OWNERSHIP):
            records = self.read()
            current = {record.itemId: record for record in records}
            kept = {
                key: observation.adopt(record) if observation is not None else record
                for key, record in current.items()
                if (observation := by_kind.get(record.kind)) is None
                or not observation.reclaims(record)
            }
            if len(kept) != len(records) or kept != current:
                try:
                    self._replace(list(kept.values()))
                except OSError:
                    logger.warning("Attention dismissal cleanup did not hold", exc_info=True)
                    return current
            return kept

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
            # Our own prune never wakes our own projector (MIK-R81 rule 3, F07): the rewrite
            # records its own descriptor identity under the store lock, and a prune that
            # writes nothing records nothing.
            note_own=True,
        )
