"""Durable cooldown records for supervisor-owned pane/liveness signals."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agents_remember.controlplane.inbox_backoff import require_redelivery_floor_seconds
from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxDeliveryState

SUPERVISOR_SIGNAL_SCHEMA = "ar-supervisor-signal/v1"
SupervisorSignalState = Literal["sent"]


class SupervisorSignalRecord(BaseModel):
    """One supervisor signal actually posted to an owner inbox."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SUPERVISOR_SIGNAL_SCHEMA, alias="schema")
    id: str
    ts: str
    state: SupervisorSignalState = "sent"
    targetAgentId: str | None = None
    targetLifecycleId: str | None = None
    targetRole: AgentRole | None = None
    leafKey: str | None = None
    seatRole: str | None = None
    findingKind: str
    detail: str
    deliveryState: InboxDeliveryState


@dataclass(frozen=True)
class SupervisorSignalTarget:
    """The owner inbox a supervisor signal is addressed to: the agent, the lifecycle it runs,
    the role it fills, and the leaf/seat it occupies. Derived as one routing decision by
    ``derive_signal_owner`` and stamped verbatim onto every :class:`SupervisorSignalRecord`."""

    agent_id: str | None = None
    lifecycle_id: str | None = None
    role: AgentRole | None = None
    leaf_key: str | None = None
    seat_role: str | None = None


@dataclass(frozen=True)
class SupervisorSignalKey:
    """What makes two supervisor signals "the same signal" for cooldown purposes: the same
    target told the same thing. Every field is compared, all of them or none -- a partial
    match is a different signal and must not suppress delivery."""

    target: SupervisorSignalTarget
    finding_kind: str
    detail: str


class SupervisorSignalCooldownStore:
    """Append-only signal log used as the supervisor's cross-sweep cooldown memory.

    260707-HFX2-L12 (CS-6 D2/D3): the cooldown check must NOT re-parse the whole
    log once per finding per sweep -- that is the L7 accidental-quadratic freeze
    reincarnated on the supervisor hot path (finding count F x log length L). The
    sweep reads the log **once** (via :meth:`compact`, which also reclaims it) and
    threads the resulting snapshot into every per-finding :meth:`in_cooldown` call
    through ``records=``; the store is therefore read at most once per sweep, and
    :meth:`compact` bounds it to one retention window of history on disk.
    """

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    def log_path(self) -> Path:
        return self._root / "workspace" / "supervisor-signals.jsonl"

    def read(self) -> list[SupervisorSignalRecord]:
        """Read the log back as validated records, skipping any unparseable line.

        A torn/legacy/version-skew line is a durability event, not a reason to
        freeze the supervisor sweep that folds this non-authoritative cooldown log
        (mirrors ``ProviderMetricsStore.read_recent`` tolerance).
        """
        path = self.log_path()
        if not path.exists():
            return []
        records: list[SupervisorSignalRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(SupervisorSignalRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def append(self, record: SupervisorSignalRecord) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json(by_alias=True, exclude_none=True) + "\n")

    def last_sent(
        self,
        signal: SupervisorSignalKey,
        *,
        records: list[SupervisorSignalRecord] | None = None,
    ) -> SupervisorSignalRecord | None:
        source = self.read() if records is None else records
        target = signal.target
        matches = [
            record
            for record in source
            if record.state == "sent"
            and record.targetAgentId == target.agent_id
            and record.targetLifecycleId == target.lifecycle_id
            and record.targetRole == target.role
            and record.leafKey == target.leaf_key
            and record.seatRole == target.seat_role
            and record.findingKind == signal.finding_kind
            and record.detail == signal.detail
        ]
        return max(matches, key=lambda record: record.ts, default=None)

    def in_cooldown(
        self,
        signal: SupervisorSignalKey,
        *,
        now: datetime,
        cooldown_seconds: float,
        records: list[SupervisorSignalRecord] | None = None,
    ) -> bool:
        """Whether an identical signal was sent within the cooldown window.

        Pass ``records`` (the sweep's one-read snapshot) so this stays O(1) per
        finding instead of a full-file re-parse; ``None`` falls back to a fresh
        read for the standalone / test path.
        """
        floor = require_redelivery_floor_seconds(
            cooldown_seconds, owner="supervisor signal cooldown"
        )
        previous = self.last_sent(signal, records=records)
        if previous is None:
            return False
        try:
            elapsed = (now - datetime.fromisoformat(previous.ts)).total_seconds()
        except ValueError:
            return False
        return elapsed < floor

    def compact(
        self, *, now: datetime, retain_seconds: float
    ) -> tuple[int, list[SupervisorSignalRecord]]:
        """Reclaim records older than the retention window; return ``(removed, kept)``.

        A record older than ``retain_seconds`` can never satisfy ``elapsed < floor``
        in :meth:`in_cooldown` (the floor is the same window), so dropping it is
        provenance-safe -- it can no longer suppress any signal. Records with an
        unparseable ``ts`` are always kept (never silently aged out). The rewrite is
        atomic (tmp + ``os.replace``); the observer server is the single writer.
        The returned ``kept`` list is the sweep's cooldown snapshot, so the caller
        reads + compacts the log in one pass.
        """
        records = self.read()
        if not records:
            return 0, []
        cutoff = now - timedelta(seconds=retain_seconds)
        kept: list[SupervisorSignalRecord] = []
        for record in records:
            try:
                ts = datetime.fromisoformat(record.ts)
            except ValueError:
                kept.append(record)
                continue
            try:
                fresh = ts >= cutoff
            except TypeError:
                # tz-naive vs tz-aware mismatch: keep rather than risk dropping a live row.
                kept.append(record)
                continue
            if fresh:
                kept.append(record)
        removed = len(records) - len(kept)
        if removed:
            self._replace(kept)
        return removed, kept

    def _replace(self, records: list[SupervisorSignalRecord]) -> None:
        path = self.log_path()
        if not records:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            "\n".join(
                record.model_dump_json(by_alias=True, exclude_none=True) for record in records
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
