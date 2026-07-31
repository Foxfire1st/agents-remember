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

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from agents_remember.observer.events import Event

WORKSPACE_SOURCE = "workspace"
WORKSPACE_CURSOR_FILE = "events.cursor.json"
WORKSPACE_LOCK_FILE = "events.lock"
HEARTBEAT_KIND = "lifecycle.heartbeat"
HEARTBEAT_SIDECAR_FILE = "heartbeat.json"


@contextmanager
def workspace_river_lock(root: Path) -> Iterator[None]:
    """Cross-process exclusive lock for workspace-river append and physical compaction.

    The workspace river has multiple process writers (serving + MCP tools), and live compaction
    physically rewrites the file. A POSIX lock on a sibling lockfile is therefore the one defensive
    mechanism this path needs: without it, a writer can append into the file while another process
    is replacing it, which is exactly the F3 tear risk.
    """
    path = root / WORKSPACE_SOURCE / WORKSPACE_LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def workspace_base_offset(root: Path) -> int:
    """Virtual byte offset already reclaimed from ``workspace/events.jsonl``."""
    path = root / WORKSPACE_SOURCE / WORKSPACE_CURSOR_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    value = payload.get("baseOffset") if isinstance(payload, dict) else None
    return value if isinstance(value, int) and value >= 0 else 0


def set_workspace_base_offset(root: Path, base_offset: int) -> None:
    """Persist the workspace river's virtual base offset.

    Callers hold :func:`workspace_river_lock`; this helper only performs the atomic sidecar write.
    """
    path = root / WORKSPACE_SOURCE / WORKSPACE_CURSOR_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps({"baseOffset": base_offset}, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def workspace_logical_size(root: Path) -> int:
    """Virtual EOF for the workspace river (base offset + current physical size)."""
    path = root / WORKSPACE_SOURCE / "events.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return workspace_base_offset(root) + size


def heartbeat_sidecar_path(root: Path, lifecycle_id: str) -> Path:
    return root / "lifecycles" / lifecycle_id / HEARTBEAT_SIDECAR_FILE


def read_heartbeat_sidecar(root: Path, lifecycle_id: str) -> Event | None:
    path = heartbeat_sidecar_path(root, lifecycle_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        event = Event.model_validate_json(text)
    except ValidationError:
        return None
    return event if event.kind == HEARTBEAT_KIND else None


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
        return self._root / WORKSPACE_SOURCE / "events.jsonl"

    def append(self, event: Event) -> None:
        """Append one event to its log, creating parent dirs on first write."""
        if event.lifecycleId and event.kind == HEARTBEAT_KIND:
            self._write_heartbeat_sidecar(event)
            return
        path = self.log_path(event.lifecycleId)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = event.model_dump_json(by_alias=True, exclude_none=True)
        if event.lifecycleId is None:
            with workspace_river_lock(self._root), path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return
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
        events = self.read_log(lifecycle_id)
        if lifecycle_id is not None:
            heartbeat = read_heartbeat_sidecar(self._root, lifecycle_id)
            if heartbeat is not None and (not events or not _event_before(heartbeat, events[-1])):
                events.append(heartbeat)
        return events

    def read_log(self, lifecycle_id: str | None) -> list[Event]:
        """Read only the JSONL file, excluding any coalesced heartbeat sidecar."""
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

    def read_heartbeat(self, lifecycle_id: str) -> Event | None:
        """Read the coalesced heartbeat sidecar for one lifecycle."""
        return read_heartbeat_sidecar(self._root, lifecycle_id)

    def _write_heartbeat_sidecar(self, event: Event) -> None:
        path = heartbeat_sidecar_path(self._root, event.lifecycleId or "")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(
                event.model_dump_json(by_alias=True, exclude_none=True) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except BaseException:
            with suppress(OSError):
                tmp.unlink()
            raise


def _event_before(left: Event, right: Event) -> bool:
    left_ts = _event_time(left)
    right_ts = _event_time(right)
    if left_ts is None or right_ts is None:
        return left.ts < right.ts
    return left_ts < right_ts


def _event_time(event: Event) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(event.ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
