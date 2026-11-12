"""Unknown dispatch syntax stays complete evidence, with explicit opacity."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.capture import capture_codex_jsonl, recapture_retained_codex
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile, raw_export_digest
from agent_evals.adapters.opaque_dispatch import opaque_action
from agent_evals.artifacts import json_digest, sha256_file


def _fixture() -> dict:
    path = Path(__file__).parent / "fixtures/opaque-dispatch-real.json"
    assert sha256_file(path) == "ce1db7fabadf8c3290a4631ef87ed3a813c71a2926d997e2576e0c2338e4eb07"
    fixture = json.loads(path.read_text())
    for recording in fixture["recordings"]:
        provenance = recording["provenance"]
        assert not provenance["nativeOriginalAvailable"]
        assert len(provenance["attemptSha256"]) == len(provenance["rawExportSha256"]) == 64
        assert json_digest(recording["records"]) == provenance["recordsSha256"]
        for row, source in zip(recording["records"], provenance["records"], strict=True):
            assert json_digest(row) == source["sha256"]
            assert row["ordinal"] == source["ordinal"]
    return fixture


def _capture_recording(recording: dict, tmp_path: Path) -> NativeCapture:
    path = tmp_path / f"{recording['caseId']}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in recording["records"]))
    return capture_codex_jsonl([path], recording["sessionId"])


@pytest.mark.parametrize("case", ["CE01", "CE11"])
def test_real_codex_unsupported_shape_is_full_opaque_action(case: str, tmp_path: Path) -> None:
    recording = next(value for value in _fixture()["recordings"] if value["caseId"] == case)
    original = copy.deepcopy(recording["records"])
    capture = _capture_recording(recording, tmp_path)
    assert not capture.unknowns
    calls = [event for event in capture.events if event.kind == "tool_call"]
    results = [event for event in capture.events if event.kind == "tool_result"]
    assert len(calls) == len(results) == len(recording["callIds"])
    for event in calls:
        identity = event.payload["call_id"]
        native = next(
            row["payload"]
            for row in original
            if row["payload"].get("type") == "custom_tool_call"
            and row["payload"].get("call_id") == identity
        )
        expected = native | {"server": None}
        meta = event.payload["native_dispatch"]
        assert {
            key: value
            for key, value in event.payload.items()
            if key not in {"opaque", "native_dispatch"}
        } == expected
        assert event.payload["opaque"] is True
        assert meta["classification"] == "opaque"
        assert meta["rawPayloadSha256"] == json_digest(expected)
        assert meta["rawPayloadBasis"] == "original-capture-event.payload"
        assert meta["kind"] == "tool_call" and meta["keys"] == sorted(expected)
        assert meta["path"] == f"$.events[{capture.events.index(event)}].payload"
        assert meta["sourcePath"] == event.source and meta["sourceSha256"] == event.source_sha256
        assert "unsupported Codex nested dispatch" in meta["reason"]
        assert "inputKind=custom_tool_call" in meta["reason"] and "inputKeys=" in meta["reason"]
        matching = [result for result in results if result.payload["call_id"] == identity]
        assert meta["pairedOutputs"] == [asdict(result) for result in matching]
        assert matching[0].payload == next(
            row["payload"]
            for row in original
            if row["payload"].get("type") == "custom_tool_call_output"
            and row["payload"].get("call_id") == identity
        )
        assert event.payload["input"] == native["input"]
    assert list(capture.raw_records) == original == recording["records"]
    assert capture.raw_export_sha256 == raw_export_digest("codex", recording["sessionId"], original)
    assert not capture.installed_server_calls
    _assert_replay_preserves_opaque(recording, capture, original)


def _assert_replay_preserves_opaque(
    recording: dict, capture: NativeCapture, original: list
) -> None:
    retained = json.loads(json.dumps(capture.to_dict()))
    source = recording["provenance"]["nativeSourceDeclared"]
    retained["source_files"] = [source]
    for event in retained["events"]:
        event["source"], event["source_sha256"] = source["path"], source["sha256"]
    retained["unknowns"] = [*recording["originalDiagnostics"], "unrelated native integrity finding"]
    with patch.object(
        Path, "open", side_effect=AssertionError("retained replay must not read live home")
    ):
        replay = recapture_retained_codex(retained)
    assert replay.unknowns == ("unrelated native integrity finding",)
    assert replay.raw_export_sha256 == capture.raw_export_sha256
    assert list(replay.raw_records) == original
    for event in replay.events:
        if event.kind == "tool_call":
            assert event.payload["opaque"] is True
            meta = event.payload["native_dispatch"]
            assert meta["sourcePath"] == source["path"] and meta["sourceSha256"] == source["sha256"]
            assert all(
                row["source"] == source["path"] and row["source_sha256"] == source["sha256"]
                for row in meta["pairedOutputs"]
            )
    with pytest.raises(ValueError, match="digest"):
        changed = copy.deepcopy(retained)
        changed["raw_records"][1]["payload"]["input"] += "tampered"
        recapture_retained_codex(changed)


def _native_pair() -> tuple[NativeCapture, NativeEvent, NativeEvent]:
    source = SourceFile("/retained/native.jsonl", "a" * 64)
    event = NativeEvent(
        "tool_call",
        "session",
        0,
        {
            "type": "custom_tool_call",
            "name": "exec",
            "call_id": "native-call",
            "input": "full original program",
            "extra": {"kept": [1, 2]},
        },
        source.path,
        source.sha256,
        "2026-10-30T00:00:00Z",
    )
    result = NativeEvent(
        "tool_result",
        "session",
        1,
        {
            "type": "custom_tool_call_output",
            "call_id": "native-call",
            "output": [{"type": "novel_result", "payload": {"failed": True}}],
        },
        source.path,
        source.sha256,
        "2026-10-30T00:00:01Z",
    )
    capture = NativeCapture("codex", "session", (event, result), (source,), (), ())
    return capture, event, result


def test_common_factory_preserves_all_actual_pairs_and_is_idempotent() -> None:
    capture, event, result = _native_pair()
    second = replace(
        result,
        sequence=2,
        payload={**result.payload, "output": "second complete native output"},
        native_timestamp=42.5,
    )
    unrelated = [
        replace(result, session_id="foreign"),
        replace(result, source="/another/source"),
        replace(result, source_sha256="b" * 64),
        replace(result, payload={**result.payload, "call_id": "another-call"}),
    ]
    capture = replace(capture, events=(event, result, second, *unrelated))
    original = copy.deepcopy(capture.to_dict())
    opaque, unknowns = opaque_action(capture, event, "schema-specific reason")
    assert not unknowns
    assert opaque.payload["opaque"] is True
    assert opaque.payload["input"] == event.payload["input"]
    assert opaque.payload["extra"] == event.payload["extra"]
    meta = opaque.payload["native_dispatch"]
    assert meta["pairedOutputs"] == [asdict(result), asdict(second)]
    assert meta["rawPayloadSha256"] == json_digest(event.payload)
    assert meta["pathBasis"] == "original pre-merge native capture events"
    assert meta["pairedOutputsSequenceBasis"] == meta["pathBasis"]
    assert meta["originalRawExportSha256"] == raw_export_digest(
        capture.harness, capture.session_id, capture.raw_records
    )
    assert capture.to_dict() == original
    again, problems = opaque_action(capture, opaque, "do not replace original reason")
    assert again == opaque and problems == unknowns
    # A later merge may insert/resequence events; these remain an explicitly
    # labelled snapshot of the original capture, rather than new identities.
    inserted = replace(event, kind="host_note", payload={"note": "later merge"})
    moved = replace(opaque, sequence=1)
    merged = replace(
        capture,
        events=(
            inserted,
            moved,
            replace(result, sequence=2),
            replace(second, sequence=3),
        ),
        raw_records=({"type": "host_note", "payload": {"note": "later merge"}},),
    )
    rebound, problems = opaque_action(merged, moved, "do not rewrite snapshot provenance")
    assert not problems
    assert rebound == moved and rebound.payload["native_dispatch"] == meta
    assert meta["path"] == "$.events[0].payload"
    assert meta["pairedOutputs"][0]["sequence"] == 1
    assert meta["originalRawExportSha256"] != raw_export_digest(
        merged.harness, merged.session_id, merged.raw_records
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing-pair",
        "foreign-session",
        "wrong-source",
        "wrong-hash",
        "earlier-result",
        "duplicate-call",
        "empty-session",
        "foreign-call-session",
        "missing-source-binding",
    ],
)
def test_common_factory_material_gaps_remain_capture_absent(fault: str) -> None:
    capture, event, result = _native_pair()
    if fault == "missing-pair":
        capture = replace(capture, events=(event,))
    elif fault == "duplicate-call":
        capture = replace(capture, events=(event, event, result))
    elif fault == "empty-session":
        capture = replace(capture, session_id="")
    elif fault == "foreign-call-session":
        event = replace(event, session_id="foreign")
        capture = replace(capture, events=(event, result))
    elif fault == "missing-source-binding":
        capture = replace(capture, source_files=())
    else:
        changes = {
            "foreign-session": {"session_id": "foreign"},
            "wrong-source": {"source": "/foreign"},
            "wrong-hash": {"source_sha256": "b" * 64},
            "earlier-result": {"sequence": -1},
        }
        result = replace(result, **changes[fault])
        capture = replace(
            capture, events=(result, event) if fault == "earlier-result" else (event, result)
        )
    opaque, unknowns = opaque_action(capture, event, "opaque schema")
    assert opaque.payload["opaque"] is True
    assert any(
        "capture-absent" in problem and "call_id=native-call" in problem for problem in unknowns
    )
    assert opaque.payload["native_dispatch"]["pairedOutputs"] == []


def test_known_e3_shapes_remain_exactly_normalized_and_failures_preserved(tmp_path: Path) -> None:
    prior = _fixture()["priorRecognizedFixture"]
    path = Path(__file__).parent / "fixtures" / prior["path"]
    assert sha256_file(path) == prior["sha256"]
    for recording in json.loads(path.read_text())["recordings"]:
        capture = _capture_recording(recording, tmp_path)
        assert not capture.unknowns
        calls = [event for event in capture.events if event.kind == "tool_call"]
        results = [event for event in capture.events if event.kind == "tool_result"]
        assert len(calls) == len(results) == 2
        assert not any(event.payload.get("opaque") for event in calls)
        assert calls[1].payload["arguments"] == recording["expected"]["arguments"]
        assert results[1].payload["output"] == recording["expected"]["result"]


def test_schema_opacity_does_not_hide_native_integrity_unknowns(tmp_path: Path) -> None:
    recording = _fixture()["recordings"][0]
    recording["records"].append(
        {"type": "world_state", "payload": {"full": "malformed", "state": []}}
    )
    capture = _capture_recording(recording, tmp_path)
    assert len(capture.unknowns) == 1 and "malformed Codex world_state" in capture.unknowns[0]
    assert all(
        event.payload.get("opaque") is True for event in capture.events if event.kind == "tool_call"
    )
