"""Bounded workspace Event River retention and cursor-safe compaction."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.observer.store import (
    WORKSPACE_SOURCE,
    set_workspace_base_offset,
    workspace_base_offset,
    workspace_river_lock,
)

# A fresh connection replays at most the workspace river's recent hour.
WORKSPACE_EVENT_TTL_SECONDS = 60 * 60
WORKSPACE_EVENT_COMPACT_INTERVAL_SECONDS = 60.0
# MIK-R114 rule 5: after every append the workspace river is never larger than
# ``WORKSPACE_EVENT_MAX_BYTES`` (16 MiB). When an append would pass the ceiling,
# the oldest rows are removed until the file is at most
# ``WORKSPACE_EVENT_TRIM_TO_BYTES`` (8 MiB), and then the row is appended. There
# is no interval between size checks, no store beside the river and no second
# file of events; the dashboard's age compaction keeps its hour and its interval.
WORKSPACE_EVENT_MAX_BYTES = 16777216  # 16 MiB
WORKSPACE_EVENT_TRIM_TO_BYTES = 8388608  # 8 MiB


@dataclass(frozen=True)
class RiverSizeBound:
    """The ceiling an admission enforces and the level a cut trims back to."""

    max_bytes: int = WORKSPACE_EVENT_MAX_BYTES
    trim_to_bytes: int = WORKSPACE_EVENT_TRIM_TO_BYTES


def initial_event_offsets(root: Path, *, now: datetime) -> dict[str, int]:
    """Starting offsets for a fresh `/api/events` connection.

    The workspace source starts at its recent retained window. Its virtual
    offset includes prior compaction so reconnects keep their cursor meaning.
    """
    offsets: dict[str, int] = {}
    workspace = root / WORKSPACE_SOURCE / "events.jsonl"
    if workspace.is_file():
        base_offset = workspace_base_offset(root)
        offsets[WORKSPACE_SOURCE] = base_offset + _first_retained_offset(
            workspace, cutoff=now - timedelta(seconds=WORKSPACE_EVENT_TTL_SECONDS)
        )
    return offsets


def compact_workspace_river(
    root: Path,
    *,
    now: datetime,
    ttl_seconds: float | None = WORKSPACE_EVENT_TTL_SECONDS,
    bound: RiverSizeBound | None = None,
    timeout: float | None = None,
) -> int:
    """Reclaim ``workspace/events.jsonl`` rows by age, by size, or both.

    One function serves both cuts (MIK-R114 rule 5): the cut by age drops rows
    older than the replay TTL, and the cut by size (``size``) drops oldest rows
    until the file is at most its trim bound. Each cut is skipped when absent,
    so the dashboard's hourly loop keeps calling with the age cut alone, while
    the write edge admits every append with the size cut.

    Rows older than the replay window can be dropped physically. Every append
    also enforces the byte ceiling, including between age-compaction passes.

    CURSOR-SAFETY: live ``/api/events`` clients carry a VIRTUAL byte offset for
    the workspace source. Physical compaction increases
    ``events.cursor.json::baseOffset`` by the bytes dropped, so a cursor issued
    before compaction still maps onto the same retained tail after the rewrite.
    The compactor and the workspace writers share ``events.lock`` through
    :func:`workspace_river_lock`, so no process appends into a file another
    process is replacing.

    Returns the number of rows reclaimed.
    """
    path = root / WORKSPACE_SOURCE / "events.jsonl"
    if not path.is_file():
        return 0
    with workspace_river_lock(root, timeout=timeout):
        return _compact_under_lock(root, now=now, ttl_seconds=ttl_seconds, bound=bound)


def _compact_under_lock(
    root: Path,
    *,
    now: datetime,
    ttl_seconds: float | None,
    bound: RiverSizeBound | None,
) -> int:
    """The cut itself; the caller holds :func:`workspace_river_lock`."""
    path = root / WORKSPACE_SOURCE / "events.jsonl"
    if not path.is_file():
        return 0
    base = workspace_base_offset(root)
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    retained_start = 0
    if ttl_seconds is not None:
        retained_start = _age_retained_start(path, now=now, ttl_seconds=ttl_seconds)
    if bound is not None:
        retained_start = _size_retained_start(
            path,
            size,
            from_start=retained_start,
            max_bytes=bound.max_bytes,
            trim_to_bytes=bound.trim_to_bytes,
        )
    if retained_start == 0:
        return 0
    dropped = _count_rows_before(path, retained_start)
    if dropped == 0:
        return 0
    with path.open("rb") as handle:
        handle.seek(retained_start)
        retained = handle.read()
    atomic_write_bytes(path, retained)
    set_workspace_base_offset(root, base + retained_start)
    return dropped


def admit_workspace_river_row(
    root: Path,
    row: bytes,
    *,
    max_bytes: int | None = None,
    trim_to_bytes: int | None = None,
    timeout: float | None = None,
) -> bool:
    """Admit one JSONL row (without its line end) to the workspace river.

    MIK-R114 rule 5: each append is admitted under the lock the river has. The
    writer takes the size of the file and adds the bytes of the row with its
    line end; when the sum is over the ceiling it first removes the oldest rows
    through :func:`_compact_under_lock` until the file leaves room for the row
    (at most the trim bound), and then appends -- check, cut, offset change
    and append under one lock acquisition. A row that does not fit after the
    removal is not written -- ``False`` -- and the caller counts it (rule 6);
    the next append tries the removal again.
    """
    path = root / WORKSPACE_SOURCE / "events.jsonl"
    line = row + b"\n"
    ceiling = WORKSPACE_EVENT_MAX_BYTES if max_bytes is None else max_bytes
    trim_to = WORKSPACE_EVENT_TRIM_TO_BYTES if trim_to_bytes is None else trim_to_bytes
    with workspace_river_lock(root, timeout=timeout):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = 0
        if size + len(line) > ceiling:
            _compact_under_lock(
                root,
                now=datetime.now(UTC),
                ttl_seconds=None,
                bound=RiverSizeBound(max_bytes=ceiling - len(line), trim_to_bytes=trim_to),
            )
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
        if size + len(line) <= ceiling:
            with path.open("ab") as handle:
                handle.write(line)
            return True
        return False


def _age_retained_start(path: Path, *, now: datetime, ttl_seconds: float) -> int:
    """Byte offset of the first row the age cut keeps (0 when none aged out)."""
    cutoff = now - timedelta(seconds=ttl_seconds)
    for start, _offset_after, payload in _iter_event_payloads(path):
        ts = _event_time(payload)
        if ts is None or ts >= cutoff:
            return start
    if any(True for _ in _iter_event_payloads(path)):
        # Every row aged out -> truncate to an empty (but present) source file.
        return path.stat().st_size
    return 0


def _size_retained_start(
    path: Path, size: int, *, from_start: int, max_bytes: int, trim_to_bytes: int
) -> int:
    """Byte offset of the first row the size cut keeps (``from_start`` when fit).

    Whole rows from the start are removed until the suffix is at most
    ``trim_to_bytes``; the cut never moves before ``from_start``, so an age cut
    made first is never undone. When even the newest row alone passes
    ``trim_to_bytes`` every row goes and the file truncates empty.
    """
    if size - from_start <= max_bytes:
        return from_start
    floor = size - trim_to_bytes
    for start in _line_start_offsets(path):
        if start >= floor and start >= from_start:
            return start
    return size


def _count_rows_before(path: Path, retained_start: int) -> int:
    """Complete rows starting before ``retained_start``."""
    return sum(1 for start in _line_start_offsets(path) if start < retained_start)


def _line_start_offsets(path: Path) -> list[int]:
    """Byte offsets at which each complete row of ``path`` starts."""
    starts: list[int] = []
    with path.open("rb") as handle:
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                return starts
            if not raw.endswith(b"\n"):
                return starts
            starts.append(start)


def _first_retained_offset(path: Path, *, cutoff: datetime) -> int:
    """Offset of the first event we cannot prove is older than ``cutoff``.

    Events whose timestamp is unparseable are kept (we never silently drop an
    event we cannot age), so only events with a valid timestamp strictly older
    than the cutoff are skipped.
    """
    end = 0
    for start, offset_after, payload in _iter_event_payloads(path):
        end = offset_after
        ts = _event_time(payload)
        if ts is None or ts >= cutoff:
            return start
    return end


def _iter_event_payloads(path: Path) -> Iterator[tuple[int, int, dict[str, Any]]]:
    with path.open("rb") as handle:
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                return
            if not raw.endswith(b"\n"):
                return
            offset_after = handle.tell()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                yield start, offset_after, payload


def _event_time(payload: dict[str, Any]) -> datetime | None:
    ts = payload.get("ts")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
