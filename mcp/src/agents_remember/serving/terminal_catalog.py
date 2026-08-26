"""Durable dashboard terminal-session catalog.

The catalog row vocabulary (``TerminalCatalogEntry`` and its literals/parsers)
lives in ``models/terminal_catalog.py``; this module owns the store.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.seats import current_seat_occupant
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import (
    DEFAULT_LIVENESS_HYSTERESIS,
    SeatTurnState,
    TerminalCatalogEntry,
    TerminalCatalogLivenessConfig,
    TerminalLivenessEvidence,
)
from agents_remember.serving.response_contract import TerminalCatalogEntryWire
from agents_remember.serving.terminal_catalog_lock import exclusive_terminal_catalog_lock
from agents_remember.serving.terminal_catalog_migration import migrate_terminal_catalog_v1

TERMINATED_RETENTION_SECONDS = 86400.0


"""Consecutive tmux-command failures needed before a catalog row is marked exited."""


"""Minimum age of the first failed command probe before hysteresis can exit-mark a row."""


"""Pane-gone evidence is definitive, so it may mark faster than command failures."""


"""Minimum spacing between full catalog sweeps, independent of the dashboard projection tick."""


def terminal_catalog_path(coordination_root: Path) -> Path:
    """Runtime catalog path for dashboard-owned terminal sessions."""

    return coordination_root / "logs" / "dashboard" / "terminal-sessions.json"


def _leaf_execution_entry(entry: TerminalCatalogEntry) -> bool:
    return entry.binding_role in {"worker", "reviewer", "curator"} and any(
        ref is not None
        for ref in (
            entry.task_document_ref,
            entry.replacement_for_task_document_ref,
        )
    )


class TerminalCatalog:
    """JSON-backed catalog for dashboard-owned terminal sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # The RLock composes threads sharing this instance. ``_catalog_access`` adds the stable-file
        # flock that composes dashboard and MCP processes; atomic replace alone only prevents torn JSON.
        self._lock = threading.RLock()
        # 260707-HFX2-L12 F1/CS-6 D2: an in-memory unit-of-work buffer for a full-catalog sweep. When a
        # ``batch()`` is active, ``_read``/``_write`` hit this buffer instead of disk, so the liveness
        # sweep's per-entry read-modify-writes cost one disk read (at batch begin) + one disk write (at
        # commit) total, not O(n) disk reads and O(n) disk rewrites. ``None`` when no batch is active.
        self._batch: list[TerminalCatalogEntry] | None = None
        self._batch_dirty = False

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        entries = self._read_snapshot()
        if include_terminated:
            return entries
        return [entry for entry in entries if entry.status != "terminated"]

    def get(self, session_id: str) -> TerminalCatalogEntry | None:
        return next((entry for entry in self._read_snapshot() if entry.id == session_id), None)

    def active_for_task(
        self, task_document_ref: TaskDocumentRef, *, seat_role: str
    ) -> TerminalCatalogEntry | None:
        """The single RUNNING occupant of ``(task document, role)``, or ``None``.

        Worker/reviewer/curator coexist on a leaf; a manager occupies its master; sprint roles
        coexist on the sprint. Gating on ``status == "running"`` means a completed or terminated
        holder frees only its own structural role slot.
        """
        return current_seat_occupant(self.list(), document=task_document_ref, role=seat_role)

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        with self._catalog_access():
            entries = [current for current in self._read() if current.id != entry.id]
            entries.append(entry)
            self._write(entries)

    def mark_attached(self, session_id: str, attached_at: str) -> TerminalCatalogEntry | None:
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_attachment(attached_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_exited(self, session_id: str) -> TerminalCatalogEntry | None:
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            if entry.status in ("landed", "terminated"):
                return entry
            updated = entry.with_status("exited")
            entries[index] = updated
            self._write(entries)
            return updated

    def record_liveness_probe(
        self,
        session_id: str,
        *,
        alive: bool,
        checked_at: datetime,
        evidence: TerminalLivenessEvidence | None = None,
        hysteresis: TerminalCatalogLivenessConfig = DEFAULT_LIVENESS_HYSTERESIS,
    ) -> TerminalCatalogEntry | None:
        """Persist one liveness observation with hysteresis and success-side self-healing."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            if alive:
                updated = entry.with_liveness_success()
            elif evidence is None:
                updated = entry
            else:
                updated = entry.with_liveness_failure(
                    evidence=evidence,
                    checked_at=checked_at,
                    failure_threshold=hysteresis.failure_threshold,
                    minimum_failure_window_seconds=hysteresis.minimum_failure_window_seconds,
                    pane_gone_failure_threshold=hysteresis.pane_gone_failure_threshold,
                )
            if updated != entry:
                entries[index] = updated
                self._write(entries)
            return updated

    def mark_terminated(self, session_id: str, terminated_at: str) -> TerminalCatalogEntry | None:
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_status("terminated", at=terminated_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_retired(
        self,
        session_id: str,
        *,
        at: str,
        by_session: str | None,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry | None:
        """The explicit retire terminal mark (260707-HFX-L8): never a zombie row, never resurrected."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_retirement(
                at=at, by_session=by_session, reason=reason, edge=edge
            )
            if updated != entries[index]:
                entries[index] = updated
                self._write(entries)
            return updated

    def mark_landed(
        self,
        session_id: str,
        *,
        at: str,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry | None:
        """Mark a successful completion as landed/archive without closing the tmux session."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            updated = entry.with_landing(at=at, reason=reason, edge=edge)
            if updated != entry:
                entries[index] = updated
                self._write(entries)
            return updated

    def set_label(self, session_id: str, label: str) -> TerminalCatalogEntry | None:
        """Rename a session's display label (identity text only -- ``spawn_role`` never changes)."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_label(label)
            entries[index] = updated
            self._write(entries)
            return updated

    def bind_session_log(
        self,
        session_id: str,
        *,
        entry_id: str,
        path: Path,
    ) -> TerminalCatalogEntry | None:
        """Persist log provenance onto the latest row without replaying an open-time snapshot."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = replace(
                entries[index],
                session_log_entry_id=entry_id,
                session_log_path=path.resolve(),
            )
            entries[index] = updated
            self._write(entries)
            return updated

    def record_turn_state(
        self, session_id: str, state: SeatTurnState, *, changed_at: str
    ) -> TerminalCatalogEntry | None:
        """Persist a live turn-state classification; a no-op write when the state did not change."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            updated = entry.with_turn_state(state, changed_at=changed_at)
            if updated != entry:
                entries[index] = updated
                self._write(entries)
            return updated

    @contextlib.contextmanager
    def batch(self) -> Iterator[None]:
        """Read-once / write-once unit of work for a full-catalog sweep (F1/CS-6 D2).

        The liveness sweep calls a per-entry mutator (``record_liveness_probe`` / ``record_turn_state``)
        for every session, and each mutator did its own full-file read + rewrite -- O(n) disk reads and
        O(n) disk rewrites per sweep, each O(n) to parse/serialise, i.e. O(n^2) disk work that grows with
        the session count. Inside this context the catalog is read from disk exactly once (here, at begin)
        and every mutator's ``_read``/``_write`` hits the in-memory buffer; the single atomic disk write
        happens on exit. The cross-process lock intentionally spans the bounded probe lifecycle:
        another process's spawn/terminate waits, then reads and composes from this committed state.
        That serialization is the narrow defense against the reproduced stale-sweep overwrite while
        preserving exactly one catalog read and one write. Nested batches reuse the outer buffer.
        """
        with self._lock:
            if self._batch is not None:
                yield
                return
        with exclusive_terminal_catalog_lock(self.path), self._lock:
            self._batch = self._read_disk()
            self._batch_dirty = False
            # Keep the existing RLock across the unit of work. Same-thread catalog mutators
            # re-enter it, while another FastAPI thread must wait instead of mistaking this
            # process-wide buffer for its own nested batch.
            try:
                yield
            finally:
                entries = self._batch
                dirty = self._batch_dirty
                self._batch = None
                self._batch_dirty = False
                if dirty and entries is not None:
                    self._write_disk(entries)

    def compact(
        self,
        *,
        now: datetime,
        retain_seconds: float = TERMINATED_RETENTION_SECONDS,
        registered_execution_ids: frozenset[str] = frozenset(),
    ) -> int:
        """Reclaim ``terminated`` tombstones older than ``retain_seconds`` so the file stays bounded.

        260707-HFX2-L12 F1/CS-6 D3: terminated rows are never resurrected (the hysteresis refuses) and
        the catalog is re-read on every sweep, so unbounded tombstone growth is the reclamation gap. Only
        ``terminated`` rows past the window are dropped -- ``running``/``exited`` rows are live, and
        ``landed`` rows are inspectable archives reclaimed by the L11 manual group-cleanup, never here.
        A task-bound worker/reviewer/curator row is retained until its id is explicitly authorized
        by the task-owned execution registrar. That registrar publishes one bounded first-evidence
        marker before reclamation, so routine retention cannot turn historical execution into
        "never started". Other row provenance remains in the observer lifecycle event stream.
        Returns the number of rows reclaimed. Composes inside ``batch()`` (drops from the buffer, folded
        into the one commit write).
        """
        with self._catalog_access():
            entries = self._read()
            kept = [
                entry
                for entry in entries
                if not (
                    entry.status == "terminated"
                    and _terminated_beyond(entry, now=now, retain_seconds=retain_seconds)
                    and (not _leaf_execution_entry(entry) or entry.id in registered_execution_ids)
                )
            ]
            if len(kept) == len(entries):
                return 0
            self._write(kept)
            return len(entries) - len(kept)

    @contextlib.contextmanager
    def _catalog_access(self) -> Iterator[None]:
        """Serialize one mutation, reusing an outer batch's held file lock."""

        with self._lock:
            if self._batch is not None:
                yield
                return
        # Acquire the process lock before re-taking the instance lock, matching ``batch``. Holding
        # the instance lock while waiting for a batch's flock would deadlock that batch at commit.
        with exclusive_terminal_catalog_lock(self.path), self._lock:
            yield

    def _read_snapshot(self) -> list[TerminalCatalogEntry]:
        """Read one coherent atomic-file snapshot without waiting for the writer flock.

        A same-instance batch exposes its current buffer under the thread lock. Other instances
        read the last committed file while a slow liveness batch is probing panes; atomic replace
        makes that snapshot coherent, and read-only access never performs schema migration.
        """

        with self._lock:
            if self._batch is not None:
                return list(self._batch)
        return self._read_disk()

    def _read(self) -> list[TerminalCatalogEntry]:
        # Inside a batch the buffer IS the current state -- a shallow copy so a caller's in-place
        # ``entries[index] = ...`` never mutates the shared buffer out from under a concurrent mutator
        # (entries are frozen dataclasses, so a shallow copy is a safe snapshot).
        if self._batch is not None:
            return list(self._batch)
        return self._read_disk()

    def _write(self, entries: list[TerminalCatalogEntry]) -> None:
        # A batch defers durability to commit: every mutator's read-modify-write stays atomic under the
        # lock (it re-reads the buffer, mutates, writes it back), so concurrency semantics are identical
        # to the per-write-to-disk path -- only the final os.replace is coalesced to one per sweep.
        if self._batch is not None:
            self._batch = list(entries)
            self._batch_dirty = True
            return
        self._write_disk(entries)

    def _read_disk(self) -> list[TerminalCatalogEntry]:
        if not self.path.exists():
            return []
        raw = _load_catalog_json(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        if set(raw) != {"schema", "sessions"}:
            raise ValueError("terminal catalog envelope contains undeclared fields")
        schema = raw.get("schema")
        sessions = raw.get("sessions", [])
        if not isinstance(sessions, list):
            raise ValueError("terminal catalog sessions must be a list")
        rows = [item for item in sessions if isinstance(item, dict)]
        if len(rows) != len(sessions):
            raise ValueError("terminal catalog sessions must contain objects only")
        if schema == "ar-dashboard-terminal-sessions/v1":
            rows = migrate_terminal_catalog_v1(self.path.parent.parent.parent, rows)
        elif schema != "ar-dashboard-terminal-sessions/v2":
            raise ValueError(f"unsupported terminal catalog schema: {schema!r}")
        validated = [
            TerminalCatalogEntryWire.model_validate(row).model_dump(
                by_alias=True, exclude_unset=True
            )
            for row in rows
        ]
        return [TerminalCatalogEntry.from_json(item) for item in validated]

    def _write_disk(self, entries: list[TerminalCatalogEntry]) -> None:
        # The unique-temp rule this method used to spell out itself — a shared fixed-name temp let
        # concurrent writers, even two request threads in one process, interleave their bytes into a
        # torn file — is now the package-wide rule in kernel.atomic_write, which is where the rest of
        # the tree learns it too. The worst case here is still a lost update, never corruption.
        rows = [entry.to_json() for entry in sorted(entries, key=lambda e: e.created_at)]
        for row in rows:
            TerminalCatalogEntryWire.model_validate(row)
        payload = {"schema": "ar-dashboard-terminal-sessions/v2", "sessions": rows}
        atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


class DispatchBriefReceiptStore:
    """Dispatch-specific receipt mutation over the terminal catalog's atomic storage unit.

    Terminal lifecycle mutation and dispatch commit-point evidence are separate responsibilities.
    This collaborator keeps the receipt rule out of the general catalog surface while composing
    with the catalog's existing cross-process lock and in-memory batch.
    """

    def __init__(self, catalog: TerminalCatalog) -> None:
        self._catalog = catalog

    def bind(self, session_id: str, *, entry_id: str) -> TerminalCatalogEntry | None:
        """Idempotently bind one pinned-brief receipt to the exact private occupant."""

        with self._catalog._catalog_access():
            entries = self._catalog._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            current = entries[index]
            if current.dispatch_brief_entry_id not in {None, entry_id}:
                raise ValueError("seat generation already has a different dispatch brief")
            updated = replace(current, dispatch_brief_entry_id=entry_id)
            if updated != current:
                entries[index] = updated
                self._catalog._write(entries)
            return updated


def _terminated_beyond(
    entry: TerminalCatalogEntry, *, now: datetime, retain_seconds: float
) -> bool:
    """Whether a terminated row's ``terminated_at`` is older than the reclamation window.

    A row with no/unparseable ``terminated_at`` is conservatively KEPT (never reclaimed on a guess).
    """
    if entry.terminated_at is None:
        return False
    try:
        stamped = datetime.fromisoformat(entry.terminated_at)
    except ValueError:
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    return (now - stamped).total_seconds() > retain_seconds


def _index_of(entries: list[TerminalCatalogEntry], session_id: str) -> int | None:
    """The position of ``session_id`` in ``entries`` (for an in-place status update), or ``None``."""
    return next((i for i, entry in enumerate(entries) if entry.id == session_id), None)


def _load_catalog_json(text: str) -> object:
    """Parse the catalog exactly; corruption must fail before a later write can erase evidence."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"terminal catalog is not valid JSON: {exc}") from exc
