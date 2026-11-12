"""Merge exact-owner sandbox host events with genuinely dated native records.

Native carriers: paseo_plugin/server/subagents.ts, turn_events.ts, turn_log.ts.
A file's presence proves no completeness: the capture owner supplies its actual
recording window, and missing lifecycle/order evidence remains HW1 unknown.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..artifacts import sha256_file
from .models import NativeCapture, NativeEvent, SourceFile, raw_export_digest


@dataclass(frozen=True)
class HostCaptureWindow:
    started_at: str
    ended_at: str
    complete: bool = False


@dataclass(frozen=True)
class HostEventMerge:
    capture: NativeCapture
    metadata: dict[str, Any]
    hw1_coverage: str


def _epoch(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo is not None else None
    except ValueError:
        return None


def _owner(row: dict[str, Any]) -> str | None:
    if row.get("type") == "agent.provider_subagents.update":
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        helper = payload.get("subagent")
        value = (
            helper.get("parentAgentId")
            if isinstance(helper, dict)
            else payload.get("parentAgentId")
        )
    elif row.get("type") == "agent.turn_ended":
        agent = row.get("agent")
        value = agent.get("id") if isinstance(agent, dict) else None
    else:
        value = row.get("agent")
    return value if isinstance(value, str) else None


def _read_host_file(
    path: Path,
    capture: NativeCapture,
    owner: str,
    *,
    turn_log: bool,
) -> tuple[list[NativeEvent], list[dict[str, Any]], SourceFile | None, list[str]]:
    if not path.is_file():
        return [], [], None, [f"missing host capture: {path}"]
    source = SourceFile(str(path.resolve()), sha256_file(path))
    events: list[NativeEvent] = []
    records: list[dict[str, Any]] = []
    unknowns: list[str] = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("host record is not an object")
            except ValueError as error:
                unknowns.append(f"{path}:{number}: invalid host record: {error}")
                continue
            native = row.get("native") if "observedAt" in row else row
            if not isinstance(native, dict):
                unknowns.append(f"{path}:{number}: native host recorder envelope is malformed")
                continue
            if _owner(native) != owner:
                if _owner(native) is None and native.get("type") in (
                    "agent.provider_subagents.update",
                    "agent.turn_ended",
                ):
                    unknowns.append(f"{path}:{number}: relevant host carrier has no owner identity")
                continue
            records.append(row)
            timestamp = row.get("at") if turn_log else row.get("observedAt", row.get("timestamp"))
            kind = "turn_end_log" if turn_log else "native"
            if (
                isinstance(native.get("payload"), dict)
                and native["payload"].get("kind") == "timeline"
            ):
                kind = "host_helper_timeline"
            events.append(
                NativeEvent(
                    kind, capture.session_id, number, native, source.path, source.sha256, timestamp
                )
            )
    if sha256_file(path) != source.sha256:
        unknowns.append(f"host capture changed while reading: {path}")
    return events, records, source, unknowns


def merge_host_evidence(
    capture: NativeCapture,
    *,
    owner_id: str,
    host_event_paths: Iterable[Path],
    turn_ends_path: Path | None = None,
    window: HostCaptureWindow | None = None,
) -> HostEventMerge:
    events = list(capture.events)
    records = list(capture.raw_records)
    sources = list(capture.source_files)
    unknowns = list(capture.unknowns)
    metadata: dict[str, Any] = {"ownerHostAgentId": owner_id, "nativeSessionId": capture.session_id}
    paths = [(path, False) for path in host_event_paths]
    if turn_ends_path is not None:
        paths.append((turn_ends_path, True))
    for path, is_log in paths:
        found, raw, source, issues = _read_host_file(path, capture, owner_id, turn_log=is_log)
        events.extend(found)
        records.extend(raw)
        unknowns.extend(issues)
        if source is not None:
            sources.append(source)
    ordered, unsequenced, order_issues = _order(events, window)
    unknowns.extend(order_issues)
    metadata["unsequencedEvents"] = [asdict(event) for event in unsequenced]
    metadata["recordingWindow"] = asdict(window) if window is not None else None
    metadata["hw1CoverageReasons"] = list(dict.fromkeys(unknowns))
    merged = replace(
        capture,
        events=tuple(replace(event, sequence=index) for index, event in enumerate(ordered)),
        raw_records=tuple(records),
        source_files=tuple(dict.fromkeys(sources)),
        unknowns=tuple(dict.fromkeys(unknowns)),
        raw_export_sha256=raw_export_digest(capture.harness, capture.session_id, records),
    )
    return HostEventMerge(merged, metadata, "unknown" if unknowns else "complete")


def merge_host_events(
    capture: NativeCapture,
    *,
    owner_id: str,
    host_event_paths: Iterable[Path],
    turn_ends_path: Path | None = None,
    window: HostCaptureWindow | None = None,
) -> HostEventMerge:
    """Merge evidence and require the full helper lifecycle for HW1 fixtures."""
    result = merge_host_evidence(
        capture,
        owner_id=owner_id,
        host_event_paths=host_event_paths,
        turn_ends_path=turn_ends_path,
        window=window,
    )
    issues = (*result.capture.unknowns, *_lifecycle_issues(list(result.capture.events), window))
    result.metadata["hw1CoverageReasons"] = list(dict.fromkeys(issues))
    return HostEventMerge(
        replace(result.capture, unknowns=tuple(dict.fromkeys(issues))),
        result.metadata,
        "unknown" if issues else "complete",
    )


def _order(
    events: list[NativeEvent], window: HostCaptureWindow | None
) -> tuple[list[NativeEvent], list[NativeEvent], list[str]]:
    groups: dict[float, list[NativeEvent]] = defaultdict(list)
    unsequenced: list[NativeEvent] = []
    issues: list[str] = []
    bounds = (
        (_epoch(window.started_at), _epoch(window.ended_at)) if window is not None else (None, None)
    )
    for event in events:
        stamp = _epoch(event.native_timestamp)
        if stamp is None:
            unsequenced.append(event)
        elif (
            bounds[0] is not None and bounds[1] is not None and not bounds[0] <= stamp <= bounds[1]
        ):
            continue  # Original records remain preserved; this event is outside the selected window.
        else:
            groups[stamp].append(event)
    ordered: list[NativeEvent] = []
    for _, group in sorted(groups.items()):
        if len({event.source for event in group}) > 1:
            unsequenced.extend(group)
        else:
            ordered.extend(group)
    if unsequenced:
        issues.append(
            "HW1 event ordering unknown: missing timestamps or equal cross-source timestamps"
        )
    return ordered, unsequenced, issues


def _lifecycle_issues(events: list[NativeEvent], window: HostCaptureWindow | None) -> list[str]:
    issues: list[str] = []
    if (
        window is None
        or not window.complete
        or _epoch(window.started_at) is None
        or _epoch(window.ended_at) is None
    ):
        issues.append("HW1 host recording window is not proven complete")
    running: set[str] = set()
    started: set[str] = set()
    turns = 0
    for event in events:
        row = event.payload
        if (
            row.get("type") == "agent.turn_ended"
            and isinstance(row.get("turnId"), str)
            and row["turnId"]
            and isinstance(row.get("timeline"), list)
        ):
            turns += 1
        issue = _helper_lifecycle(row, running, started)
        if issue is not None:
            issues.append(issue)
    if not started or running or not turns:
        issues.append("HW1 complete helper lifecycle and scoped host turn-end capture are required")
    return issues


def _helper_lifecycle(row: dict[str, Any], running: set[str], started: set[str]) -> str | None:
    payload = row.get("payload")
    if row.get("type") != "agent.provider_subagents.update" or not isinstance(payload, dict):
        return None
    if payload.get("kind") == "timeline":
        return None
    helper = payload.get("subagent")
    if payload.get("kind") == "upsert" and isinstance(helper, dict):
        if not all(
            isinstance(helper.get(key), str) and helper[key]
            for key in ("id", "status", "createdAt")
        ):
            return "HW1 helper lifecycle native fields are incomplete"
        identity = helper["id"]
        if helper["status"] == "running":
            started.add(identity)
            running.add(identity)
            return None
    else:
        identity = payload.get("subagentId")
    if not isinstance(identity, str) or identity not in started:
        return "HW1 helper stop has no captured native start"
    running.discard(identity)
    return None
