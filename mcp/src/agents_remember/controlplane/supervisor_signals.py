"""Durable cooldown records for supervisor-owned pane/liveness signals."""

from __future__ import annotations

import os
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
        *,
        target_agent_id: str | None,
        target_lifecycle_id: str | None,
        target_role: AgentRole | None,
        leaf_key: str | None,
        finding_kind: str,
        detail: str,
        seat_role: str | None = None,
        records: list[SupervisorSignalRecord] | None = None,
    ) -> SupervisorSignalRecord | None:
        source = self.read() if records is None else records
        matches = [
            record
            for record in source
            if record.state == "sent"
            and record.targetAgentId == target_agent_id
            and record.targetLifecycleId == target_lifecycle_id
            and record.targetRole == target_role
            and record.leafKey == leaf_key
            and record.seatRole == seat_role
            and record.findingKind == finding_kind
            and record.detail == detail
        ]
        return max(matches, key=lambda record: record.ts, default=None)

    def in_cooldown(
        self,
        *,
        target_agent_id: str | None,
        target_lifecycle_id: str | None,
        target_role: AgentRole | None,
        leaf_key: str | None,
        finding_kind: str,
        detail: str,
        now: datetime,
        cooldown_seconds: float,
        seat_role: str | None = None,
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
        previous = self.last_sent(
            target_agent_id=target_agent_id,
            target_lifecycle_id=target_lifecycle_id,
            target_role=target_role,
            leaf_key=leaf_key,
            seat_role=seat_role,
            finding_kind=finding_kind,
            detail=detail,
            records=records,
        )
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
