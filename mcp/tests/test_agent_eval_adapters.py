"""Native adapter boundaries use synthetic records and executable stubs only."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters import (
    capture_claude_cli_jsonl,
    capture_claude_jsonl,
    capture_codex_jsonl,
    capture_hermes_db,
)
from agent_evals.adapters import hermes as hermes_adapter
from agent_evals.adapters.capture import recapture_retained_codex
from agent_evals.adapters.discovery import discover_capture
from agent_evals.adapters.execution import (
    CODEX_ACTOR,
    HERMES_ACTOR,
    HERMES_GPT_ACTOR,
    HERMES_KIMI_ACTOR,
    OPUS_JUDGE,
    LaunchProfile,
    NativeBinding,
    NativeExecution,
    NativeInvocation,
    actor_profile,
    execute_native,
    preflight,
)
from agent_evals.adapters.helper_wait import helper_wait_snapshot
from agent_evals.adapters.hermes import (
    MESSAGE_COLUMNS,
    SESSION_COLUMNS,
    hermes_final_stop,
    recapture_retained_hermes,
)
from agent_evals.adapters.host_events import HostCaptureWindow, merge_host_events
from agent_evals.adapters.judges import (
    JudgeRequest,
    codex_judge_argv,
    judge_profile,
    opus_judge_argv,
    validate_judge_project,
)
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.adapters.native_trials import (
    NativeJudgeOptions,
    capture_trial_actor,
    host_window,
    run_native_judge,
)
from agent_evals.adapters.paseo import PaseoConnection, PaseoNativeAdapter, WaitContext
from agent_evals.adapters.paseo_session import paseo_session
from agent_evals.adapters.usage import native_usage
from agent_evals.artifacts import json_digest
from agent_evals.graders.helpers import HelperObservation, grade_helpers
from agent_evals.package_budget import DiskPackageContext, fit_package
from agent_evals.regrade import _retained_capture
from agents_remember.cli.paseo_bridge import BRIDGE_TIMEOUT, PaseoBridgeFailure
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


@dataclass(frozen=True)
class _TrialSources:
    project_root: Path
    paseo_home: Path
    settings_path: Path
    tool_build: str
    native_capture_roots: dict[str, Path]
    host_event_paths: tuple[Path, ...] = ()
    turn_ends_path: Path | None = None

    def environment(self) -> dict[str, str]:
        return {"PATH": "/stub"}


def _jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_helper_wait_real_hermes_dispatches_remain_unknown_not_running() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/helper-wait-real-dispatches.json").read_text()
    )
    assert len(fixture["rows"]) == 8
    for row in fixture["rows"]:
        selected = row["selectedNativeEvents"]
        assert all(json_digest(x["record"]) == x["canonicalValueSha256"] for x in selected)
        events = tuple(NativeEvent(**x["record"]) for x in selected)
        sources = tuple(dict.fromkeys(SourceFile(x.source, x.source_sha256) for x in events))
        capture = NativeCapture("hermes", row["sessionId"], events, sources, (), ())
        actual = helper_wait_snapshot(capture)
        assert actual["pending_ids"] == row["expectedHelperIds"]
        assert actual["running_ids"] == actual["observed_running_ids"] == []
        assert actual["terminal_ids"] == actual["reentries"] == []
        assert actual["liveness"] == "unobservable"
        assert actual["helpers"][0]["delegation_ids"] == [row["expectedDelegationId"]]
        assert actual["helpers"][0]["observed_status"] == "unknown"
        assert actual["helpers"][0]["dispatches"][0]["source_sha256"] == events[1].source_sha256
        assert all(
            selection["record"] == "agent.turn_ended"
            for host in row["hostTurnEndedSelections"]
            for selection in host["rawSelectedValues"]
            if selection["originalJsonPointer"].endswith("/native/type")
        )


def test_helper_wait_host_observations_do_not_claim_process_local_liveness() -> None:
    source = SourceFile("host.jsonl", "a" * 64)
    owner = {"paseoActorIdentity": {"id": "owner", "persistence": {"sessionId": "native"}}}
    rows = _host_rows("owner")
    events = tuple(
        NativeEvent("native", "native", i, row, source.path, source.sha256)
        for i, row in enumerate(rows)
    )
    capture = NativeCapture("hermes", "native", events[:1], (source,), (owner,), ())
    running = helper_wait_snapshot(capture)
    assert running["observed_running_ids"] == ["helper"]
    assert running["running_ids"] == [] and running["liveness"] == "unobservable"
    assert running["helpers"][0]["observations"][0]["carrier"] == "host-provider-update"
    removed = helper_wait_snapshot(replace(capture, events=events))
    assert removed["pending_ids"] == ["helper"] and removed["terminal_ids"] == []
    assert removed["helpers"][0]["observed_status"] == "removed"
    completed = copy.deepcopy(rows[0])
    completed["payload"]["subagent"]["status"] = "completed"
    terminal = helper_wait_snapshot(
        replace(capture, events=(replace(events[0], payload=completed), *events[1:]))
    )
    assert terminal["terminal_ids"] == ["helper"] and terminal["pending_ids"] == []
    assert terminal["helpers"][0]["observed_status"] == "completed"
    foreign = copy.deepcopy(rows[0])
    foreign["payload"]["subagent"]["parentAgentId"] = "another-owner"
    assert not helper_wait_snapshot(
        replace(capture, events=(replace(events[0], payload=foreign),))
    )["helpers"]


def test_helper_wait_unknown_carriers_and_text_do_not_invent_reentry() -> None:
    source = SourceFile("native.jsonl", "b" * 64)
    payloads = [
        ("tool_call", {"name": "functions.spawn_agent", "call_id": "new-shape"}),
        ("tool_result", {"name": "delegate_task", "call_id": "unpaired", "output": {}}),
        ("native", {"type": "collab_new_helper_shape", "agent_id": "unmapped"}),
        ("message", {"role": "user", "content": "[ASYNC DELEGATION COMPLETE — not-proof]"}),
    ]
    events = tuple(
        NativeEvent(kind, "native", i, payload, source.path, source.sha256)
        for i, (kind, payload) in enumerate(payloads)
    )
    capture = NativeCapture("hermes", "native", events, (source,), (), ())
    actual = helper_wait_snapshot(capture)
    assert not actual["helpers"] and not actual["reentries"]
    assert len(actual["unknowns"]) == 4
    assert all(item["source_sha256"] == source.sha256 for item in actual["unknowns"])
    unbound = helper_wait_snapshot(replace(capture, source_files=()))
    assert len(unbound["unknowns"]) == 4 and not unbound["helpers"]


def _opaque_diagnostics(capture: NativeCapture) -> list[str]:
    calls = [
        event
        for event in capture.events
        if event.kind == "tool_call" and event.payload.get("opaque") is True
    ]
    assert calls
    for call in calls:
        original = {
            key: value
            for key, value in call.payload.items()
            if key not in {"opaque", "native_dispatch"}
        }
        metadata = call.payload["native_dispatch"]
        assert metadata["classification"] == "opaque"
        assert metadata["rawPayloadSha256"] == json_digest(original)
        assert metadata["rawPayloadBasis"] == "original-capture-event.payload"
        assert (
            metadata["sourcePath"] == call.source and metadata["sourceSha256"] == call.source_sha256
        )
        identity = call.payload.get("call_id")
        paired = [
            asdict(event)
            for event in capture.events
            if identity
            and event.kind == "tool_result"
            and event.payload.get("call_id") == identity
            and (event.session_id, event.source, event.source_sha256)
            == (call.session_id, call.source, call.source_sha256)
            and event.sequence > call.sequence
        ]
        assert metadata["pairedOutputs"] == paired
        assert not any(
            event.kind == "tool_call"
            and isinstance(identity, str)
            and str(event.payload.get("call_id")).startswith(identity + "/return/")
            for event in capture.events
        )
    return [call.payload["native_dispatch"]["reason"] for call in calls]


def test_native_codex_preserves_exact_call_and_detects_only_native_server(tmp_path: Path) -> None:
    path = _jsonl(
        tmp_path / "session.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "original", "model": "gpt-6.1-sol"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call1",
                    "name": "mcp__agents_remember_task__knowledge_write",
                    "arguments": '{"text":"mcp__agents_remember__fake is only prose"}',
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call1",
                    "output": {"refused": True, "reason": "exact input retained"},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "call2",
                    "name": "mcp__agents_remember__server_info",
                    "arguments": "{}",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"input_tokens": 9, "output_tokens": 4}},
                },
            },
        ],
    )
    capture = capture_codex_jsonl([path], "original")
    assert not capture.unknowns
    assert len(capture.installed_server_calls) == 1
    assert capture.installed_server_calls[0].payload["call_id"] == "call2"
    assert (
        capture.events[1].payload["arguments"]
        == json.loads(path.read_text().splitlines()[1])["payload"]["arguments"]
    )
    assert capture.events[2].payload["output"]["refused"] is True
    assert capture.source_files[0].sha256 == capture.events[0].source_sha256
    assert capture.raw_records[0]["payload"]["id"] == "original"


def test_missing_malformed_and_opaque_native_records_stay_unknown(tmp_path: Path) -> None:
    path = _jsonl(
        tmp_path / "opaque.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "s"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "functions.exec",
                    "arguments": "tools.mcp__agents_remember__server_info({})",
                },
            },
        ],
    )
    with path.open("a") as stream:
        stream.write("{truncated\n")
    capture = capture_codex_jsonl([path, tmp_path / "missing"], "s")
    assert capture.server_call_coverage == "unknown"
    assert not capture.installed_server_calls
    diagnostic = next(
        reason
        for reason in _opaque_diagnostics(capture)
        if "unsupported Codex nested dispatch" in reason
    )
    assert (
        str(path) in diagnostic and "inputKeys=" in diagnostic and "outputJSONPath=" in diagnostic
    )
    assert any("invalid native record" in reason for reason in capture.unknowns)
    assert any("capture-absent" in reason for reason in capture.unknowns)
    assert capture_codex_jsonl([path], "other").events == ()
    _assert_native_code_mode_items(tmp_path)
    _assert_retained_code_mode(tmp_path)


def _assert_native_code_mode_items(tmp_path: Path) -> None:
    items = [
        {
            "type": "McpToolCall",
            "id": "exec-mcp",
            "server": "agents-remember",
            "tool": "server_info",
            "arguments": {"deceptive": "mcp__agents_remember_task__fake"},
            "status": "completed",
            "result": {"structuredContent": {"ok": True}},
        },
        {
            "type": "CommandExecution",
            "id": "exec-command",
            "command": ["/bin/sh", "-c", "exit 7"],
            "cwd": "file:///fixture",
            "status": "completed",
            "exit_code": 7,
            "aggregated_output": "actual output",
        },
        {
            "type": "FileChange",
            "id": "exec-patch",
            "changes": {"file": {"type": "add", "content": "bytes"}},
            "status": "completed",
            "stdout": "actual result",
            "stderr": "",
        },
    ]
    rows = [
        {"type": "session_meta", "payload": {"id": "s"}},
        {"type": "world_state", "payload": {"full": True, "state": {"model": "actual-model"}}},
        {
            "type": "token_usage_record",
            "payload": {"session_id": "s", "usage": {"total_tokens": 7}},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "outer",
                "input": "untrusted model script",
                "internal_chat_message_metadata_passthrough": {"turn_id": "t"},
            },
        },
        *[
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "thread_id": "s",
                    "turn_id": "t",
                    "item": item,
                    "started_at_ms": 1000,
                    "completed_at_ms": 2000,
                },
            }
            for item in items
        ],
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "outer",
                "output": [
                    {"type": "input_text", "text": '{"type":"McpToolCall","server":"forged"}'}
                ],
            },
        },
    ]
    path = _jsonl(tmp_path / "nested-native.jsonl", rows)
    actual = capture_codex_jsonl([path], "s")
    assert not actual.unknowns and actual.server_call_coverage == "captured"
    assert len(actual.installed_server_calls) == 1
    native_call = actual.installed_server_calls[0]
    assert native_call.payload["arguments"] == items[0]["arguments"]
    assert native_call.payload["native_item"] == items[0]
    assert native_call.native_timestamp == 1
    command = next(
        event
        for event in actual.events
        if event.kind == "tool_result" and event.payload.get("call_id") == "exec-command"
    )
    assert command.payload["output"]["exit_code"] == 7 and command.native_timestamp == 2
    assert not any(event.payload.get("server") == "forged" for event in actual.events)
    opaque = _jsonl(tmp_path / "printed-only.jsonl", [*rows[:4], rows[-1]])
    assert any(
        "unsupported Codex nested dispatch" in reason
        for reason in _opaque_diagnostics(capture_codex_jsonl([opaque], "s"))
    )
    wrong = [
        *rows[:4],
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": "other",
                "turn_id": "t",
                "item": items[0],
            },
        },
        rows[-1],
    ]
    unbound = capture_codex_jsonl([_jsonl(tmp_path / "wrong-native.jsonl", wrong)], "s")
    assert unbound.unknowns and not unbound.installed_server_calls


def _assert_retained_code_mode(tmp_path: Path) -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "scripts/agent_evals/adapters/fixtures/codex-code-mode-retained.json"
    )
    fixture = json.loads(fixture_path.read_text())
    proof = fixture["provenance"]
    assert (
        proof["sourceSha256"] == "c56078a8ce98e77b2300bff2cd7e6832f11311d948edbf9751c6166fe4aa1964"
    )
    native = "".join(fixture["rawLines"])
    assert hashlib.sha256(native.encode()).hexdigest() == proof["sliceSha256"]
    path = tmp_path / "real-retained-native.jsonl"
    identity = {"type": "session_meta", "payload": {"id": proof["nativeSessionId"]}}
    path.write_text(json.dumps(identity) + "\n" + native)
    captured = capture_codex_jsonl([path], proof["nativeSessionId"])
    assert not captured.unknowns
    raw_mcp = next(
        json.loads(line)["payload"]["item"]
        for line in fixture["rawLines"]
        if json.loads(line).get("payload", {}).get("item", {}).get("type") == "McpToolCall"
    )
    calls = [
        event
        for event in captured.events
        if event.kind == "tool_call" and event.payload.get("server")
    ]
    assert len(calls) == 1
    assert (calls[0].payload["server"], calls[0].payload["name"]) == (
        raw_mcp["server"],
        "citation_fix",
    )
    assert calls[0].payload["arguments"] == raw_mcp["arguments"]
    result = next(
        event
        for event in captured.events
        if event.kind == "tool_result" and event.payload.get("call_id") == raw_mcp["id"]
    )
    assert result.payload["output"] == raw_mcp["result"]
    broken = [
        json.loads(line)
        for line in fixture["rawLines"]
        if json.loads(line).get("payload", {}).get("type") != "item_completed"
    ]
    missing = capture_codex_jsonl(
        [_jsonl(tmp_path / "missing-real-native.jsonl", [identity, *broken])],
        proof["nativeSessionId"],
    )
    assert any(
        "unsupported Codex nested dispatch" in reason for reason in _opaque_diagnostics(missing)
    )


def test_claude_uses_structured_blocks_and_retains_model_usage(tmp_path: Path) -> None:
    path = _jsonl(
        tmp_path / "claude.jsonl",
        [
            {
                "sessionId": "c",
                "type": "assistant",
                "message": {
                    "model": "claude-opus-5-5",
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t",
                            "name": "mcp__agents_remember__read_ar_files",
                            "input": {"files": ["all inputs remain"]},
                        }
                    ],
                },
            },
            {
                "sessionId": "c",
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t", "content": "full result"}
                    ]
                },
            },
        ],
    )
    capture = capture_claude_jsonl([path], "c")
    assert len(capture.installed_server_calls) == 1
    assert capture.events[-1].payload["content"] == "full result"
    assert (
        next(event for event in capture.events if event.kind == "usage").payload["model"]
        == "claude-opus-5-5"
    )


def test_hermes_exact_native_schema_reads_selected_session_only(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"CREATE TABLE sessions ({','.join(SESSION_COLUMNS)})")
        db.execute(f"CREATE TABLE messages ({','.join(MESSAGE_COLUMNS)})")
        row: dict[str, Any] = {key: None for key in SESSION_COLUMNS}
        row.update(
            id="h",
            model=HERMES_ACTOR.model,
            input_tokens=12,
            output_tokens=4,
            message_count=3,
            started_at=0,
        )
        db.execute(
            f"INSERT INTO sessions VALUES ({','.join('?' for _ in row)})", tuple(row.values())
        )
        for session in ("h", "unrelated"):
            message: dict[str, Any] = {key: None for key in MESSAGE_COLUMNS}
            message.update(
                id=1,
                session_id=session,
                role="assistant",
                content="message",
                timestamp=1,
                active=1,
                tool_calls=json.dumps(
                    [
                        {
                            "id": "native-call",
                            "function": {
                                "name": "mcp__agents_remember_task__knowledge_write",
                                "arguments": '{"complete":"input"}',
                            },
                        }
                    ]
                ),
            )
            db.execute(
                f"INSERT INTO messages VALUES ({','.join('?' for _ in message)})",
                tuple(message.values()),
            )
        for updates in (
            {
                "id": 2,
                "role": "tool",
                "timestamp": 2,
                "tool_call_id": "native-call",
                "content": "full result",
            },
            {
                "id": 3,
                "role": "assistant",
                "timestamp": 3,
                "finish_reason": "stop",
                "content": "Complete",
            },
        ):
            message = {key: None for key in MESSAGE_COLUMNS}
            message.update({"session_id": "h", "active": 1, **updates})
            db.execute(
                f"INSERT INTO messages VALUES ({','.join('?' for _ in message)})",
                tuple(message.values()),
            )
        db.commit()
    capture = capture_hermes_db(path, "h")
    assert not capture.unknowns
    assert len(capture.raw_records) == 4
    assert all(record["row"].get("session_id", "h") == "h" for record in capture.raw_records)
    assert (
        next(event for event in capture.events if event.kind == "tool_call").payload["arguments"]
        == '{"complete":"input"}'
    )
    assert capture_hermes_db(path, "absent").unknowns


def _compressed_hermes_capture(tmp_path: Path) -> tuple[NativeCapture, dict[str, Any]]:
    fixture = json.loads(
        (
            Path(__file__).resolve().parent / "fixtures/hermes-compression-active-rows.json"
        ).read_text()
    )
    assert json_digest(fixture["rows"]) == fixture["rowsSha256"]
    session = {key: fixture["session"][key] for key in SESSION_COLUMNS}
    # Rows are exact native values; this synthetic DB holds only that declared
    # slice, so its counter describes the selected active rows in the slice.
    session["message_count"] = sum(row["active"] == 1 for row in fixture["rows"])
    path = tmp_path / "compressed-state.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"CREATE TABLE sessions ({','.join(SESSION_COLUMNS)})")
        db.execute(f"CREATE TABLE messages ({','.join(MESSAGE_COLUMNS)})")
        db.execute(
            f"INSERT INTO sessions VALUES ({','.join('?' for _ in SESSION_COLUMNS)})",
            tuple(session.values()),
        )
        for row in fixture["rows"]:
            db.execute(
                f"INSERT INTO messages VALUES ({','.join('?' for _ in MESSAGE_COLUMNS)})",
                tuple(row[key] for key in MESSAGE_COLUMNS),
            )
        db.commit()
    return capture_hermes_db(path, session["id"]), fixture


def _archived_hermes_capture(
    tmp_path: Path, variant: str
) -> tuple[NativeCapture, list[dict[str, Any]]]:
    def row(identity: int, role: str, **values: Any) -> dict[str, Any]:
        return dict.fromkeys(MESSAGE_COLUMNS) | {
            "id": identity,
            "session_id": "compacted",
            "role": role,
            "timestamp": identity,
            "active": 0,
            **values,
        }

    def call(identity: str) -> dict[str, Any]:
        name = (
            "tool_call"
            if variant in {"dispatcher", "server-opaque"}
            else "mcp__agents_remember_task__knowledge_read"
            if variant == "server"
            else "terminal"
        )
        arguments: dict[str, Any] = (
            {"calls": [{"name": "mcp__agents_remember_task__knowledge_read", "arguments": {}}]}
            if variant in {"dispatcher", "server-opaque"}
            else {"command": "same arguments, distinct native executions"}
        )
        if variant == "server-opaque":
            arguments["calls"] *= 2
        return {
            "id": identity,
            "call_id": identity,
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }

    tool_name = (
        "mcp__agents_remember_task__knowledge_read"
        if variant in {"dispatcher", "server", "server-opaque"}
        else "terminal"
    )
    rows = [
        row(1, "user", content="duplicate plain"),
        row(
            2,
            "assistant",
            content="complete" if variant.startswith("duplicates") else "archived assistant",
        ),
        row(3, "assistant", tool_calls=json.dumps([call("old")]), finish_reason="tool_calls"),
        row(4, "tool", tool_call_id="old", tool_name=tool_name, content="receipt"),
        row(
            10,
            "user",
            active=1,
            timestamp=50,
            content="[CONTEXT COMPACTION — REFERENCE ONLY] current summary",
        ),
        row(
            20,
            "assistant",
            active=1,
            timestamp=60,
            tool_calls=json.dumps([call("new")]),
            finish_reason="tool_calls",
        ),
        row(
            21,
            "tool",
            active=1,
            timestamp=61,
            tool_call_id="new",
            tool_name=tool_name,
            content="receipt",
        ),
        row(22, "assistant", active=1, timestamp=62, content="complete", finish_reason="stop"),
    ]
    if variant.startswith("duplicates"):
        # Complete copied rows and repeated archived identities are suppressed.
        if variant == "duplicates-partial":
            rows[2]["tool_calls"] = json.dumps([call("old"), call("archive-only")])
        copied = call("old")
        if variant == "duplicates-alias":
            del copied["id"]
        elif variant == "duplicates-bad-id":
            copied["id"] = 0
        rows.extend(
            [
                row(5, "assistant", tool_calls=json.dumps([call("archive-only")])),
                row(6, "tool", tool_call_id="archive-only", tool_name=tool_name, content="unique"),
                row(7, "tool", tool_call_id="archive-only", tool_name=tool_name, content="unique"),
                row(8, "user", content="complete"),
                row(9, "assistant", tool_calls=json.dumps([call("archive-only")])),
                row(11, "assistant", active=1, timestamp=3, tool_calls=json.dumps([copied])),
                row(
                    12,
                    "tool",
                    active=1,
                    timestamp=4,
                    tool_call_id="old",
                    tool_name=tool_name,
                    content="receipt",
                ),
                row(13, "user", active=1, timestamp=55, content="duplicate plain"),
            ]
        )
    if variant == "context":
        rows.extend(
            [
                row(
                    5,
                    "user",
                    timestamp=100,
                    content="[CONTEXT COMPACTION — REFERENCE ONLY] " + "older summary " * 5000,
                ),
                row(6, "assistant", tool_calls=json.dumps([call("after-summary")])),
                row(
                    7, "tool", tool_call_id="after-summary", tool_name=tool_name, content="receipt"
                ),
            ]
        )
    rows.sort(key=lambda item: item["id"])
    session = dict.fromkeys(SESSION_COLUMNS) | {
        "id": "compacted",
        "source": "acp",
        "model": HERMES_ACTOR.model,
        "started_at": 0,
        "message_count": sum(item["active"] == 1 for item in rows),
    }
    path = tmp_path / "compacted-state.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"CREATE TABLE sessions ({','.join(SESSION_COLUMNS)})")
        db.execute(f"CREATE TABLE messages ({','.join(MESSAGE_COLUMNS)})")
        db.execute(
            f"INSERT INTO sessions VALUES ({','.join('?' for _ in SESSION_COLUMNS)})",
            tuple(session.values()),
        )
        for item in rows:
            db.execute(
                f"INSERT INTO messages VALUES ({','.join('?' for _ in MESSAGE_COLUMNS)})",
                tuple(item[key] for key in MESSAGE_COLUMNS),
            )
        db.commit()
    return capture_hermes_db(path, "compacted"), rows


def _archived_live_and_replay(native: NativeCapture) -> tuple[NativeCapture, NativeCapture]:
    value = native.to_dict()
    # A retained pre-repair capture may contain only active events. Rebuild from
    # its exact raw rows without using the old action list or reopening SQLite.
    value["events"] = [
        event
        for event in value["events"]
        if event["kind"] not in {"tool_call", "tool_result"} and event["payload"].get("active") != 0
    ]
    with patch.object(Path, "open", side_effect=AssertionError("replay reopened native files")):
        replay = recapture_retained_hermes(value)
    assert replay.raw_records == native.raw_records
    assert replay.raw_export_sha256 == native.raw_export_sha256
    return native, replay


def test_hermes_unique_archived_actions_survive_live_and_retained_capture(tmp_path: Path) -> None:
    native, rows = _archived_hermes_capture(tmp_path, "unique")
    for capture in _archived_live_and_replay(native):
        assert not capture.unknowns, capture.unknowns
        calls = [event for event in capture.events if event.kind == "tool_call"]
        assert [event.payload["call_id"] for event in calls] == ["old", "new"]
        projected = [
            event.payload for event in capture.events if event.kind in {"message", "context"}
        ]
        assert projected == rows
        stop = hermes_final_stop(capture)
        assert stop is not None and stop["rowId"] == 22
        assert [
            record["row"] for record in capture.raw_records if record["table"] == "messages"
        ] == rows


def test_hermes_archived_duplicates_keep_only_unique_native_actions(tmp_path: Path) -> None:
    native, _ = _archived_hermes_capture(tmp_path, "duplicates")
    for capture in _archived_live_and_replay(native):
        assert not capture.unknowns, capture.unknowns
        for kind in ("tool_call", "tool_result"):
            assert [event.payload["call_id"] for event in capture.events if event.kind == kind] == [
                "archive-only",
                "old",
                "new",
            ]
        projected = [
            event.payload for event in capture.events if event.kind in {"message", "context"}
        ]
        assert [row["id"] for row in projected] == [5, 6, 8, 10, 11, 12, 13, 20, 21, 22]
        assert [call["id"] for call in json.loads(projected[0]["tool_calls"])] == ["archive-only"]
        assert hermes_final_stop(capture) is not None

    alias_root = tmp_path / "native-alias"
    alias_root.mkdir()
    alias, _ = _archived_hermes_capture(alias_root, "duplicates-alias")
    for capture in _archived_live_and_replay(alias):
        assert not capture.unknowns, capture.unknowns
        assert [
            event.payload["call_id"] for event in capture.events if event.kind == "tool_call"
        ] == ["archive-only", "old", "new"]

    bad_root = tmp_path / "invalid-present-id"
    bad_root.mkdir()
    bad, _ = _archived_hermes_capture(bad_root, "duplicates-bad-id")
    for capture in _archived_live_and_replay(bad):
        assert "Hermes selected native tool-call shape is incomplete" in capture.unknowns
        assert hermes_final_stop(capture) is None


def test_hermes_archived_partial_copy_fails_closed_without_emitting_unique_half(
    tmp_path: Path,
) -> None:
    native, rows = _archived_hermes_capture(tmp_path, "duplicates-partial")
    for capture in _archived_live_and_replay(native):
        assert any("archived row partially copied" in reason for reason in capture.unknowns)
        assert not any(
            event.kind == "message" and event.payload.get("id") == 3 for event in capture.events
        )
        assert hermes_final_stop(capture) is None
        assert [r["row"] for r in capture.raw_records if r.get("table") == "messages"] == rows


def test_hermes_archived_retained_server_placements_are_recomputed_without_io(
    tmp_path: Path,
) -> None:
    for variant in ("server", "server-opaque"):
        root = tmp_path / variant
        root.mkdir()
        native, _ = _archived_hermes_capture(root, variant)
        log = SourceFile("/retained/ar-tools.jsonl", "b" * 64)
        records, reasons = list(native.raw_records), []
        for number, stamp in ((1, 3.5), (2, 40)):
            row = {
                "time": stamp,
                "tool": "knowledge_read",
                "arguments": {}
                if variant == "server-opaque"
                else {"command": "same arguments, distinct native executions"},
                "ok": True,
                "code": None,
                "dropped": 0,
            }
            records.append(
                {
                    "fixtureServerCall": {
                        "source": log.path,
                        "source_sha256": log.sha256,
                        "line": number,
                        "row_sha256": json_digest(row),
                        "row": row,
                        "placement": None,
                    }
                }
            )
            reasons.append(
                f"binding-unavailable: unplaceable fixture AR call {row['tool']!r}; "
                f"{log.path}:{number}; sha256={json_digest(row)}"
            )
        value = replace(
            native,
            source_files=(*native.source_files, log),
            raw_records=tuple(records),
            raw_export_sha256=None,
            unknowns=(*reasons, "unrelated recording fault"),
        ).to_dict()
        value["events"] = [e for e in value["events"] if e["payload"].get("call_id") != "old"]
        before = copy.deepcopy(value)
        with patch.object(Path, "open", side_effect=AssertionError("replay reopened evidence")):
            replay = _retained_capture(value, "hermes")
            again = _retained_capture(replay.to_dict(), "hermes")
        assert replay.unknowns == (reasons[1], "unrelated recording fault") or replay.unknowns == (
            "unrelated recording fault",
            reasons[1],
        )
        assert value == before
        assert replay.raw_records == tuple(value["raw_records"])
        assert replay.raw_export_sha256 == value["raw_export_sha256"]
        assert replay.source_files == (*native.source_files, log)
        assert again == replay
        recovered = [
            e
            for e in replay.events
            if e.payload.get("native_dispatch", {}).get("wire") == "fixture-server-log/v1"
        ]
        if variant == "server-opaque":
            assert [e.kind for e in recovered] == ["tool_call", "tool_result"]
            assert all(e.payload["call_id"] == f"server-log/{log.sha256}:1" for e in recovered)
        else:
            assert not recovered
            call = next(
                e
                for e in replay.events
                if e.kind == "tool_call" and e.payload.get("call_id") == "old"
            )
            assert call.payload["fixture_server_calls"][0]["placement"] == {
                "call_id": "old",
                "basis": "native-arguments-and-interval",
            }


def test_hermes_archived_dispatcher_batch_normalizes_like_an_active_batch(tmp_path: Path) -> None:
    native, _ = _archived_hermes_capture(tmp_path, "dispatcher")
    for capture in _archived_live_and_replay(native):
        assert not capture.unknowns, capture.unknowns
        calls = [event for event in capture.events if event.kind == "tool_call"]
        assert [event.payload["call_id"] for event in calls] == ["old", "new"]
        for event in calls:
            assert event.payload["name"] == "mcp__agents_remember_task__knowledge_read"
            assert event.payload["arguments"] == {}
            assert event.payload["native_dispatch"]["payload"]["name"] == "tool_call"
        assert (
            calls[0].payload["native_dispatch"]["payload"]["arguments"]
            == calls[1].payload["native_dispatch"]["payload"]["arguments"]
        )


def test_hermes_archived_compaction_stays_context_between_archived_actions(tmp_path: Path) -> None:
    native, _ = _archived_hermes_capture(tmp_path, "context")
    for capture in _archived_live_and_replay(native):
        assert not capture.unknowns, capture.unknowns
        assert [event.payload["id"] for event in capture.events if event.kind == "context"] == [
            5,
            10,
        ]
        assert [
            event.payload["call_id"] for event in capture.events if event.kind == "tool_call"
        ] == ["old", "after-summary", "new"]
        assert all(
            event.payload.get("id") not in {5, 10}
            for event in capture.events
            if event.kind == "message"
        )
        rows = [
            event.payload["id"] for event in capture.events if event.kind in {"message", "context"}
        ]
        assert rows == sorted(rows)
        assert hermes_final_stop(capture) is not None


def test_real_hermes_compression_uses_only_active_rows(tmp_path: Path) -> None:
    capture, fixture = _compressed_hermes_capture(tmp_path)
    assert not capture.unknowns, capture.unknowns
    messages = [event.payload for event in capture.events if event.kind in {"message", "context"}]
    assert messages == [row for row in fixture["rows"] if row["active"] == 1]
    assert [row["row"] for row in capture.raw_records if row.get("table") == "messages"] == fixture[
        "rows"
    ]
    calls = [event for event in capture.events if event.kind == "tool_call"]
    results = [event for event in capture.events if event.kind == "tool_result"]
    assert len(calls) == len(results) == 1
    assert calls[0].payload["call_id"] == fixture["selectedNativeCallId"]
    assert results[0].payload["call_id"] == fixture["selectedNativeCallId"]
    assert isinstance(calls[0].native_timestamp, (int, float))
    assert isinstance(results[0].native_timestamp, (int, float))
    assert calls[0].native_timestamp <= results[0].native_timestamp
    context = next(event for event in capture.events if event.kind == "context")
    assert context.payload["id"] == fixture["compactionContextRowId"]
    assert context.native_timestamp == context.payload["timestamp"]


def test_retained_hermes_replay_preserves_raw_rows_supplements_and_unrelated_unknowns(
    tmp_path: Path,
) -> None:
    native, _ = _compressed_hermes_capture(tmp_path)
    host_source = SourceFile("/retained/host-events.jsonl", "host-hash")
    host = NativeEvent(
        "host",
        native.session_id,
        len(native.events),
        {"native": "host-proof"},
        host_source.path,
        host_source.sha256,
        native.events[-1].native_timestamp,
    )
    value = replace(
        native,
        source_files=(*native.source_files, host_source),
        events=(*native.events, host),
        raw_records=(*native.raw_records, {"native": "host-proof"}),
        raw_export_sha256=None,
        unknowns=(
            "Hermes selected message chronology is incomplete",
            native.source_files[0].path + ": unsupported Hermes dispatch kind='tool_call'; old",
            "unrelated permission capture absent",
        ),
    ).to_dict()
    with patch.object(Path, "open", side_effect=AssertionError("replay reopened native files")):
        replay = recapture_retained_hermes(value)
    assert replay.unknowns == ("unrelated permission capture absent",)
    assert replay.raw_records == tuple(value["raw_records"])
    assert replay.raw_export_sha256 == value["raw_export_sha256"]
    assert replay.source_files == (*native.source_files, host_source)
    retained_host = [event for event in replay.events if event.source == host_source.path]
    assert len(retained_host) == 1
    assert retained_host[0].payload == host.payload
    assert retained_host[0].native_timestamp == host.native_timestamp
    native_rows = [event.payload for event in replay.events if event.kind in {"message", "context"}]
    assert [row["id"] for row in native_rows] == sorted(row["id"] for row in native_rows)
    assert [event.sequence for event in replay.events] == list(range(len(replay.events)))
    broken = copy.deepcopy(value)
    broken["raw_records"][1]["row"]["content"] = "changed"
    with unittest.TestCase().assertRaisesRegex(ValueError, "digest differs"):
        recapture_retained_hermes(broken)
    unbound = copy.deepcopy(value)
    unbound["events"][0]["source_sha256"] = "not-the-native-source-hash"
    with unittest.TestCase().assertRaisesRegex(ValueError, "original native source/hash"):
        recapture_retained_hermes(unbound)


def test_hermes_novel_compaction_context_is_not_tolerated(tmp_path: Path) -> None:
    native, fixture = _compressed_hermes_capture(tmp_path)
    records = copy.deepcopy(native.raw_records)
    context = next(
        row["row"]
        for row in records
        if row.get("table") == "messages" and row["row"]["id"] == fixture["compactionContextRowId"]
    )
    context["content"] = context["content"].replace("REFERENCE ONLY", "UNRECOGNIZED", 1)
    replay = recapture_retained_hermes(
        replace(native, raw_records=records, raw_export_sha256=None).to_dict()
    )
    problem = next(
        reason for reason in replay.unknowns if "unsupported Hermes compaction context" in reason
    )
    assert "kind='messages'" in problem and "keys=" in problem and "JSONPath=" in problem
    assert native.source_files[0].path in problem and native.source_files[0].sha256 in problem
    assert hermes_final_stop(replay) is None


def test_hermes_final_stop_rejects_incomplete_or_unbound_active_projection(tmp_path: Path) -> None:
    native, _ = _compressed_hermes_capture(tmp_path)
    assert hermes_final_stop(native) is not None
    for change in ("count", "terminal", "active", "chronology", "foreign-session"):
        records = copy.deepcopy(native.raw_records)
        if change == "count":
            records[0]["row"]["message_count"] += 1
        elif change == "terminal":
            records[-1]["row"]["finish_reason"] = "length"
        elif change == "active":
            records[1]["row"]["active"] = "unknown"
        elif change == "chronology":
            records[-1]["row"]["timestamp"] = 0
        else:
            records[-1]["row"]["session_id"] = "a different native session"
        assert (
            hermes_final_stop(replace(native, raw_records=records, raw_export_sha256=None)) is None
        )
    assert hermes_final_stop(replace(native, events=native.events[:-1])) is None
    assert hermes_final_stop(replace(native, raw_export_sha256="tampered")) is None


def test_paseo_undecided_wait_is_settled_only_by_bound_native_final_stop(tmp_path: Path) -> None:
    native, fixture = _compressed_hermes_capture(tmp_path)
    invocation = _invocation(tmp_path, "")
    binding = replace(invocation.binding, profile=HERMES_ACTOR)
    config = McpRuntimeConfig(binding.settings_path, tmp_path, binding.project_root, tmp_path)
    wait = fixture["finalUndecidedWait"]

    def bridge(command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "agents-live":
            return {"complete": True, "agentIds": []}
        if command == "workspace-open":
            return {"workspace": {"id": "fixture-workspace"}}
        if command == "agent-create":
            return {
                "existing": "prompt" in payload,
                "agent": {**payload, "id": payload["agentId"], "cwd": str(binding.project_root)},
            }
        if command == "agent-wait":
            return {"wait": copy.deepcopy(wait)}
        return {}

    adapter = PaseoNativeAdapter(PaseoConnection(config, binding, tmp_path, {}), call=bridge)
    with patch.object(adapter, "probe", return_value={}):
        result = adapter.run(instructions="fixture", prompt="fixture", capture=lambda _: native)
    assert not result.unknowns, result.unknowns
    settlement = result.waits[-1]["nativeSettlement"]
    assert settlement["sessionId"] == native.session_id
    assert settlement["outcome"] == "finished"
    assert settlement["rowId"] == fixture["rows"][-1]["id"]
    assert result.waits[-1]["state"] == "undecided"  # Original host receipt survives.
    broken = replace(native, raw_records=native.raw_records[:-1], raw_export_sha256=None)
    with patch.object(adapter, "probe", return_value={}):
        unresolved = adapter.run(instructions="fixture", prompt="fixture", capture=lambda _: broken)
    assert any("native wait unresolved: undecided" in reason for reason in unresolved.unknowns)
    wait["reason"] = "an unrecognized host wait condition"
    with patch.object(adapter, "probe", return_value={}):
        novel = adapter.run(instructions="fixture", prompt="fixture", capture=lambda _: native)
    assert any("native wait unresolved: undecided" in reason for reason in novel.unknowns)


def _invocation(tmp_path: Path, code: str) -> NativeInvocation:
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "paseo"
    home.mkdir()
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    binding = NativeBinding(
        project, home, settings, "candidate-sha256", ("agents-remember-task",), CODEX_ACTOR
    )
    return NativeInvocation(
        (sys.executable, "-c", code), project, {"LANG": "C.UTF-8"}, tmp_path, binding
    )


def test_execution_checks_native_binding_before_stub_start(tmp_path: Path) -> None:
    invocation = _invocation(
        tmp_path, "import sys; print(sys.stdin.read()); print('stderr', file=sys.stderr)"
    )
    with unittest.TestCase().assertRaisesRegex(AuthorityError, "observed native binding"):
        preflight(invocation, lambda item: replace(item.binding, tool_build="wrong"))
    assert not (tmp_path / "native.stdin").exists()
    result = execute_native(invocation, resolver=lambda item: item.binding, prompt="actor input")
    assert result.returncode == 0
    assert not result.unknowns
    assert result.stdout_path.read_text() == "actor input\n"
    assert result.stderr_path.read_text() == "stderr\n"
    preflight(
        replace(invocation, binding=replace(invocation.binding, servers=("agents-remember",))),
        lambda item: item.binding,
    )


def test_execution_timeout_and_output_limit_are_unknown_not_behavior_failure(
    tmp_path: Path,
) -> None:
    invocation = replace(
        _invocation(tmp_path, "import threading; threading.Event().wait()"),
        timeout_seconds=HANG_GUARD_SECONDS,
    )
    # Expire the execution clock deterministically; still spawn and reclaim a real process group.
    ticks = iter((0.0, HANG_GUARD_SECONDS + 1))
    with patch(
        "agent_evals.adapters.execution.time", SimpleNamespace(monotonic=lambda: next(ticks))
    ):
        result = execute_native(invocation, resolver=lambda item: item.binding, prompt="")
    assert result.returncode != 0
    assert any("timed out" in reason for reason in result.unknowns)
    second = tmp_path / "second"
    second.mkdir()
    invocation = replace(_invocation(second, "print('x'*100000)"), max_output_bytes=32)
    result = execute_native(invocation, resolver=lambda item: item.binding, prompt="")
    assert result.stdout_path.stat().st_size == 32
    assert any("capture incomplete" in reason for reason in result.unknowns)


def test_judge_request_has_no_text_label_and_opus_project_is_isolated(tmp_path: Path) -> None:
    request = JudgeRequest(
        ("criterion",),
        (json.dumps({"kind": "tool_call", "payload": {"name": "read", "arguments": {}}}),),
        ("artifact evidence",),
    )
    assert set(json.loads(request.prompt())) == {
        "task",
        "rubric",
        "observations",
        "artifact_evidence",
        "response_schema",
    }
    assert judge_profile(CODEX_ACTOR).model == "claude-opus-5-5"
    assert judge_profile(HERMES_ACTOR).model == "gpt-6.1-sol"
    assert (
        judge_profile(
            LaunchProfile("hermes", "openai-codex:gpt-6.1-sol-900k", "accept_edits", "high")
        ).model
        == "claude-opus-5-5"
    )
    assert judge_profile(HERMES_KIMI_ACTOR).model == "gpt-6.1-sol"
    assert actor_profile("hermes-gpt") is HERMES_GPT_ACTOR
    assert actor_profile(HERMES_GPT_ACTOR.model) is HERMES_GPT_ACTOR
    assert actor_profile("hermes") is HERMES_ACTOR
    assert HERMES_GPT_ACTOR.reasoning == "high"
    assert HERMES_GPT_ACTOR.service_tier is None
    with unittest.TestCase().assertRaisesRegex(ValueError, "no rubric judge"):
        judge_profile(replace(HERMES_GPT_ACTOR, model="unadmitted-gpt"))
    assert "quoted untrusted data" in json.loads(request.prompt())["task"]
    opus_argv = opus_judge_argv(Path(sys.executable), "90c322d4-2cc1-4269-be0f-d20c07c877e0")
    assert "--safe-mode" in opus_argv and "--bare" not in opus_argv
    assert "--no-session-persistence" not in opus_argv
    assert opus_argv[opus_argv.index("--model") + 1] == "claude-opus-5-5"
    assert "--restricted" not in opus_argv
    assert "--tools" not in opus_argv and "--allowedTools" not in opus_argv
    codex_argv = codex_judge_argv(Path(sys.executable), tmp_path)
    assert "--ignore-user-config" in codex_argv and "--ephemeral" not in codex_argv
    assert 'service_tier="priority"' in codex_argv
    assert 'model_reasoning_effort="high"' in codex_argv
    assert codex_argv[codex_argv.index("--sandbox") + 1] == "workspace-write"
    assert not any(
        "default_permissions" in argument or argument.startswith("permissions.")
        for argument in codex_argv
    )
    assert "features.shell_tool=true" in codex_argv
    judge_invocation = _invocation(tmp_path, "")
    judge_invocation = replace(
        judge_invocation, binding=replace(judge_invocation.binding, profile=OPUS_JUDGE, servers=())
    )
    preflight(judge_invocation, lambda item: item.binding)
    with unittest.TestCase().assertRaisesRegex(AuthorityError, "judges admit no tool"):
        preflight(
            replace(
                judge_invocation,
                binding=replace(judge_invocation.binding, servers=("agents-remember-task",)),
            ),
            lambda item: item.binding,
        )
    project = tmp_path / "projects" / "judge"
    project.mkdir(parents=True)
    validate_judge_project(project)
    (project / "evidence.txt").write_text("declared evidence is supplied by the strict disk loader")
    validate_judge_project(project)

    for number, actor in enumerate(
        (CODEX_ACTOR, HERMES_GPT_ACTOR, HERMES_ACTOR, HERMES_KIMI_ACTOR)
    ):
        _assert_native_judge_route(tmp_path / str(number), request, actor)


def _assert_native_judge_route(tmp_path: Path, request: JudgeRequest, actor: LaunchProfile) -> None:
    evidence = (tmp_path / "evidence").resolve()
    selected_judge = judge_profile(actor)
    request, manifest = fit_package(
        request,
        [{"index": 0, "path": "artifact.txt", "criteria": [0]}],
        context=DiskPackageContext(evidence, "constructed-unit-trial", selected_judge.harness),
        observation_bindings=[{"index": 0, "path": "observation.json", "criteria": [0]}],
        opaque_indices=(),
    )
    (evidence / "judge-evidence-manifest.json").write_text(json.dumps(manifest))

    def execute(invocation: NativeInvocation, **kwargs: Any) -> NativeExecution:
        assert kwargs["prompt"] == request.prompt()
        assert invocation.binding.profile == selected_judge
        assert invocation.binding.servers == ()
        assert invocation.environment == provisioned.environment()
        assert ("--safe-mode" in invocation.argv) == (selected_judge.harness == "claude")
        assert invocation.cwd == evidence / "rubric-judge/evidence"
        identity = (
            invocation.argv[invocation.argv.index("--session-id") + 1]
            if selected_judge.harness == "claude"
            else "constructed-codex"
        )
        output = _jsonl(
            invocation.run_root / "native.stdout",
            [
                {"type": "system", "session_id": identity},
                {
                    "type": "assistant",
                    "session_id": identity,
                    "message": {
                        "id": "judge-message",
                        "model": "claude-opus-5-5",
                        "content": [{"type": "text", "text": "actual verdict"}],
                    },
                },
            ],
        )
        error = invocation.run_root / "native.stderr"
        error.write_text("")
        return NativeExecution(0, output, error, ())

    provisioned = _TrialSources(
        tmp_path,
        tmp_path,
        tmp_path / "settings",
        "trial-build",
        {"codex": tmp_path / "native-codex"},
    )
    codex_capture = NativeCapture(
        "codex",
        "constructed-codex",
        (
            NativeEvent(
                "message",
                "constructed-codex",
                0,
                {"content": "actual verdict"},
                "constructed",
                "a" * 64,
            ),
        ),
        (),
        (),
        (),
    )
    with (
        patch("agent_evals.adapters.native_trials.shutil.which", return_value=sys.executable),
        patch("agent_evals.adapters.native_trials.execute_judge_native", side_effect=execute),
        patch(
            "agent_evals.adapters.native_trials.native_candidates", return_value=()
        ) as candidates,
        patch("agent_evals.adapters.native_trials.discover_capture", return_value=codex_capture),
    ):
        capture = run_native_judge(provisioned, actor, request, evidence, NativeJudgeOptions(10))
    if selected_judge.harness == "codex":
        assert candidates.call_args.args[1] == provisioned.native_capture_roots["codex"]
    else:
        candidates.assert_not_called()
    assert not capture.unknowns
    assert any("actual verdict" in str(event.payload) for event in capture.events)
    assert not any("judgeEvidenceReadAudit" in record for record in capture.raw_records)
    assert (evidence / "rubric-judge/judge-read-audit.json").is_file()


def test_paseo_native_connection_probes_build_and_applies_actor_before_message(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path, "")
    binding = invocation.binding
    config = McpRuntimeConfig(binding.settings_path, tmp_path, binding.project_root, tmp_path)
    server = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-P", "-m", "agents_remember.mcp", "--config", str(binding.settings_path)],
        "env": {},
    }
    connection = PaseoConnection(config, binding, tmp_path, server)
    calls: list[tuple[str, dict[str, Any]]] = []
    applied: dict[str, Any] = {}

    def bridge(command: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((command, payload))
        if command == "runtime-info":
            return {"serverId": "sandbox-only"}
        if command == "agents-live":
            return {"complete": True, "agentIds": []}
        if command == "workspace-open":
            return {"workspace": {"id": "fixture-workspace"}}
        if command == "agent-create":
            if "prompt" not in payload:
                applied.update(payload)
            return {
                "existing": "prompt" in payload,
                "agent": {
                    "id": payload["agentId"],
                    "provider": payload["provider"],
                    "model": payload["model"],
                    "modeId": payload["modeId"],
                    "thinkingOptionId": payload["thinkingOptionId"],
                    "featureValues": payload["featureValues"],
                    "cwd": str(binding.project_root),
                    "workspaceId": "fixture-workspace",
                },
            }
        if command == "agent-wait":
            return {"wait": {"state": "ended", "outcome": "finished"}}
        return {}

    def tool_probe(definition: dict[str, Any]) -> dict[str, Any]:
        assert definition == server
        return {
            "configPath": str(config.config_path),
            "coordinationRoot": str(config.coordination_root),
            "workspaceRoot": str(config.workspace_root),
            "servingBuild": {"sourceDigest": binding.tool_build},
        }

    with patch(
        "agent_evals.adapters.paseo.require_bridge_runtime",
        return_value=SimpleNamespace(home=binding.sandbox_home),
    ):
        adapter = PaseoNativeAdapter(connection, call=bridge, tool_probe=tool_probe)
        result = adapter.run(
            instructions="exact effective instructions",
            prompt="fixture message",
            capture=lambda _: NativeCapture("codex", "original-native-session", (), (), (), ()),
        )
        assert not result.unknowns
        assert result.capture is not None and result.capture.session_id != result.agent_id
        assert applied["systemPrompt"] == "exact effective instructions"
        assert "prompt" not in applied and "parentAgentId" not in applied
        assert applied["featureValues"] == {"service_tier": "priority"}
        assert set(applied["mcpServers"]) == {"agents-remember-task"}
        creation_calls = [payload for command, payload in calls if command == "agent-create"]
        assert len(creation_calls) == 2
        assert {k: v for k, v in creation_calls[1].items() if k != "prompt"} == creation_calls[0]
        assert creation_calls[1]["prompt"].startswith("fixture message\n\n") and (
            f"Native host seat: {result.agent_id}" in creation_calls[1]["prompt"]
        )
        wait_input = next(payload for command, payload in calls if command == "agent-wait")
        assert wait_input["messageId"] == f"eval:{result.agent_id}:first-message"
        assert calls[-2] == ("agent-archive", {"agentId": result.agent_id})
        assert result.host_counts == {"before_first_start": 0, "after_first_start": 0, "at_end": 0}
        mismatched = PaseoNativeAdapter(
            connection,
            call=bridge,
            tool_probe=lambda _: {**tool_probe(server), "servingBuild": {"sourceDigest": "wrong"}},
        )
        before = len(calls)
        with unittest.TestCase().assertRaisesRegex(AuthorityError, "sourceDigest"):
            mismatched.probe()
        assert len(calls) == before

    shared = tmp_path / "batch"
    metadata = shared / "agent-eval"
    metadata.mkdir(parents=True)
    marker = {
        "schema": "ar-curator-eval-batch/v1",
        "sandboxHome": str(shared),
        "identity": "90c322d4-2cc1-4269-be0f-d20c07c877e0",
    }
    (metadata / "batch.json").write_text(json.dumps(marker))
    batch = PaseoNativeAdapter(
        replace(
            connection, binding=replace(binding, sandbox_home=shared), admitted_batch_home=shared
        )
    )
    batch._batch_authority()
    (metadata / "batch.json").write_text(json.dumps({**marker, "sandboxHome": "/unrelated"}))
    with unittest.TestCase().assertRaisesRegex(AuthorityError, "ownership marker"):
        batch._batch_authority()


def test_native_identity_discovery_requires_unique_fresh_exact_project(tmp_path: Path) -> None:
    project = tmp_path / "fresh-project"
    project.mkdir()
    path = _jsonl(
        tmp_path / "native1.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-10-29T12:00:00Z",
                "payload": {"id": "original-native-id", "cwd": str(project)},
            },
        ],
    )
    capture = discover_capture("codex", [path], project=project, not_before=1)
    assert capture.session_id == "original-native-id"
    assert not capture.unknowns
    second = _jsonl(
        tmp_path / "native2.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-10-29T12:00:00Z",
                "payload": {"id": "different-native-id", "cwd": str(project)},
            },
        ],
    )
    ambiguous = discover_capture("codex", [path, second], project=project, not_before=1)
    assert not ambiguous.session_id and any("observed 2" in item for item in ambiguous.unknowns)
    source_home = tmp_path / "native-home"
    sessions = source_home / "sessions"
    sessions.mkdir(parents=True)
    fresh = sessions / "fresh.jsonl"
    fresh.write_bytes(path.read_bytes())
    _assert_bound_native_session(tmp_path, project, sessions, fresh, second)
    stale = discover_capture("codex", [path], project=project, not_before=9999999999)
    assert not stale.session_id and stale.unknowns
    wrong_project = discover_capture("codex", [path], project=tmp_path, not_before=1)
    assert not wrong_project.session_id and wrong_project.unknowns


def _assert_bound_native_session(
    tmp_path: Path, project: Path, sessions: Path, fresh: Path, second: Path
) -> None:
    (sessions / "guardian.jsonl").write_bytes(second.read_bytes())
    unrelated = _jsonl(
        sessions / "unrelated.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-10-29T12:00:00Z",
                "payload": {
                    "id": "unrelated-session",
                    "cwd": str(tmp_path),
                    "private": "must not copy",
                },
            }
        ],
    )
    owner = "90c322d4-2cc1-4269-be0f-d20c07c877e0"
    host_folder = tmp_path / "agents" / str(project).strip("/").replace("/", "-")
    host_folder.mkdir(parents=True)
    metadata = host_folder / f"{owner}.json"
    stored = {
        "id": owner,
        "provider": "codex",
        "cwd": str(project),
        "persistence": {"provider": "codex", "sessionId": "original-native-id"},
        "runtimeInfo": {
            "provider": "codex",
            "sessionId": "original-native-id",
            "model": "gpt-6.1-sol",
        },
        "config": {"systemPrompt": "private config must not copy"},
    }
    metadata.write_text(json.dumps(stored))
    provisioned = _TrialSources(
        project, tmp_path, tmp_path / "settings", "trial-build", {"codex": sessions.parent}
    )
    with patch(
        "agent_evals.adapters.native_trials._fixture_server_gate",
        side_effect=lambda _sources, value, *_: value,
    ):
        actor = capture_trial_actor(provisioned, "codex", owner, 1)
    assert actor.session_id == "original-native-id" and not actor.unknowns
    assert {item.path for item in actor.source_files} == {str(fresh), str(metadata)}
    assert "private config" not in json.dumps(actor.raw_records)
    assert all("different-native-id" not in json.dumps(row) for row in actor.raw_records)
    known = discover_capture(
        "codex",
        [fresh, sessions / "guardian.jsonl", unrelated],
        project=project,
        not_before=1,
        session_id="original-native-id",
    )
    assert known.session_id == "original-native-id" and len(known.source_files) == 1
    ambiguous = discover_capture(
        "codex", [fresh, sessions / "guardian.jsonl", unrelated], project=project, not_before=1
    )
    assert len(ambiguous.source_files) == 2 and all(
        item.path != str(unrelated) for item in ambiguous.source_files
    )
    metadata.write_text(
        json.dumps({**stored, "runtimeInfo": {"provider": "codex", "sessionId": "wrong-native"}})
    )
    failed = capture_trial_actor(provisioned, "codex", owner, 1)
    assert not failed.raw_records and any("disagree" in item for item in failed.unknowns)
    metadata.write_text(json.dumps(stored))
    assert paseo_session(tmp_path, project, owner, "codex").session_id == "original-native-id"


def test_native_wait_observes_only_exact_sandbox_codex_ar_answer(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path, "")
    binding = invocation.binding
    config = McpRuntimeConfig(binding.settings_path, tmp_path, binding.project_root, tmp_path)
    connection = PaseoConnection(config, binding, tmp_path, {})
    native_waits = iter(
        [
            {"wait": {"state": "permission", "permission": "request-exact"}},
            {"wait": {"state": "ended", "outcome": "finished"}},
        ]
    )
    adapter = PaseoNativeAdapter(connection, call=lambda _, __: next(native_waits))
    answer = {
        "agentId": "fresh-agent",
        "provider": "codex",
        "server": "agents-remember-task",
        "sandboxHome": str(binding.sandbox_home),
        "permission": "request-exact",
        "answered": True,
    }
    context = WaitContext(
        "fresh-agent", {}, time.monotonic() + HANG_GUARD_SECONDS, lambda _: answer
    )
    waits: list[dict[str, Any]] = []
    unknowns: list[str] = []
    adapter._wait(context, waits, unknowns)
    assert len(waits) == 2 and not unknowns
    for field, forbidden in (
        ("server", "unrelated-server"),
        ("provider", "hermes"),
        ("sandboxHome", "/real/home"),
        ("agentId", "other-agent"),
        ("permission", "unrelated"),
        ("answered", False),
    ):
        changed = replace(
            context,
            permission_observer=lambda _, field=field, forbidden=forbidden: {
                **answer,
                field: forbidden,
            },
        )
        assert not adapter._answered_permission(changed, {"permission": "request-exact"})
    installed = replace(
        context, permission_observer=lambda _: {**answer, "server": "agents-remember"}
    )
    assert adapter._answered_permission(installed, {"permission": "request-exact"})
    hermes = PaseoNativeAdapter(replace(connection, binding=replace(binding, profile=HERMES_ACTOR)))
    hermes_context = replace(
        context, permission_observer=lambda _: {**answer, "provider": "hermes"}
    )
    assert hermes._answered_permission(hermes_context, {"permission": "request-exact"})
    unanswered = replace(context, permission_observer=None)
    assert not adapter._answered_permission(unanswered, {"permission": "request-exact"})

    permitted = iter(
        [
            {
                "wait": {
                    "state": "permission",
                    "permission": "mcp__agents_remember__knowledge_write",
                }
            },
            {"wait": {"state": "ended", "outcome": "finished"}},
        ]
    )
    actual = PaseoNativeAdapter(connection, call=lambda *_: next(permitted))
    unresolved: list[str] = []
    observed_waits: list[dict[str, Any]] = []
    actual._wait(unanswered, observed_waits, unresolved)
    assert len(observed_waits) == 2 and not unresolved
    other = PaseoNativeAdapter(
        connection, call=lambda *_: {"wait": {"state": "permission", "permission": "shell"}}
    )
    other._wait(unanswered, [], unresolved)
    assert any("permission='shell'" in reason for reason in unresolved)


def _host_rows(owner: str) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": "2026-10-29T12:00:01Z",
            "type": "agent.provider_subagents.update",
            "payload": {
                "kind": "upsert",
                "subagent": {
                    "id": "helper",
                    "parentAgentId": owner,
                    "status": "running",
                    "createdAt": "2026-10-29T12:00:01Z",
                },
            },
        },
        {
            "timestamp": "2026-10-29T12:00:02Z",
            "type": "agent.provider_subagents.update",
            "payload": {"kind": "remove", "parentAgentId": owner, "subagentId": "helper"},
        },
        {
            "timestamp": "2026-10-29T12:00:04Z",
            "type": "agent.turn_ended",
            "agent": {"id": owner},
            "turnId": "own-turn",
            "timeline": [{"type": "assistant_message", "turnId": "own-turn", "text": "Complete"}],
        },
    ]


def _dated_native(tmp_path: Path, complete_at: str) -> NativeCapture:
    path = _jsonl(
        tmp_path / "dated-native.jsonl",
        [
            {
                "timestamp": "2026-10-29T12:00:00Z",
                "type": "session_meta",
                "payload": {"id": "native-original"},
            },
            {
                "timestamp": complete_at,
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "Complete"},
            },
        ],
    )
    return capture_codex_jsonl([path], "native-original")


def test_host_merge_filters_exact_owner_preserves_sources_and_absent_archive(
    tmp_path: Path,
) -> None:
    native = _dated_native(tmp_path, "2026-10-29T12:00:03Z")
    rows = _host_rows("host-owner")
    host = _jsonl(tmp_path / "host-events.jsonl", [*_host_rows("other-owner"), *reversed(rows)])
    log = _jsonl(
        tmp_path / "turn-ends.jsonl",
        [
            {
                "at": "2026-10-29T12:00:04.5Z",
                "agent": "host-owner",
                "role": "worker",
                "task": "fixture",
                "kind": "left-alone",
                "count": 1,
                "reason": "actual log reason",
            }
        ],
    )
    (tmp_path / "turn-ends.1.jsonl").write_text("not an admitted source")
    window = HostCaptureWindow("2026-10-29T12:00:00Z", "2026-10-29T12:00:05Z", True)
    merged = merge_host_events(
        native, owner_id="host-owner", host_event_paths=[host], turn_ends_path=log, window=window
    )
    assert merged.hw1_coverage == "complete" and not merged.capture.unknowns
    assert "turnEndsArchive" not in merged.metadata
    assert merged.capture.session_id == "native-original"
    assert [event.native_timestamp for event in merged.capture.events] == [
        "2026-10-29T12:00:00Z",
        "2026-10-29T12:00:01Z",
        "2026-10-29T12:00:02Z",
        "2026-10-29T12:00:03Z",
        "2026-10-29T12:00:04Z",
        "2026-10-29T12:00:04.5Z",
    ]
    assert all("other-owner" not in json.dumps(row) for row in merged.capture.raw_records)
    assert rows[0] in merged.capture.raw_records
    assert len(merged.capture.source_files) == 3
    assert (
        grade_helpers(HelperObservation(merged.capture, "host-owner", complete=True)).status
        == "pass"
    )

    _assert_native_recorder(tmp_path)


def _assert_native_recorder(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node is not None
    source = (
        Path(__file__).resolve().parents[2]
        / "scripts/agent_evals/adapters/recorder_plugin/index.server.ts"
    )
    home = tmp_path / "native-recorder"
    project = home / "project"
    project.mkdir(parents=True)
    output = home / "agent-eval"
    output.mkdir()
    (output / "recorder-settings.json").write_text(json.dumps({"projectRoot": str(project)}))
    driver = r"""
const [sourcePath, homePath, projectPath] = process.argv.slice(2)
const { default: contribute } = await import(sourcePath)
process.env.PASEO_HOME = homePath
let opening, ending, listener, ready
const gate = new Promise(resolve => { ready = resolve })
const server = {
  before(name, callback) { if (name === 'agent.session_open') opening = callback; return () => {} },
  on(name, callback) { if (name === 'agent.turn_ended') ending = callback; return () => {} }
}
const cleanup = contribute(server)
const pending = opening({ request: { agentId: 'owner', cwd: projectPath } }, { paseo: {
  observeEvents(names, options) {
    if (names.join(',') !== 'agent.provider_subagents.update' || options.notifications !== false) throw Error('wrong native subscription')
    return { ready: gate, subscribe(value) { listener = value }, release: async () => {} }
  }
}})
ready()
await pending
listener.snapshot()
const native = { type: 'agent.provider_subagents.update', payload: { kind: 'upsert', subagent: {
  id: 'helper', parentAgentId: 'owner', status: 'running', createdAt: new Date().toISOString()
}} }
listener.update(native)
listener.update({ type: native.type, payload: { kind: 'remove', parentAgentId: 'owner', subagentId: 'helper' } })
ending({ agent: { id: 'owner' }, turnId: 'actual-turn', outcome: { kind: 'completed' }, timeline: [] })
// Keep this as a live observed window: cleanup would correctly mark it stopped.
"""
    done = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            "--input-type=module",
            "-",
            source.as_uri(),
            str(home),
            str(project),
        ],
        input=driver,
        text=True,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    rows = [json.loads(line) for line in (output / "host-events.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and rows[0]["native"]["payload"]["subagent"]["id"] == "helper"
    sources = _TrialSources(
        project, home, home / "settings", "build", {}, (output / "host-events.jsonl",)
    )
    observed = host_window(sources, "owner")
    assert observed is not None and observed.complete
    native = NativeCapture("codex", "actual-native-session", (), (), (), ())
    merged = merge_host_events(
        native, owner_id="owner", host_event_paths=sources.host_event_paths, window=observed
    )
    assert not merged.capture.unknowns
    assert merged.capture.raw_records == tuple(rows)
    assert merged.capture.events[-1].payload["turnId"] == "actual-turn"
    marker = output / "owner.window.json"
    value = json.loads(marker.read_text())
    value["errors"] = ["actual subscription gap"]
    marker.write_text(json.dumps(value))
    gap = host_window(sources, "owner")
    assert gap is not None and not gap.complete


def test_host_merge_orders_native_turn_end_before_later_helper_stop(tmp_path: Path) -> None:
    native = _dated_native(tmp_path, "2026-10-29T12:00:01.5Z")
    host = _jsonl(tmp_path / "host-events.jsonl", list(reversed(_host_rows("owner"))))
    merged = merge_host_events(
        native,
        owner_id="owner",
        host_event_paths=[host],
        window=HostCaptureWindow("2026-10-29T12:00:00Z", "2026-10-29T12:00:05Z", True),
    )
    grade = grade_helpers(HelperObservation(merged.capture, "owner", complete=True))
    assert grade.status == "fail"
    assert any("silent turn end with running helpers" in item for item in grade.evidence)
    assert [event.payload.get("type") for event in merged.capture.events].index(
        "task_complete"
    ) < next(
        index
        for index, event in enumerate(merged.capture.events)
        if event.payload.get("payload", {}).get("kind") == "remove"
    )


def test_host_log_presence_and_undated_or_open_lifecycle_remain_unknown(tmp_path: Path) -> None:
    native = _dated_native(tmp_path, "2026-10-29T12:00:03Z")
    rows = _host_rows("owner")
    rows.pop(1)  # No stop was captured; never infer it from a turn end.
    del rows[0]["timestamp"]  # createdAt is helper creation, not this event's timestamp.
    host = _jsonl(tmp_path / "host-events.jsonl", rows)
    log = _jsonl(tmp_path / "turn-ends.jsonl", [])
    merged = merge_host_events(
        native, owner_id="owner", host_event_paths=[host], turn_ends_path=log
    )
    assert merged.hw1_coverage == "unknown"
    assert any("ordering unknown" in reason for reason in merged.capture.unknowns)
    assert merged.metadata["unsequencedEvents"][0]["payload"] == rows[0]
    assert (
        grade_helpers(HelperObservation(merged.capture, "owner", complete=True)).status == "unknown"
    )
    assert not any(
        row.get("kind") == "subagent-wait-declared" for row in merged.capture.raw_records
    )


def test_hermes_live_wal_freezes_selected_rows_independent_of_unrelated_writes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(f"CREATE TABLE sessions ({','.join(SESSION_COLUMNS)})")
        writer.execute(f"CREATE TABLE messages ({','.join(MESSAGE_COLUMNS)})")
        session: dict[str, Any] = dict.fromkeys(SESSION_COLUMNS)
        session.update(
            id="selected",
            source="acp",
            model=HERMES_ACTOR.model,
            started_at=0,
            message_count=1,
            api_call_count=1,
            input_tokens=10,
            output_tokens=2,
            cache_read_tokens=3,
            cache_write_tokens=4,
            reasoning_tokens=1,
        )
        writer.execute(
            f"INSERT INTO sessions VALUES ({','.join('?' for _ in session)})",
            tuple(session.values()),
        )
        writer.execute(
            f"INSERT INTO sessions VALUES ({','.join('?' for _ in session)})",
            tuple({**session, "id": "unrelated"}.values()),
        )
        message: dict[str, Any] = dict.fromkeys(MESSAGE_COLUMNS)
        message.update(
            id=1,
            session_id="selected",
            role="assistant",
            content="Complete",
            timestamp=1,
            active=1,
            finish_reason="stop",
        )
        writer.execute(
            f"INSERT INTO messages VALUES ({','.join('?' for _ in message)})",
            tuple(message.values()),
        )
        writer.commit()
        before = capture_hermes_db(path, "selected")
        original_coverage = hermes_adapter._coverage

        def concurrent_write(builder, row, rows):
            writer.execute(
                "UPDATE sessions SET model = 'unrelated-new-model' WHERE id = 'unrelated'"
            )
            writer.commit()
            original_coverage(builder, row, rows)

        with patch("agent_evals.adapters.hermes._coverage", side_effect=concurrent_write):
            after = capture_hermes_db(path, "selected")
        assert not after.unknowns
        assert after.raw_export_sha256 == before.raw_export_sha256
        assert any(source.path.endswith("state.db-wal") for source in after.source_files)
        assert all("unrelated" not in json.dumps(row) for row in after.raw_records)
        assert native_usage(after)["total_tokens"] == 19  # reasoning is already within output.
        _assert_hermes_lineage_usage(writer, path, message)
        writer.execute("DELETE FROM messages WHERE session_id = 'selected'")
        writer.commit()
        missing = capture_hermes_db(path, "selected")
        assert any("incomplete" in reason for reason in missing.unknowns)
        assert missing.raw_export_sha256 != after.raw_export_sha256
        after.raw_records[0]["row"]["model"] = "changed after freeze"
        with unittest.TestCase().assertRaisesRegex(ValueError, "changed after"):
            after.to_dict()


def test_native_usage_keeps_cumulative_counts_and_requires_actual_claude_output(
    tmp_path: Path,
) -> None:
    codex_path = _jsonl(
        tmp_path / "codex-usage.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "c"}},
            *[
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "total_tokens": total,
                                "input_tokens": total - 5,
                                "output_tokens": 5,
                            }
                        },
                    },
                }
                for total in (50, 90, 90)
            ],
        ],
    )
    assert native_usage(capture_codex_jsonl([codex_path], "c"))["total_tokens"] == 90
    zero_path = _jsonl(
        tmp_path / "codex-zero.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "z"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 0}},
                },
            },
        ],
    )
    assert native_usage(capture_codex_jsonl([zero_path], "z"))["total_tokens"] == 0
    assert native_usage(capture_codex_jsonl([], "missing"))["total_tokens"] is None
    placeholder = {
        "input_tokens": 10,
        "output_tokens": 1,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 2,
    }
    rows = [
        {
            "session_id": "claude-native",
            "type": "assistant",
            "message": {
                "id": "one-api-response",
                "model": "claude-opus-5-5",
                "usage": placeholder,
                "content": [],
            },
        }
    ] * 2
    partial_path = _jsonl(tmp_path / "claude-partial.jsonl", rows)
    partial = native_usage(capture_claude_cli_jsonl([partial_path], "claude-native"))
    assert partial["total_tokens"] is None and "placeholders" in partial["unknown_reason"]
    assert len(partial["components"]["by_message_id"]) == 1
    final = {
        "session_id": "claude-native",
        "type": "result",
        "usage": {**placeholder, "output_tokens": 7},
    }
    full_path = _jsonl(tmp_path / "claude-final.jsonl", [*rows, final, final])
    full = native_usage(capture_claude_cli_jsonl([full_path], "claude-native"))
    assert full["total_tokens"] is None and "session modelUsage absent" in full["unknown_reason"]
    assert full["components"]["final_call_usage"] == [final["usage"]]


def _assert_hermes_lineage_usage(
    writer: sqlite3.Connection, path: Path, message: dict[str, Any]
) -> None:
    inactive = {**message, "id": 2, "active": 0, "timestamp": 2, "content": "rewound history"}
    writer.execute(
        f"INSERT INTO messages VALUES ({','.join('?' for _ in inactive)})",
        tuple(inactive.values()),
    )
    writer.commit()
    projected = capture_hermes_db(path, "selected")
    assert not projected.unknowns
    assert (
        native_usage(projected)["total_tokens"] == 19
    )  # Active message projection is not token accounting.
    writer.execute(
        "UPDATE sessions SET parent_session_id = 'earlier-native-session' WHERE id = 'selected'"
    )
    writer.commit()
    child_usage = native_usage(capture_hermes_db(path, "selected"))
    assert child_usage["total_tokens"] is None
    assert child_usage["components"]["native_session_total_tokens"] == 19
    assert "lineage" in child_usage["unknown_reason"]
    writer.execute(
        "UPDATE sessions SET parent_session_id = NULL, end_reason = 'compression' WHERE id = 'selected'"
    )
    writer.commit()
    parent_usage = native_usage(capture_hermes_db(path, "selected"))
    assert parent_usage["total_tokens"] is None
    assert parent_usage["components"]["native_session_total_tokens"] == 19


def _returned_dispatch_fixtures() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parent / "fixtures/codex-dispatch-returns.json"
    fixtures = json.loads(path.read_text())["fixtures"]
    for fixture in fixtures:
        assert json_digest(fixture["records"]) == fixture["provenance"]["recordsSha256"]
        assert fixture["provenance"]["containerPath"].endswith("/attempt.json")
        assert len(fixture["provenance"]["containerSha256"]) == 64
    return fixtures


def test_actual_codex_returned_dispatches_and_local_only_cells(tmp_path: Path) -> None:
    cases = _returned_dispatch_fixtures()
    assert {case["case"] for case in cases} == {"CE01", "CE07", "CE08", "CE13"}
    for fixture in cases:
        path = _jsonl(tmp_path / (fixture["case"] + ".jsonl"), fixture["records"])
        capture = capture_codex_jsonl([path], fixture["sessionId"])
        assert not capture.unknowns, capture.unknowns
        calls = [
            event
            for event in capture.events
            if event.kind == "tool_call" and "native_dispatch" in event.payload
        ]
        results = [
            event
            for event in capture.events
            if event.kind == "tool_result" and "native_dispatch" in event.payload
        ]
        if fixture["case"] == "CE13":
            assert calls == results == []  # Four source-local load/text cells, no invented child.
            continue
        assert len(calls) == len(results) == 1
        call, result = calls[0], results[0]
        provenance = call.payload["native_dispatch"]
        assert call.payload["call_id"] == provenance["outerCallId"] + "/return/0"
        assert result.payload["call_id"] == call.payload["call_id"]
        assert "not a native TurnItem id" in provenance["identityBasis"]
        source_call = next(
            row["payload"]
            for row in fixture["records"]
            if row.get("payload", {}).get("call_id") == provenance["outerCallId"]
            and row["payload"]["type"] == "custom_tool_call"
        )
        source_output = next(
            row["payload"]
            for row in fixture["records"]
            if row.get("payload", {}).get("call_id") == provenance["outerCallId"]
            and row["payload"]["type"] == "custom_tool_call_output"
        )
        assert provenance["inputSha256"] == json_digest(source_call)
        assert provenance["outputSha256"] == json_digest(source_output)
        if fixture["case"] == "CE01":
            assert call.payload["name"] == "apply_patch"
            assert call.payload["arguments"].startswith("*** Begin Patch\n")
            assert result.payload["output"]["status"] == "failed"
            assert result.payload["output"]["error"] == source_output["output"][1]["text"]
        else:
            assert call.payload["name"] == "exec_command"
            assert result.payload["output"] == json.loads(source_output["output"][1]["text"])
            assert call.payload["arguments"]["cmd"].startswith("cat ")
            assert call.payload["arguments"]["max_output_tokens"] == (
                8500 if fixture["case"] == "CE07" else 6500
            )


def test_codex_store_binding_requires_native_matching_result_and_unknown_names_schema(
    tmp_path: Path,
) -> None:
    fixture = next(case for case in _returned_dispatch_fixtures() if case["case"] == "CE07")
    records = copy.deepcopy(fixture["records"])
    for row in records:
        item = row.get("payload", {}).get("item", {})
        if item.get("type") == "McpToolCall" and item.get("tool") == "memory_quality_check":
            item["result"] = {"not": "the returned stored result"}
    path = _jsonl(tmp_path / "unbound-store.jsonl", records)
    capture = capture_codex_jsonl([path], fixture["sessionId"])
    diagnostic = next(
        reason for reason in _opaque_diagnostics(capture) if "unbound store key" in reason
    )
    assert str(path) in diagnostic
    assert (
        "inputKind=custom_tool_call" in diagnostic
        and "outputKind=custom_tool_call_output" in diagnostic
    )
    assert (
        "inputKeys=" in diagnostic
        and "outputKeys=" in diagnostic
        and "$.payload.output" in diagnostic
    )
    assert "call_id=call_47869f977a234982993a0d471214c7c2" in diagnostic
    assert not any("opaque dispatch exec" in reason for reason in capture.unknowns)


def test_codex_retained_replay_preserves_host_evidence_and_rejects_changed_export(
    tmp_path: Path,
) -> None:
    fixture = next(case for case in _returned_dispatch_fixtures() if case["case"] == "CE08")
    path = _jsonl(tmp_path / "native.jsonl", fixture["records"])
    original = capture_codex_jsonl([path], fixture["sessionId"])
    host_source = SourceFile("/host/retained-events.jsonl", "host-sha")
    host_event = NativeEvent(
        "host",
        fixture["sessionId"],
        999,
        {"event": "retained-host-evidence"},
        host_source.path,
        host_source.sha256,
    )
    combined = NativeCapture(
        "codex",
        original.session_id,
        (*original.events, host_event),
        (*original.source_files, host_source),
        (*original.raw_records, {"paseoActorIdentity": {"id": "original-host"}}),
        (str(path) + ": opaque dispatch exec; nested calls not captured",),
    )
    value = combined.to_dict()
    with patch.object(Path, "open", side_effect=AssertionError("replay must not read live files")):
        replayed = recapture_retained_codex(value)
    assert not replayed.unknowns
    assert replayed.raw_records == combined.raw_records
    assert replayed.raw_export_sha256 == value["raw_export_sha256"]
    assert replayed.source_files == combined.source_files
    assert any(
        event.payload == host_event.payload and event.source == host_event.source
        for event in replayed.events
    )
    value["raw_records"] = [*value["raw_records"], {"changed": True}]
    with unittest.TestCase().assertRaisesRegex(ValueError, "digest differs"):
        recapture_retained_codex(value)


def _dispatch_rows(identity: str, script: str, *, failed: bool = False) -> list[dict]:
    header = "Script failed" if failed else "Script completed"
    result = (
        "Script error:\nexec_command failed"
        if failed
        else json.dumps(
            {"chunk_id": "test", "wall_time_seconds": 0.1, "exit_code": 0, "output": "ok"}
        )
    )
    return [
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": identity,
                "input": script,
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": identity,
                "output": [
                    {"type": "input_text", "text": header + "\nWall time 0.1 seconds\nOutput:\n"},
                    {"type": "input_text", "text": result},
                ],
            },
        },
    ]


def test_codex_failed_cell_cannot_bind_an_unexecuted_store(tmp_path: Path) -> None:
    records = [{"type": "session_meta", "payload": {"id": "failed-store"}}]
    records += _dispatch_rows(
        "failed",
        'text(await tools.exec_command({cmd:"fails"})); store("poison",{cmd:"unexecuted"});',
        failed=True,
    )
    records += _dispatch_rows("later", 'text(await tools.exec_command(load("poison")));')
    capture = capture_codex_jsonl(
        [_jsonl(tmp_path / "failed-store.jsonl", records)], "failed-store"
    )
    assert not capture.unknowns
    assert any("unbound store key 'poison'" in reason for reason in _opaque_diagnostics(capture))


def test_codex_argument_mutation_and_late_declaration_are_not_reconstructed(tmp_path: Path) -> None:
    scripts = [
        'let a={cmd:"safe"}; a.cmd="other"; text(await tools.exec_command(a));',
        'const a={cmd:"safe"}; a.cmd++; text(await tools.exec_command(a));',
        'text(await tools.exec_command(a)); var a={cmd:"not-yet-bound"};',
        'const a={cmd:"safe"}; if(true) {a.cmd="other";} text(await tools.exec_command(a));',
    ]
    for index, script in enumerate(scripts):
        records = [{"type": "session_meta", "payload": {"id": "mutation"}}]
        records += _dispatch_rows("mutated", script)
        path = _jsonl(tmp_path / f"mutation-{index}.jsonl", records)
        capture = capture_codex_jsonl([path], "mutation")
        assert not capture.unknowns
        assert any(
            "mutating AST kind=" in reason or "declaration after tool call" in reason
            for reason in _opaque_diagnostics(capture)
        ), _opaque_diagnostics(capture)


def test_codex_unsupported_store_cell_cannot_reuse_a_stale_loaded_value(tmp_path: Path) -> None:
    records = [{"type": "session_meta", "payload": {"id": "mutated-store"}}]
    records += _dispatch_rows("initial", 'store("a",{cmd:"old"});')
    records += _dispatch_rows(
        "changed", 'const a=load("a"); const ignored=mutate(a); store("b",a);'
    )
    records += _dispatch_rows("later", 'text(await tools.exec_command(load("b")));')
    path = _jsonl(tmp_path / "mutated-store.jsonl", records)
    capture = capture_codex_jsonl([path], "mutated-store")
    assert not capture.unknowns
    assert any("unbound store key 'b'" in reason for reason in _opaque_diagnostics(capture))


def test_agent_create_failure_preserves_observed_host_counts_and_actor_id(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path, "")
    config = McpRuntimeConfig(tmp_path / "settings.json", tmp_path, tmp_path, tmp_path)
    connection = PaseoConnection(config, invocation.binding, tmp_path, {})
    created = []

    def bridge(command, payload):
        if command == "agents-live":
            return {"complete": True, "agentIds": list(created)}
        if command == "workspace-open":
            return {"workspace": {"id": "fixture-workspace"}}
        if command == "agent-create":
            created.append(payload["agentId"])
            raise PaseoBridgeFailure(BRIDGE_TIMEOUT, "agent-create ran out of time")
        raise AssertionError(command)

    adapter = PaseoNativeAdapter(connection, call=bridge)
    with patch.object(adapter, "probe", return_value={}):
        try:
            adapter.run(
                instructions="fixture",
                prompt="fixture",
                capture=lambda _: NativeCapture("codex", "unused", (), (), (), ()),
            )
        except PaseoBridgeFailure:
            pass
        else:
            raise AssertionError("The injected creation failure was swallowed")
    assert adapter.last_run is not None
    assert adapter.last_run.host_counts == {"before_first_start": 0, "at_end": 1}
    assert created == [adapter.last_run.agent_id]
    assert adapter.last_run.capture is None


def test_native_judge_rejects_an_oversized_request_before_any_execution(tmp_path: Path) -> None:
    """The paid boundary also guards retained requests that bypass fresh packaging."""
    request = JudgeRequest(("criterion",), (), ("x" * 2_900_000,))
    provisioned = _TrialSources(tmp_path, tmp_path, tmp_path / "settings", "trial-build", {})
    with (
        patch("agent_evals.adapters.native_trials.shutil.which", return_value=sys.executable),
        patch(
            "agent_evals.adapters.native_trials.execute_judge_native",
            side_effect=AssertionError("paid boundary reached"),
        ) as execute,
        unittest.TestCase().assertRaisesRegex(ValueError, "input-package-over-budget"),
    ):
        run_native_judge(
            provisioned, CODEX_ACTOR, request, tmp_path / "evidence", NativeJudgeOptions(10)
        )
    execute.assert_not_called()
    assert not (tmp_path / "evidence/rubric-judge").exists()
