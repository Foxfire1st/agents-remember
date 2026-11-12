"""Retained Codex custom cells preserve executed actions and local-only work."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.capture import capture_codex_jsonl, recapture_retained_codex
from agent_evals.adapters.codex_dispatch import returned_dispatches
from agent_evals.adapters.models import NativeCapture, raw_export_digest
from agent_evals.artifacts import json_digest, sha256_file

_FAILED = "call_5ab833a44d5a459aa27c6ba4f2349dd3"
_LOCAL = "call_eb715feff129465cb1bb8137162067e7"


def _fixture() -> dict:
    path = Path(__file__).parent / "fixtures/codex-custom-local-ce13.json"
    assert sha256_file(path) == "6a23962558b285a9fbc664369d792352ddef6aef4ba8f5d864d9d873e0729f4e"
    value = json.loads(path.read_text())
    assert json_digest(value["records"]) == value["provenance"]["recordsSha256"]
    assert value["provenance"]["containerSha256"] == (
        "5e9acdce5caf78da14710732ef7dd127d14aa0fad753f43faf2e93d2d5d4fc90"
    )
    return value


def test_retained_ce13_local_custom_cells_preserve_error_and_create_no_tools(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    records = fixture["records"]
    path = tmp_path / "retained.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    capture = capture_codex_jsonl([path], fixture["sessionId"])
    assert not capture.unknowns
    assert list(capture.raw_records) == records
    calls = [event.payload for event in capture.events if event.kind == "tool_call"]
    results = [event.payload for event in capture.events if event.kind == "tool_result"]
    assert calls == [
        row["payload"] | {"server": None}
        for row in records
        if row["payload"].get("type") == "custom_tool_call"
    ]
    assert results == [
        row["payload"] for row in records if row["payload"].get("type") == "custom_tool_call_output"
    ]
    returned, errors = returned_dispatches(records, set(), fixture["sessionId"])
    assert not errors
    assert returned[_FAILED].kind == "failed-local-prefix"
    assert returned[_LOCAL].kind == "local-only"
    assert all(result.name is None for result in returned.values())
    assert "TypeError" in results[0]["output"][1]["text"]


@pytest.mark.parametrize(
    "change", ["wrong-column", "wrong-property", "tool-before", "arbitrary-test", "native-child"]
)
def test_ambiguous_custom_cells_remain_unknown(change: str) -> None:
    fixture = _fixture()
    records = copy.deepcopy(fixture["records"])
    failed_call, failed_output, local_call = (records[index]["payload"] for index in (1, 2, 3))
    identity = _FAILED
    if change == "wrong-column":
        failed_output["output"][1]["text"] = failed_output["output"][1]["text"].replace(
            ":1:44", ":1:45"
        )
    elif change == "wrong-property":
        failed_output["output"][1]["text"] = failed_output["output"][1]["text"].replace(
            "reading '0'", "reading '9'"
        )
    elif change == "tool-before":
        failed_call["input"] = failed_call["input"].replace(
            'load("template")', "tools.read_mcp_resource({})"
        )
        column = failed_call["input"].index("[0]") + 1
        failed_output["output"][1]["text"] = failed_output["output"][1]["text"].replace(
            ":1:44", f":1:{column}"
        )
    elif change == "arbitrary-test":
        identity = _LOCAL
        local_call["input"] = local_call["input"].replace(
            "/file writer|covers|proofs|repoint|History rows|history.*row|effect/.test",
            "matcher.test",
        )
    else:
        records.insert(
            2,
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "thread_id": fixture["sessionId"],
                    "turn_id": failed_call["internal_chat_message_metadata_passthrough"]["turn_id"],
                    "item": {"type": "McpToolCall", "id": "unexpected-native-child"},
                },
            },
        )
    returned, errors = returned_dispatches(records, set(), fixture["sessionId"])
    assert identity not in returned
    assert "inputKind=custom_tool_call" in errors[identity]
    assert "inputKeys=" in errors[identity]
    assert "outputJSONPath=$.payload.output" in errors[identity]
    assert (
        f"inputSha256={json_digest(failed_call if identity == _FAILED else local_call)}"
        in errors[identity]
    )
    output = failed_output if identity == _FAILED else records[4]["payload"]
    assert f"outputSha256={json_digest(output)}" in errors[identity]


def _e3_recording(case: str) -> dict:
    path = Path(__file__).parent / "fixtures/codex-custom-e3-ce01-ce11.json"
    assert sha256_file(path) == "c82b207567ed3b28d1f7f39839ba6f04e75b751e333b02ca6786da673d716406"
    fixture = json.loads(path.read_text())
    recording = next(row for row in fixture["recordings"] if row["caseId"] == case)
    provenance = recording["provenance"]
    assert json_digest(recording["records"]) == provenance["recordsSha256"]
    assert not provenance["nativeOriginalAvailable"]
    assert len(provenance["attemptSha256"]) == len(provenance["rawExportSha256"]) == 64
    assert len(provenance["nativeSourceDeclared"]["sha256"]) == 64
    for record, source in zip(recording["records"], provenance["records"], strict=True):
        assert json_digest(record) == source["canonicalSha256"]
        assert record["ordinal"] == source["ordinal"]
        assert source["jsonPath"] == f"$.capture.raw_records[{source['arrayIndex']}]"
    return recording


@pytest.mark.parametrize("case", ["CE01", "CE11"])
def test_e3_retained_custom_dispatch_preserves_exact_graded_actions(
    case: str, tmp_path: Path
) -> None:
    recording = _e3_recording(case)
    records = recording["records"]
    original = copy.deepcopy(records)
    path = tmp_path / f"{case}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    capture = capture_codex_jsonl([path], recording["sessionId"])
    assert not capture.unknowns
    identity = recording["callId"]
    expected = recording["expected"]
    calls = [event for event in capture.events if event.kind == "tool_call"]
    results = [event for event in capture.events if event.kind == "tool_result"]
    assert [event.payload["name"] for event in calls] == ["exec", expected["name"]]
    assert [event.payload["call_id"] for event in calls] == [identity, identity + "/return/0"]
    assert [event.payload["call_id"] for event in results] == [identity, identity + "/return/0"]
    assert calls[0].payload == records[1]["payload"] | {"server": None}
    assert results[0].payload == records[2]["payload"]
    assert calls[1].payload["arguments"] == expected["arguments"]
    assert results[1].payload["output"] == expected["result"]
    provenance = calls[1].payload["native_dispatch"]
    assert provenance == results[1].payload["native_dispatch"]
    assert provenance["outerCallId"] == identity
    assert provenance["inputSha256"] == json_digest(records[1]["payload"])
    assert provenance["outputSha256"] == json_digest(records[2]["payload"])
    assert "not a native TurnItem id" in provenance["identityBasis"]
    assert all(event.source == str(path) for event in [*calls, *results])
    assert all(event.source_sha256 == sha256_file(path) for event in [*calls, *results])
    if case == "CE01":
        assert provenance["returnedShape"] == "failed-first-apply-patch"
        assert results[1].payload["output"]["status"] == "failed"
        assert results[1].payload["output"]["error"] == records[2]["payload"]["output"][1]["text"]
        assert not capture.installed_server_calls
    else:
        assert provenance["returnedShape"] == "tool-then-local-catalog"
        assert provenance["localOutput"] == records[2]["payload"]["output"][2]
        assert results[1].payload["output"]["exit_code"] == 1
        assert "No such file or directory" in results[1].payload["output"]["output"]
        assert records[2]["payload"]["output"][2]["text"] == "[]"
    assert list(capture.raw_records) == original == records
    assert capture.raw_export_sha256 == raw_export_digest("codex", recording["sessionId"], records)
    _assert_e3_retained_replay(capture, recording, original)


def _assert_e3_retained_replay(
    capture: NativeCapture, recording: dict, original: list[dict]
) -> None:
    retained = json.loads(json.dumps(capture.to_dict()))
    source = recording["provenance"]["nativeSourceDeclared"]
    retained["source_files"] = [source]
    for event in retained["events"]:
        event["source"], event["source_sha256"] = source["path"], source["sha256"]
    with patch.object(
        Path, "open", side_effect=AssertionError("replay must not reopen native files")
    ):
        replayed = recapture_retained_codex(retained)
    assert not replayed.unknowns
    assert list(replayed.raw_records) == original
    assert replayed.raw_export_sha256 == capture.raw_export_sha256
    assert [event.payload for event in replayed.events] == [
        event.payload for event in capture.events
    ]
    assert all(event.source == source["path"] for event in replayed.events)
    assert all(event.source_sha256 == source["sha256"] for event in replayed.events)


def _mutate_e3_output(change: str, output: dict) -> bool:
    if change == "malformed-json":
        output["output"][1]["text"] = "{not native JSON"
    elif change == "extra-output":
        output["output"].append({"type": "input_text", "text": "[]"})
    elif change == "missing-output":
        output["output"].pop()
    elif change == "reordered-output":
        output["output"][1], output["output"][2] = output["output"][2], output["output"][1]
    elif change == "novel-kind":
        output["output"][1]["type"] = "novel_native_block"
    elif change == "novel-keys":
        output["output"][1]["unexpectedShape"] = True
    else:
        return False
    return True


def _e3_negative_records(change: str) -> tuple[dict, list[dict]]:
    recording = _e3_recording(
        "CE01" if change in {"generic-error", "earlier-tool", "unrelated-prefix"} else "CE11"
    )
    records = recording["records"]
    call, output = records[1]["payload"], records[2]["payload"]
    if _mutate_e3_output(change, output):
        pass
    elif change == "generic-error":
        output["output"][1]["text"] = "Script error:\nTypeError: tool failed"
    elif change == "earlier-tool":
        call["input"] = (
            'text(await tools.exec_command({cmd:"possibly executed"}));\n' + call["input"]
        )
    elif change == "unrelated-prefix":
        output["output"][1]["text"] = output["output"][1]["text"].replace(
            "apply_patch verification failed:", "unrelated verification failed:"
        )
    elif change == "nonlocal-statement":
        call["input"] += "unrecognized_global_action();\n"
    elif change == "arbitrary-test":
        call["input"] = call["input"].replace("/skills.*(list|read)/.test", "matcher.test")
    elif change == "argument-mutation":
        local_print = call["input"].splitlines()[1]
        call["input"] = (
            'const args={cmd:"safe"};args.cmd="mutated";text(await tools.exec_command(args));\n'
            + local_print
        )
    else:
        raise AssertionError(f"undeclared negative control {change}")
    return recording, records


@pytest.mark.parametrize(
    "change",
    [
        "generic-error",
        "earlier-tool",
        "unrelated-prefix",
        "nonlocal-statement",
        "malformed-json",
        "extra-output",
        "missing-output",
        "reordered-output",
        "arbitrary-test",
        "argument-mutation",
        "novel-kind",
        "novel-keys",
    ],
)
def test_e3_ambiguous_dispatch_retains_loud_source_bound_diagnostic(
    change: str, tmp_path: Path
) -> None:
    recording, records = _e3_negative_records(change)
    path = tmp_path / "unsupported.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    capture = capture_codex_jsonl([path], recording["sessionId"])
    assert not capture.unknowns
    calls = [event.payload for event in capture.events if event.kind == "tool_call"]
    assert len(calls) == 1 and calls[0]["opaque"] is True
    original = records[1]["payload"] | {"server": None}
    assert {
        key: value for key, value in calls[0].items() if key not in {"opaque", "native_dispatch"}
    } == original
    metadata = calls[0]["native_dispatch"]
    assert metadata["rawPayloadSha256"] == json_digest(original)
    assert metadata["pairedOutputs"] == [
        asdict(event) for event in capture.events if event.kind == "tool_result"
    ]
    diagnostic = metadata["reason"]
    assert str(path) in diagnostic
    assert "inputKind=custom_tool_call" in diagnostic and "inputKeys=" in diagnostic
    assert "outputKind=custom_tool_call_output" in diagnostic and "outputKeys=" in diagnostic
    assert "outputJSONPath=$.payload.output" in diagnostic
    assert f"inputSha256={json_digest(records[1]['payload'])}" in diagnostic
    assert f"outputSha256={json_digest(records[2]['payload'])}" in diagnostic
    if change in {"novel-kind", "novel-keys"}:
        assert "$.payload.output[1]" in diagnostic
        assert (
            "novel_native_block" in diagnostic
            if change == "novel-kind"
            else "unexpectedShape" in diagnostic
        )
    assert list(capture.raw_records) == records


def test_e3_failed_first_patch_does_not_bind_unreached_citation_store() -> None:
    recording = _e3_recording("CE01")
    records = recording["records"]
    probe_call = {
        "type": "custom_tool_call",
        "name": "exec",
        "call_id": "probe-unreached-store",
        "input": 'text(await tools.exec_command(load("citationPlan")));',
    }
    probe_output = {
        "type": "custom_tool_call_output",
        "call_id": probe_call["call_id"],
        "output": [
            {"type": "input_text", "text": "Script completed\nWall time 0.0 seconds\nOutput:\n"},
            {
                "type": "input_text",
                "text": json.dumps(
                    {
                        "chunk_id": "probe",
                        "wall_time_seconds": 0,
                        "exit_code": 0,
                        "output": "retained output is not permission to invent an argument",
                    }
                ),
            },
        ],
    }
    records.extend(
        [
            {"type": "response_item", "payload": probe_call},
            {"type": "response_item", "payload": probe_output},
        ]
    )
    returned, errors = returned_dispatches(records, set(), recording["sessionId"])
    assert returned[recording["callId"]].name == "apply_patch"
    assert "probe-unreached-store" not in returned
    assert "unbound store key 'citationPlan'" in errors["probe-unreached-store"]
