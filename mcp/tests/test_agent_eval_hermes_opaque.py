"""Real complete unrecognised Hermes wrappers remain one opaque observed action."""

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

FIXTURE = Path(__file__).parent / "fixtures/hermes-opaque-dispatch-real.json"
ROWS = json.loads(FIXTURE.read_bytes())["rows"]


def _capture(row: dict) -> NativeCapture:
    return NativeCapture(
        "hermes",
        row["sessionId"],
        tuple(NativeEvent(**event) for event in row["events"]),
        tuple(SourceFile(**source) for source in row["sourceFiles"]),
        ({"retainedNativeEvents": row["events"]},),
        (),
    )


def test_retained_pair_fixture_identity() -> None:
    assert (
        hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        == "e3ff833a94b865f650f0b3ce46462662b1cf36c799f9d65913da25f15e9fed2e"
    )
    assert len(ROWS) == 7
    assert len({row["attemptId"] for row in ROWS}) == 6
    assert all(len(row["originalAttemptSha256"]) == 64 for row in ROWS)


@pytest.mark.parametrize(
    "row", ROWS, ids=[f"{r['case']}-{r['events'][0]['sequence']}" for r in ROWS]
)
def test_full_native_wrapper_and_outputs_are_one_opaque_action(row: dict) -> None:
    before = _capture(row)
    after = normalize_hermes_dispatches(before)
    assert not after.unknowns
    assert len(after.events) == len(before.events)
    assert after.events[1:] == before.events[1:]
    call = after.events[0]
    payload = dict(call.payload)
    assert payload.pop("opaque") is True
    native = payload.pop("native_dispatch")
    assert payload == before.events[0].payload
    assert native["classification"] == "opaque"
    assert native["kind"] == "tool_call"
    assert native["keys"] == sorted(payload)
    assert native["path"] == "$.events[0].payload"
    assert native["sourcePath"] == call.source
    assert native["sourceSha256"] == call.source_sha256
    assert (
        native["rawPayloadSha256"]
        == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert native["rawPayloadBasis"] == "original-capture-event.payload"
    assert native["pairedOutputs"] == [asdict(event) for event in before.events[1:]]
    assert all(
        marker in native["reason"]
        for marker in ("unsupported Hermes dispatch", "kind=", "keys=", "JSONPath=", "sha256=")
    )
    assert after.raw_records == before.raw_records
    assert after.source_files == before.source_files
    assert after.to_dict()["raw_export_sha256"] == before.to_dict()["raw_export_sha256"]
    assert after.installed_server_calls == ()  # Requested children are not executed evidence.
    assert normalize_hermes_dispatches(after) == after


def test_all_outputs_remain_full_and_unrelated_unknown_remains() -> None:
    before = _capture(ROWS[0])
    call, output = before.events
    second = replace(
        output,
        sequence=output.sequence + 1,
        payload={**output.payload, "output": "full second output: " + "x" * 2000},
    )
    before = replace(before, events=(call, output, second), unknowns=("another recording fault",))
    after = normalize_hermes_dispatches(before)
    assert after.unknowns == before.unknowns
    assert after.events[0].payload["native_dispatch"]["pairedOutputs"] == [
        asdict(output),
        asdict(second),
    ]
    assert after.events[1:] == before.events[1:]


@pytest.mark.parametrize("mutation", ["absent", "session", "source", "hash", "early"])
def test_material_pair_absence_is_named_capture_absent(mutation: str) -> None:
    before = _capture(ROWS[0])
    call, output = before.events
    if mutation == "absent":
        events = (call,)
    else:
        output = replace(output, payload=copy.deepcopy(output.payload))
        if mutation == "session":
            output = replace(output, session_id="other-session")
        elif mutation == "source":
            output = replace(output, source="/other/state.db")
        elif mutation == "hash":
            output = replace(output, source_sha256="0" * 64)
        else:
            output = replace(output, sequence=call.sequence - 1)
        events = (call, output)
    after = normalize_hermes_dispatches(replace(before, events=events))
    assert after.events[0].payload["opaque"] is True
    assert after.unknowns
    assert all(problem.startswith("capture-absent:") for problem in after.unknowns)
    assert all("call_id=" in problem for problem in after.unknowns)
