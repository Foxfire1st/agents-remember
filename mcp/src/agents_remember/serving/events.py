"""The raw observer-event SSE channel: byte-offset tail of the append-only logs (4b).

The ``state`` channel (``app.py``) serves the *folded* projection and re-snapshots on
reconnect -- right for a bounded projection. This channel instead streams the underlying
``ar-observer-event/v1`` records **verbatim** with exact byte-offset resume, because the
event log is append-only and unbounded: a reconnecting client must resume where it left
off, not replay history. It powers the future event-log panel and sim scrubbing.

Each per-lifecycle log (``lifecycles/<id>/events.jsonl``) and the workspace log
(``workspace/events.jsonl``) is an independent append-only file, so the resume position is
a **per-source byte-offset map**, carried opaquely in the SSE ``id`` (base64url JSON).
On (re)connect the ``Last-Event-ID`` cursor is decoded and every source resumes from its
own offset -- so this stays a *separate* endpoint from ``/api/stream`` rather than mixing
byte-offset resume with the state channel's snapshot resume on one stream.

The tail reader is a pure function (``read_new_events``) over a root + offset map, kept
independent of the HTTP layer so resume, multi-source ordering, and partial-line handling
are unit-testable without a client.

MIK-R114 rule 7: this stream deletes nothing. Lifecycle-log reclamation belongs to
the projection pass alone (``projection_inputs.py`` keeps its protection of a
running master); a connected browser changes no file under ``logs/observer/``.
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

# The workspace log has no lifecycle id; this reserved cursor key routes it. A lifecycle
# id can never collide (lifecycle ids are ULIDs -- Crockford base32, never "workspace").
_WORKSPACE = WORKSPACE_SOURCE

# Heartbeats are a liveness signal (already carried by the projection's status file), not agent
# activity, so the river never streams them.
_HEARTBEAT_KIND = "lifecycle.heartbeat"
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


def _source_path(root: Path, source: str) -> Path:
    if source == _WORKSPACE:
        return root / "workspace" / "events.jsonl"
    return root / "lifecycles" / source / "events.jsonl"


def _discover_sources(root: Path) -> list[str]:
    """Every readable log source under ``root``, lifecycles sorted then workspace last."""
    sources: list[str] = []
    lifecycles_dir = root / "lifecycles"
    if lifecycles_dir.is_dir():
        sources.extend(
            entry.name
            for entry in sorted(lifecycles_dir.iterdir())
            if entry.is_dir() and (entry / "events.jsonl").is_file()
        )
    if (root / "workspace" / "events.jsonl").is_file():
        sources.append(_WORKSPACE)
    return sources


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


def _read_source_lines(
    root: Path, source: str, virtual_start: int
) -> tuple[list[tuple[bytes, int]], int]:
    """Read complete lines and return offsets in the source's cursor coordinate system.

    Lifecycle logs remain physical-offset streams. The workspace river is physically compacted while
    live, so its SSE cursor is virtual: ``baseOffset`` (bytes already reclaimed) + current file offset.
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
    path = _source_path(root, source)
    if source != _WORKSPACE:
        return _read_lines_from(path, virtual_start)
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


def _is_heartbeat_event(payload: dict[str, object]) -> bool:
    """True when a parsed observer-event object is a heartbeat (liveness, not activity)."""
    return payload.get("kind") == _HEARTBEAT_KIND


def read_new_events(
    root: Path, offsets: dict[str, int], *, limit: int | None = None
) -> tuple[list[RawEvent], dict[str, int]]:
    """Read complete lines past ``offsets`` across every source; return events + new offsets.

    Pure and deterministic: sources are walked in a fixed order (lifecycles sorted, workspace
    last) and each event carries the offset map snapshot *after* it, so a client resuming from
    any event's cursor skips exactly what it already received -- across all sources. Heartbeat
    lines advance the offset but are never emitted (liveness, not activity). When ``limit`` is
    set the read returns early after that many emitted events, with offsets pointing just past
    the last consumed line, so the caller can drain a large backlog in bounded chunks.
    """
    current = dict(offsets)
    events: list[RawEvent] = []
    for source in _discover_sources(root):
        lines, end = _read_source_lines(root, source, current.get(source, 0))
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
            if _is_heartbeat_event(payload):
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
    for source in _discover_sources(root):
        raw = _source_last_ts(_source_path(root, source))
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

    This stream never writes: no prune, no compaction, no cursor sidecar. Lifecycle-log
    reclamation is the projection pass's alone (MIK-R114 rule 7).
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
