"""Durable scheduling data for recipients whose waiting messages have a known reset."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.kernel.primitives.checkout_coordination import require_durable_write_target

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


@dataclass(frozen=True, slots=True)
class ResetTarget:
    agent_id: str
    receipt_path: Path
    workspace_path: Path
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class ResetEntry:
    target: ResetTarget
    generation: str


class HeldResetIndex:
    """One replaceable record per waiting recipient, ordered by its next eligible attempt."""

    def __init__(self, coordination_root: Path) -> None:
        self.path = coordination_root / "temp" / "held-resets.sqlite"

    @contextmanager
    def _writer(self) -> Iterator[sqlite3.Connection]:
        require_durable_write_target(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS due (
                    agent TEXT PRIMARY KEY, receipt TEXT NOT NULL, workspace TEXT NOT NULL,
                    reset REAL NOT NULL, next_attempt REAL NOT NULL, generation TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS due_time ON due(next_attempt, agent)")
            yield connection

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection | None]:
        if not self.path.exists():
            yield None
            return
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as connection:
            yield connection

    @staticmethod
    def _values(target: ResetTarget) -> tuple[str, str, str, float, float, str]:
        reset = target.reset_at.timestamp()
        return (
            target.agent_id,
            str(target.receipt_path),
            str(target.workspace_path),
            reset,
            reset,
            uuid.uuid4().hex,
        )

    def schedule(self, target: ResetTarget) -> None:
        """Publish the reset currently known to the limited hold's writer."""
        with self._writer() as connection:
            connection.execute(
                """
                INSERT INTO due VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent) DO UPDATE SET
                    receipt=excluded.receipt, workspace=excluded.workspace,
                    reset=excluded.reset, next_attempt=excluded.next_attempt,
                    generation=excluded.generation
                """,
                self._values(target),
            )

    def enroll(self, targets: Sequence[ResetTarget]) -> None:
        """Adopt one walk chunk of saved waits while preserving live entries."""
        if not targets:
            return
        with self._writer() as connection:
            connection.executemany(
                "INSERT INTO due VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(agent) DO NOTHING",
                (self._values(target) for target in targets),
            )

    def next_attempt(self) -> datetime | None:
        with self._reader() as connection:
            if connection is None:
                return None
            row = connection.execute("SELECT MIN(next_attempt) FROM due").fetchone()
        return None if row[0] is None else datetime.fromtimestamp(row[0], UTC)

    def due_at(self, now: datetime) -> list[ResetEntry]:
        with self._reader() as connection:
            if connection is None:
                return []
            rows = connection.execute(
                """
                SELECT agent, receipt, workspace, reset, generation FROM due
                WHERE next_attempt <= ? ORDER BY next_attempt, agent
                """,
                (now.timestamp(),),
            ).fetchall()
        return [
            ResetEntry(
                ResetTarget(
                    agent, Path(receipt), Path(workspace), datetime.fromtimestamp(reset, UTC)
                ),
                generation,
            )
            for agent, receipt, workspace, reset, generation in rows
        ]

    def finish(self, entry: ResetEntry, retry_at: datetime | None) -> None:
        """Confine an acknowledgement or deferral to the generation it observed."""
        identity = (entry.target.agent_id, entry.generation)
        with self._writer() as connection:
            if retry_at is None:
                connection.execute("DELETE FROM due WHERE agent=? AND generation=?", identity)
            else:
                connection.execute(
                    "UPDATE due SET next_attempt=? WHERE agent=? AND generation=?",
                    (retry_at.timestamp(), *identity),
                )
