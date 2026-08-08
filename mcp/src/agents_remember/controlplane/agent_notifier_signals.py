"""Durable cooldown records for agent-notifier-owned pane/liveness signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from agents_remember.controlplane.durable_store import (
    AGENT_NOTIFIER_SIGNAL_OWNERSHIP,
    DurableRecord,
    append_line,
    exclusive_access,
    read_log_text,
    rewrite_lines,
)
from agents_remember.controlplane.inbox_backoff import require_redelivery_floor_seconds
from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxDeliveryState

# The schema string and log filename are retained during the rename window (durable rows in
# existing deployments); the Python identifiers are the new names. Removal rides the schema
# migration that owns the cooldown log.
AGENT_NOTIFIER_SIGNAL_SCHEMA = "ar-supervisor-signal/v1"
AgentNotifierSignalState = Literal["sent"]


class AgentNotifierSignalRecord(DurableRecord):
    """One agent-notifier signal actually posted to an owner inbox."""

    schema_version: str = Field(default=AGENT_NOTIFIER_SIGNAL_SCHEMA, alias="schema")
    id: str
    ts: str
    state: AgentNotifierSignalState = "sent"
    targetAgentId: str | None = None
    targetLifecycleId: str | None = None
    targetRole: AgentRole | None = None
    leafKey: str | None = None
    seatRole: str | None = None
    findingKind: str
    detail: str
    deliveryState: InboxDeliveryState


@dataclass(frozen=True)
class AgentNotifierSignalTarget:
    """The owner inbox an agent-notifier signal is addressed to: the agent, the lifecycle it runs,
    the role it fills, and the leaf/seat it occupies. Derived as one routing decision by
    ``derive_signal_owner`` and stamped verbatim onto every :class:`AgentNotifierSignalRecord`."""

    agent_id: str | None = None
    lifecycle_id: str | None = None
    role: AgentRole | None = None
    leaf_key: str | None = None
    seat_role: str | None = None


@dataclass(frozen=True)
class AgentNotifierSignalKey:
    """What makes two agent-notifier signals "the same signal" for cooldown purposes: the same
    target told the same thing. Every field is compared, all of them or none -- a partial
    match is a different signal and must not suppress delivery."""

    target: AgentNotifierSignalTarget
    finding_kind: str
    detail: str


class AgentNotifierSignalCooldownStore:
    """Append-only signal log used as the agent-notifier's cross-sweep cooldown memory.

    260707-HFX2-L12 (CS-6 D2/D3): the cooldown check must NOT re-parse the whole
    log once per finding per sweep -- that is the L7 accidental-quadratic freeze
    reincarnated on the agent-notifier hot path (finding count F x log length L). The
    sweep reads the log **once** (via :meth:`compact`, which also reclaims it) and
    threads the resulting snapshot into every per-finding :meth:`in_cooldown` call
    through ``records=``; the store is therefore read at most once per sweep, and
    :meth:`compact` bounds it to one retention window of history on disk.
    """

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    def log_path(self) -> Path:
        # Retained legacy artifact name during the rename window; removal rides the schema
        # migration that owns the cooldown log.
        return self._root / "workspace" / "supervisor-signals.jsonl"

    def read(self) -> list[AgentNotifierSignalRecord]:
        """Read the log back as validated records, skipping any unparseable line.

        A torn/legacy/version-skew line is a durability event, not a reason to
        freeze the agent-notifier sweep that folds this non-authoritative cooldown log
        (mirrors ``ProviderMetricsStore.read_recent`` tolerance).
        """
        records: list[AgentNotifierSignalRecord] = []
        for line in read_log_text(self.log_path()).splitlines():
            if not line.strip():
                continue
            try:
                records.append(AgentNotifierSignalRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def append(self, record: AgentNotifierSignalRecord) -> None:
        AGENT_NOTIFIER_SIGNAL_OWNERSHIP.check_declared_writer()
        path = self.log_path()
        with exclusive_access(path, AGENT_NOTIFIER_SIGNAL_OWNERSHIP):
            append_line(path, record.model_dump_json(by_alias=True, exclude_none=True))

    def last_sent(
        self,
        signal: AgentNotifierSignalKey,
        *,
        records: list[AgentNotifierSignalRecord] | None = None,
    ) -> AgentNotifierSignalRecord | None:
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
        signal: AgentNotifierSignalKey,
        *,
        now: datetime,
        cooldown_seconds: float,
        records: list[AgentNotifierSignalRecord] | None = None,
    ) -> bool:
        """Whether an identical signal was sent within the cooldown window.

        Pass ``records`` (the sweep's one-read snapshot) so this stays O(1) per
        finding instead of a full-file re-parse; ``None`` falls back to a fresh
        read for the standalone / test path.
        """
        floor = require_redelivery_floor_seconds(
            cooldown_seconds, owner="agent-notifier signal cooldown"
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
    ) -> tuple[int, list[AgentNotifierSignalRecord]]:
        """Reclaim records older than the retention window; return ``(removed, kept)``.

        A record older than ``retain_seconds`` can never satisfy ``elapsed < floor``
        in :meth:`in_cooldown` (the floor is the same window), so dropping it is
        provenance-safe -- it can no longer suppress any signal. Records with an
        unparseable ``ts`` are always kept (never silently aged out). The rewrite is atomic
        and single-owner: the dashboard's agent-notifier sweep is the only process that appends to
        or reclaims this log (``AGENT_NOTIFIER_SIGNAL_OWNERSHIP``). It is LOCKED all the same, and
        the read is held under the same lock as the rewrite. "One process writes this file" is a
        deployment fact, not a structural one; a draft of this leaf left this store unlocked on
        exactly that reasoning and the proof run measured 31.45% loss on its twin
        (attention-dismissals), whose single-writer claim was just as true.
        The returned ``kept`` list is the sweep's cooldown snapshot, so the caller
        reads + compacts the log in one pass.
        """
        with exclusive_access(self.log_path(), AGENT_NOTIFIER_SIGNAL_OWNERSHIP):
            return self._compact_locked(now=now, retain_seconds=retain_seconds)

    def _compact_locked(
        self, *, now: datetime, retain_seconds: float
    ) -> tuple[int, list[AgentNotifierSignalRecord]]:
        """The read-filter-rewrite half of :meth:`compact`, with the log's lock held."""
        records = self.read()
        if not records:
            return 0, []
        cutoff = now - timedelta(seconds=retain_seconds)
        kept: list[AgentNotifierSignalRecord] = []
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

    def _replace(self, records: list[AgentNotifierSignalRecord]) -> None:
        """Rewrite the log. Its lock -- held by the caller across the read too -- is what makes
        this safe, and it is the ONLY thing checked: ``rewrite_lines`` verifies the lock is held
        and nothing else. No owner check happens here or anywhere below here;
        ``AGENT_NOTIFIER_SIGNAL_OWNERSHIP`` is passed so a refusal can name the store."""
        rewrite_lines(
            self.log_path(),
            [record.model_dump_json(by_alias=True, exclude_none=True) for record in records],
            AGENT_NOTIFIER_SIGNAL_OWNERSHIP,
        )
