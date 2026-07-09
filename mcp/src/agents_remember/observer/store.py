"""Append-only event store for the observer substrate.

Per-lifecycle truth lives in ``lifecycles/<lifecycle-id>/events.jsonl``;
lifecycle-less events (provider/watcher activity) go to
``workspace/events.jsonl``. Each lifecycle file has exactly one writer at a time
because a lifecycle is adopted by exactly one live session, so appends need no
cross-process lock.

The single-writer invariant is total: the *only* events written to a lifecycle
file are written by that lifecycle's live owner. A dormant fleeting lifecycle
past its TTL is never terminated by a written event (which would be a non-owner
append) -- readers project ``abandoned`` from its log and the sweep prunes the
directory. So nothing ever appends to a lifecycle file it does not own.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from agents_remember.observer.events import Event


class EventStore:
    """Resolve per-lifecycle / workspace log paths and append events as JSONL."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self, lifecycle_id: str | None) -> Path:
        """The JSONL log a given event routes to (workspace when lifecycle-less)."""
        if lifecycle_id:
            return self._root / "lifecycles" / lifecycle_id / "events.jsonl"
        return self._root / "workspace" / "events.jsonl"

    def append(self, event: Event) -> None:
        """Append one event to its log, creating parent dirs on first write."""
        path = self.log_path(event.lifecycleId)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json(by_alias=True, exclude_none=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self, lifecycle_id: str | None) -> list[Event]:
        """Read a log back as validated events (empty when the log is absent).

        Corrupt/legacy/version-skew lines are skipped rather than raised (CS-6 D3,
        260707-HFX2-L12): this read feeds the shared projection tick
        (``project_and_write`` -> ``read_lifecycle_logs``), so one torn append line
        must not freeze the whole projection fleet-wide -- it costs at most the one
        unparseable row, mirroring ``ProviderMetricsStore.read_recent`` tolerance.
        """
        path = self.log_path(lifecycle_id)
        if not path.exists():
            return []
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(Event.model_validate_json(line))
            except ValidationError:
                continue
        return events
