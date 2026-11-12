"""The production capture path must bind fixture logs, not infer completeness."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.adapters.native_trials import TrialSources, capture_trial_actor
from agent_evals.artifacts import sha256_file


def _fixture_capture(tmp_path: Path, logged_tool: str) -> tuple[NativeCapture, Path, dict]:
    trial = tmp_path / "trial"
    project = trial / "project"
    project.mkdir(parents=True)
    settings = trial / "settings.json"
    logs = trial / "tool-logs"
    logs.mkdir()
    settings.write_text(json.dumps({"transcriptRoot": str(logs)}))
    home = tmp_path / "paseo"
    marker = home / "agent-eval/owner.window.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "readyAt": "2026-10-30T00:00:01Z",
                "lastTurnEndedAt": "2026-10-30T00:00:04Z",
                "agentId": "owner",
                "projectRoot": str(project),
                "errors": [],
            }
        )
    )
    row = {
        "time": "2026-10-30T00:00:02.500Z",
        "tool": logged_tool,
        "arguments": {},
        "ok": True,
        "code": None,
        "dropped": 0,
    }
    log = logs / "ar-tools-2026-10-30.jsonl"
    probe = {**row, "time": "2026-10-30T00:00:00Z", "tool": "server_info"}
    log.write_text(json.dumps(probe) + "\n" + json.dumps(row) + "\n")
    source = trial / "native.jsonl"
    source.write_text("native source identity\n")
    source_hash = sha256_file(source)
    events = tuple(
        NativeEvent(kind, "native-session", index, payload, str(source), source_hash, stamp)
        for index, (kind, payload, stamp) in enumerate(
            [
                ("session", {"id": "native-session"}, "2026-10-30T00:00:01Z"),
                (
                    "tool_call",
                    {
                        "call_id": "direct",
                        "name": "knowledge_read",
                        "arguments": {},
                        "server": "agents-remember-task",
                    },
                    "2026-10-30T00:00:02Z",
                ),
                ("tool_result", {"call_id": "direct", "output": {}}, "2026-10-30T00:00:03Z"),
                ("native", {"type": "task_complete"}, "2026-10-30T00:00:04Z"),
            ]
        )
    )
    native = NativeCapture(
        "codex", "native-session", events, (SourceFile(str(source), source_hash),), (), ()
    )
    provisioned = SimpleNamespace(
        project_root=project,
        paseo_home=home,
        settings_path=settings,
        native_capture_roots={"codex": tmp_path / "codex"},
        host_event_paths=(),
        turn_ends_path=None,
    )
    binding = SimpleNamespace(
        session_id="native-session", projection={}, source=SourceFile(str(source), source_hash)
    )
    with (
        patch("agent_evals.adapters.native_trials.paseo_session", return_value=binding),
        patch("agent_evals.adapters.native_trials.discover_capture", return_value=native),
    ):
        captured = capture_trial_actor(cast(TrialSources, provisioned), "codex", "owner", 0)
    return captured, log, row


def test_production_capture_retains_exact_server_row_and_pre_actor_receipt(tmp_path: Path) -> None:
    capture, log, row = _fixture_capture(tmp_path, "knowledge_read")
    assert not capture.unknowns
    retained = [
        record["fixtureServerCall"]
        for record in capture.raw_records
        if "fixtureServerCall" in record
    ]
    assert len(retained) == 1
    assert retained[0]["row"] == row
    assert retained[0]["source"] == str(log)
    assert retained[0]["source_sha256"] == sha256_file(log)
    checkpoint = next(
        record["fixtureServerWindow"]
        for record in capture.raw_records
        if "fixtureServerWindow" in record
    )
    assert checkpoint["checkpointRow"]["agentId"] == "owner"
    assert checkpoint["startedAt"] == "2026-10-30T00:00:01Z"


def test_production_capture_names_an_unplaceable_server_call(tmp_path: Path) -> None:
    capture, log, _ = _fixture_capture(tmp_path, "knowledge_write")
    assert any(
        reason.startswith("binding-unavailable:")
        and "unplaceable" in reason
        and "knowledge_write" in reason
        and str(log) in reason
        and "sha256=" in reason
        for reason in capture.unknowns
    ), capture.unknowns


def test_missing_fixture_log_binding_is_not_complete_capture(tmp_path: Path) -> None:
    project = tmp_path / "trial/project"
    project.mkdir(parents=True)
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps({"session": "native-session"}))
    provisioned = SimpleNamespace(
        project_root=project,
        paseo_home=tmp_path / "paseo",
        settings_path=tmp_path / "trial/settings.json",
        native_capture_roots={"codex": tmp_path / "codex"},
        host_event_paths=(),
        turn_ends_path=None,
    )
    binding = SimpleNamespace(
        session_id="native-session",
        projection={"session": "native-session"},
        source=SourceFile(str(identity), sha256_file(identity)),
    )
    native = NativeCapture("codex", "native-session", (), (), (), ())
    with (
        patch("agent_evals.adapters.native_trials.paseo_session", return_value=binding),
        patch("agent_evals.adapters.native_trials.discover_capture", return_value=native),
    ):
        captured = capture_trial_actor(cast(TrialSources, provisioned), "codex", "owner", 0)
    assert any(
        reason.startswith("binding-unavailable:") and "settings.json" in reason
        for reason in captured.unknowns
    ), captured.unknowns
