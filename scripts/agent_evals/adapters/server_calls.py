"""Cross-check fixture AR calls against native actions and opaque intervals.

The log producer is application/tool_answer_log.py::_call_line_dict. Its IDs
are not provider call IDs, and its arguments may be length/digest summaries.
Only unique, source-bound placement recovers a call. No script is evaluated.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from fnmatch import fnmatchcase
from itertools import islice
from pathlib import Path
from typing import Any

from agents_remember.application.tool_answer_log import (
    TOOL_LOG_KEEP_DAYS_DEFAULT,
    TOOL_LOG_MAX_BYTES_DEFAULT,
)

from ..artifacts import json_digest
from .hermes import hermes_final_stop
from .models import NativeCapture, NativeEvent, SourceFile, raw_export_digest


@dataclass(frozen=True)
class FixtureServerWindow:
    log_root: Path
    started_at: str
    ended_at: str
    checkpoint: SourceFile
    owner_id: str
    project_root: Path
    max_bytes: int = TOOL_LOG_MAX_BYTES_DEFAULT
    # Instrument discovery/read bound, not a claim about configured retention.
    max_files: int = TOOL_LOG_KEEP_DAYS_DEFAULT + 1


def _time(value: object) -> float | None:
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo is not None else None
        except ValueError:
            return None
    return None


def _read(path: Path, budget: list[int]) -> tuple[bytes, SourceFile]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"binding-unavailable: server evidence is missing/symlink: {path}")
    before = path.stat()
    if before.st_size > budget[0]:
        raise ValueError(
            f"binding-unavailable: server evidence read cap: {path}; bytes={before.st_size}; remaining={budget[0]}; incomplete, not truncated"
        )
    data = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f"schema-incompatible: server evidence changed during capture: {path}")
    budget[0] -= len(data)
    return data, SourceFile(str(path.resolve()), hashlib.sha256(data).hexdigest())


def _checkpoint(binding: FixtureServerWindow, budget: list[int]) -> dict[str, Any]:
    start, end = _time(binding.started_at), _time(binding.ended_at)
    if (
        start is None
        or end is None
        or start > end
        or binding.max_bytes <= 0
        or binding.max_files <= 0
    ):
        raise ValueError("binding-unavailable: fixture server recording window is invalid")
    data, source = _read(Path(binding.checkpoint.path), budget)
    if source != binding.checkpoint:
        raise ValueError(
            f"binding-unavailable: server checkpoint digest/path differs: {source.path}"
        )
    row = json.loads(data)
    ended = _time(row.get("lastTurnEndedAt")) if isinstance(row, dict) else None
    if (
        not isinstance(row, dict)
        or row.get("readyAt") != binding.started_at
        or row.get("errors") != []
        or not binding.owner_id
        or row.get("agentId") != binding.owner_id
        or not binding.project_root.is_absolute()
        or row.get("projectRoot") != str(binding.project_root)
        or ended is None
        or not start <= ended <= end
    ):
        raise ValueError(
            f"binding-unavailable: incomplete fixture server checkpoint: {source.path}"
        )
    return row


def _terminal(capture: NativeCapture) -> bool:
    if capture.harness == "hermes":
        return hermes_final_stop(capture) is not None
    if capture.harness != "codex":
        return False
    turns = [
        event.payload.get("type")
        for event in capture.events
        if event.kind == "native"
        and event.session_id == capture.session_id
        and event.payload.get("type") in {"task_started", "task_complete", "turn_aborted"}
    ]
    return bool(turns and turns[-1] == "task_complete")


def _arguments(value: object) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _logged_arguments(value: dict[str, Any]) -> dict[str, Any]:
    """The producer's non-secret shortening rule, never an expansion of hashes."""
    result = {}
    for key, item in value.items():
        text = (
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, sort_keys=True)
        )
        result[key] = (
            {"length": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
            if len(text) > 200
            else item
        )
    return result


def _identity(event: NativeEvent) -> tuple[str, str, str, str] | None:
    call_id = event.payload.get("call_id")
    return (
        (event.session_id, event.source, event.source_sha256, call_id)
        if isinstance(call_id, str) and call_id
        else None
    )


class _CallBounds:
    def __init__(self, capture: NativeCapture):
        self.session = capture.session_id
        self.sources = set(capture.source_files)
        self.calls: dict[tuple[str, str, str, str], list[NativeEvent]] = {}
        self.results: dict[tuple[str, str, str, str], list[NativeEvent]] = {}
        self.cached: dict[int, tuple[float, float] | None] = {}
        for event in capture.events:
            key = _identity(event)
            target = self.calls if event.kind == "tool_call" else self.results
            if key is not None and event.kind in {"tool_call", "tool_result"}:
                target.setdefault(key, []).append(event)

    def _direct(self, event: NativeEvent) -> tuple[float, float] | None:
        cache_key = id(event)
        if cache_key in self.cached:
            return self.cached[cache_key]
        self.cached[cache_key] = None
        key = _identity(event)
        if (
            key is None
            or event.session_id != self.session
            or SourceFile(event.source, event.source_sha256) not in self.sources
            or len(self.calls.get(key, [])) != 1
        ):
            return None
        metadata = event.payload.get("internal_chat_message_metadata_passthrough")
        start = _time(metadata.get("create_time")) if isinstance(metadata, dict) else None
        if start is None:
            start = _time(event.native_timestamp)
        paired = [item for item in self.results.get(key, []) if item.sequence > event.sequence]
        ends = [_time(item.native_timestamp) for item in paired]
        if start is None or not ends or any(end is None or end < start for end in ends):
            return None
        result = (start, max(end for end in ends if end is not None))
        self.cached[cache_key] = result
        return result

    def bounds(self, event: NativeEvent) -> tuple[float, float] | None:
        provenance = event.payload.get("native_dispatch")
        outer_id = provenance.get("outerCallId") if isinstance(provenance, dict) else None
        if outer_id is None:
            return self._direct(event)
        if not isinstance(outer_id, str):
            return None
        key = (event.session_id, event.source, event.source_sha256, outer_id)
        parents = self.calls.get(key, [])
        if (
            len(parents) != 1
            or parents[0].sequence >= event.sequence
            or not any(result.sequence > event.sequence for result in self.results.get(key, []))
        ):
            return None
        # Child binding itself must also be complete; the outer interval provides
        # an observed start when the native child only has completion time.
        if self._direct(event) is None:
            return None
        return self._direct(parents[0])


@dataclass
class _IntervalNode:
    event: NativeEvent
    start: float
    end: float
    left: _IntervalNode | None = None
    right: _IntervalNode | None = None
    active: bool = True
    maximum: float = -math.inf

    @property
    def key(self) -> tuple[float, int]:
        return self.start, self.event.sequence

    def refresh(self) -> None:
        self.maximum = max(
            self.end if self.active else -math.inf,
            self.left.maximum if self.left else -math.inf,
            self.right.maximum if self.right else -math.inf,
        )


class _Intervals:
    def __init__(self, entries: list[tuple[NativeEvent, tuple[float, float]]]):
        ordered = sorted(entries, key=lambda item: (item[1][0], item[0].sequence))
        self.root = self._build(ordered, 0, len(ordered))

    def _build(
        self, entries: list[tuple[NativeEvent, tuple[float, float]]], start: int, end: int
    ) -> _IntervalNode | None:
        if start >= end:
            return None
        middle = (start + end) // 2
        event, (lower, upper) = entries[middle]
        node = _IntervalNode(
            event,
            lower,
            upper,
            self._build(entries, start, middle),
            self._build(entries, middle + 1, end),
        )
        node.refresh()
        return node

    def _find(self, node: _IntervalNode | None, stamp: float, found: list[NativeEvent]) -> None:
        if node is None or len(found) >= 2 or node.maximum < stamp:
            return
        self._find(node.left, stamp, found)
        if len(found) < 2 and node.active and node.start <= stamp <= node.end:
            found.append(node.event)
        if node.start <= stamp:
            self._find(node.right, stamp, found)

    def matches(self, stamp: float) -> list[NativeEvent]:
        found: list[NativeEvent] = []
        self._find(self.root, stamp, found)
        return found

    def _remove(self, node: _IntervalNode | None, key: tuple[float, int]) -> None:
        if node is None:
            return
        if node.key == key:
            node.active = False
        else:
            self._remove(node.left if key < node.key else node.right, key)
        node.refresh()

    def remove(self, event: NativeEvent, bounds: tuple[float, float]) -> None:
        self._remove(self.root, (bounds[0], event.sequence))


class _Placements:
    def __init__(self, capture: NativeCapture):
        binding = _CallBounds(capture)
        native: dict[tuple[str, str], list[tuple[NativeEvent, tuple[float, float]]]] = {}
        opaque: list[tuple[NativeEvent, tuple[float, float]]] = []
        self.bounds: dict[int, tuple[float, float]] = {}
        for event in capture.events:
            if event.kind != "tool_call" or (bounds := binding.bounds(event)) is None:
                continue
            self.bounds[event.sequence] = bounds
            if event.payload.get("opaque") is True:
                opaque.append((event, bounds))
            name = event.payload.get("name")
            args = _arguments(event.payload.get("arguments"))
            if (
                event.payload.get("server") == "agents-remember-task"
                and isinstance(name, str)
                and args is not None
            ):
                key = (name.rsplit("__", 1)[-1], json_digest(_logged_arguments(args)))
                native.setdefault(key, []).append((event, bounds))
        self.native = {key: _Intervals(entries) for key, entries in native.items()}
        self.opaque = _Intervals(opaque)

    def place(self, row: dict[str, Any]) -> tuple[NativeEvent, str] | None:
        stamp = _time(row["time"])
        if stamp is None:
            return None
        index = self.native.get((row["tool"], json_digest(row["arguments"])))
        native = index.matches(stamp) if index is not None else []
        if len(native) == 1 and index is not None:
            event = native[0]
            index.remove(event, self.bounds[event.sequence])
            return event, "native-arguments-and-interval"
        if native:
            return None
        opaque = self.opaque.matches(stamp)
        return (opaque[0], "unique-opaque-interval") if len(opaque) == 1 else None


def _valid_row(row: object) -> bool:
    return bool(
        isinstance(row, dict)
        and _time(row.get("time")) is not None
        and isinstance(row.get("tool"), str)
        and row["tool"]
        and isinstance(row.get("arguments"), dict)
        and isinstance(row.get("ok"), bool)
        and (row.get("code") is None or isinstance(row["code"], str))
        and type(row.get("dropped")) is int
        and row["dropped"] >= 0
    )


def _rows(binding: FixtureServerWindow, budget: list[int], skipped_blank_lines: dict[str, Any]):
    root = binding.log_root
    if root.is_symlink() or not root.is_dir():
        raise ValueError(
            f"binding-unavailable: fixture server log directory absent/symlink: {root}"
        )
    # Bound raw discovery too: glob may internally materialize a directory and
    # empty/nonmatching entries do not consume the separate byte-read budget.
    with os.scandir(root) as entries:
        discovered = list(islice(entries, binding.max_files + 1))
    if len(discovered) > binding.max_files:
        raise ValueError(
            f"binding-unavailable: fixture server file discovery cap: {root}; "
            f"entries>{binding.max_files}; incomplete, not truncated; instrument cap, not server retention"
        )
    paths = sorted(
        root / entry.name for entry in discovered if fnmatchcase(entry.name, "ar-tools-*.jsonl")
    )
    if not paths:
        raise ValueError(f"binding-unavailable: fixture server call logs absent: {root}")
    start, end = _time(binding.started_at), _time(binding.ended_at)
    for path in paths:
        data, source = _read(path, budget)
        skipped_blank_lines["bySource"][source.path] = 0
        yield source, None, None
        for number, line in enumerate(data.splitlines(), 1):
            if not line.strip():
                skipped_blank_lines["total"] += 1
                skipped_blank_lines["bySource"][source.path] += 1
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError) as error:
                raise ValueError(
                    f"schema-incompatible: fixture server record {path}:{number}; sha256={hashlib.sha256(line).hexdigest()}; {type(error).__name__}"
                ) from error
            if not _valid_row(row):
                kind = type(row).__name__
                keys = sorted(row) if isinstance(row, dict) else []
                raise ValueError(
                    f"schema-incompatible: fixture server record {path}:{number}; kind={kind}; keys={keys}; sha256={json_digest(row)}"
                )
            stamp = _time(row["time"])
            if (
                start is not None
                and end is not None
                and stamp is not None
                and start <= stamp <= end
            ):
                yield source, number, row


def _recovered(
    capture: NativeCapture, event: NativeEvent, source: SourceFile, number: int, row: dict[str, Any]
) -> tuple[NativeEvent, NativeEvent]:
    identity = f"server-log/{source.sha256}:{number}"
    provenance = {
        "wire": "fixture-server-log/v1",
        "identityBasis": "server source digest and line; not a native provider call ID",
        "parentOpaqueCallId": event.payload["call_id"],
        "path": f"{source.path}:{number}",
        "rowSha256": json_digest(row),
        "argumentsEncoding": "server-log-shortened/v1",
        "timeBasis": "server completion log time; no invented start timestamp",
    }
    call = NativeEvent(
        "tool_call",
        capture.session_id,
        0,
        {
            "call_id": identity,
            "name": row["tool"],
            "server": "agents-remember-task",
            "arguments": row["arguments"],
            "native_dispatch": provenance,
        },
        source.path,
        source.sha256,
        row["time"],
    )
    result = replace(
        call,
        kind="tool_result",
        payload={
            "call_id": identity,
            "output": {
                "serverLogOutcome": {
                    key: row.get(key)
                    for key in ("ok", "code", "detail", "resultDigest", "resultBytes")
                },
                "resultEncoding": "server-log-outcome/v1",
            },
            "native_dispatch": provenance,
        },
    )
    return call, result


def _capture_issue(capture: NativeCapture) -> str | None:
    if not capture.session_id:
        return "capture-absent: fixture server gate has no native session"
    if not _terminal(capture):
        return "capture-absent: fixture server gate has no native final stop"
    return None


_UNPLACEABLE = "binding-unavailable: unplaceable fixture AR call "


def _recovered_from_log(event: NativeEvent) -> bool:
    provenance = event.payload.get("native_dispatch")
    return isinstance(provenance, dict) and provenance.get("wire") == "fixture-server-log/v1"


def replay_server_placements(capture: NativeCapture) -> NativeCapture:
    """Re-place retained server rows on rebuilt native events without reading logs.

    Raw records, their historical placement and their export digest stay exact.
    Only derived placement references, unplaceable diagnostics and opaque-interval
    recoveries are rebuilt; unrelated recording failures remain failures.
    """
    records = [
        row["fixtureServerCall"] for row in capture.raw_records if "fixtureServerCall" in row
    ]
    if not records:
        return capture
    events = [
        replace(
            event,
            payload={
                key: value for key, value in event.payload.items() if key != "fixture_server_calls"
            },
        )
        for event in capture.events
        if not _recovered_from_log(event)
    ]
    placements = _Placements(replace(capture, events=tuple(events)))
    positions = {event.sequence: index for index, event in enumerate(events)}
    unknowns = [reason for reason in capture.unknowns if not reason.startswith(_UNPLACEABLE)]
    additions: dict[int, list[NativeEvent]] = {}
    for record in records:
        row, number = record["row"], record["line"]
        source = SourceFile(record["source"], record["source_sha256"])
        placement = placements.place(row)
        if placement is None:
            unknowns.append(
                f"{_UNPLACEABLE}{row['tool']!r}; {source.path}:{number}; sha256={json_digest(row)}"
            )
            continue
        event, basis = placement
        if basis == "unique-opaque-interval":
            additions.setdefault(event.sequence, []).extend(
                _recovered(capture, event, source, number, row)
            )
        else:
            index = positions[event.sequence]
            derived = {**record, "placement": {"call_id": event.payload["call_id"], "basis": basis}}
            refs = [*events[index].payload.get("fixture_server_calls", []), derived]
            events[index] = replace(
                events[index], payload={**events[index].payload, "fixture_server_calls": refs}
            )
    merged = []
    for event in events:
        merged.append(event)
        merged.extend(additions.get(event.sequence, []))
    return replace(
        capture,
        events=tuple(replace(event, sequence=index) for index, event in enumerate(merged)),
        unknowns=tuple(dict.fromkeys(unknowns)),
    )


def recover_server_calls(capture: NativeCapture, binding: FixtureServerWindow) -> NativeCapture:
    """Keep exact server evidence and fail closed for calls that cannot be placed."""
    unknowns = list(capture.unknowns)
    issue = _capture_issue(capture)
    if issue is not None:
        unknowns.append(issue)
    sources, records = list(capture.source_files), list(capture.raw_records)
    additions: dict[int, list[NativeEvent]] = {}
    events = list(capture.events)
    positions = {event.sequence: index for index, event in enumerate(events)}
    placements = _Placements(capture)
    budget = [binding.max_bytes]
    skipped_blank_lines: dict[str, Any] = {"total": 0, "bySource": {}}
    try:
        checkpoint = _checkpoint(binding, budget)
        sources.append(binding.checkpoint)
        records.append(
            {
                "fixtureServerWindow": {
                    "logRoot": str(binding.log_root),
                    "startedAt": binding.started_at,
                    "endedAt": binding.ended_at,
                    "checkpoint": asdict(binding.checkpoint),
                    "checkpointRow": checkpoint,
                    "ownerId": binding.owner_id,
                    "projectRoot": str(binding.project_root),
                    "maxBytes": binding.max_bytes,
                    "maxFiles": binding.max_files,
                    "fileCapBasis": "instrument discovery/read cap; producer default keep-days plus current day, not configured server retention",
                    "skippedBlankLines": skipped_blank_lines,
                }
            }
        )
        for source, number, row in _rows(binding, budget, skipped_blank_lines):
            if source not in sources:
                sources.append(source)
            if row is None or number is None:
                continue
            placement = placements.place(row)
            where = (
                {"call_id": placement[0].payload.get("call_id"), "basis": placement[1]}
                if placement
                else None
            )
            record = {
                "source": source.path,
                "source_sha256": source.sha256,
                "line": number,
                "row_sha256": json_digest(row),
                "row": row,
                "placement": where,
            }
            records.append({"fixtureServerCall": record})
            if row["dropped"]:
                unknowns.append(
                    f"binding-unavailable: fixture server log reports {row['dropped']} dropped calls; {source.path}:{number}; sha256={json_digest(row)}"
                )
            if placement is None:
                unknowns.append(
                    f"binding-unavailable: unplaceable fixture AR call {row['tool']!r}; {source.path}:{number}; sha256={json_digest(row)}"
                )
                continue
            event, basis = placement
            if basis == "unique-opaque-interval":
                additions.setdefault(event.sequence, []).extend(
                    _recovered(capture, event, source, number, row)
                )
            else:
                index = positions[event.sequence]
                refs = [*events[index].payload.get("fixture_server_calls", []), record]
                events[index] = replace(
                    events[index], payload={**events[index].payload, "fixture_server_calls": refs}
                )
    except (OSError, ValueError, UnicodeError) as error:
        reason = str(error)
        unknowns.append(
            reason
            if reason.startswith(("binding-unavailable:", "schema-incompatible:"))
            else f"schema-incompatible: fixture server log capture: {reason}"
        )
    merged = []
    for event in events:
        merged.append(event)
        merged.extend(additions.get(event.sequence, []))
    return replace(
        capture,
        events=tuple(replace(event, sequence=index) for index, event in enumerate(merged)),
        source_files=tuple(dict.fromkeys(sources)),
        raw_records=tuple(records),
        raw_export_sha256=raw_export_digest(capture.harness, capture.session_id, records),
        unknowns=tuple(dict.fromkeys(unknowns)),
    )
