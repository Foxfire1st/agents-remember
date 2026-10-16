"""Raw workspace observer events with cursor-safe SSE resume.

The state channel publishes the resolved projection. This channel publishes the
workspace river's underlying records verbatim, using virtual byte offsets that
survive its age and size compaction. A fresh connection starts in the retained
recent window; reconnects resume after their last complete record. This reader
changes no source file: the river's writer and compactor own retention.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.sse import ServerSentEvent

from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.observer.event_retention import initial_event_offsets
from agents_remember.observer.store import (
    WORKSPACE_LOCK_FILE,
    WORKSPACE_SOURCE,
    workspace_base_offset,
    workspace_river_read,
)
from agents_remember.serving.background_accounting import (
    OWNER_EVENT_TAIL,
    BackgroundLedger,
    measured,
    offload,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )

# The river has one workspace source; its cursor retains the transport's source map.
_WORKSPACE = WORKSPACE_SOURCE

# A fresh connect streams its (already window-bounded) backlog in chunks, so the loop is never
# blocked materializing thousands of records before the first byte.
DEFAULT_EVENT_BATCH = 500
# How many trailing bytes one source's tail scan reads to find its newest event
# timestamp for the ``ready`` marker (MIK-R114 rule 8: the panel names the last
# feed even when the stream's window holds nothing).
_LAST_TS_TAIL_BYTES = 8192


@dataclass(frozen=True)
class RawEvent:
    """One raw event line plus the resume cursor a client would send to continue after it.

    ``data`` is the verbatim JSONL line (already the camelCase wire form on disk). ``payload`` is
    the same line parsed once and proven to be a top-level JSON object; the SSE boundary uses it
    directly so ServerSentEvent single-encodes it like the state channel. ``cursor`` is the encoded
    per-source offset map *after* this event, i.e. the ``Last-Event-ID`` to resume the whole stream
    from this point.
    """

    source: str
    data: str
    payload: dict[str, object]
    cursor: str


def encode_cursor(offsets: dict[str, int]) -> str:
    """Encode a per-source offset map as one opaque, newline-free SSE id (base64url JSON)."""
    raw = json.dumps(offsets, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> dict[str, int]:
    """Decode a ``Last-Event-ID`` cursor back to an offset map (empty when absent/garbage)."""
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): value
        for key, value in data.items()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }


def _source_path(root: Path) -> Path:
    return root / WORKSPACE_SOURCE / "events.jsonl"


def _discover_sources(root: Path) -> list[str]:
    return [_WORKSPACE] if _source_path(root).is_file() else []


def _read_lines_from(path: Path, start: int) -> tuple[list[tuple[bytes, int]], int]:
    """Complete records (bytes, byte-offset-after) from an authoritative boundary, + the end.

    A trailing partial line (no terminating newline) is left unconsumed so a half-written
    append is never emitted; the end offset stays at the last complete-line boundary.  A nonzero
    client offset is accepted only when its preceding byte is a newline.  Otherwise the suffix of
    that client-sliced record is consumed and reading resumes at the next server-owned boundary.
    Offsets beyond EOF settle at the current EOF.
    """
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = min(start, size)
        if start > 0:
            handle.seek(start - 1)
            if handle.read(1) != b"\n":
                handle.seek(start)
                handle.readline()
                start = handle.tell()
        handle.seek(start)
        chunk = handle.read()
    lines: list[tuple[bytes, int]] = []
    consumed = 0
    while True:
        newline = chunk.find(b"\n", consumed)
        if newline == -1:
            break
        record = chunk[consumed:newline]
        consumed = newline + 1
        lines.append((record, start + consumed))
    return lines, start + consumed


# The workspace read establishes its (base, file) pair by construction, never by
# counting attempts. A pair counts only (A) when base and file were both read
# while holding the shared river lock, or (B) when no lockfile existed before
# the read and none exists after it, so no writer came into being. There is no
# attempt bound because no bound is needed: a lockless read that finds a lock
# afterwards repeats the read once under the shared lock, and the hold reports
# itself -- where it reports not held the read returns no rows with the
# caller's cursor unchanged.


def _read_source_lines(root: Path, virtual_start: int) -> tuple[list[tuple[bytes, int]], int]:
    """Read complete lines and return offsets in the source's cursor coordinate system.

    The workspace river is physically compacted while live, so its SSE cursor is virtual: ``baseOffset`` (bytes already reclaimed) + current file offset.
    The read holds the shared river lock without creating any observer file, so the base sidecar
    and the file are a consistent pair while this read maps a client cursor to the current physical
    file; when no lockfile exists the read proceeds without it rather than creating one.

    That unlocked missing-lock branch can still race first writers: a writer can
    create the lock and compact between this read's base snapshot and its file
    read. The compactor replaces the file first and writes the base second, both
    under the one writer lock, so equal base reads around the file read prove
    nothing there -- but the lockfile always covers that window, because the
    writer creates it when first acquiring the lock and it is never deleted.
    The pair therefore counts by construction: (A) base and file both read while
    holding the shared river lock, which excludes every append and every cut
    for the hold, or (B) no lockfile before the read and none after it, so no
    writer came into being. A lockless read that finds a lockfile afterwards
    discards its window and repeats the read once under the shared lock; the
    hold reports itself, and where it reports not held the read returns no rows
    with the caller's cursor unchanged, so the rows come with the next read. A delayed row is allowed; a
    lost or a repeated row is not. The branch still creates, rewrites and
    deletes no observer file.
    """
    path = _source_path(root)
    lock_path = root / WORKSPACE_SOURCE / WORKSPACE_LOCK_FILE
    if not lock_path.exists():
        base = workspace_base_offset(root)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        lines, physical_end = _read_lines_from(path, min(max(virtual_start - base, 0), size))
        if not lock_path.exists():
            return [
                (record, base + offset_after) for record, offset_after in lines
            ], base + physical_end
    with workspace_river_read(root) as held:
        if not held:
            return [], virtual_start
        if not lock_path.exists():
            return [], virtual_start
        base = workspace_base_offset(root)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        lines, physical_end = _read_lines_from(path, min(max(virtual_start - base, 0), size))
        return [
            (record, base + offset_after) for record, offset_after in lines
        ], base + physical_end


def read_new_events(
    root: Path, offsets: dict[str, int], *, limit: int | None = None
) -> tuple[list[RawEvent], dict[str, int]]:
    """Read complete lines past ``offsets`` across every source; return events + new offsets.

    Each event carries its virtual offset after the complete record, so a client
    resuming from that cursor skips exactly what it received. When ``limit`` is
    set the read returns early after that many emitted events, with offsets pointing just past
    the last consumed line, so the caller can drain a large backlog in bounded chunks.
    """
    current = dict(offsets)
    events: list[RawEvent] = []
    for source in _discover_sources(root):
        lines, end = _read_source_lines(root, current.get(source, 0))
        for record, offset_after in lines:
            current[source] = offset_after
            try:
                text = record.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue
            if not text:
                continue
            try:
                payload: object = json.loads(text)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            events.append(
                RawEvent(source=source, data=text, payload=payload, cursor=encode_cursor(current))
            )
            if limit is not None and len(events) >= limit:
                return events, current
        current[source] = end
    return events, current


def last_event_ts(root: Path) -> str | None:
    """The newest event timestamp across every source, or ``None`` when empty.

    Read from each source's tail only, so a connect never scans whole logs: the
    ``ready`` marker carries this so the panel can name the last feed even when
    the stream's replay window holds nothing (MIK-R114 rule 8). Unparseable rows
    never veto a readable one.
    """
    newest: datetime | None = None
    newest_raw: str | None = None
    for _source in _discover_sources(root):
        raw = _source_last_ts(_source_path(root))
        if raw is None:
            continue
        parsed = _parse_ts(raw)
        if parsed is None:
            continue
        if newest is None or parsed > newest:
            newest = parsed
            newest_raw = raw
    return newest_raw


def _source_last_ts(path: Path) -> str | None:
    """The ``ts`` of the last complete row of one log file, if it parses."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _LAST_TS_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return None
    for raw in reversed(tail.split(b"\n")):
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        ts = payload.get("ts")
        if isinstance(ts, str) and ts and _parse_ts(ts) is not None:
            return ts
    return None


def _parse_ts(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _initial_offsets_and_last_ts(root: Path) -> tuple[dict[str, int], str | None]:
    """One worker-thread pass for a fresh connect: replay offsets + last feed."""
    now = datetime.now(UTC)
    return initial_event_offsets(root, now=now), last_event_ts(root)


def _ready_payload(stored_ts: str | None) -> dict[str, object]:
    """The ``ready`` marker: coherence signal, plus the last feed when known."""
    if stored_ts is None:
        return {"ready": True}
    return {"ready": True, "lastEventTs": stored_ts}


async def stream_raw_events(
    config: McpRuntimeConfig,
    *,
    last_event_id: str | None = None,
    interval: float = 1.0,
    ledger: BackgroundLedger | None = None,
) -> AsyncGenerator[ServerSentEvent]:
    """Emit raw ``event`` SSE records, resuming from ``last_event_id`` then tailing new lines.

    Connect cost is bounded: the offset map is computed once (not re-scanned every tick),
    and the backlog is streamed in bounded chunks -- yielding to the loop between chunks --
    so a large history never blocks the event loop or materializes all at once before the
    first byte. The ``ready`` marker is sent once the (window-bounded) backlog has drained,
    signalling a coherent first snapshot; it carries ``lastEventTs`` (the newest event
    timestamp across every source, which may be older than the replay window) so the panel
    can name the last feed on a silent river.

    This stream never writes: no prune, no compaction, no cursor sidecar.
    """
    root = observer_root(config)
    # CS-6 D1 (260707-HFX2-L12): the initial-offset scan reads the workspace river
    # backlog from byte 0. Run it in a worker thread so a large events.jsonl on a fresh connect
    # never blocks the shared asyncio loop (which serves every SSE stream + every API request),
    # matching how ``read_new_events`` below is already offloaded.
    cursor_offsets = decode_cursor(last_event_id)
    if cursor_offsets:
        offsets = cursor_offsets
        stored_ts: str | None = None
    else:
        offsets, stored_ts = await offload(
            OWNER_EVENT_TAIL, ledger, _initial_offsets_and_last_ts, root
        )
    ready_sent = False
    while True:
        events, offsets = await offload(
            OWNER_EVENT_TAIL, ledger, read_new_events, root, offsets, limit=DEFAULT_EVENT_BATCH
        )
        for event in events:
            # ServerSentEvent JSON-encodes whatever it is given (the state channel passes dicts),
            # so emit the object parsed and validated by ``read_new_events`` -- passing the
            # pre-serialized JSONL string would double-encode the wire (`data: "{...}"`).
            with measured(OWNER_EVENT_TAIL, ledger):
                payload = ServerSentEvent(
                    data=event.payload, event="event", id=event.cursor, retry=2000
                )
            yield payload
        if events:
            # More backlog may remain; drain it promptly but yield to the loop between chunks.
            await asyncio.sleep(0)
            continue
        if not ready_sent:
            ready_sent = True
            with measured(OWNER_EVENT_TAIL, ledger):
                payload = ServerSentEvent(
                    data=_ready_payload(stored_ts),
                    event="ready",
                    id=encode_cursor(offsets),
                    retry=2000,
                )
            yield payload
        await asyncio.sleep(interval)
