"""Real Hermes nested authoring calls remain visible to the same history grader."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.hermes_dispatch import normalize_hermes_dispatches
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.graders.case_history import _authoring_calls

FIXTURE = Path(__file__).parent / "fixtures/hermes-nested-authoring.json"
ROWS = json.loads(FIXTURE.read_bytes())["rows"]


def _capture(row: dict) -> NativeCapture:
    data = row["capture"]
    return NativeCapture(
        data["harness"],
        data["session_id"],
        tuple(NativeEvent(**event) for event in data["events"]),
        tuple(SourceFile(**source) for source in data["source_files"]),
        tuple(data["raw_records"]),
        tuple(data["unknowns"]),
    )


def test_real_fixture_identity() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        "7be6c2b286b92310c28309c4e3c6a30cf9792acc836c378e514bdd8ef740d02a"
    )
    assert sum(row["selectedAuthoringCount"] for row in ROWS) == 15


@pytest.mark.parametrize("row", ROWS, ids=[row["attemptId"] for row in ROWS])
def test_actual_nested_calls_enter_the_existing_graded_action_list(row: dict) -> None:
    before = _capture(row)
    assert not _authoring_calls(before)  # The exact pre-repair false-unknown antecedent.
    after = normalize_hermes_dispatches(before)
    assert len(_authoring_calls(after)) == row["selectedAuthoringCount"]
    assert not after.unknowns
    assert after.raw_records == before.raw_records
    assert after.source_files == before.source_files
    assert normalize_hermes_dispatches(after) == after
    for original, normalized in zip(before.events, after.events, strict=True):
        assert normalized.source == original.source
        assert normalized.source_sha256 == original.source_sha256
        assert normalized.sequence == original.sequence
        assert normalized.native_timestamp == original.native_timestamp
        if original.kind == "tool_result":
            assert normalized == original
            continue
        child = json.loads(original.payload["arguments"])["calls"][0]
        assert normalized.payload["name"] == child["name"]
        assert normalized.payload["arguments"] == child["arguments"]
        assert normalized.payload["call_id"] == original.payload["call_id"]
        assert normalized.payload["native_dispatch"]["payload"] == original.payload


def test_real_ce14_refusal_and_planned_count_are_not_changed() -> None:
    row = next(row for row in ROWS if row["case"] == "CE14")
    before = _capture(row)
    after = normalize_hermes_dispatches(before)
    result = next(event for event in after.events if event.kind == "tool_result")
    output = result.payload["output"]
    envelope = json.loads(output.split("\n\n", 1)[1].rsplit("\n</untrusted_tool_result>", 1)[0])
    receipt = json.loads(envelope["result"])
    assert receipt["state"] == "refused"
    assert receipt["plannedBeforeRefusal"] == 2
    assert result == next(event for event in before.events if event.kind == "tool_result")


@pytest.mark.parametrize("mutation", ["key", "multiple", "result"])
def test_unobserved_dispatch_shape_is_named_with_location_and_hash(mutation: str) -> None:
    before = _capture(ROWS[0])
    events = list(before.events)
    call = events[0]
    payload = copy.deepcopy(call.payload)
    args = json.loads(payload["arguments"])
    if mutation == "key":
        args["unobserved"] = True
    elif mutation == "multiple":
        args["calls"].append(args["calls"][0])
    else:
        events = [event for event in events if event.kind != "tool_result"]
    payload["arguments"] = json.dumps(args)
    events[0] = replace(call, payload=payload)
    after = normalize_hermes_dispatches(replace(before, events=tuple(events)))
    assert bool(after.unknowns) == (mutation == "result")
    assert all(problem.startswith("capture-absent:") for problem in after.unknowns)
    opaque = dict(after.events[0].payload)
    assert opaque.pop("opaque") is True
    native = opaque.pop("native_dispatch")
    reason = native["reason"]
    assert "kind='tool_call'" in reason
    assert "keys=" in reason and "JSONPath=$.events[0]" in reason and "sha256=" in reason
    assert opaque == payload
    assert native["pairedOutputs"] == [
        asdict(event)
        for event in events
        if event.kind == "tool_result" and event.payload.get("call_id") == payload["call_id"]
    ]
