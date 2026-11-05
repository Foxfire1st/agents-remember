"""Constructed Claude read-channel controls; these are not live read proof.

The native fields follow the existing Claude stream parser and official CLI
schema. Initial controls predate the root-owned live captures. The limit error text below
instantiates the installed 2.1.303 MaxFileReadTokenExceededError constructor;
its token counts are constructed, independent of the manifest's actual bytes.
The root separately owns the two admitted live judge calls.

Later Grep controls retain exact rows from the historical D642 Claude call,
whose grant was superseded by D643. Their path rebinding and repeated views are explicit
constructed replays; they do not launch another paid call or alter its source.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters import judges
from agent_evals.adapters.capture import capture_claude_cli_jsonl
from agent_evals.adapters.judge_failures import finalize_judge_context
from agent_evals.adapters.models import NativeCapture

SESSION = "constructed-claude-read-session"
CRITERION = "The mandatory carrier supports the stated claim."
NOT_SEEN_REASON = "the kept log is not known to be complete on this harness"
READ_LIMIT = (
    "File content (30000 tokens) exceeds maximum allowed tokens (25000). "
    "Use offset and limit parameters to read specific portions of the file, "
    "or search for specific content instead of reading the whole file."
)


class _Binding(SimpleNamespace):
    root: Path
    rows: tuple[dict[str, Any], ...]
    applied_limits: dict[str, int]
    judge_harness: str
    package_identity: str


def _binding(tmp_path: Path, content: bytes = b'{"claim":"source-bound"}\n') -> _Binding:
    root = (tmp_path / "evidence").resolve()
    root.mkdir()
    (root / "mandatory.json").write_bytes(content)
    return _Binding(
        root=root,
        rows=(
            {
                "path": "mandatory.json",
                "sizeBytes": len(content),
                "characters": len(content.decode()),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mustRead": True,
                "criteria": (CRITERION,),
            },
        ),
        applied_limits={"utf8Bytes": 154000, "characters": 154000},
        judge_harness="claude",
        package_identity="constructed-read-channel-package",
    )


def _call(name: str, arguments: dict, identifier: str = "read-1") -> dict:
    return {
        "type": "assistant",
        "session_id": SESSION,
        "message": {
            "id": "constructed-assistant-message",
            "content": [{"type": "tool_use", "id": identifier, "name": name, "input": arguments}],
        },
    }


def _result(content: str, *, error: bool = False, identifier: str = "read-1") -> dict:
    return {
        "type": "user",
        "session_id": SESSION,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": identifier,
                    "content": content,
                    "is_error": error,
                }
            ]
        },
    }


def _capture(
    tmp_path: Path,
    binding: _Binding,
    *rows: dict,
    denials: tuple[dict, ...] = (),
) -> NativeCapture:
    records = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": SESSION,
            "cwd": str(binding.root),
            "tools": ["Read", "Grep", "Glob", "Bash"],
        },
        *rows,
        {
            "type": "result",
            "subtype": "success",
            "session_id": SESSION,
            "is_error": False,
            "num_turns": len(
                {
                    row["message"]["id"]
                    for row in rows
                    if row.get("type") == "assistant"
                    and isinstance(row.get("message"), dict)
                    and isinstance(row["message"].get("id"), str)
                }
            ),
            "permission_denials": list(denials),
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    ]
    path = tmp_path / "constructed-claude-stream.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    return capture_claude_cli_jsonl([path], SESSION)


def _missing_paths(audit: dict) -> set[str]:
    missing = audit["missingReferencedEvidence"]
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["readObservationLimit"]["countsComparable"] is False
    assert (
        audit["readObservationLimit"]["source"]
        == "native kept-log observations; no completeness inference"
    )
    assert audit["causeCanFire"] is False
    assert audit["causeAvailability"] == "instrument-unavailable"
    assert audit["rateMeasurable"] is False
    assert audit["rateReason"] == NOT_SEEN_REASON
    assert all(row["cause"] is None for row in missing)
    assert all(row["causeCanFire"] is False for row in missing)
    assert all(row["causeAvailability"] == "instrument-unavailable" for row in missing)
    assert all(row["rateMeasurable"] is False for row in missing)
    assert all(row["rateReason"] == NOT_SEEN_REASON for row in missing)
    assert all(row["state"] == "not seen in kept log" for row in missing)
    assert all(row["reason"] == NOT_SEEN_REASON for row in missing)
    missing_paths = {path for row in missing for path in row["paths"]}
    states = [row for row in audit["referenceStates"] if row["path"] in missing_paths]
    assert {row["path"] for row in states} == missing_paths
    assert all(row["state"] == "not seen in kept log" for row in states)
    assert all(row["reason"] == NOT_SEEN_REASON for row in states)
    assert all(tuple(row["criteria"]) == (CRITERION,) for row in missing)
    return {path for row in missing for path in row["paths"]}


def test_claude_successful_read_proves_opened_must_read(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    arguments = {"file_path": str(binding.root / "mandatory.json")}
    capture = _capture(tmp_path, binding, _call("Read", arguments), _result("1\tactual file bytes"))
    call = next(event for event in capture.events if event.kind == "tool_call")
    result = next(event for event in capture.events if event.kind == "tool_result")
    assert call.payload["id"] == result.payload["tool_use_id"] == "read-1"
    assert call.payload["input"] == arguments
    assert "call_id" not in call.payload and "arguments" not in call.payload

    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True
    assert {row["path"] for row in audit["opened"]} == {"mandatory.json"}
    assert not audit["outsideAttempts"]
    assert not audit["missingReferencedEvidence"]
    assert not audit["unknowns"]


@pytest.mark.parametrize("path_kind", ["absolute", "relative", "symlink"])
@pytest.mark.parametrize("denied", [False, True], ids=["successful-read", "denied-read"])
def test_claude_outside_read_is_recorded_without_capture_unknown(
    tmp_path: Path, path_kind: str, denied: bool
) -> None:
    binding = _binding(tmp_path)
    outside = tmp_path / "harmless-outside-control.txt"
    outside.write_text("constructed control only")
    if path_kind == "relative":
        attempted = "../harmless-outside-control.txt"
    elif path_kind == "symlink":
        link = binding.root / "outside-link.txt"
        link.symlink_to(outside)
        attempted = str(link)
    else:
        attempted = str(outside)
    denial = {
        "type": "system",
        "subtype": "permission_denied",
        "session_id": SESSION,
        "tool_name": "Read",
        "tool_use_id": "read-1",
        "message": "Constructed outside read denial; no live call occurred.",
    }
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": attempted}),
        *([denial] if denied else []),
        _result(denial["message"] if denied else outside.read_text(), error=denied),
        denials=(
            {
                "tool_name": "Read",
                "tool_use_id": "read-1",
                "tool_input": {"file_path": attempted},
            },
        )
        if denied
        else (),
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is False
    assert not audit["opened"]
    assert audit["outsideAttempts"]
    assert str(outside) in json.dumps(audit["outsideAttempts"])
    attempts = [row for row in audit["outsideAttempts"] if row["callId"] == "read-1"]
    assert attempts
    assert all(row["resultState"] == ("refused" if denied else "read") for row in attempts)
    assert all(
        row["behavior"] == ("attempted-refused-recorded" if denied else "attempted-read-recorded")
        for row in attempts
    )
    assert not audit["unknowns"]


@pytest.mark.parametrize("pairing", ["absent", "foreign-id", "foreign-session", "foreign-source"])
def test_outside_read_without_bound_result_does_not_invent_a_read_or_refusal(
    tmp_path: Path, pairing: str
) -> None:
    binding = _binding(tmp_path)
    outside = tmp_path / "harmless-outside-control.txt"
    outside.write_text("constructed outside control only")
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(outside)}, "outside-unbound"),
        *(
            []
            if pairing == "absent"
            else [_result(outside.read_text(), identifier="outside-unbound")]
        ),
    )
    if pairing != "absent":
        events = list(capture.events)
        index = next(i for i, event in enumerate(events) if event.kind == "tool_result")
        if pairing == "foreign-id":
            events[index] = replace(
                events[index], payload={**events[index].payload, "tool_use_id": "other"}
            )
        else:
            field = "session_id" if pairing == "foreign-session" else "source_sha256"
            events[index] = replace(events[index], **{field: "other"})
        capture = replace(capture, events=tuple(events))
    audit = judges.audit_judge_reads(capture, binding)
    assert not audit["opened"]
    attempt = next(row for row in audit["outsideAttempts"] if row["callId"] == "outside-unbound")
    assert attempt["path"] == str(outside)
    assert attempt["resultState"] == "unavailable"
    assert attempt["behavior"] == "attempted-result-unavailable-recorded"
    assert not audit["unknowns"]


def test_unopened_referenced_evidence_has_metrics_without_capture_unknown(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    capture = _capture(tmp_path, binding)
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is False
    assert not audit["opened"]
    assert _missing_paths(audit) == {"mandatory.json"}
    assert not audit["unknowns"]


def test_final_permission_denial_records_outside_attempt_without_tool_use(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    outside = tmp_path / "harmless-outside-control.txt"
    capture = _capture(
        tmp_path,
        binding,
        denials=(
            {
                "tool_name": "Read",
                "tool_use_id": "denied-call-absent-from-stream",
                "tool_input": {"file_path": str(outside)},
            },
        ),
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is False
    assert not audit["opened"]
    assert str(outside) in json.dumps(audit["outsideAttempts"])
    attempt = next(
        row for row in audit["outsideAttempts"] if row["callId"] == "denied-call-absent-from-stream"
    )
    assert attempt["resultState"] == "refused"
    assert attempt["behavior"] == "attempted-refused-recorded"
    assert not audit["unknowns"]


@pytest.mark.parametrize(
    "command,output,state,whole_read",
    [
        ("cat mandatory.json", "first claim\nsecond line\nthird line\n", "opened", True),
        ("sed -n '1,2p' mandatory.json", "first claim\nsecond line\n", "searched", False),
        ("head -n 2 mandatory.json", "first claim\nsecond line\n", "searched", False),
        ("tail -n 2 mandatory.json", "second line\nthird line\n", "searched", False),
        ("rg claim mandatory.json", "first claim\n", "searched", None),
        ("grep claim mandatory.json", "first claim\n", "searched", None),
    ],
    ids=["cat", "sed-range", "head-range", "tail-range", "rg-match", "grep-match"],
)
def test_claude_bash_literal_file_reads_keep_native_identity_and_coverage(
    tmp_path: Path, command: str, output: str, state: str, whole_read: bool | None
) -> None:
    binding = _binding(tmp_path, b"first claim\nsecond line\nthird line\n")
    capture = _capture(
        tmp_path,
        binding,
        _call("Bash", {"command": command}, "shell-read-1"),
        _result(output, identifier="shell-read-1"),
    )
    call = next(event for event in capture.events if event.kind == "tool_call")
    result = next(event for event in capture.events if event.kind == "tool_result")
    assert call.payload["id"] == result.payload["tool_use_id"] == "shell-read-1"
    assert call.payload["input"] == {"command": command}
    audit = judges.audit_judge_reads(capture, binding)
    reference = next(row for row in audit["referenceStates"] if row["path"] == "mandatory.json")
    assert reference["state"] == state
    if whole_read is None:
        assert not audit["opened"]  # A search hit does not prove a whole read.
    else:
        opened = next(row for row in audit["opened"] if row["path"] == "mandatory.json")
        assert opened["wholeRead"] is whole_read
        assert opened["callId"] == "shell-read-1"
    assert not audit["missingReferencedEvidence"]
    assert not audit["unknowns"]


@pytest.mark.parametrize("command", ["cat *.md", "cat $F"], ids=["unexpanded-glob", "variable"])
def test_claude_bash_unresolved_file_selection_leaves_reference_not_seen_in_kept_log(
    tmp_path: Path, command: str
) -> None:
    binding = _binding(tmp_path)
    (binding.root / "second.json").write_bytes((binding.root / "mandatory.json").read_bytes())
    binding.rows += ({**binding.rows[0], "path": "second.json"},)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}, "canary-read"),
        _result("1\tactual file bytes", identifier="canary-read"),
        _call("Bash", {"command": command}, "unresolved-shell-read"),
        _result("unspecified file content\n", identifier="unresolved-shell-read"),
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True
    assert {row["path"] for row in audit["opened"]} == {"mandatory.json"}
    state = next(row for row in audit["referenceStates"] if row["path"] == "second.json")
    assert state["state"] == "not seen in kept log"
    assert state["reason"] == NOT_SEEN_REASON
    assert not any(
        row.get("cause") == "judge-did-not-open-referenced-evidence"
        for row in audit["missingReferencedEvidence"]
    )


def test_claude_bash_outside_read_is_recorded_without_capture_unknown(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    outside = tmp_path / "harmless-outside-control.txt"
    outside.write_text("constructed outside control\n")
    capture = _capture(
        tmp_path,
        binding,
        _call("Bash", {"command": "cat ../harmless-outside-control.txt"}, "outside-shell-read"),
        _result(outside.read_text(), identifier="outside-shell-read"),
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert not audit["opened"]
    attempt = next(row for row in audit["outsideAttempts"] if row["callId"] == "outside-shell-read")
    assert attempt["path"] == str(outside)
    assert attempt["resultState"] == "read"
    assert attempt["behavior"] == "attempted-read-recorded"
    assert not audit["unknowns"]


def test_runtime_unseen_reference_is_instrument_unavailable_despite_positive_read(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    content = b"second mandatory carrier\n"
    (binding.root / "second.json").write_bytes(content)
    binding.rows += (
        {
            "path": "second.json",
            "sizeBytes": len(content),
            "characters": len(content.decode()),
            "sha256": hashlib.sha256(content).hexdigest(),
            "mustRead": True,
            "criteria": (CRITERION,),
        },
    )
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result("1\tactual file bytes"),
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True
    assert {row["path"] for row in audit["opened"]} == {"mandatory.json"}
    assert _missing_paths(audit) == {"second.json"}
    assert (
        next(row["state"] for row in audit["referenceStates"] if row["path"] == "second.json")
        == "not seen in kept log"
    )
    assert audit["criteriaCarryingCause"] == 0
    assert audit["observedEligibleCriteria"] == 0
    assert audit["unobservableCriteria"] == 1
    assert "not a measured" in audit["criteriaCarryingCauseInterpretation"]
    assert not audit["unknowns"]


def test_missing_native_turn_count_does_not_charge_reference_absent_from_kept_log(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path)
    content = (binding.root / "mandatory.json").read_bytes()
    (binding.root / "second.json").write_bytes(content)
    binding.rows += ({**binding.rows[0], "path": "second.json"},)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result("1\tactual file bytes"),
    )
    records = copy.deepcopy(capture.raw_records)
    for row in records:
        if row.get("type") == "result":
            row.pop("num_turns")
    source = Path(capture.source_files[0].path)
    source.write_text("".join(json.dumps(row) + "\n" for row in records))
    capture = capture_claude_cli_jsonl([source], SESSION)
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True
    assert {row["path"] for row in audit["opened"]} == {"mandatory.json"}
    state = next(row for row in audit["referenceStates"] if row["path"] == "second.json")
    assert state["state"] == "not seen in kept log"
    assert state["reason"] == NOT_SEEN_REASON
    assert not any(
        row.get("cause") == "judge-did-not-open-referenced-evidence"
        for row in audit["missingReferencedEvidence"]
    )


@pytest.mark.parametrize("tool", ["Grep", "Glob"])
def test_grep_glob_discovery_does_not_count_as_mandatory_file_opened(
    tmp_path: Path, tool: str
) -> None:
    binding = _binding(tmp_path)
    arguments = {"path": str(binding.root), "pattern": "mandatory"}
    capture = _capture(tmp_path, binding, _call(tool, arguments), _result("mandatory.json"))
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is False
    assert not audit["opened"]
    assert _missing_paths(audit) == {"mandatory.json"}
    if tool == "Grep":
        assert any(
            "judge-read-channel-not-observable" in value for value in audit["coverageDiagnostics"]
        )
    assert not audit["unknowns"]


@pytest.mark.parametrize("identity", ["tool_use_id", "session_id", "source_sha256"])
def test_unpaired_read_result_is_channel_unknown(tmp_path: Path, identity: str) -> None:
    binding = _binding(tmp_path)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result("1\tactual file bytes"),
    )
    events = list(capture.events)
    index = next(i for i, event in enumerate(events) if event.kind == "tool_result")
    if identity == "tool_use_id":
        events[index] = replace(events[index], payload={**events[index].payload, identity: "other"})
    else:
        events[index] = replace(events[index], **{identity: "other"})
    audit = judges.audit_judge_reads(replace(capture, events=tuple(events)), binding)
    assert not audit["opened"]
    assert _missing_paths(audit) == {"mandatory.json"}
    assert any(
        "judge-read-channel-not-observable" in reason for reason in audit["coverageDiagnostics"]
    )
    assert not audit["unknowns"]


def test_context_read_refusal_retains_file_size_without_terminal_attribution(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path, b"x" * 528213)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result(READ_LIMIT, error=True),
    )
    audit = judges.audit_judge_reads(capture, binding)
    finalize_judge_context(audit, {}, (CRITERION,))
    assert not audit["opened"]
    assert not audit["unknowns"]
    (fact,) = audit["readerLimitRefusals"]
    assert fact["path"] == "mandatory.json" and fact["sizeBytes"] == 528213
    assert fact["reason"] == "native Read refused its token limit"
    assert _missing_paths(audit) == {"mandatory.json"}


def test_successful_file_text_cannot_impersonate_native_context_refusal(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result(READ_LIMIT, error=False),
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert {row["path"] for row in audit["opened"]} == {"mandatory.json"}
    assert not audit["unknowns"]


GREP_FIXTURE = Path(__file__).parent / "fixtures/judge-claude-grep-real.json"
REAL_GREP = json.loads(GREP_FIXTURE.read_bytes())
ACTION = "actions/0005-89bfff9875e301bd0d4277394996358a40ef78938cd2c7f6e6145e9522985730.json"


def _real_grep_control(
    tmp_path: Path, indices: tuple[int, ...] = (39, 40, 54, 55, 56, 57)
) -> tuple[NativeCapture, _Binding]:
    """Rebind exact retained rows into a constructed local evidence directory."""
    root = (tmp_path / "real-grep-evidence").resolve()
    (root / "actions").mkdir(parents=True)
    (root / "controls").mkdir()
    (root / "controls/must-read.txt").write_text(REAL_GREP["successfulCanary"]["content"])
    rows = copy.deepcopy(REAL_GREP["manifestRows"])
    for row in rows:
        if row["path"] == ACTION:
            # This additional reference duty is constructed, not a mutation of
            # the original manifest. It detects the false unopened accusation.
            row.update(mustRead=True, inline=False)
    binding = _Binding(
        root=root,
        rows=tuple(rows),
        applied_limits=REAL_GREP["appliedLimits"],
        judge_harness="claude",
        package_identity="constructed-rebound-real-grep-package",
    )
    records = [
        copy.deepcopy(item["row"])
        for item in REAL_GREP["rawRows"]
        if item["rawIndex"] in (5, 6, *indices)
    ]
    # Path rebinding is explicit. The production fixture keeps the exact
    # original raw values and full containing-stream identity unchanged.
    rebound = json.loads(json.dumps(records).replace(REAL_GREP["originalEvidenceRoot"], str(root)))
    source = tmp_path / "constructed-rebound-real-grep.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rebound))
    return capture_claude_cli_jsonl([source], rebound[0]["session_id"]), binding


def test_real_claude_grep_fixture_retains_native_modes_and_source_identity() -> None:
    assert hashlib.sha256(GREP_FIXTURE.read_bytes()).hexdigest() == (
        "bb348964c1cb6a0eb1bd4d5a27aa45e22430d92aa141bc206b959a38d3d44301"
    )
    assert REAL_GREP["source"]["sha256"] == (
        "984a40d052b9b333fbfc17e6d9363e924bf47696a5451c3a70fcd7e5d9b9a02b"
    )
    raw = {item["rawIndex"]: item["row"] for item in REAL_GREP["rawRows"]}
    assert raw[40]["tool_use_result"]["mode"] == "count"
    assert raw[55]["tool_use_result"]["mode"] == "content"
    assert raw[57]["tool_use_result"]["mode"] == "content"
    assert any(row["path"] == ACTION for row in REAL_GREP["manifestRows"])


def test_real_count_mode_grep_does_not_expose_or_open_referenced_content(tmp_path: Path) -> None:
    capture, binding = _real_grep_control(tmp_path, (39, 40))
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True  # Actual successful canary rows 5/6.
    assert {row["path"] for row in audit["opened"]} == {"controls/must-read.txt"}
    assert not audit.get("contentExposures", [])
    assert not any(
        ACTION in row["paths"] and row["cause"] == "judge-did-not-open-referenced-evidence"
        for row in audit["missingReferencedEvidence"]
    )


@pytest.mark.parametrize("indices", [(54, 55), (56, 57)], ids=["raw54-55", "raw56-57"])
def test_real_content_mode_grep_is_exposure_without_full_read_credit(
    tmp_path: Path, indices: tuple[int, int]
) -> None:
    capture, binding = _real_grep_control(tmp_path, indices)
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True
    assert {row["path"] for row in audit["opened"]} == {"controls/must-read.txt"}
    exposures = [row for row in audit["contentExposures"] if row["path"] == ACTION]
    assert len(exposures) == 1
    grep_id = next(
        event.payload["id"]
        for event in capture.events
        if event.kind == "tool_call" and event.payload.get("name") == "Grep"
    )
    assert exposures[0]["callId"] == grep_id
    assert exposures[0]["classification"] == "referenced-evidence-seen-only-through-grep"
    assert exposures[0]["opened"] is False and exposures[0]["fullRead"] is False
    assert not any(
        ACTION in row["paths"] and row["cause"] == "judge-did-not-open-referenced-evidence"
        for row in audit["missingReferencedEvidence"]
    )


def test_identical_native_tool_call_and_result_views_are_counted_once(tmp_path: Path) -> None:
    capture, binding = _real_grep_control(tmp_path)
    expanded = [
        item
        for event in capture.events
        for item in ((event, event) if event.kind in {"tool_call", "tool_result"} else (event,))
    ]
    events = tuple(replace(event, sequence=index) for index, event in enumerate(expanded))
    audit = judges.audit_judge_reads(replace(capture, events=events), binding)
    assert not audit["unknowns"]
    assert {row["path"] for row in audit["opened"]} == {"controls/must-read.txt"}
    assert audit["deduplication"]["nativeToolCallEvents"] == 8
    assert audit["deduplication"]["uniqueToolUseIds"] == 4
    assert audit["deduplication"]["duplicateToolCallEventsIgnored"] == 4
    exposed = audit["contentExposures"]
    assert len({(row["path"], row["callId"]) for row in exposed}) == len(exposed)


@pytest.mark.parametrize("kind", ["tool_call", "tool_result"])
def test_conflicting_duplicate_native_identity_remains_channel_unknown(
    tmp_path: Path, kind: str
) -> None:
    capture, binding = _real_grep_control(tmp_path, (54, 55))
    event = next(
        item
        for item in capture.events
        if item.kind == kind
        and (
            item.payload.get("name") == "Grep" or "actions/0005" in item.payload.get("content", "")
        )
    )
    payload = copy.deepcopy(event.payload)
    if kind == "tool_call":
        payload["input"]["pattern"] = "conflicting constructed pattern"
    else:
        payload["content"] += "\nconflicting constructed result"
    events = []
    for original in capture.events:
        events.append(original)
        if original is event:
            events.append(replace(event, payload=payload))
    capture = replace(
        capture, events=tuple(replace(item, sequence=i) for i, item in enumerate(events))
    )
    audit = judges.audit_judge_reads(capture, binding)
    assert any(
        "judge-read-channel-not-observable" in reason for reason in audit["coverageDiagnostics"]
    )
    assert not audit["unknowns"]


def test_live_claude_inline_artifact_read_remains_manifest_bound(tmp_path: Path) -> None:
    """Real live rows43/44: successful read, with only temporary paths rebound."""
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/judge-claude-inline-read-real.json").read_text()
    )
    for row, expected in zip(fixture["records"], fixture["recordSha256"], strict=True):
        actual = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        assert actual == expected
    rows = json.loads(json.dumps(fixture["records"]))
    original = fixture["manifestRow"]
    raw = rows[1]["tool_use_result"]["file"]["content"].encode()
    assert hashlib.sha256(raw).hexdigest() == original["sha256"]
    binding = _binding(tmp_path, raw)
    binding.rows = ({**original, "path": "mandatory.json"},)
    assert original["inline"] is True and original["mustRead"] is False
    rows[0]["message"]["content"][0]["input"]["file_path"] = str(binding.root / "mandatory.json")
    rows[1]["tool_use_result"]["file"]["filePath"] = str(binding.root / "mandatory.json")
    source = tmp_path / "real-format-path-rebound.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    capture = capture_claude_cli_jsonl([source], rows[0]["session_id"])
    audit = judges.audit_judge_reads(capture, binding)
    assert audit["opened"] and audit["opened"][0]["manifestBound"] is True
    assert audit["opened"][0]["sha256"] == original["sha256"]
    assert audit["channelProven"] is True
    assert not audit["missingReferencedEvidence"]
    assert not audit["unknowns"]


@pytest.mark.parametrize("outcome", ["pass", "fail", "unknown"])
def test_reader_token_limit_is_nonfatal_when_a_strict_verdict_is_returned(
    tmp_path: Path, outcome: str
) -> None:
    binding = _binding(tmp_path)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result(READ_LIMIT, error=True),
    )
    audit = judges.audit_judge_reads(capture, binding)
    verdict = {
        "criteria": [
            {
                "criterion": CRITERION,
                "outcome": outcome,
                "evidence": "Later offset or shell views supplied the quoted evidence.",
            }
        ]
    }
    finalize_judge_context(audit, verdict, (CRITERION,))
    assert not audit["unknowns"]
    assert len(audit["readerLimitRefusals"]) == 1
    fact = audit["readerLimitRefusals"][0]
    assert (
        fact["path"] == "mandatory.json" and fact["reason"] == "native Read refused its token limit"
    )
    assert fact["nativeEvidence"]["sourceSha256"] == capture.source_files[0].sha256
    assert audit["readerLimitDisposition"]["validVerdictReturned"] is True
    assert audit["readerLimitDisposition"]["contextFailureCauses"] == 0


@pytest.mark.parametrize("change", ["absent", "wrong-order", "empty-evidence", "invalid-outcome"])
def test_unreturned_or_invalid_verdict_does_not_attribute_earlier_reader_limit(
    tmp_path: Path, change: str
) -> None:
    binding = _binding(tmp_path)
    capture = _capture(
        tmp_path,
        binding,
        _call("Read", {"file_path": str(binding.root / "mandatory.json")}),
        _result(READ_LIMIT, error=True),
    )
    audit = judges.audit_judge_reads(capture, binding)
    verdict = {
        "criteria": [{"criterion": CRITERION, "outcome": "pass", "evidence": "Observed evidence"}]
    }
    if change == "absent":
        verdict = {}
    elif change == "wrong-order":
        verdict["criteria"][0]["criterion"] = "invented criterion"
    elif change == "empty-evidence":
        verdict["criteria"][0]["evidence"] = " "
    else:
        verdict["criteria"][0]["outcome"] = "right"
    audit["unknowns"].append("independent capture problem")
    finalize_judge_context(audit, verdict, (CRITERION,))
    assert audit["unknowns"] == ["independent capture problem"]
    assert len(audit["readerLimitRefusals"]) == 1
    assert audit["readerLimitDisposition"]["validVerdictReturned"] is False
    assert audit["readerLimitDisposition"]["contextFailureCauses"] == 0
