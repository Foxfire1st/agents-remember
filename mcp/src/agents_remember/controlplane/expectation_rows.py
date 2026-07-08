"""R2 (260707-HFX2-L1): durable what-must-happen-by-when rows, written atomically at dispatch.

Every dispatch surface -- ``spawn_agent_session`` (briefed-by, and turn-report-by when the spawn
claims a leaf), a gate opening (verdict-by), and an operator-inbox post (ack-by) -- writes one
durable :class:`ExpectationRow` in the SAME call that performs the dispatch, never as a
follow-up step a caller could forget. Deadlines are ROWS an L2 sweep scans, never in-memory
timers -- the Restate durable-timer lesson (R2): a row survives a daemon/MCP restart; a timer
does not.

``ExpectationKind`` must be kept in sync with ``KNOWN_EXPECTATION_KINDS`` in
``kernel/agentic_settings.py`` (duplicated there to avoid a kernel<->controlplane import cycle).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EXPECTATION_ROW_SCHEMA = "ar-expectation-row/v1"

ExpectationKind = Literal["briefed-by", "turn-report-by", "verdict-by", "ack-by"]
ExpectationState = Literal["pending", "met", "missed"]


class ExpectationRow(BaseModel):
    """One append-only ``ar-expectation-row/v1`` snapshot: what must happen, by when."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=EXPECTATION_ROW_SCHEMA, alias="schema")
    id: str
    ts: str
    kind: ExpectationKind
    state: ExpectationState
    createdAt: str
    dueAt: str
    # The dispatch surface's own id this row rides beside -- a spawned session id (briefed-by /
    # turn-report-by), a gate id (verdict-by), or an operator-inbox entry id (ack-by). Lets a
    # sweep or a dashboard resolve straight back to the thing it is a deadline FOR.
    sourceId: str
    subjectAgentId: str | None = None
    subjectLifecycleId: str | None = None
    leafKey: str | None = None
    note: str | None = None
    metAt: str | None = None
    missedAt: str | None = None


def create_expectation_row(
    *,
    row_id: str,
    now: str,
    kind: ExpectationKind,
    due_at: str,
    source_id: str,
    subject_agent_id: str | None = None,
    subject_lifecycle_id: str | None = None,
    leaf_key: str | None = None,
    note: str | None = None,
) -> ExpectationRow:
    """Create a pending expectation row. Pure: the caller mints ``row_id``/``now``/``due_at``."""
    return ExpectationRow(
        id=row_id,
        ts=now,
        kind=kind,
        state="pending",
        createdAt=now,
        dueAt=due_at,
        sourceId=source_id,
        subjectAgentId=subject_agent_id,
        subjectLifecycleId=subject_lifecycle_id,
        leafKey=leaf_key,
        note=note,
    )


def due_at_from_sla(*, now: datetime, sla_seconds: float) -> str:
    """The durable ``dueAt`` timestamp: ``now`` + the kind's configured SLA."""
    return (now + timedelta(seconds=sla_seconds)).isoformat()


def mark_met(row: ExpectationRow, *, now: str) -> ExpectationRow:
    """Return a snapshot marking the expectation fulfilled (idempotent past the first call)."""
    if row.state != "pending":
        return row
    return row.model_copy(update={"ts": now, "state": "met", "metAt": now})


def mark_missed(row: ExpectationRow, *, now: str) -> ExpectationRow:
    """Return a snapshot marking the expectation missed (idempotent past the first call).

    This leaf only reserves the transition -- the L2 sweep is the actual caller.
    """
    if row.state != "pending":
        return row
    return row.model_copy(update={"ts": now, "state": "missed", "missedAt": now})


class ExpectationRowStore:
    """Append-only expectation-row log, folded by id -- same shape as ``OperatorInboxStore``."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    def log_path(self) -> Path:
        return self._root / "workspace" / "expectation-rows.jsonl"

    def append(self, row: ExpectationRow) -> None:
        path = self.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(row.model_dump_json(by_alias=True, exclude_none=True) + "\n")

    def read(self) -> list[ExpectationRow]:
        path = self.log_path()
        if not path.exists():
            return []
        return [
            ExpectationRow.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def current(self) -> dict[str, ExpectationRow]:
        """Fold the log by row id, last-wins."""
        latest: dict[str, ExpectationRow] = {}
        for row in self.read():
            latest[row.id] = row
        return latest

    def pending(self) -> list[ExpectationRow]:
        return sorted(
            (row for row in self.current().values() if row.state == "pending"),
            key=lambda row: row.dueAt,
        )

    def find_by_source(self, source_id: str, *, kind: ExpectationKind | None = None) -> ExpectationRow | None:
        """The most recent pending row for ``source_id`` (optionally kind-filtered) -- the
        write-once-consume-once lookup a dispatch surface's fulfillment path uses."""
        candidates = [
            row
            for row in self.pending()
            if row.sourceId == source_id and (kind is None or row.kind == kind)
        ]
        return max(candidates, key=lambda row: row.createdAt, default=None)

    def overdue(self, *, now: datetime) -> list[ExpectationRow]:
        """Pending rows whose ``dueAt`` has already passed -- the L2 sweep's predicate input."""
        due: list[ExpectationRow] = []
        for row in self.pending():
            try:
                due_at = datetime.fromisoformat(row.dueAt)
            except ValueError:
                continue
            if now >= due_at:
                due.append(row)
        return due

    def mark_met(self, row_id: str, *, now: str) -> ExpectationRow:
        current = self.current().get(row_id)
        if current is None:
            raise KeyError(f"no expectation row {row_id!r}")
        met = mark_met(current, now=now)
        if met is not current:
            self.append(met)
        return met

    def mark_missed(self, row_id: str, *, now: str) -> ExpectationRow:
        current = self.current().get(row_id)
        if current is None:
            raise KeyError(f"no expectation row {row_id!r}")
        missed = mark_missed(current, now=now)
        if missed is not current:
            self.append(missed)
        return missed

    def _replace(self, rows: list[ExpectationRow]) -> None:
        path = self.log_path()
        if not rows:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            "\n".join(row.model_dump_json(by_alias=True, exclude_none=True) for row in rows) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)


def write_expectation_row(
    store: ExpectationRowStore,
    *,
    row_id: str,
    now: datetime,
    kind: ExpectationKind,
    sla_seconds: float,
    source_id: str,
    subject_agent_id: str | None = None,
    subject_lifecycle_id: str | None = None,
    leaf_key: str | None = None,
    note: str | None = None,
) -> ExpectationRow:
    """Create + append one expectation row in one call -- the atomic-write-at-dispatch helper
    every dispatch surface calls (spawn / gate-open / inbox-post), so the row is never a
    forgettable follow-up step."""
    now_text = now.isoformat()
    row = create_expectation_row(
        row_id=row_id,
        now=now_text,
        kind=kind,
        due_at=due_at_from_sla(now=now, sla_seconds=sla_seconds),
        source_id=source_id,
        subject_agent_id=subject_agent_id,
        subject_lifecycle_id=subject_lifecycle_id,
        leaf_key=leaf_key,
        note=note,
    )
    store.append(row)
    return row
