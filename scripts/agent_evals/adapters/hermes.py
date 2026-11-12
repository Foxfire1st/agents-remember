"""Capture an exact Hermes session from its observed native SQLite schema.

Schema authority: table definitions in ~/.hermes/state.db inspected read-only,
2026-10-29. Only the selected session's message and model/usage fields are read;
credentials, system prompt tables and unrelated sessions are never extracted.

Both observed compression shapes preserve native ID order. The 2026-10-29
projection copies retained actions into active rows; v0.21.7 can instead archive
the middle turns without an active copy. Emit unique archived actions before
their later native rows, preferring active copies by native call/result identity
and role/content for prose. Every row remains exact export provenance. Compaction
summaries remain context whether active or archived; active counters, chronology
and the terminal-turn proof still describe only the active projection.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..artifacts import sha256_file
from .capture import CaptureBuilder, _event_time
from .hermes_dispatch import normalize_hermes_dispatches
from .models import NativeCapture, NativeEvent, SourceFile, raw_export_digest

SESSION_COLUMNS = (
    "id",
    "source",
    "model",
    "parent_session_id",
    "started_at",
    "ended_at",
    "end_reason",
    "message_count",
    "api_call_count",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "cwd",
)
MESSAGE_COLUMNS = (
    "id",
    "session_id",
    "role",
    "content",
    "tool_call_id",
    "tool_calls",
    "tool_name",
    "timestamp",
    "token_count",
    "finish_reason",
    "reasoning",
    "reasoning_content",
    "active",
)


def capture_hermes_db(path: Path, session_id: str) -> NativeCapture:
    builder = CaptureBuilder("hermes", session_id)
    if not path.is_file():
        builder.unknowns.append(f"missing native transcript: {path}")
        return normalize_hermes_dispatches(builder.finish())
    source = SourceFile(str(path.resolve()), sha256_file(path))
    builder.sources.append(source)
    if not _read_session(builder, path, source):
        return normalize_hermes_dispatches(builder.finish())
    # The read transaction's exported rows are the frozen transcript. Main/WAL
    # hashes describe the observed native source, never invalidate that selected
    # snapshot merely because another session changes the live database.
    wal = path.with_name(path.name + "-wal")
    if wal.is_file():
        builder.sources.append(SourceFile(str(wal.resolve()), sha256_file(wal)))
    return normalize_hermes_dispatches(builder.finish())


def _retained_rows(
    value: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], SourceFile]:
    session_id = value.get("session_id")
    rows = value.get("raw_records")
    if value.get("harness") != "hermes" or not isinstance(session_id, str) or not session_id:
        raise ValueError("retained capture is not a bound Hermes session")
    if not isinstance(rows, (list, tuple)) or raw_export_digest(
        "hermes", session_id, rows
    ) != value.get("raw_export_sha256"):
        raise ValueError("retained native row export digest differs or is absent")
    sessions, messages = [], []
    for index, record in enumerate(rows):
        if not isinstance(record, dict):
            raise ValueError(f"retained Hermes record is not an object: $.raw_records[{index}]")
        table = record.get("table")
        if table is None:
            continue  # Preserved supplementary host provenance, parsed by its owner.
        row = record.get("row")
        columns = SESSION_COLUMNS if table == "sessions" else MESSAGE_COLUMNS
        if (
            table not in {"sessions", "messages"}
            or not isinstance(row, dict)
            or set(row) != set(columns)
        ):
            raise ValueError(
                f"unsupported retained Hermes kind={table!r}; keys={sorted(record)}; "
                f"JSONPath=$.raw_records[{index}]; sha256={value['raw_export_sha256']}"
            )
        if row.get("id" if table == "sessions" else "session_id") != session_id:
            raise ValueError("retained Hermes row belongs to a different native session")
        (sessions if table == "sessions" else messages).append(row)
    if len(sessions) != 1:
        raise ValueError("retained Hermes export must contain exactly one selected session row")
    events = [NativeEvent(**event) for event in value["events"]]
    bindings = {
        (event.source, event.source_sha256)
        for event in events
        if event.kind == "session"
        and event.session_id == session_id
        and event.payload == sessions[0]
    }
    sources = [SourceFile(**source) for source in value["source_files"]]
    native = [source for source in sources if (source.path, source.sha256) in bindings]
    if len(bindings) != 1 or len(native) != 1:
        raise ValueError("retained Hermes session does not bind one original native source/hash")
    _message_order(messages)
    return sessions[0], messages, native[0]


def _message_order(messages: list[dict[str, Any]]) -> None:
    ids = [row["id"] for row in messages]
    if not all(type(identity) is int for identity in ids) or ids != sorted(set(ids)):
        raise ValueError("retained Hermes rows do not preserve unique native message id order")


_REDERIVED_DIAGNOSTICS = frozenset(
    {
        "Hermes selected active message rows/count are incomplete",
        "Hermes selected message chronology is incomplete",
        "Hermes selected native turn has no terminal assistant stop",
        "Hermes selected native tool-call shape is incomplete",
        "Hermes selected native tool calls/results are incomplete",
        "Hermes tool_calls is not a JSON array",
        "Hermes tool_calls has an unrecognized entry",
    }
)


def recapture_retained_hermes(value: dict[str, Any]) -> NativeCapture:
    """Reparse frozen selected rows, retaining inactive rows and host evidence.

    No live database or original source is reopened. Only explicitly enumerated
    native parser diagnostics are rederived; unrelated unknowns remain unknown.
    """
    session, messages, source = _retained_rows(value)
    builder = CaptureBuilder("hermes", value["session_id"])
    builder.sources.extend(SourceFile(**item) for item in value["source_files"])
    _session_rows(builder, session, messages, source)
    capture = normalize_hermes_dispatches(builder.finish())
    old_unknowns = [
        reason
        for reason in value["unknowns"]
        if reason not in _REDERIVED_DIAGNOSTICS
        and not reason.startswith(source.path + ": unsupported Hermes dispatch ")
    ]
    supplement = [
        NativeEvent(**event) for event in value["events"] if event["source"] != source.path
    ]
    events = _merge_supplement(capture.events, supplement)
    return replace(
        capture,
        events=tuple(replace(event, sequence=index) for index, event in enumerate(events)),
        raw_records=tuple(value["raw_records"]),
        raw_export_sha256=value["raw_export_sha256"],
        unknowns=tuple(dict.fromkeys([*capture.unknowns, *old_unknowns])),
    )


def _merge_supplement(
    native: tuple[NativeEvent, ...], supplement: list[NativeEvent]
) -> list[NativeEvent]:
    """Merge host time witnesses without sorting the native-ID stream."""
    pending = iter(sorted(supplement, key=_event_time))
    item = next(pending, None)
    result = []
    for event in native:
        if not (event.kind == "context" and _compaction_context(event.payload)):
            while item is not None and _event_time(item) <= _event_time(event):
                result.append(item)
                item = next(pending, None)
        result.append(event)
    if item is not None:
        result.append(item)
    result.extend(pending)
    return result


def hermes_final_stop(capture: NativeCapture) -> dict[str, Any] | None:
    """Prove a complete active native turn ended, without trusting actor prose."""
    try:
        session, messages, source = _retained_rows(capture.to_dict())
        builder = CaptureBuilder("hermes", capture.session_id)
        _session_rows(builder, session, messages, source)
        if builder.unknowns or any(row["active"] not in (0, 1) for row in messages):
            return None
        terminal = [row for row in messages if row["active"] == 1][-1]
        events = [
            event
            for event in capture.events
            if event.kind == "message"
            and event.session_id == capture.session_id
            and event.payload == terminal
            and (event.source, event.source_sha256) == (source.path, source.sha256)
        ]
        if len(events) != 1:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "sessionId": capture.session_id,
        "rowId": terminal["id"],
        "sequence": events[0].sequence,
        "source": source.path,
        "sourceSha256": source.sha256,
        "rawExportSha256": capture.to_dict()["raw_export_sha256"],
        "outcome": "finished",
    }


def _read_session(builder: CaptureBuilder, path: Path, source: SourceFile) -> bool:
    session_id = builder.session_id
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            sessions = db.execute(
                f"SELECT {','.join(SESSION_COLUMNS)} FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchall()
            if len(sessions) != 1:
                builder.unknowns.append("selected native Hermes session absent")
                return False
            session = dict(sessions[0])
            messages = [
                dict(message)
                for message in db.execute(
                    f"SELECT {','.join(MESSAGE_COLUMNS)} FROM messages WHERE session_id = ? ORDER BY id",
                    (session_id,),
                )
            ]
            _session_rows(builder, session, messages, source)
    except (sqlite3.Error, ValueError) as exc:
        builder.unknowns.append(f"Hermes native schema/capture error: {exc}")
    return True


def _session_rows(
    builder: CaptureBuilder,
    session: dict[str, Any],
    messages: list[dict[str, Any]],
    source: SourceFile,
) -> None:
    """One native parser for SQLite selection and verified retained row exports."""
    builder.native_timestamp = session["started_at"]
    builder.records.append({"table": "sessions", "row": session})
    builder.event("session", session, source)
    builder.event(
        "usage", {key: session[key] for key in SESSION_COLUMNS if key.endswith("tokens")}, source
    )
    _coverage(builder, session, messages)
    first_active = next((row for row in messages if row["active"] == 1), None)
    seen = _active_identities(messages)
    for index, message in enumerate(messages):
        builder.records.append({"table": "messages", "row": message})
        if message["active"] == 1:
            _context_shape(builder, message, first_active, source, index)
            _message(builder, message, source)
        elif message["active"] == 0:
            _archived_message(builder, message, source, seen, index)
        else:
            builder.unknowns.append(
                f"{source.path}: unsupported Hermes message active={message['active']!r}; "
                f"kind='messages'; keys={sorted(message)}; "
                f"JSONPath=$.messages[{index}].active; sha256={source.sha256}"
            )


def _call_ids(call: dict[str, Any]) -> set[str]:
    return {
        identity
        for key in ("id", "call_id")
        if isinstance(identity := call.get(key), str) and identity
    }


def _row_identities(row: dict[str, Any]) -> list[set[tuple[str, str]]]:
    try:
        calls = _calls(row)
    except (TypeError, ValueError):
        return []  # Emit the row so the ordinary parser records its failure.
    groups = [{("call", identity) for identity in _call_ids(call)} for call in calls]
    if row["role"] == "tool":
        if isinstance(row["tool_call_id"], str) and row["tool_call_id"]:
            groups.append({("result", row["tool_call_id"])})
    elif not calls:
        groups.append({("message", json.dumps([row["role"], row["content"]], sort_keys=True))})
    return groups


def _active_identities(messages: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {
        identity
        for row in messages
        if row["active"] == 1
        for group in _row_identities(row)
        for identity in group
    }


def _archived_message(
    builder: CaptureBuilder,
    row: dict[str, Any],
    source: SourceFile,
    seen: set[tuple[str, str]],
    index: int,
) -> None:
    groups = _row_identities(row)
    copied = [bool(group & seen) for group in groups]
    if any(copied):
        if not all(copied):
            builder.unknowns.append(
                f"{source.path}: unsupported Hermes archived row partially copied by compaction; "
                f"kind='messages'; keys={sorted(row)}; "
                f"JSONPath=$.messages[{index}].tool_calls; sha256={source.sha256}"
            )
        return  # A partial row is retained raw, never treated as a complete action.
    _message(builder, row, source)
    seen.update(identity for group in groups for identity in group)


def _context_shape(
    builder: CaptureBuilder,
    row: dict[str, Any],
    first_active: dict[str, Any] | None,
    source: SourceFile,
    index: int,
) -> None:
    if (
        isinstance(row.get("content"), str)
        and row["content"].startswith("[CONTEXT COMPACTION")
        and (not _compaction_context(row) or row is not first_active)
    ):
        builder.unknowns.append(
            f"{source.path}: unsupported Hermes compaction context; kind='messages'; "
            f"keys={sorted(row)}; JSONPath=$.messages[{index}]; sha256={source.sha256}"
        )


def _coverage(builder: CaptureBuilder, session: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Check actual selected data; Hermes counters describe active rows.

    Native authority: hermes_state_messages.py resets counters from the active
    projection after compression/rewind; inactive original rows remain exported.
    """
    active = [row for row in rows if row["active"] == 1]
    if not active or session["message_count"] != len(active):
        builder.unknowns.append("Hermes selected active message rows/count are incomplete")
    all_timestamps = [row["timestamp"] for row in active]
    valid = all(type(stamp) in (int, float) and math.isfinite(stamp) for stamp in all_timestamps)
    # The reference-only first row was created at compression time, unlike the
    # active action rows copied with their original timestamps and native order.
    ordered = active[1:] if active and _compaction_context(active[0]) else active
    timestamps = [row["timestamp"] for row in ordered]
    if not valid or timestamps != sorted(timestamps):
        builder.unknowns.append("Hermes selected message chronology is incomplete")
    terminal = active[-1] if active else {}
    calls = terminal.get("tool_calls")
    try:
        calls = json.loads(calls) if calls else []
    except (TypeError, ValueError):
        calls = "unreadable"
    if terminal.get("role") != "assistant" or calls or terminal.get("finish_reason") != "stop":
        builder.unknowns.append("Hermes selected native turn has no terminal assistant stop")
    _tool_coverage(builder, active)


def _compaction_context(row: dict[str, Any]) -> bool:
    return (
        row.get("role") == "user"
        and isinstance(row.get("content"), str)
        and row["content"].startswith("[CONTEXT COMPACTION — REFERENCE ONLY] ")
        and all(
            row.get(key) is None
            for key in ("tool_calls", "tool_call_id", "tool_name", "finish_reason")
        )
    )


def _tool_coverage(builder: CaptureBuilder, rows: list[dict[str, Any]]) -> None:
    calls: set[str] = set()
    results: set[str] = set()
    for row in rows:
        try:
            calls.update(call["id"] for call in _calls(row) if isinstance(call.get("id"), str))
        except ValueError:
            builder.unknowns.append("Hermes selected native tool-call shape is incomplete")
        if row["role"] == "tool" and isinstance(row["tool_call_id"], str):
            results.add(row["tool_call_id"])
    if calls != results:
        builder.unknowns.append("Hermes selected native tool calls/results are incomplete")


def _calls(row: dict[str, Any]) -> list[dict[str, Any]]:
    if not row["tool_calls"]:
        return []
    if not isinstance(row["tool_calls"], str):
        raise ValueError("Hermes tool_calls must be native serialized JSON")
    calls = json.loads(row["tool_calls"])
    if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
        raise ValueError("Hermes tool_calls is not an array of native call objects")
    # The order admits both native identity spellings; normalize only the parsed
    # call view, never the exported row or its serialized tool_calls bytes.
    calls = [call if "id" in call else {**call, "id": call.get("call_id")} for call in calls]
    if not all(isinstance(call.get("id"), str) and call["id"] for call in calls):
        raise ValueError("Hermes native tool call id is absent")
    return calls


def _message(
    builder: CaptureBuilder,
    row: dict[str, Any],
    source: SourceFile,
) -> None:
    builder.native_timestamp = row["timestamp"]
    builder.event("context" if _compaction_context(row) else "message", row, source)
    try:
        calls = _calls(row)
    except (TypeError, ValueError):
        builder.unknowns.append("Hermes tool_calls is not a JSON array")
        return
    for call in calls:
        if not isinstance(call.get("function"), dict):
            builder.unknowns.append("Hermes tool_calls has an unrecognized entry")
            continue
        builder.call({"call_id": call.get("id"), **call["function"]}, source)
    if row["role"] == "tool":
        builder.event(
            "tool_result",
            {
                "call_id": row["tool_call_id"],
                "name": row["tool_name"],
                "output": row["content"],
            },
            source,
        )
