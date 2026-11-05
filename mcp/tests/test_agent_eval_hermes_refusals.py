"""Retained native Hermes dispatcher refusals do not invent child actions.

Each pair is test data from a retained attempt, with synthetic-home paths;
not read from a live Hermes home. The event source/hash remain the native source
identity; attempt SHA-256 identifies the original containing evidence bytes.
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.hermes_dispatch import normalize_hermes_dispatches
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile

ROWS = json.loads((Path(__file__).parent / "fixtures/hermes-refusal-real-records.json").read_text())


def _capture(row: dict) -> NativeCapture:
    call, result = NativeEvent(**row["call"]), NativeEvent(**row["result"])
    return NativeCapture(
        "hermes",
        call.session_id,
        (call, result),
        (SourceFile(call.source, call.source_sha256),),
        ({"retained_call": row["call"], "retained_result": row["result"]},),
        (),
    )


@pytest.mark.parametrize(
    "row", ROWS, ids=[f"{row['case']}-{row['native_event_index']}" for row in ROWS]
)
def test_native_dispatch_refusal_is_visible_without_a_guessed_child(row: dict) -> None:
    before = _capture(row)
    after = normalize_hermes_dispatches(before)
    assert not after.unknowns
    assert len(after.events) == 2
    assert after.events[1] == before.events[1]
    assert after.events[0].kind == "tool_call"
    payload = dict(after.events[0].payload)
    native = payload.pop("native_dispatch")
    assert payload == before.events[0].payload
    assert native == {
        "payload": payload,
        "path": "$.events[0].payload.arguments",
        "outcome": "refused",
        "effect": "none",
        "refusal": before.events[1].payload,
    }
    assert after.raw_records == before.raw_records
    assert after.source_files == before.source_files
    assert after.to_dict()["raw_export_sha256"] == before.to_dict()["raw_export_sha256"]
    assert normalize_hermes_dispatches(after) == after


@pytest.mark.parametrize(
    "mutation",
    ["absent", "duplicate", "early", "session", "source", "hash", "name", "output", "shape"],
)
def test_unproven_refusal_is_opaque_with_material_unknown_only(mutation: str) -> None:
    before = _capture(ROWS[0])
    call, result = before.events
    if mutation == "absent":
        events = (call,)
    elif mutation == "duplicate":
        events = (call, result, result)
    else:
        result = replace(result, payload=copy.deepcopy(result.payload))
        if mutation == "early":
            result = replace(result, sequence=call.sequence - 1)
        elif mutation == "session":
            result = replace(result, session_id="another-native-session")
        elif mutation == "source":
            result = replace(result, source="/another/state.db")
        elif mutation == "hash":
            result = replace(result, source_sha256="0" * 64)
        elif mutation == "name":
            result.payload["name"] = "guessed_child"
        elif mutation == "output":
            result.payload["output"] = '{"error":"tool_call refused for an unobserved reason"}'
        else:
            result.payload["output"] = (
                '{"error":"tool_call calls[0] requires a \'name\'","extra":true}'
            )
        events = (call, result)
    before = replace(before, events=events)
    after = normalize_hermes_dispatches(before)
    assert after.events[1:] == before.events[1:]
    payload = dict(after.events[0].payload)
    assert payload.pop("opaque") is True
    native = payload.pop("native_dispatch")
    assert payload == before.events[0].payload
    material_missing = mutation in {"absent", "early", "session", "source", "hash"}
    assert bool(after.unknowns) == material_missing
    assert all(problem.startswith("capture-absent:") for problem in after.unknowns)
    assert all(
        marker in native["reason"]
        for marker in (
            "unsupported Hermes dispatch",
            "kind='tool_call'",
            "keys=",
            "JSONPath=",
            "sha256=",
        )
    )


def test_unrelated_unknown_is_never_cleared_by_a_known_refusal() -> None:
    before = replace(_capture(ROWS[0]), unknowns=("another recording fault",))
    assert normalize_hermes_dispatches(before).unknowns == before.unknowns


@pytest.mark.parametrize("mutation", ["named_child", "second_child", "outer_key", "valid_json"])
def test_native_error_cannot_prove_a_different_input_was_refused(mutation: str) -> None:
    before = _capture(ROWS[2] if mutation == "valid_json" else ROWS[0])
    call, result = before.events
    payload = copy.deepcopy(call.payload)
    arguments = json.loads(payload["arguments"])
    if mutation == "valid_json":
        arguments["calls"] = "[]"
    elif mutation == "outer_key":
        arguments["unobserved"] = True
    elif mutation == "second_child":
        arguments["calls"].append(copy.deepcopy(arguments["calls"][0]))
    else:
        arguments["calls"][0]["name"] = "a_real_child_might_have_run"
    payload["arguments"] = json.dumps(arguments)
    before = replace(before, events=(replace(call, payload=payload), result))
    after = normalize_hermes_dispatches(before)
    assert not after.unknowns
    assert after.events[0].payload["opaque"] is True
    assert after.events[0].payload["native_dispatch"]["classification"] == "opaque"
    assert after.events[0].payload["arguments"] == payload["arguments"]
    assert after.events[0].payload["native_dispatch"]["pairedOutputs"] == [asdict(result)]
    assert after.events[1] == result


@pytest.mark.parametrize("mutation", ["duplicate_call", "source_absent", "provenance"])
def test_refusal_requires_unique_call_and_unchanged_native_provenance(mutation: str) -> None:
    before = _capture(ROWS[0])
    if mutation == "duplicate_call":
        before = replace(before, events=(before.events[0], *before.events))
    elif mutation == "source_absent":
        before = replace(before, source_files=())
    else:
        before = normalize_hermes_dispatches(before)
        call, result = before.events
        payload = copy.deepcopy(call.payload)
        payload["native_dispatch"]["effect"] = "unproven"
        before = replace(before, events=(replace(call, payload=payload), result))
    after = normalize_hermes_dispatches(before)
    assert after.events[0].payload["opaque"] is True
    assert after.events[0].payload["native_dispatch"]["classification"] == "opaque"
    assert [event for event in after.events if event.kind == "tool_result"] == [
        event for event in before.events if event.kind == "tool_result"
    ]
    assert bool(after.unknowns) == (mutation != "provenance")
    assert all(problem.startswith("capture-absent:") for problem in after.unknowns)
