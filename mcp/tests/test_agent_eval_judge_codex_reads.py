"""Real Codex wire format plus explicitly constructed judge read controls.

Constructed path-rebound receipts exercise the pure observer. They are not
evidence that a live judge or the native OS sandbox performed these reads.
"""

from __future__ import annotations

import base64
import copy
import gzip
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters import judges, native_trials
from agent_evals.adapters.capture import CaptureBuilder
from agent_evals.adapters.codex_items import completed_item
from agent_evals.adapters.execution import CODEX_JUDGE, NativeBinding, NativeInvocation
from agent_evals.adapters.judge_failures import finalize_judge_context
from agent_evals.adapters.judges import JudgeReadBinding, audit_judge_reads
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.artifacts import json_digest, sha256_file
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


def _assert_rate_unmeasurable(record: dict) -> None:
    assert record["rateMeasurable"] is False
    assert record["rateReason"] == "the kept log is not known to be complete on this harness"
    assert record["causeCanFire"] is False
    assert record["causeAvailability"] == "instrument-unavailable"
    for row in record.get("rows", []):
        _assert_rate_unmeasurable(row)


def _real() -> dict:
    path = Path(__file__).parent / "fixtures/judge-codex-read-real.json"
    fixture = json.loads(path.read_text())
    assert fixture["nativeOriginalAvailable"] is False
    assert fixture["rawArrayIndex"] == fixture["ordinal"] == 53
    assert fixture["observedJsonPath"] == "$.capture.raw_records[53]"
    assert fixture["sourceAttemptSha256"] == (
        "28605951a09d18216455bbd341d47d8527a036dd883e4bd3168318c123008f94"
    )
    assert json_digest(fixture["record"]) == fixture["recordCanonicalSha256"]
    return fixture


def _binding(root: Path, *, limits: dict | None = None) -> JudgeReadBinding:
    root.mkdir(exist_ok=True)
    content = "évidence read control\n"
    path = root / "must-read.txt"
    path.write_text(content)
    return cast(
        JudgeReadBinding,
        SimpleNamespace(
            root=root,
            rows=(
                {
                    "path": path.name,
                    "sizeBytes": len(content.encode()),
                    "characters": len(content),
                    "sha256": sha256_file(path),
                    "mustRead": True,
                    "criteria": ["read the retained card"],
                },
            ),
            applied_limits=limits or {"characters": 100_000, "utf8Bytes": 100_000},
            judge_harness="codex",
            package_identity="constructed-control-package",
        ),
    )


def _constructed_capture(
    binding: JudgeReadBinding, command: list[str], **item_changes: object
) -> NativeCapture:
    """Rebind a real item shape; every changed field is constructed test data."""
    real = _real()
    item = copy.deepcopy(real["record"]["payload"]["item"])
    item.update(
        command=command,
        cwd=binding.root.as_uri(),
        parsed_cmd=[{"type": "unknown", "cmd": "constructed control"}],
        status="completed",
        exit_code=0,
        aggregated_output=(binding.root / "must-read.txt").read_text(),
    )
    item.update(item_changes)
    source = SourceFile(**real["nativeSourcesDeclared"][0])
    session = real["record"]["payload"]["thread_id"]
    call = NativeEvent(
        "tool_call",
        session,
        0,
        {
            "name": "CommandExecution",
            "call_id": item["id"],
            "native_item": item,
            "arguments": {"command": item["command"], "cwd": item["cwd"]},
        },
        source.path,
        source.sha256,
    )
    result = replace(
        call,
        kind="tool_result",
        sequence=1,
        payload={"call_id": item["id"], "native_item": copy.deepcopy(item), "output": item},
    )
    return NativeCapture("codex", session, (call, result), (source,), (), ())


def test_unchanged_retained_codex_wire_preserves_full_read_receipt() -> None:
    fixture = _real()
    original = copy.deepcopy(fixture["record"])
    payload = original["payload"]
    source = SourceFile(**fixture["nativeSourcesDeclared"][0])
    builder = CaptureBuilder("codex", payload["thread_id"])
    builder.sources.append(source)
    completed_item(builder, payload, source)
    call, result = builder.events
    assert call.payload["native_item"] == result.payload["native_item"] == payload["item"]
    assert call.payload["arguments"] == {
        key: payload["item"][key] for key in ("command", "cwd", "parsed_cmd")
    }
    assert result.payload["output"] == payload["item"]
    assert call.session_id == result.session_id == payload["thread_id"]
    assert call.source == result.source == source.path
    assert call.source_sha256 == result.source_sha256 == source.sha256
    assert payload["item"]["command"][2].startswith("sed -n ")
    assert payload["item"]["parsed_cmd"][0]["type"] == "read"
    assert payload["item"]["status"] == "completed" and payload["item"]["exit_code"] == 0
    assert original == fixture["record"]


@pytest.mark.parametrize("shape", ["direct", "relative", "shell", "shell-quoted"])
def test_constructed_codex_cat_proves_opened_must_read(tmp_path: Path, shape: str) -> None:
    binding = _binding(tmp_path / "evidence")
    target = binding.root / "must-read.txt"
    command = {
        "direct": ["/bin/cat", "--", str(target)],
        "relative": ["cat", target.name],
        "shell": ["/bin/bash", "-lc", f"cat -- {target}"],
        "shell-quoted": ["/bin/bash", "-c", f"cat -- '{target}'"],
    }[shape]
    capture = _constructed_capture(binding, command)
    original = copy.deepcopy(capture.to_dict())
    audit = audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True
    assert audit["unknowns"] == [] and audit["outsideAttempts"] == []
    assert audit["missingReferencedEvidence"] == []
    # Codex has no admitted whole-capture source: the positive read is known,
    # while the trial's absence-rate denominator remains unavailable.
    assert audit["observedEligibleCriteria"] == 0 and audit["unobservableCriteria"] == 1
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["readObservationLimit"]["countsComparable"] is False
    assert audit["causeCanFire"] is False
    assert audit["causeAvailability"] == "instrument-unavailable"
    assert audit["referenceStates"][0]["state"] == "opened"
    assert audit["packageIdentity"] == binding.package_identity
    (opened,) = audit["opened"]
    assert opened == {
        "path": target.name,
        "callId": capture.events[0].payload["call_id"],
        "source": capture.events[0].source,
        "sourceSha256": capture.events[0].source_sha256,
        "nativeResultSha256": json_digest(capture.events[1].payload),
        "sizeBytes": binding.rows[0]["sizeBytes"],
        "characters": binding.rows[0]["characters"],
        "sha256": binding.rows[0]["sha256"],
        "manifestBound": True,
        "wholeRead": True,
        "coverage": None,
    }
    assert capture.to_dict() == original


@pytest.mark.parametrize("result_present", [True, False])
def test_constructed_outside_attempt_is_a_recorded_fact_not_an_unknown_cause(
    tmp_path: Path, result_present: bool
) -> None:
    binding = _binding(tmp_path / "evidence")
    outside = tmp_path / "outside-canary.txt"
    outside.write_text("harmless outside canary")
    capture = _constructed_capture(
        binding,
        ["/bin/cat", "--", str(outside)],
        status="failed",
        exit_code=1,
        aggregated_output="cat: Permission denied",
    )
    if not result_present:
        capture = replace(capture, events=capture.events[:1])
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == [] and audit["channelProven"] is False
    (attempt,) = audit["outsideAttempts"]
    assert attempt["path"] == attempt["requestedPath"] == str(outside)
    assert attempt["callId"] == capture.events[0].payload["call_id"]
    assert attempt["source"] == capture.events[0].source
    assert attempt["sourceSha256"] == capture.events[0].source_sha256
    assert audit["outsideAttempts"][0]["classification"] == "judge-read-outside-evidence"
    assert not any("judge-read-outside-evidence" in cause for cause in audit["unknowns"])


@pytest.mark.parametrize(
    "shell",
    [
        "cat -- must-read.txt && cat -- another.txt",
        "cat -- must-read.txt | head -1",
        "python3 -c 'open(\"must-read.txt\").read()'",
        "cat -- $TARGET",
        "cat -- `pwd`/must-read.txt",
        "cat -- must-read.txt\ncat -- another.txt",
        "cat -- must-read.txt > copied.txt",
        "TARGET=must-read.txt cat -- must-read.txt",
    ],
)
def test_constructed_unclassified_command_does_not_prove_a_read(tmp_path: Path, shell: str) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = _constructed_capture(binding, ["/bin/bash", "-lc", shell])
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == [] and audit["channelProven"] is False
    assert audit["coverageDiagnostics"]
    assert audit["unknowns"] == []
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["referenceStates"][0]["state"] == "not seen in kept log"
    assert (
        audit["referenceStates"][0]["reason"]
        == "the kept log is not known to be complete on this harness"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing-result",
        "foreign-call-session",
        "foreign-result-session",
        "foreign-source",
        "foreign-hash",
        "missing-source",
        "duplicate-call",
        "earlier-result",
        "changed-item",
    ],
)
def test_constructed_unbound_receipt_never_counts_as_opened(tmp_path: Path, fault: str) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = _constructed_capture(binding, ["cat", "--", "must-read.txt"])
    call, result = capture.events
    if fault == "missing-result":
        capture = replace(capture, events=(call,))
    elif fault == "missing-source":
        capture = replace(capture, source_files=())
    elif fault == "duplicate-call":
        conflicting = copy.deepcopy(call.payload)
        conflicting["native_item"]["command"] = ["cat", "--", "different.txt"]
        conflicting["arguments"]["command"] = ["cat", "--", "different.txt"]
        capture = replace(capture, events=(call, replace(call, payload=conflicting), result))
    else:
        if fault == "foreign-call-session":
            call = replace(call, session_id="foreign")
        elif fault == "foreign-result-session":
            result = replace(result, session_id="foreign")
        elif fault == "foreign-source":
            result = replace(result, source="/foreign/native.jsonl")
        elif fault == "foreign-hash":
            result = replace(result, source_sha256="f" * 64)
        elif fault == "earlier-result":
            result = replace(result, sequence=-1)
        elif fault == "changed-item":
            payload = copy.deepcopy(result.payload)
            payload["native_item"]["aggregated_output"] += "changed"
            result = replace(result, payload=payload)
        capture = replace(capture, events=(call, result))
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == [] and audit["channelProven"] is False
    assert audit["coverageDiagnostics"]
    assert audit["unknowns"] == []
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["referenceStates"][0]["state"] == "not seen in kept log"
    assert (
        audit["referenceStates"][0]["reason"]
        == "the kept log is not known to be complete on this harness"
    )


def test_constructed_identical_native_observations_count_one_tool_identity(tmp_path: Path) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = _constructed_capture(binding, ["cat", "--", "must-read.txt"])
    call, result = capture.events
    capture = replace(capture, events=(call, call, result, result))
    audit = audit_judge_reads(capture, binding)
    assert audit["channelProven"] is True and audit["unknowns"] == []
    assert len(audit["opened"]) == 1
    assert audit["deduplication"] == {
        "nativeToolCallEvents": 2,
        "uniqueToolUseIds": 1,
        "duplicateToolCallEventsIgnored": 1,
        "nativeToolResultEvents": 2,
        "duplicateToolResultEventsIgnored": 1,
    }


def test_constructed_absent_session_is_not_a_bound_read_receipt(tmp_path: Path) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = _constructed_capture(binding, ["cat", "--", "must-read.txt"])
    capture = replace(
        capture,
        session_id="",
        events=tuple(replace(event, session_id="") for event in capture.events),
    )
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == [] and audit["channelProven"] is False
    assert audit["coverageDiagnostics"]
    assert audit["unknowns"] == []
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["referenceStates"][0]["state"] == "not seen in kept log"
    assert (
        audit["referenceStates"][0]["reason"]
        == "the kept log is not known to be complete on this harness"
    )


@pytest.mark.parametrize("flag", ["truncated", "output_truncated"])
def test_constructed_truncated_read_records_fact_without_terminal_context_cause(
    tmp_path: Path, flag: str
) -> None:
    binding = _binding(tmp_path / "evidence", limits={"characters": 4, "utf8Bytes": 8})
    capture = _constructed_capture(binding, ["cat", "--", "must-read.txt"], **{flag: True})
    audit = audit_judge_reads(capture, binding)
    finalize_judge_context(audit, {}, ("constructed criterion",))
    assert audit["opened"] == [] and audit["channelProven"] is False
    assert not audit["unknowns"]
    (row,) = binding.rows
    (fact,) = audit["readerLimitRefusals"]
    assert fact["path"] == "must-read.txt"
    assert fact["sizeBytes"] == row["sizeBytes"]
    assert fact["characters"] == row["characters"]
    assert audit["filesAloneExceedAppliedLimit"] == [
        {
            "path": row["path"],
            "sizeBytes": row["sizeBytes"],
            "characters": row["characters"],
            "opened": False,
        }
    ]


def test_constructed_silence_and_self_report_are_not_positive_read_evidence(tmp_path: Path) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = NativeCapture("codex", "session", (), (), (), ())
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == [] and audit["channelProven"] is False
    assert audit["unobservableCriteria"] == 1 and audit["observedEligibleCriteria"] == 0
    (missing,) = audit["missingReferencedEvidence"]
    assert (
        missing["cause"] is None
        and missing["reason"] == "the kept log is not known to be complete on this harness"
    )
    assert missing["paths"] == ["must-read.txt"]
    self_report = NativeEvent(
        "final_report",
        "session",
        0,
        {"text": "I opened must-read.txt and read it completely."},
        "/constructed/native.jsonl",
        "a" * 64,
    )
    claimed = audit_judge_reads(replace(capture, events=(self_report,)), binding)
    assert claimed == audit


@pytest.mark.parametrize("cwd", ["file://foreign-host/evidence", "https://example.com/evidence"])
def test_constructed_foreign_cwd_cannot_prove_local_file_open(tmp_path: Path, cwd: str) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = _constructed_capture(binding, ["cat", "--", "must-read.txt"], cwd=cwd)
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == [] and audit["channelProven"] is False
    assert audit["coverageDiagnostics"]
    assert audit["unknowns"] == []
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["referenceStates"][0]["state"] == "not seen in kept log"
    assert (
        audit["referenceStates"][0]["reason"]
        == "the kept log is not known to be complete on this harness"
    )


def test_constructed_symlink_escape_is_outside_evidence(tmp_path: Path) -> None:
    binding = _binding(tmp_path / "evidence")
    outside = tmp_path / "outside-canary.txt"
    outside.write_text("harmless outside control")
    (binding.root / "link.txt").symlink_to(outside)
    capture = _constructed_capture(binding, ["cat", "--", "link.txt"])
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == []
    assert audit["outsideAttempts"][0]["path"] == str(outside)
    assert audit["outsideAttempts"][0]["classification"] == "judge-read-outside-evidence"
    assert not any("judge-read-outside-evidence" in cause for cause in audit["unknowns"])


def _stub_invocation(root: Path) -> NativeInvocation:
    project, home, settings = root / "project", root / "home", root / "settings.json"
    project.mkdir()
    home.mkdir()
    settings.write_text("{}\n")
    # mkdir's default 0777 is intentional: mkdtemp's fixed 0700 would make
    # the private-umask assertion pass even when the spawn grant does nothing.
    script = (
        "import json, pathlib, stat, uuid; "
        "root=pathlib.Path.cwd()/('native-'+uuid.uuid4().hex); root.mkdir(); "
        "child=root/'random-child'; child.mkdir(); "
        "print(json.dumps({'paths':[str(root),str(child)],"
        "'modes':[stat.S_IMODE(root.stat().st_mode),stat.S_IMODE(child.stat().st_mode)]}))"
    )
    return NativeInvocation(
        argv=(str(Path(sys.executable).resolve()), "-c", script),
        cwd=project,
        environment={},
        run_root=root,
        binding=NativeBinding(
            project, home, settings, "constructed-native-stub-only", (), CODEX_JUDGE
        ),
        timeout_seconds=HANG_GUARD_SECONDS,
    )


def test_judge_spawn_inherits_private_umask_and_restores_parent(tmp_path: Path) -> None:
    invocation = _stub_invocation(tmp_path)
    prior = os.umask(0o022)
    try:
        execution = judges.execute_judge_native(invocation, prompt="constructed no-model stub")
        after = os.umask(0o022)
        assert after == 0o022
    finally:
        os.umask(prior)
    assert execution.returncode == 0 and execution.unknowns == ()
    result = json.loads(execution.stdout_path.read_text())
    assert result["modes"] == [0o700, 0o700]
    assert all(Path(path).is_relative_to(invocation.cwd) for path in result["paths"])
    assert all(Path(path).stat().st_mode & 0o777 == 0o700 for path in result["paths"])


def test_judge_spawn_restores_umask_when_native_executor_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation = _stub_invocation(tmp_path)
    observed = []

    def fail_execute(*args: object, **kwargs: object) -> None:
        observed.append(os.umask(0o077))
        raise RuntimeError("constructed execution failure")

    monkeypatch.setattr(judges, "execute_native", fail_execute)
    prior = os.umask(0o022)
    try:
        with pytest.raises(RuntimeError, match="constructed execution failure"):
            judges.execute_judge_native(invocation, prompt="constructed no-model stub")
        after = os.umask(0o022)
        assert after == 0o022
    finally:
        os.umask(prior)
    assert observed == [0o077]


def _retained_claude_metrics_capture() -> tuple[dict, NativeCapture]:
    path = Path(__file__).parent / "fixtures/judge-claude-usage-real.json"
    fixture = json.loads(path.read_text())
    assert sha256_file(path) == "e356ae5a0d6c96aafe56bb2d73f5de3bde0af79d4497769f096be37a95186329"
    assert fixture["sourceSha256"] == (
        "984a40d052b9b333fbfc17e6d9363e924bf47696a5451c3a70fcd7e5d9b9a02b"
    )
    packed = gzip.decompress(base64.b64decode(fixture["projectedRecordsGzipBase64"]))
    records = json.loads(packed)
    assert json_digest(records) == fixture["projectedRecordsSha256"]
    assert len(records) == fixture["nativeRecordCount"] == 61
    for index, (record, proof) in enumerate(zip(records, fixture["records"], strict=True)):
        assert proof["nativeRawOrdinal"] == index and proof["sourceLine"] == index + 1
        assert json_digest(record) == proof["projectionSha256"]
        assert len(proof["originalRecordSha256"]) == 64
    assert records[60] == fixture["finalResultRecord"]
    assert json_digest(records[60]) == fixture["records"][60]["originalRecordSha256"]
    source = SourceFile(fixture["sourcePath"], fixture["sourceSha256"])
    capture = NativeCapture("claude", records[60]["session_id"], (), (source,), tuple(records), ())
    return fixture, capture


def test_retained_claude_metrics_use_authoritative_model_usage_without_adding_lower_usage() -> None:
    fixture, capture = _retained_claude_metrics_capture()
    original = copy.deepcopy(capture.to_dict())
    prompt = "é: compact inline control\n"
    metrics = native_trials.judge_run_metrics(capture, prompt)
    final = fixture["finalResultRecord"]
    assert metrics["inlinePackageCharacters"] == len(prompt)
    assert metrics["inlinePackageUtf8Bytes"] == len(prompt.encode())
    assert metrics["turns"] == 19 and metrics["observedAssistantMessageGroups"] == 6
    assert metrics["tokenComponents"] == {
        "inputTokens": 199114,
        "cacheReadInputTokens": 3192194,
        "cacheCreationInputTokens": 1002456,
        "outputTokens": 10269,
        "totalTokens": 4404033,
    }
    assert metrics["modelUsage"] == final["modelUsage"]
    assert metrics["finalResultUsage"] == final["usage"]
    assert metrics["finalResultUsageTotal"] == 3404495
    assert metrics["tokenComponents"]["totalTokens"] != metrics["finalResultUsageTotal"]
    assert capture.to_dict() == original


def test_retained_tool_result_bytes_deduplicate_ids_without_guessing_native_turns() -> None:
    _, capture = _retained_claude_metrics_capture()
    # Native stdout has duplicate blocks/message IDs. Repeated observations
    # appended here are an explicit constructed deduplication control.
    repeated = tuple(row for row in capture.raw_records if row.get("type") in {"assistant", "user"})
    duplicated = replace(capture, raw_records=(*capture.raw_records, *repeated))
    original_metrics = native_trials.judge_run_metrics(capture, "inline")
    metrics = native_trials.judge_run_metrics(duplicated, "inline")
    assert metrics["toolResults"] == original_metrics["toolResults"]
    assert metrics["uniqueToolResults"] == 17
    assert metrics["toolResultUtf8Bytes"] == 502237
    assert metrics["serializedToolResultUtf8Bytes"] == 527477
    assert metrics["observedAssistantMessageGroups"] == 6 and metrics["turns"] == 19
    parent = {}
    groups = {}
    expected = []
    for index, row in enumerate(capture.raw_records):
        message = row.get("message", {})
        if row.get("type") == "assistant":
            identity = message["id"]
            groups.setdefault(identity, len(groups) + 1)
            for block in message["content"]:
                if block.get("type") == "tool_use":
                    parent[block["id"]] = (identity, row["request_id"], groups[identity])
        elif row.get("type") == "user":
            for block in message["content"]:
                if block.get("type") != "tool_result":
                    continue
                identity = block["tool_use_id"]
                message_id, request_id, group = parent[identity]
                expected.append(
                    {
                        "toolUseId": identity,
                        "nativeRawOrdinal": index,
                        "parentMessageId": message_id,
                        "requestId": request_id,
                        "messageGroupOrdinal": group,
                        "contentUtf8Bytes": len(block["content"].encode()),
                        "serializedContentUtf8Bytes": len(json.dumps(block["content"]).encode()),
                        "turnIndex": None,
                        "remainingTurns": None,
                        "byteTurnExposure": None,
                        "turnIndexUnknown": "native-turn-index-unavailable",
                    }
                )
    assert metrics["toolResults"] == expected
    assert sum(row["contentUtf8Bytes"] for row in expected) == metrics["toolResultUtf8Bytes"]


@pytest.mark.parametrize("fault", ["missing-model-usage", "missing-component", "invalid-component"])
def test_constructed_missing_native_usage_is_not_filled_from_lower_usage(fault: str) -> None:
    _, capture = _retained_claude_metrics_capture()
    records = copy.deepcopy(list(capture.raw_records))
    final = records[60]
    if fault == "missing-model-usage":
        final.pop("modelUsage")
    elif fault == "missing-component":
        final["modelUsage"]["claude-opus-5-5"].pop("outputTokens")
    else:
        final["modelUsage"]["claude-opus-5-5"]["outputTokens"] = -1
    altered = replace(capture, raw_records=tuple(records))
    metrics = native_trials.judge_run_metrics(altered, "inline")
    assert metrics["tokenComponents"]["totalTokens"] is None
    assert metrics["finalResultUsageTotal"] == 3404495
    assert metrics["finalResultUsage"] == final["usage"]


def test_constructed_missing_native_turn_count_is_not_message_group_count() -> None:
    _, capture = _retained_claude_metrics_capture()
    records = copy.deepcopy(list(capture.raw_records))
    records[60].pop("num_turns")
    metrics = native_trials.judge_run_metrics(
        replace(capture, raw_records=tuple(records)), "inline"
    )
    assert metrics["turns"] is None and metrics["observedAssistantMessageGroups"] == 6
    assert all(row["turnIndex"] is None for row in metrics["toolResults"])


def _answered_controls() -> tuple[dict, dict, judges.JudgeRequest]:
    request = judges.JudgeRequest(
        ("confident but unread", "unknown but searched", "fail whole read", "unreferenced"), (), ()
    )
    verdict = {
        "criteria": [
            {
                "criterion": criterion,
                "outcome": outcome,
                "evidence": f" verbatim evidence {index}\n",
            }
            for index, (criterion, outcome) in enumerate(
                zip(request.rubric, ("pass", "unknown", "fail", "pass"), strict=True)
            )
        ]
    }
    audit = {
        "channelProven": True,
        "readObservationLimit": {
            "countsComparable": False,
            "source": "constructed noncomparable observation counts",
        },
        "referenceStates": [
            {
                "path": "a.txt",
                "state": "not seen in kept log",
                "reason": "the kept log is not known to be complete on this harness",
                "criteria": [0],
            },
            {
                "path": "shared.txt",
                "state": "not seen in kept log",
                "reason": "the kept log is not known to be complete on this harness",
                "criteria": [0, 0],
            },
            {"path": "searched.txt", "state": "searched", "criteria": [1]},
            {"path": "whole.txt", "state": "opened", "criteria": [2]},
        ],
    }
    return audit, verdict, request


def test_answered_read_metrics_join_actual_answers_without_mutating_verdict() -> None:
    audit, verdict, request = _answered_controls()
    original = copy.deepcopy((audit, verdict))
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaGraded"] == 4 and metrics["referencedCriteriaGraded"] == 3
    assert metrics["criteriaCarryingCause"] == 0 and metrics["unobservableCriteria"] == 3
    assert metrics["paths"] == []
    _assert_rate_unmeasurable(metrics)
    assert len(metrics["rows"]) == 4
    for index, row in enumerate(metrics["rows"]):
        answer = verdict["criteria"][index]
        assert row["criterionIndex"] == index
        assert (row["criterion"], row["outcome"], row["evidence"]) == (
            answer["criterion"],
            answer["outcome"],
            answer["evidence"],
        )
    unread, searched, whole, unreferenced = metrics["rows"]
    assert (
        set(unread["referencedPaths"])
        == set(unread["notSeenInKeptLogPaths"])
        == {"a.txt", "shared.txt"}
    )
    assert unread["cause"] is None
    assert "notOpenedPaths" not in unread
    assert searched["searchedPaths"] == ["searched.txt"] and searched["cause"] is None
    assert whole["wholeReadPaths"] == ["whole.txt"] and whole["cause"] is None
    assert unreferenced["referencedPaths"] == [] and unreferenced["cause"] is None
    assert (audit, verdict) == original


@pytest.mark.parametrize("outcome", ["pass", "fail", "unknown"])
def test_answered_log_absence_preserves_judge_outcome_without_a_charge(outcome: str) -> None:
    audit, verdict, request = _answered_controls()
    verdict["criteria"] = [{**verdict["criteria"][0], "outcome": outcome}]
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaGraded"] == metrics["referencedCriteriaGraded"] == 1
    assert metrics["criteriaCarryingCause"] == 0
    assert metrics["rows"][0]["outcome"] == outcome
    assert metrics["rows"][0]["cause"] is None
    _assert_rate_unmeasurable(metrics)


def test_unanswered_later_criteria_are_excluded_from_answer_counts() -> None:
    audit, verdict, request = _answered_controls()
    verdict["criteria"] = verdict["criteria"][:2]
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaGraded"] == metrics["referencedCriteriaGraded"] == 2
    assert metrics["criteriaCarryingCause"] == 0
    assert [row["criterionIndex"] for row in metrics["rows"]] == [0, 1]
    assert "whole.txt" not in metrics["paths"]


def test_unproven_read_channel_reports_unobservable_answers_without_cause() -> None:
    audit, verdict, request = _answered_controls()
    audit["channelProven"] = False
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaGraded"] == 4 and metrics["referencedCriteriaGraded"] == 3
    assert metrics["criteriaCarryingCause"] == 0 and metrics["unobservableCriteria"] == 3
    assert metrics["paths"] == []
    assert all(row["cause"] is None for row in metrics["rows"])


@pytest.mark.parametrize("fault", ["criterion-order", "outcome", "evidence"])
def test_invalid_answer_rows_are_metadata_and_do_not_enter_answer_counts(fault: str) -> None:
    audit, verdict, request = _answered_controls()
    row = copy.deepcopy(verdict["criteria"][0])
    row.update(
        {
            "criterion-order": {"criterion": request.rubric[1]},
            "outcome": {"outcome": "not-a-verdict"},
            "evidence": {"evidence": None},
        }[fault]
    )
    verdict["criteria"] = [row]
    original = copy.deepcopy(verdict)
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaGraded"] == metrics["referencedCriteriaGraded"] == 0
    assert metrics["criteriaCarryingCause"] == 0 and metrics["rows"] == []
    assert metrics["invalidRows"]
    assert verdict == original


def test_retained_six_of_nineteen_messages_cannot_charge_unseen_evidence(tmp_path: Path) -> None:
    _, capture = _retained_claude_metrics_capture()
    binding = _binding(tmp_path / "evidence")
    binding = cast(
        JudgeReadBinding,
        SimpleNamespace(
            **{
                **vars(binding),
                "judge_harness": "claude",
                "rows": ({**binding.rows[0], "criteria": [0]},),
            }
        ),
    )
    audit = audit_judge_reads(capture, binding)
    coverage = audit["readObservationLimit"]
    assert "complete" not in coverage
    assert "captureCoverage" not in audit
    assert coverage["keptMessages"] == 6 and coverage["numTurns"] == 19
    assert coverage["source"] and coverage["countsComparable"] is False
    assert audit["referenceStates"] == [
        {
            "path": "must-read.txt",
            "criteria": [0],
            "state": "not seen in kept log",
            "reason": "the kept log is not known to be complete on this harness",
        }
    ]
    request = judges.JudgeRequest(("unseen file",), (), ())
    verdict = {"criteria": [{"criterion": "unseen file", "outcome": "pass", "evidence": "claim"}]}
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaGraded"] == metrics["referencedCriteriaGraded"] == 1
    assert metrics["criteriaCarryingCause"] == 0 and metrics["unobservableCriteria"] == 1
    assert metrics["paths"] == [] and metrics["rows"][0]["cause"] is None
    assert metrics["rows"][0]["notSeenInKeptLogPaths"] == ["must-read.txt"]


def test_constructed_changed_evidence_stays_unknown_coverage_beside_observations() -> None:
    audit, verdict, request = _answered_controls()
    for row in audit["referenceStates"]:
        if row["state"] == "not seen in kept log":
            row["state"] = "unknown_coverage"
            row["reason"] = "constructed changed evidence bytes"
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaCarryingCause"] == 0 and metrics["paths"] == []
    # The absence rate is not measurable. Keep the separate changed-byte,
    # searched and whole-read observations without turning any into a charge.
    assert metrics["unobservableCriteria"] == 3
    assert set(metrics["rows"][0]["unknownCoveragePaths"]) == {"a.txt", "shared.txt"}
    assert metrics["rows"][1]["searchedPaths"] == ["searched.txt"]
    assert metrics["rows"][2]["wholeReadPaths"] == ["whole.txt"]
    assert metrics["rows"][0]["cause"] is None


def test_observed_read_channel_does_not_make_absence_measurable() -> None:
    audit, verdict, request = _answered_controls()
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaCarryingCause"] == 0 and metrics["paths"] == []
    assert all(row["cause"] is None for row in metrics["rows"])


@pytest.mark.parametrize("surface", ["contract", "argv"])
def test_codex_uses_ordinary_workspace_grant_without_allowlists(
    tmp_path: Path, surface: str
) -> None:
    """Inspect only pure contract/argv metadata; no native CLI is executed."""
    if surface == "contract":
        contract = judges.judge_access_contract("codex")
        assert contract["toolGrant"] == (
            "ordinary agent, workspace = evidence folder, shell included, no AR server"
        )
        assert contract["sandbox"] == "workspace-write"
        assert contract["cwdRule"] == "evidence-root" and contract["mcpServers"] == []
        assert "allowedTools" not in contract and "permissionProfile" not in contract
    else:
        argv = judges.codex_judge_argv(Path(sys.executable), tmp_path)
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert "read-only" not in argv and "--allowedTools" not in argv
        overrides = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
        assert not any(
            value.startswith(("default_permissions=", "permissions.")) for value in overrides
        )
        assert "mcp_servers={}" in overrides and "features.shell_tool=true" in overrides


def test_constructed_successful_outside_read_is_an_observation_without_grade_unknown(
    tmp_path: Path,
) -> None:
    binding = _binding(tmp_path / "evidence")
    outside = tmp_path / "outside-canary.txt"
    outside.write_text("harmless constructed outside control")
    capture = _constructed_capture(
        binding, ["cat", "--", str(outside)], aggregated_output=outside.read_text()
    )
    original = copy.deepcopy(capture.to_dict())
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == []
    (attempt,) = audit["outsideAttempts"]
    assert attempt["path"] == str(outside)
    assert attempt["callId"] == capture.events[0].payload["call_id"]
    assert attempt["classification"] == "judge-read-outside-evidence"
    assert audit["unknowns"] == []
    assert capture.to_dict() == original


@pytest.mark.parametrize("wrapper", ["direct", "shell"])
@pytest.mark.parametrize("tool", ["cat", "sed", "head", "tail", "rg", "grep"])
def test_constructed_plain_shell_paths_have_precise_read_coverage(
    tmp_path: Path, wrapper: str, tool: str
) -> None:
    binding = _binding(tmp_path / "evidence")
    target = binding.root / "must-read.txt"
    args = {
        "cat": ["cat", "--", str(target)],
        "sed": ["sed", "-n", "1,2p", str(target)],
        "head": ["head", "-n", "2", str(target)],
        "tail": ["tail", "-n", "2", str(target)],
        "rg": ["rg", "-n", "control", str(target)],
        "grep": ["grep", "-n", "control", str(target)],
    }[tool]
    # All targets are literal paths. The wrapper is constructed, not a claim
    # that the retained native judge actually ran these particular commands.
    command = args if wrapper == "direct" else ["/bin/bash", "-lc", " ".join(args)]
    output = "1:control\n" if tool in {"rg", "grep"} else target.read_text()
    capture = _constructed_capture(binding, command, aggregated_output=output)
    original = copy.deepcopy(capture.to_dict())
    audit = audit_judge_reads(capture, binding)
    assert audit["referenceStates"][0]["state"] == ("opened" if tool == "cat" else "searched")
    assert audit["missingReferencedEvidence"] == []
    if tool in {"cat", "sed", "head", "tail"}:
        (opened,) = audit["opened"]
        assert opened["path"] == target.name
        assert opened["wholeRead"] is (tool == "cat")
        assert opened["callId"] == capture.events[0].payload["call_id"]
    else:
        assert audit["opened"] == []
        assert any(row.get("path") == target.name for row in audit["searches"])
    assert audit["criteriaCarryingCause"] == 0
    assert capture.to_dict() == original


@pytest.mark.parametrize("shell", ["cat *.md", "cat $F", "cat must-read.txt && cat other.txt"])
def test_constructed_ambiguous_shell_is_not_seen_in_kept_log_never_unread(
    tmp_path: Path, shell: str
) -> None:
    binding = _binding(tmp_path / "evidence")
    capture = _constructed_capture(binding, ["/bin/bash", "-lc", shell])
    audit = audit_judge_reads(capture, binding)
    assert audit["opened"] == []
    assert "captureCoverage" not in audit
    assert "complete" not in audit["readObservationLimit"]
    assert audit["referenceStates"][0]["state"] == "not seen in kept log"
    assert (
        audit["referenceStates"][0]["reason"]
        == "the kept log is not known to be complete on this harness"
    )
    assert audit["criteriaCarryingCause"] == 0
    assert all(row["cause"] is None for row in audit["missingReferencedEvidence"])


def test_constructed_judge_folder_integrity_binds_immutable_before_manifest(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    root.mkdir()
    originals = {"changed.txt": "before", "removed.txt": "removed", "unchanged.txt": "same"}
    before = {}
    for name, content in originals.items():
        path = root / name
        path.write_text(content)
        before[name] = {
            "sha256": sha256_file(path),
            "sizeBytes": path.stat().st_size,
            "criteria": [0],
        }
    saved = copy.deepcopy(before)
    (root / "changed.txt").write_text("after bytes")
    (root / "removed.txt").unlink()
    (root / "added.txt").write_text("judge-created file")
    diff = native_trials.judge_folder_integrity(root, before)

    def meta(name: str) -> dict:
        return {"sha256": sha256_file(root / name), "sizeBytes": (root / name).stat().st_size}

    assert diff["changed"] == [
        {
            "path": "changed.txt",
            "before": {key: saved["changed.txt"][key] for key in ("sha256", "sizeBytes")},
            "after": meta("changed.txt"),
            "criteria": [0],
        }
    ]
    assert diff["removed"] == [
        {
            "path": "removed.txt",
            "before": {key: saved["removed.txt"][key] for key in ("sha256", "sizeBytes")},
            "criteria": [0],
        }
    ]
    assert diff["added"] == [{"path": "added.txt", "after": meta("added.txt"), "criteria": []}]
    assert diff["unchangedCount"] == 1
    assert before == saved


def test_constructed_answer_on_judge_added_file_is_unknown_coverage_without_grade_mutation() -> (
    None
):
    audit, verdict, request = _answered_controls()
    audit["referenceStates"] = []
    audit["folderIntegrity"] = {
        "changed": [],
        "removed": [],
        "added": [
            {"path": "generated.txt", "after": {"sha256": "a" * 64, "sizeBytes": 7}, "criteria": []}
        ],
        "unchangedCount": 0,
    }
    verdict["criteria"] = [
        {**verdict["criteria"][0], "evidence": "generated.txt: judge-produced assertion"}
    ]
    original = copy.deepcopy(verdict)
    metrics = judges.answered_read_metrics(audit, verdict, request)
    (row,) = metrics["rows"]
    assert row["unknownCoveragePaths"] == ["generated.txt"]
    assert row["wholeReadPaths"] == [] and row["cause"] is None
    assert row["evidence"] == original["criteria"][0]["evidence"]
    assert metrics["criteriaGraded"] == 1 and metrics["criteriaCarryingCause"] == 0
    assert verdict == original


@pytest.mark.parametrize(
    "native_turns",
    [19, 6],
    ids=["retained-real-6-messages-19-turns", "counterfactual-equal-counts-are-not-completeness"],
)
def test_native_count_observations_never_prove_completeness_or_judge_zero(
    tmp_path: Path, native_turns: int
) -> None:
    _, retained = _retained_claude_metrics_capture()
    # The 19-turn branch is the unchanged retained native projection. Only the
    # equal-count branch is counterfactual, proving equality is not authority.
    records = copy.deepcopy(retained.raw_records)
    if native_turns != 19:
        for record in records:
            if record.get("type") == "result":
                record["num_turns"] = native_turns
    capture = replace(retained, raw_records=records)
    original = copy.deepcopy(capture.to_dict())
    binding = _binding(tmp_path / "evidence")
    binding = cast(
        JudgeReadBinding,
        SimpleNamespace(
            **{
                **vars(binding),
                "judge_harness": "claude",
                "rows": ({**binding.rows[0], "criteria": [0]},),
            }
        ),
    )
    audit = audit_judge_reads(capture, binding)
    coverage = audit["readObservationLimit"]
    assert "complete" not in coverage
    assert "captureCoverage" not in audit
    assert coverage["keptMessages"] == 6 and coverage["numTurns"] == native_turns
    assert coverage["countsComparable"] is False
    assert coverage["source"] == "native kept-log observations; no completeness inference"
    assert audit["referenceStates"][0]["state"] == "not seen in kept log"
    assert (
        audit["referenceStates"][0]["reason"]
        == "the kept log is not known to be complete on this harness"
    )
    _assert_rate_unmeasurable(audit)
    assert audit["criteriaCarryingCause"] == 0
    assert audit["causeCanFire"] is False and audit["causeAvailability"] == "instrument-unavailable"
    request = judges.JudgeRequest(("unseen file",), (), ())
    verdict = {"criteria": [{"criterion": "unseen file", "outcome": "pass", "evidence": "claim"}]}
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaCarryingCause"] == 0
    assert (
        metrics["causeCanFire"] is False
        and metrics["causeAvailability"] == "instrument-unavailable"
    )
    assert metrics["observedEligibleCriteria"] == 0 and metrics["unobservableCriteria"] == 1
    (row,) = metrics["rows"]
    assert row["cause"] is None and row["causeCanFire"] is False
    assert row["causeAvailability"] == "instrument-unavailable"
    assert capture.to_dict() == original


def test_legacy_synthetic_complete_input_cannot_activate_any_absence_charge() -> None:
    audit, verdict, request = _answered_controls()
    # Negative input control, not a compatibility contract: removed metadata
    # must not resurrect the deleted charge mechanism, even when labelled.
    audit["captureCoverage"] = {"complete": True, "synthetic": True}
    for row in audit["referenceStates"]:
        if row["state"] == "not seen in kept log":
            row["state"] = "not_opened"
    metrics = judges.answered_read_metrics(audit, verdict, request)
    assert metrics["criteriaCarryingCause"] == 0
    assert "captureCoverage" not in metrics
    assert all("notOpenedPaths" not in row for row in metrics["rows"])
    assert all(row["cause"] is None for row in metrics["rows"])
    _assert_rate_unmeasurable(metrics)
