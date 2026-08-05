"""Append-only served-onboarding ledger, co-located with the observer substrate.

A served record is the durable fact "this onboarding piece was already served to
this lifecycle, at this content hash". It lets ``read_ar_files`` dedup the
auto-attached overview/sidecar bodies so a repeated read of the same file/route
does not re-spam onboarding the model already holds. The ledger survives a
context compaction (the same lifecycle continues in place), so the dedup state is
on-disk, not only in process memory.

A compaction would otherwise leave the served set stale: onboarding the model lost
to truncation would not re-serve. ``refresh=true`` forces the re-serve, and
``clear`` / a new chat yields a fresh lifecycle and a fresh ledger. The
``compact-reset.json`` marker that would make that recovery automatic has no
producer yet; the producer is owned by ``260628_developer-notification-reliability``
leaf 05 (POST-COMPACTION RECOVERY, gap G2), and
``read_files._maybe_reset_served`` is the consumer already waiting for it.

Served records live in ``<observer_root>/lifecycles/<lifecycle-id>/served.jsonl``
beside that lifecycle's ``events.jsonl`` / ``gates.jsonl`` -- the GateStore
pattern. Append-only and history-preserving: :meth:`served_set` folds the log
into the set of ``"<kind>:<path>:<hash>"`` keys served so far. One writer per
file in practice (a lifecycle is owned by one live session), the same
single-writer assumption the event and gate stores make.

This is a *record*, not a public MCP response: it carries no token fields and is
never returned by a tool, so it is not registered in
``PUBLIC_TOOL_RESPONSE_MODELS``.

Two classes, one substrate: :class:`ServedStore` is the file (paths, append, read,
fold), and :class:`ServedLedger` is the live read-through cache over it that the
running process actually asks -- the hot path is a set membership test, not a log
read.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SERVED_RECORD_SCHEMA = "ar-served-record/v1"


def now_iso() -> str:
    """Served-record timestamp: ISO 8601 with offset (UTC)."""
    return datetime.now(UTC).isoformat()


def served_key(kind: str, path: str, content_hash: str) -> str:
    """The dedup key folded over the ledger: ``<kind>:<path>:<hash>``."""
    return f"{kind}:{path}:{content_hash}"


class ServedRecord(BaseModel):
    """One ``ar-served-record/v1`` snapshot of a served onboarding piece.

    camelCase-free leaf fields keep the wire form small; ``schema_version``
    carries the lone alias because ``schema`` is an awkward attribute name --
    always dump with ``model_dump_json(by_alias=True, exclude_none=True)`` so it
    renders as ``schema``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SERVED_RECORD_SCHEMA, alias="schema")
    kind: str
    path: str
    hash: str
    ts: str

    def key(self) -> str:
        return served_key(self.kind, self.path, self.hash)


class ServedStore:
    """Resolve per-lifecycle served-ledger paths and append/read records."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self, lifecycle_id: str) -> Path:
        """The served log for a lifecycle (beside its events/gates logs)."""
        return self._root / "lifecycles" / lifecycle_id / "served.jsonl"

    def append(self, lifecycle_id: str, record: ServedRecord) -> None:
        """Append one record, creating parent dirs on first write."""
        path = self.log_path(lifecycle_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json(by_alias=True, exclude_none=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self, lifecycle_id: str) -> list[ServedRecord]:
        """Read a served log back as validated records (empty when absent).

        260707-HFX2-L12 F12: skip a torn/legacy line rather than raise — a served ledger read that
        feeds ``read_ar_files`` dedup + the dashboard must degrade to "re-serve once" on one bad row,
        not crash the read."""
        path = self.log_path(lifecycle_id)
        if not path.exists():
            return []
        records: list[ServedRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(ServedRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def served_set(self, lifecycle_id: str) -> set[str]:
        """Fold the log into the set of ``"<kind>:<path>:<hash>"`` keys served."""
        return {record.key() for record in self.read(lifecycle_id)}


class ServedLedger:
    """The live served-key sets, read through to :class:`ServedStore` on first use.

    One set per lifecycle, hydrated from ``served.jsonl`` the first time that lifecycle
    is asked about, so the dedup survives a context compaction (the same process keeps
    running) without re-reading the log on every attach decision.

    ITS OWN LOCK, DELIBERATELY, not the ambient lifecycle's. The lifecycle's lock exists
    because the heartbeat ticker thread and the request thread both append to one
    ``events.jsonl`` and the event store is single-writer-per-file; this ledger writes a
    different file that the ticker never touches. What it does need is protection of the
    in-memory sets against two request threads, which is what this lock is. The one
    ordering that exists -- the lifecycle takes its own lock and then calls
    :meth:`forget` -- is safe because nothing here ever calls back into the lifecycle.

    Durability must never break the read it records, so a disk error is contained in
    both writers; the in-memory set still advances, which is what stops the same process
    re-serving the same piece twice.
    """

    def __init__(self, store: ServedStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._sets: dict[str, set[str]] = {}

    def is_served(self, lifecycle_id: str, kind: str, path: str, content_hash: str) -> bool:
        """True when this exact ``(kind, path, hash)`` was already served."""
        with self._lock:
            return served_key(kind, path, content_hash) in self._hydrated(lifecycle_id)

    def record(
        self, lifecycle_id: str, kind: str, path: str, content_hash: str, *, ts: str
    ) -> None:
        """Record a served onboarding piece in memory and on disk (append-only)."""
        with self._lock:
            self._hydrated(lifecycle_id).add(served_key(kind, path, content_hash))
            with contextlib.suppress(OSError):
                self._store.append(
                    lifecycle_id,
                    ServedRecord(kind=kind, path=path, hash=content_hash, ts=ts),
                )

    def reset(self, lifecycle_id: str) -> None:
        """Clear the served set for a lifecycle (the compaction/refresh reset).

        Drops the in-memory set and deletes the on-disk ``served.jsonl`` so the next read
        re-serves every onboarding piece from scratch. The single live owner deleting its
        own ledger keeps the single-writer invariant intact.
        """
        with self._lock:
            self._sets.pop(lifecycle_id, None)
            with contextlib.suppress(OSError):
                path = self._store.log_path(lifecycle_id)
                if path.exists():
                    path.unlink()

    def forget(self, lifecycle_id: str) -> None:
        """Drop the in-memory set for an ended or left lifecycle; keep the log.

        The counterpart of :meth:`reset`: that one is a deliberate re-serve and deletes
        the durable record, this one only releases memory for a lifecycle nothing will
        ask about again. Deleting the log here would destroy the history of a lifecycle
        that merely paused.
        """
        with self._lock:
            self._sets.pop(lifecycle_id, None)

    def _hydrated(self, lifecycle_id: str) -> set[str]:
        """The set for a lifecycle, read through from disk on first use (lock held)."""
        cached = self._sets.get(lifecycle_id)
        if cached is None:
            cached = self._store.served_set(lifecycle_id)
            self._sets[lifecycle_id] = cached
        return cached
