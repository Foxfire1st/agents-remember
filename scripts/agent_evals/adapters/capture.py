"""Explicit native transcript schemas, preserving inputs and unclassified rows.

No recursive string/blob search is evidence of a tool call. Unsupported dispatch
syntax stays an opaque action with its complete source-bound native outputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..artifacts import json_digest, sha256_file
from .codex_dispatch import DispatchReturn, returned_dispatches
from .codex_items import completed_item, covered_dispatches
from .models import Harness, NativeCapture, NativeEvent, SourceFile, raw_export_digest, tool_server
from .opaque_dispatch import opaque_action


class CaptureBuilder:
    def __init__(self, harness: Harness, session_id: str) -> None:
        self.harness: Harness = harness
        self.session_id = session_id
        self.events: list[NativeEvent] = []
        self.sources: list[SourceFile] = []
        self.records: list[dict[str, Any]] = []
        self.unknowns: list[str] = []
        self.native_timestamp: str | float | int | None = None
        self.opaque_dispatches: dict[tuple[str, str, str], str] = {}

    def event(self, kind: str, payload: dict[str, Any], source: SourceFile) -> None:
        self.events.append(
            NativeEvent(
                kind,
                self.session_id,
                len(self.events),
                payload,
                source.path,
                source.sha256,
                self.native_timestamp,
            )
        )

    def call(
        self, payload: dict[str, Any], source: SourceFile, *, native_children: bool = False
    ) -> None:
        name = payload.get("name")
        if not isinstance(name, str):
            self.unknowns.append(
                f"{source.path}: tool call missing name; kind={payload.get('type')!r}; keys={sorted(payload)}; JSONPath=$.payload"
            )
            return
        payload = {**payload, "server": tool_server(name, payload.get("server"))}
        self.event("tool_call", payload, source)
        if not native_children and name in {"functions.exec", "exec", "mcp_call", "call_tool"}:
            reason = f"{source.path}: unsupported nested dispatch name={name!r}; kind={payload.get('type')!r}; keys={sorted(payload)}; JSONPath=$.payload"
            if self.harness == "codex":
                self.opaque_dispatches[_opaque_key(self.events[-1])] = reason
            else:
                self.unknowns.append(reason)

    def finish(self) -> NativeCapture:
        if not self.events:
            self.unknowns.append("no native events captured")
        return NativeCapture(
            self.harness,
            self.session_id,
            tuple(self.events),
            tuple(self.sources),
            tuple(self.records),
            tuple(dict.fromkeys(self.unknowns)),
            raw_export_sha256=raw_export_digest(self.harness, self.session_id, self.records),
        )


def jsonl_records(
    builder: CaptureBuilder, paths: Iterable[Path]
) -> Iterable[tuple[dict[str, Any], SourceFile]]:
    for path in paths:
        if not path.is_file():
            builder.unknowns.append(f"missing native transcript: {path}")
            continue
        source = SourceFile(str(path.resolve()), sha256_file(path))
        builder.sources.append(source)
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("record is not an object")
                except (ValueError, UnicodeError) as exc:
                    builder.unknowns.append(f"{path}:{number}: invalid native record: {exc}")
                    continue
                yield row, source
        if sha256_file(path) != source.sha256:
            builder.unknowns.append(f"{path}: source changed during capture")


def capture_codex_jsonl(paths: Iterable[Path], session_id: str) -> NativeCapture:
    builder = CaptureBuilder("codex", session_id)
    identities: dict[str, str] = {}
    selected: list[tuple[dict[str, Any], SourceFile]] = []
    for row, source in jsonl_records(builder, paths):
        kind, payload = row.get("type"), row.get("payload")
        if not isinstance(payload, dict):
            builder.unknowns.append(
                f"{source.path}: unrecognized Codex record kind={kind!r}; keys={sorted(row)}; JSONPath=$.payload"
            )
            continue
        if kind == "session_meta":
            identity = payload.get("id")
            if isinstance(identity, str):
                identities[source.path] = identity
        if identities.get(source.path) != session_id:
            builder.unknowns.append(f"{source.path}: absent or different native session identity")
            continue
        builder.records.append(row)
        selected.append((row, source))
    covered = covered_dispatches(builder.records, session_id)
    returned, errors = returned_dispatches(builder.records, covered, builder.session_id)
    for row, source in selected:
        builder.native_timestamp = row.get("timestamp")
        _codex_event(builder, row.get("type"), row["payload"], source, (covered, returned, errors))
    return _opaque_codex_capture(builder.finish(), builder.opaque_dispatches)


def _opaque_key(event: NativeEvent) -> tuple[str, str, str]:
    return event.source, event.source_sha256, json_digest(event.payload)


def _opaque_codex_capture(
    capture: NativeCapture, reasons: dict[tuple[str, str, str], str]
) -> NativeCapture:
    """Classify only dispatch-parser failures, after every native output is present."""
    events = []
    unknowns = list(capture.unknowns)
    for event in capture.events:
        reason = reasons.get(_opaque_key(event)) if event.kind == "tool_call" else None
        classified = event
        if reason is not None:
            classified, problems = opaque_action(capture, event, reason)
            unknowns.extend(problems)
        events.append(classified)
    return replace(capture, events=tuple(events), unknowns=tuple(dict.fromkeys(unknowns)))


def _codex_event(
    builder: CaptureBuilder,
    kind: object,
    payload: dict[str, Any],
    source: SourceFile,
    dispatches: tuple[set[str], dict[str, DispatchReturn], dict[str, str]],
) -> None:
    if kind in {"session_meta", "turn_context"}:
        builder.event("session" if kind == "session_meta" else "context", payload, source)
    elif kind == "response_item":
        _codex_response(builder, payload, source, dispatches)
    elif kind == "world_state":
        if not isinstance(payload.get("full"), bool) or not isinstance(payload.get("state"), dict):
            builder.unknowns.append(
                f"{source.path}: malformed Codex world_state; keys={sorted(payload)}; JSONPath=$.payload"
            )
        builder.event("context", payload, source)
    elif kind == "token_usage_record":
        if payload.get("session_id") != builder.session_id or not isinstance(
            payload.get("usage"), dict
        ):
            builder.unknowns.append(
                f"{source.path}: malformed/mismatched Codex token_usage_record; keys={sorted(payload)}; JSONPath=$.payload"
            )
        builder.event("usage", payload, source)
    elif kind == "event_msg" and payload.get("type") == "item_completed":
        completed_item(builder, payload, source)
    elif kind == "event_msg":
        builder.event(
            "usage" if payload.get("type") == "token_count" else "native", payload, source
        )
    else:
        builder.unknowns.append(
            f"{source.path}: unsupported Codex record type {kind!r}; keys={sorted(payload)}; JSONPath=$.payload"
        )


def _codex_response(
    builder: CaptureBuilder,
    payload: dict[str, Any],
    source: SourceFile,
    dispatches: tuple[set[str], dict[str, DispatchReturn], dict[str, str]],
) -> None:
    item = payload.get("type")
    if item in {"function_call", "custom_tool_call"}:
        _codex_call(builder, payload, source, dispatches)
    else:
        event_kind = {
            "function_call_output": "tool_result",
            "custom_tool_call_output": "tool_result",
            "message": "message",
        }.get(str(item), "native")
        builder.event(event_kind, payload, source)
        identity = payload.get("call_id")
        returned = dispatches[1].get(identity) if isinstance(identity, str) else None
        if event_kind == "tool_result" and returned is not None and returned.name is not None:
            builder.event(
                "tool_result",
                {
                    "call_id": returned.call_id + "/return/0",
                    "output": returned.result,
                    "native_dispatch": returned.provenance,
                },
                source,
            )


def _codex_call(
    builder: CaptureBuilder,
    payload: dict[str, Any],
    source: SourceFile,
    dispatches: tuple[set[str], dict[str, DispatchReturn], dict[str, str]],
) -> None:
    covered, returned, errors = dispatches
    identity = payload.get("call_id")
    if payload.get("name") not in {"exec", "functions.exec"}:
        builder.call(payload, source)
        return
    builder.call(payload, source, native_children=True)
    result = returned.get(identity) if isinstance(identity, str) else None
    if result is not None and result.name is not None:
        builder.event(
            "tool_call",
            {
                "call_id": result.call_id + "/return/0",
                "name": result.name,
                "arguments": result.arguments,
                "server": tool_server(result.name),
                "native_dispatch": result.provenance,
            },
            source,
        )
    elif identity not in covered and result is None:
        detail = errors.get(
            identity if isinstance(identity, str) else "",
            f"inputKind={payload.get('type')}; inputKeys={sorted(payload)}; outputJSONPath=$.payload.output; no paired supported dispatch record",
        )
        builder.opaque_dispatches[_opaque_key(builder.events[-1])] = (
            f"{source.path}: unsupported Codex nested dispatch call_id={identity}: {detail}"
        )


def recapture_retained_codex(value: dict[str, Any]) -> NativeCapture:
    """Reparse immutable retained native rows; never reopen a live transcript."""
    if value.get("harness") != "codex" or not isinstance(value.get("session_id"), str):
        raise ValueError("retained capture is not a bound Codex session")
    return _replay_codex(value, value["session_id"])


def _replay_codex(value: dict[str, Any], session_id: str) -> NativeCapture:
    rows = value.get("raw_records")
    if not isinstance(rows, (list, tuple)) or raw_export_digest(
        "codex", session_id, rows
    ) != value.get("raw_export_sha256"):
        raise ValueError("retained native row export digest differs or is absent")
    events = [NativeEvent(**event) for event in value["events"]]
    sources = [SourceFile(**source) for source in value["source_files"]]
    native_names = {
        event.source
        for event in events
        if event.kind == "session" and event.payload.get("id") == session_id
    }
    native_sources = [source for source in sources if source.path in native_names]
    if len(native_sources) != 1:
        raise ValueError(
            "retained native session does not bind exactly one original rollout source"
        )
    source = native_sources[0]
    builder = CaptureBuilder("codex", session_id)
    builder.sources.extend(sources)
    builder.records.extend(
        row for row in rows if "type" in row and isinstance(row.get("payload"), dict)
    )
    covered = covered_dispatches(builder.records, session_id)
    returned, errors = returned_dispatches(builder.records, covered, builder.session_id)
    for row in builder.records:
        builder.native_timestamp = row.get("timestamp")
        _codex_event(builder, row["type"], row["payload"], source, (covered, returned, errors))
    supplement = [event for event in events if event.source not in native_names]
    combined = sorted([*builder.events, *supplement], key=_event_time)
    old_unknowns = [
        reason
        for reason in value["unknowns"]
        if not reason.startswith(source.path + ": opaque dispatch exec;")
        and not reason.startswith(source.path + ": unsupported Codex nested dispatch")
    ]
    capture = builder.finish()
    replay = replace(
        capture,
        events=tuple(replace(event, sequence=index) for index, event in enumerate(combined)),
        raw_records=tuple(rows),
        raw_export_sha256=value["raw_export_sha256"],
        unknowns=tuple(dict.fromkeys([*capture.unknowns, *old_unknowns])),
    )
    return _opaque_codex_capture(replay, builder.opaque_dispatches)


def _event_time(event: NativeEvent) -> float:
    stamp = event.native_timestamp
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return float("inf")
    return float(stamp) if isinstance(stamp, (int, float)) else float("inf")


def capture_claude_jsonl(paths: Iterable[Path], session_id: str) -> NativeCapture:
    builder = CaptureBuilder("claude", session_id)
    for row, source in jsonl_records(builder, paths):
        if row.get("sessionId") != session_id:
            builder.unknowns.append(f"{source.path}: absent or different native session identity")
            continue
        builder.records.append(row)
        builder.native_timestamp = row.get("timestamp")
        message = row.get("message")
        if row.get("type") not in {"user", "assistant"} or not isinstance(message, dict):
            builder.event("native", row, source)
            continue
        _claude_message(builder, message, source)
    return builder.finish()


def capture_claude_cli_jsonl(paths: Iterable[Path], session_id: str) -> NativeCapture:
    """Explicit Claude stream-json schema, including genuine final result usage.

    CLI/SDK records name session_id; native project records name sessionId.
    These are separate routes, never guessing through a missing-field fallback.
    """
    builder = CaptureBuilder("claude", session_id)
    for row, source in jsonl_records(builder, paths):
        if row.get("session_id") != session_id:
            builder.unknowns.append(
                f"{source.path}: absent or different native CLI session identity"
            )
            continue
        builder.records.append(row)
        builder.native_timestamp = row.get("timestamp")
        kind = row.get("type")
        if kind == "result":
            builder.event(
                "usage",
                {"result_usage": row.get("usage"), "modelUsage": row.get("modelUsage")},
                source,
            )
        elif kind == "system":
            builder.event("session", row, source)
        elif kind in ("assistant", "user") and isinstance(row.get("message"), dict):
            _claude_message(builder, row["message"], source)
        else:
            builder.event("native", row, source)
    return builder.finish()


def _claude_message(builder: CaptureBuilder, message: dict[str, Any], source: SourceFile) -> None:
    builder.event("message", message, source)
    if isinstance(message.get("usage"), dict):
        builder.event(
            "usage",
            {
                "usage": message["usage"],
                "model": message.get("model"),
                "message_id": message.get("id"),
            },
            source,
        )
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            builder.unknowns.append(f"{source.path}: unrecognized Claude content block")
        elif block.get("type") == "tool_use":
            builder.call(block, source)
        elif block.get("type") == "tool_result":
            builder.event("tool_result", block, source)
