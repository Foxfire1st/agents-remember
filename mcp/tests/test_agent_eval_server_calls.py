"""Fixture AR log placement is independent of opaque script recognition."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters import server_calls
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.adapters.server_calls import FixtureServerWindow, recover_server_calls
from agent_evals.artifacts import json_digest, sha256_file


def _setup(root: Path, *, opaque: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "owner.window.json"
    receipt = {
        "agentId": "host-owner",
        "projectRoot": str(root / "project"),
        "readyAt": "2026-10-30T10:00:00Z",
        "lastTurnEndedAt": "2026-10-30T10:00:10Z",
        "errors": [],
    }
    marker.write_text(json.dumps(receipt))
    logs = root / "tool-logs"
    logs.mkdir()
    source = SourceFile(str(root / "native.jsonl"), "a" * 64)
    call = {
        "call_id": "opaque-parent" if opaque else "native-call",
        "name": "exec" if opaque else "mcp__agents_remember_task__knowledge_read",
        "server": None if opaque else "agents-remember-task",
        "arguments": {"repo_id": "code"},
    }
    if opaque:
        call.update(
            opaque=True,
            input="arbitrary preserved native script",
            native_dispatch={
                "classification": "opaque",
                "reason": "real wire is intentionally not interpreted",
            },
        )
    payloads = [
        ("session", {"id": "native-session"}, "2026-10-30T10:00:00Z"),
        ("tool_call", call, "2026-10-30T10:00:01Z"),
        (
            "tool_result",
            {"call_id": call["call_id"], "output": "full native output"},
            "2026-10-30T10:00:09Z",
        ),
        ("native", {"type": "task_complete"}, "2026-10-30T10:00:10Z"),
    ]
    events = tuple(
        NativeEvent(kind, "native-session", index, payload, source.path, source.sha256, stamp)
        for index, (kind, payload, stamp) in enumerate(payloads)
    )
    capture = NativeCapture("codex", "native-session", events, (source,), (), ())
    row = {
        "time": "2026-10-30T10:00:05Z",
        "tool": "knowledge_read",
        "operation": "read",
        "caller": {"agentId": None, "role": None, "task": None},
        "ok": True,
        "code": None,
        "durationMs": 12.2,
        "answerTokens": 10,
        "resultBytes": 90,
        "resultDigest": None,
        "guardCut": False,
        "detail": None,
        "dropped": 0,
        "arguments": {"repo_id": "code"},
    }
    log = logs / "ar-tools-2026-10-30.jsonl"
    log.write_text(json.dumps(row) + "\n")
    binding = FixtureServerWindow(
        logs,
        receipt["readyAt"],
        "2026-10-30T10:00:11Z",
        SourceFile(str(marker), sha256_file(marker)),
        receipt["agentId"],
        Path(receipt["projectRoot"]),
    )
    return capture, binding, row, log, receipt


def test_server_gate_keeps_exact_log_and_checkpoint_after_cleanup(tmp_path: Path) -> None:
    capture, binding, row, log, receipt = _setup(tmp_path)
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    raw = next(
        record["fixtureServerCall"]
        for record in result.raw_records
        if "fixtureServerCall" in record
    )
    assert raw["row"] == row
    assert raw["source_sha256"] == sha256_file(log)
    assert raw["row_sha256"] == json_digest(row)
    assert raw["placement"] == {"call_id": "native-call", "basis": "native-arguments-and-interval"}
    window = next(
        record["fixtureServerWindow"]
        for record in result.raw_records
        if "fixtureServerWindow" in record
    )
    assert window["checkpointRow"] == receipt
    assert binding.checkpoint in result.source_files
    assert len([event for event in result.events if event.kind == "tool_call"]) == 1
    saved = json.loads(json.dumps(result.to_dict()))
    log.unlink()
    Path(binding.checkpoint.path).unlink()
    assert saved["raw_records"][-1]["fixtureServerCall"]["row"] == row


def test_server_gate_recovers_only_actual_call_in_unique_opaque_interval(tmp_path: Path) -> None:
    capture, binding, row, _, _ = _setup(tmp_path, opaque=True)
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    calls = [event for event in result.events if event.kind == "tool_call"]
    assert [event.payload["name"] for event in calls] == ["exec", "knowledge_read"]
    assert calls[0].payload == capture.events[1].payload
    assert calls[1].payload["arguments"] == row["arguments"]
    assert calls[1].payload["call_id"].startswith("server-log/")
    assert "not a native provider call ID" in calls[1].payload["native_dispatch"]["identityBasis"]
    assert calls[1].native_timestamp == row["time"]


@pytest.mark.parametrize(
    "change",
    [
        "unplaceable",
        "ambiguous",
        "no-session",
        "no-stop",
        "missing-logs",
        "bad-receipt",
        "read-cap",
        "dropped",
    ],
)
def test_server_gate_never_turns_missing_or_unplaceable_evidence_into_success(
    change: str, tmp_path: Path
) -> None:
    capture, binding, row, log, _ = _setup(tmp_path, opaque=change == "ambiguous")
    if change == "unplaceable":
        row["arguments"] = {"repo_id": "foreign"}
        log.write_text(json.dumps(row) + "\n")
    elif change == "ambiguous":
        duplicate = replace(
            capture.events[1],
            sequence=2,
            payload={**capture.events[1].payload, "call_id": "other-parent"},
        )
        output = replace(
            capture.events[2],
            sequence=3,
            payload={"call_id": "other-parent", "output": "other full output"},
        )
        capture = replace(capture, events=(*capture.events, duplicate, output))
    elif change == "no-session":
        capture = replace(capture, session_id="")
    elif change == "no-stop":
        capture = replace(capture, events=capture.events[:-1])
    elif change == "missing-logs":
        log.unlink()
    elif change == "bad-receipt":
        Path(binding.checkpoint.path).write_text("{}")
    elif change == "read-cap":
        binding = replace(binding, max_bytes=1)
    else:
        row["dropped"] = 1
        log.write_text(json.dumps(row) + "\n")
    result = recover_server_calls(capture, binding)
    assert result.unknowns
    assert all(
        reason.startswith(("capture-absent:", "binding-unavailable:", "schema-incompatible:"))
        for reason in result.unknowns
    )
    if change in {"unplaceable", "ambiguous"}:
        reason = next(reason for reason in result.unknowns if "unplaceable" in reason)
        assert str(log) in reason and "knowledge_read" in reason and json_digest(row) in reason
    if change == "read-cap":
        assert any("incomplete, not truncated" in reason for reason in result.unknowns)


def test_server_gate_excludes_only_receipted_pre_actor_rows(tmp_path: Path) -> None:
    capture, binding, row, log, _ = _setup(tmp_path)
    before = {**row, "time": "2026-10-30T09:59:59Z", "tool": "server_info"}
    log.write_text(json.dumps(before) + "\n" + json.dumps(row) + "\n")
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    rows = [
        record["fixtureServerCall"]["row"]
        for record in result.raw_records
        if "fixtureServerCall" in record
    ]
    assert rows == [row]


def test_server_gate_does_not_reuse_one_native_call_for_two_log_calls(tmp_path: Path) -> None:
    capture, binding, row, log, _ = _setup(tmp_path)
    second = {**row, "time": "2026-10-30T10:00:06Z"}
    log.write_text(json.dumps(row) + "\n" + json.dumps(second) + "\n")
    result = recover_server_calls(capture, binding)
    assert any("unplaceable" in reason and f"{log}:2" in reason for reason in result.unknowns)


def test_server_gate_uses_proven_parent_interval_for_completed_native_child(
    tmp_path: Path,
) -> None:
    capture, binding, _, _, _ = _setup(tmp_path)
    child = replace(
        capture.events[1],
        sequence=2,
        native_timestamp="2026-10-30T10:00:08Z",
        payload={**capture.events[1].payload, "native_dispatch": {"outerCallId": "outer"}},
    )
    parent = replace(
        capture.events[1],
        payload={"call_id": "outer", "name": "exec", "server": None, "input": "native script"},
    )
    child_result = replace(capture.events[2], sequence=3, native_timestamp=child.native_timestamp)
    parent_result = replace(
        capture.events[2], sequence=4, payload={"call_id": "outer", "output": "full output"}
    )
    capture = replace(
        capture,
        events=(
            capture.events[0],
            parent,
            child,
            child_result,
            parent_result,
            replace(capture.events[3], sequence=5),
        ),
    )
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    record = next(
        row["fixtureServerCall"] for row in result.raw_records if "fixtureServerCall" in row
    )
    assert record["placement"]["call_id"] == child.payload["call_id"]


@pytest.mark.parametrize(
    "field,value", [("agentId", "foreign-owner"), ("projectRoot", "/foreign/project")]
)
def test_server_gate_rejects_foreign_checkpoint(field: str, value: str, tmp_path: Path) -> None:
    capture, binding, _, _, receipt = _setup(tmp_path)
    receipt[field] = value
    marker = Path(binding.checkpoint.path)
    marker.write_text(json.dumps(receipt))
    binding = replace(binding, checkpoint=SourceFile(str(marker), sha256_file(marker)))
    result = recover_server_calls(capture, binding)
    assert any("binding-unavailable:" in reason for reason in result.unknowns)


@pytest.mark.parametrize("size", [16, 64])
def test_server_gate_indexes_native_events_once(size: int, tmp_path: Path) -> None:
    capture, binding, row, log, _ = _setup(tmp_path)
    iterations = [0]

    class MeteredEvents(tuple):
        def __iter__(self):
            for event in super().__iter__():
                iterations[0] += 1
                yield event

    events = [capture.events[0]]
    rows = []
    for index in range(size):
        call_id = f"call-{index}"
        arguments = {"repo_id": f"code-{index}"}
        events.extend(
            [
                replace(
                    capture.events[1],
                    sequence=2 * index + 1,
                    payload={
                        **capture.events[1].payload,
                        "call_id": call_id,
                        "arguments": arguments,
                    },
                ),
                replace(
                    capture.events[2],
                    sequence=2 * index + 2,
                    payload={"call_id": call_id, "output": "full native output"},
                ),
            ]
        )
        rows.append({**row, "arguments": arguments})
    events.append(replace(capture.events[-1], sequence=2 * size + 1))
    capture = replace(capture, events=MeteredEvents(events))
    log.write_text("".join(json.dumps(item) + "\n" for item in rows))
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    assert iterations[0] <= 20 * len(events)


@pytest.mark.parametrize("size", [32, 128])
def test_server_gate_bounds_opaque_interval_lookup(size: int, tmp_path: Path, monkeypatch) -> None:
    capture, binding, row, log, _ = _setup(tmp_path, opaque=True)
    base = datetime.fromisoformat(binding.started_at.replace("Z", "+00:00")).timestamp()
    events = [capture.events[0]]
    rows = []
    for index in range(size):
        lower = base + (8 * index / size) + 0.1
        upper = lower + (4 / size)
        identity = f"opaque-{index}"
        events.extend(
            [
                replace(
                    capture.events[1],
                    sequence=2 * index + 1,
                    native_timestamp=lower,
                    payload={**capture.events[1].payload, "call_id": identity},
                ),
                replace(
                    capture.events[2],
                    sequence=2 * index + 2,
                    native_timestamp=upper,
                    payload={"call_id": identity, "output": "full native output"},
                ),
            ]
        )
        rows.append({**row, "time": (lower + upper) / 2})
    events.append(replace(capture.events[-1], sequence=2 * size + 1))
    capture = replace(capture, events=tuple(events))
    log.write_text("".join(json.dumps(item) + "\n" for item in rows))
    visits = [0]
    original = server_calls._Intervals._find

    def measured(self, node, stamp, found):
        visits[0] += 1
        return original(self, node, stamp, found)

    monkeypatch.setattr(server_calls._Intervals, "_find", measured)
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    bound = 8 * size * (math.ceil(math.log2(size)) + 1)
    print(f"opaque intervals={size} node_visits={visits[0]} bound={bound}")
    assert visits[0] <= bound


@pytest.mark.parametrize("size", [20, 200])
def test_server_gate_bounds_empty_file_discovery(size: int, tmp_path: Path, monkeypatch) -> None:
    capture, binding, _, log, _ = _setup(tmp_path)
    log.unlink()
    for index in range(size):
        (binding.log_root / f"ar-tools-{index:04d}.jsonl").touch()
    visits, reads = [0], [0]
    original_scan = server_calls.os.scandir
    original_read = server_calls._read

    class MeasuredScan:
        def __init__(self, path):
            self.entries = original_scan(path)
            self.measured = Path(path) == binding.log_root

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self.entries)
            if self.measured:
                visits[0] += 1
            return entry

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.entries.close()

    def measured_read(path, budget):
        if path.parent == binding.log_root:
            reads[0] += 1
        return original_read(path, budget)

    monkeypatch.setattr("os.scandir", MeasuredScan)
    monkeypatch.setattr(server_calls, "_read", measured_read)
    result = recover_server_calls(capture, binding)
    assert any(
        "binding-unavailable:" in reason
        and "file discovery cap" in reason
        and "incomplete" in reason
        for reason in result.unknowns
    )
    assert visits[0] <= 16
    assert reads[0] == 0
    print(f"empty files={size} discovery_visits={visits[0]} file_reads={reads[0]}")


def test_recorded_empty_digest_line_is_skipped_counted_and_keeps_physical_rows(
    tmp_path: Path,
) -> None:
    # Retained CE02 efd68fc5 diagnostic (attempt SHA34586dde7cdc48558ef0312a4aa9f7cc391955843ed19ae2dca548be64d5a7c5)
    # names line1 and the empty-line digest. The deleted whole log is not reconstructed here.
    capture, binding, row, log, _ = _setup(tmp_path)
    empty_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert server_calls.hashlib.sha256(b"").hexdigest() == empty_digest
    log.write_bytes(b"\n" + json.dumps(row).encode() + b"\n \t\r\n")
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    window = next(
        record["fixtureServerWindow"]
        for record in result.raw_records
        if "fixtureServerWindow" in record
    )
    assert window["skippedBlankLines"] == {"total": 2, "bySource": {str(log.resolve()): 2}}
    calls = [
        record["fixtureServerCall"]
        for record in result.raw_records
        if "fixtureServerCall" in record
    ]
    assert len(calls) == 1 and calls[0]["line"] == 2 and calls[0]["row"] == row
    assert calls[0]["source_sha256"] == sha256_file(log)
    saved = result.to_dict()
    assert saved["raw_records"][-1]["fixtureServerCall"]["line"] == 2
    saved_window = next(
        record["fixtureServerWindow"]
        for record in saved["raw_records"]
        if "fixtureServerWindow" in record
    )
    assert saved_window["skippedBlankLines"] == window["skippedBlankLines"]


@pytest.mark.parametrize("malformed", [b"{", b"\xff"])
def test_skipped_count_survives_real_nonblank_corruption(malformed: bytes, tmp_path: Path) -> None:
    capture, binding, _, log, _ = _setup(tmp_path)
    log.write_bytes(b"\n \t\n" + malformed + b"\n")
    result = recover_server_calls(capture, binding)
    window = next(
        record["fixtureServerWindow"]
        for record in result.raw_records
        if "fixtureServerWindow" in record
    )
    assert window["skippedBlankLines"] == {"total": 2, "bySource": {str(log.resolve()): 2}}
    assert len(result.unknowns) == 1
    assert "schema-incompatible:" in result.unknowns[0]
    assert f"{log}:3" in result.unknowns[0]
    assert server_calls.hashlib.sha256(malformed).hexdigest() in result.unknowns[0]
    assert server_calls.hashlib.sha256(b"").hexdigest() not in result.unknowns[0]


@pytest.mark.parametrize("content, count", [(b"", 0), (b"\n \t\r\n", 2)])
def test_empty_or_all_blank_log_is_not_invented_missing_capture(
    content: bytes, count: int, tmp_path: Path
) -> None:
    capture, binding, _, log, _ = _setup(tmp_path)
    capture = replace(capture, events=(capture.events[0], capture.events[-1]))
    log.write_bytes(content)
    result = recover_server_calls(capture, binding)
    assert not result.unknowns
    window = next(
        record["fixtureServerWindow"]
        for record in result.raw_records
        if "fixtureServerWindow" in record
    )
    assert window["skippedBlankLines"] == {"total": count, "bySource": {str(log.resolve()): count}}
    assert SourceFile(str(log.resolve()), sha256_file(log)) in result.source_files
    assert not any("fixtureServerCall" in record for record in result.raw_records)
