"""Real Codex verdicts for Hermes runs parsed successfully and declared uncertainty."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.judges import JudgeRequest
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.graders import cases
from agent_evals.graders.cases import RubricSubmission, parse_native_verdict, resolve_rubric
from agent_evals.graders.models import Grade
from agent_evals.judge_outcomes import unknown_verdict_reason
from agents_remember.errors import AuthorityError

FIXTURE = (
    Path(__file__).resolve().parents[2] / "mcp/tests/fixtures/codex-hermes-unknown-verdicts.json"
)
ROWS = json.loads(FIXTURE.read_bytes())["rows"]


def test_retained_projection_has_fixed_identity_and_five_actual_verdicts() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == (
        "5001180be3072f30bc4cdb9804a1cb6dae07181cd1674f32e9aba4ffd3661a14"
    )
    assert len(ROWS) == 5
    assert sum(len(row["unknownCriteria"]) for row in ROWS) == 6


@pytest.mark.parametrize("row", ROWS, ids=[row["attempt"] for row in ROWS])
def test_real_raw_json_unknown_verdict_is_accepted_without_becoming_a_pass(row: dict) -> None:
    # The projection retains both exact native payloads and their normalized events.
    assert [item["payload"] for item in row["nativeRecords"]] == [
        item["payload"] for item in row["events"]
    ]
    assert row["events"][0]["payload"]["model"] == "gpt-6.1-sol"
    assert all(
        len(row[key]) == 64 for key in ("captureSha256", "nativeStdoutSha256", "requestSha256")
    )
    capture = NativeCapture(
        harness="codex",
        session_id=row["sessionId"],
        events=tuple(NativeEvent(**item) for item in row["events"]),
        source_files=tuple(SourceFile(**item) for item in row["sourceFiles"]),
        raw_records=tuple(row["nativeRecords"]),
        unknowns=(),
    )
    verdict, path = parse_native_verdict(capture)
    assert path == "raw_json"
    assert [item["criterion"] for item in verdict["criteria"]] == row["rubric"]
    assert all(
        isinstance(item["evidence"], str) and item["evidence"].strip()
        for item in verdict["criteria"]
    )
    assert [
        item["criterion"] for item in verdict["criteria"] if item["outcome"] == "unknown"
    ] == row["unknownCriteria"]
    submission = RubricSubmission(
        request=JudgeRequest(tuple(row["rubric"]), (), ()),
        actor_model="opencode-go:deepseek-v4.1-flash",
        judge_capture=capture,
    )
    expected = (
        "wrong"
        if any(item["outcome"] == "fail" for item in verdict["criteria"])
        else "missing_capture"
    )
    assert resolve_rubric(Grade(row["case"], "needs_rubric"), submission) == expected
    reason = unknown_verdict_reason(capture)
    assert reason.startswith("judge explicitly reported unknown criteria:")
    assert all(criterion in reason for criterion in row["unknownCriteria"])


CLAUDE_FIXTURE = Path(__file__).parent / "fixtures/claude-prose-verdict-retained.json"
CLAUDE = json.loads(CLAUDE_FIXTURE.read_bytes())


def _claude_final_capture(text: str | None = None) -> NativeCapture:
    event = copy.deepcopy(CLAUDE["event"])
    if text is not None:
        event["payload"]["content"] = [{"type": "text", "text": text}]
    native = copy.deepcopy(CLAUDE["nativeRow"])
    native["message"] = event["payload"]
    return NativeCapture(
        "claude",
        CLAUDE["sessionId"],
        (NativeEvent(**event),),
        (SourceFile(CLAUDE["sourcePath"], CLAUDE["sourceSha256"]),),
        (native,),
        (),
    )


def _claude_text() -> str:
    return "".join(
        block["text"] for block in CLAUDE["event"]["payload"]["content"] if block["type"] == "text"
    )


def test_retained_claude_prose_then_json_fence_is_decoded_without_schema_change() -> None:
    assert (
        hashlib.sha256(CLAUDE_FIXTURE.read_bytes()).hexdigest()
        == "376327ed1a76bdce82d9797a0acf2b809e7ebb259a91b62057c60162ec629707"
    )
    assert (
        CLAUDE["sourceSha256"] == "dd874c705e879066aec614ee6bea3b570c35b1f828a576984eabbe32352ef9e2"
    )
    assert CLAUDE["nativeRow"]["message"] == CLAUDE["event"]["payload"]
    text = _claude_text()
    assert len(text.encode()) == CLAUDE["messageTextUtf8Bytes"] == 7979
    assert hashlib.sha256(text.encode()).hexdigest() == CLAUDE["messageTextSha256"]
    captured = _claude_final_capture()
    verdict, path = parse_native_verdict(captured)
    assert path == "final_message_json_fence"
    assert [row["criterion"] for row in verdict["criteria"]] == CLAUDE["rubric"]
    submission = RubricSubmission(
        JudgeRequest(tuple(CLAUDE["rubric"]), (), ()), "gpt-6.1-sol", captured
    )
    assert resolve_rubric(Grade("CE05", "needs_rubric"), submission) == "right"


def test_two_valid_final_message_fences_are_ambiguous_not_selected() -> None:
    text = _claude_text()
    fence = text[text.index("```json") :]
    capture = _claude_final_capture(text + "\n" + fence)
    assert parse_native_verdict(capture) == ({}, "invalid")
    provenance = cases.verdict_parse_provenance(capture)
    assert provenance["status"] == "ambiguous"
    assert provenance["validFencedCandidateCount"] == 2
    assert "multiple valid" in provenance["reason"]
    assert provenance["blockIndex"] is None
    assert "multiple valid" in unknown_verdict_reason(capture)


def test_fence_provenance_offsets_are_utf8_bytes_and_preserve_all_prose() -> None:
    text = "é🙂\n" + _claude_text()
    capture = _claude_final_capture(text)
    verdict, _ = parse_native_verdict(capture)
    provenance = cases.verdict_parse_provenance(capture)
    raw = text.encode()
    assert provenance["status"] == "decoded"
    assert provenance["fullMessageText"] == text
    assert provenance["messageSequence"] == CLAUDE["event"]["sequence"]
    assert provenance["messageTextSha256"] == hashlib.sha256(raw).hexdigest()
    assert provenance["blockIndex"] == 0
    assert provenance["fenceByteOffset"] == raw.index(b"```json")
    assert provenance["fenceByteOffset"] > text.index("```json")
    start, end = provenance["payloadByteOffset"], provenance["payloadByteEndOffset"]
    assert json.loads(raw[start:end]) == verdict
    assert raw[provenance["fenceByteOffset"] : provenance["fenceByteEndOffset"]].endswith(b"```")


@pytest.mark.parametrize("change", ["missing-evidence", "invalid-outcome", "not-object"])
def test_fenced_invalid_schema_is_not_accepted(change: str) -> None:
    text = _claude_text()
    value = json.loads(text[text.index("```json\n") + 8 : text.rindex("\n```")])
    if change == "missing-evidence":
        del value["criteria"][0]["evidence"]
    elif change == "invalid-outcome":
        value["criteria"][0]["outcome"] = "right"
    else:
        value = [value]
    captured = _claude_final_capture("Account remains.\n```json\n" + json.dumps(value) + "\n```")
    assert parse_native_verdict(captured) == ({}, "invalid")
    submission = RubricSubmission(
        JudgeRequest(tuple(CLAUDE["rubric"]), (), ()), "gpt-6.1-sol", captured
    )
    assert resolve_rubric(Grade("CE05", "needs_rubric"), submission) == "missing_capture"


def test_only_final_assistant_message_can_supply_a_verdict() -> None:
    capture = _claude_final_capture()
    final = NativeEvent(
        "message", capture.session_id, 900, {"role": "assistant", "content": []}, "native", "a" * 64
    )
    assert parse_native_verdict(replace(capture, events=(*capture.events, final))) == (
        {},
        "invalid",
    )


def test_last_complete_fence_is_used_and_earlier_invalid_block_is_not_a_candidate() -> None:
    text = "```json\n{}\n```\n" + _claude_text() + "\n```json\nunfinished"
    captured = _claude_final_capture(text)
    verdict, path = parse_native_verdict(captured)
    assert path == "final_message_json_fence"
    assert [row["criterion"] for row in verdict["criteria"]] == CLAUDE["rubric"]
    assert cases.verdict_parse_provenance(captured)["blockIndex"] == 1


def test_later_invalid_complete_fence_cannot_fall_back_to_an_earlier_valid_one() -> None:
    captured = _claude_final_capture(_claude_text() + "\n```json\n{}\n```")
    assert parse_native_verdict(captured) == ({}, "invalid")


def test_prose_fence_does_not_relax_exact_criterion_order_or_judge_model() -> None:
    request = JudgeRequest(tuple(CLAUDE["rubric"]), (), ())
    text = _claude_text()
    value = json.loads(text[text.index("```json\n") + 8 : text.rindex("\n```")])
    value["criteria"].reverse()
    captured = _claude_final_capture("Prose\n```json\n" + json.dumps(value) + "\n```")
    with pytest.raises(AuthorityError, match="omitted, reordered or invented"):
        resolve_rubric(
            Grade("CE05", "needs_rubric"), RubricSubmission(request, "gpt-6.1-sol", captured)
        )
    captured = _claude_final_capture()
    event = replace(
        captured.events[0], payload={**captured.events[0].payload, "model": "unadmitted-model"}
    )
    captured = replace(captured, events=(event,))
    assert (
        resolve_rubric(
            Grade("CE05", "needs_rubric"), RubricSubmission(request, "gpt-6.1-sol", captured)
        )
        == "missing_capture"
    )
