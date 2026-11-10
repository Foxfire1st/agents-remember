"""R62 item30: surviving c02 field explanations match actual result projections."""

import re
from pathlib import Path

import pytest
from agents_remember.application.knowledge_gate.gate import (
    ITEM_OPEN,
    RUN_INCOMPLETE,
    VALIDATOR,
    GateFinding,
    GateResult,
)
from agents_remember.application.memory_quality.answer import quality_result_answer
from agents_remember.application.memory_quality.knowledge_inputs import knowledge_briefs

ROOT = Path.cwd()
DETAIL = ROOT / "skills/l-01-agent-lifecycles/reference/curator/worklist.md"


def documented_fields() -> tuple[set[str], set[str]]:
    text = DETAIL.read_text()
    before, after = text.split("`knowledgeGate` is the compact gate judgment.")
    pattern = r"^\| `([^`]+)` \|"
    return set(re.findall(pattern, before, re.M)), set(re.findall(pattern, after, re.M))


def proof_cases() -> list[dict[str, object]]:
    cases = []
    for state in ("complete", "incomplete"):
        worklist = {
            "state": state,
            "digest": "measured-worklist-identity",
            "items": [
                {"kind": "invariant", "satisfiedBy": "current-row"},
                {"kind": "invariant"},
                {"kind": "family"},
            ],
            "incomplete": [] if state == "complete" else [{"input": "unreadable-code"}],
        }
        for refused in (False, True):
            findings = (
                (
                    GateFinding(ITEM_OPEN, "source.py", "open item", item="item-1"),
                    GateFinding(RUN_INCOMPLETE, "source.py", "unreadable input"),
                    GateFinding(f"{VALIDATOR}-unreadable", "record.json", "validator finding"),
                )
                if refused
                else ()
            )
            gate = GateResult(
                owner="leaf-owner" if refused else None,
                worklist=worklist,
                worklist_path="reports/worklist.json" if refused else None,
                findings=findings,
                reports=(GateFinding("report-only", "record.json", "nonblocking"),),
            )
            blocks = knowledge_briefs((worklist, gate.worklist_path), gate)
            whole = {
                "ok": not refused,
                "repoId": "fixture",
                "reportPath": "reports/checklist.md",
                "attestationPath": "reports/checklist.attestation.json",
                **blocks,
            }
            cases.append(
                {
                    "state": state,
                    "refused": refused,
                    "whole": whole,
                    "answer": quality_result_answer(whole),
                }
            )
    return cases


@pytest.mark.parametrize("case", proof_cases())
def test_every_documented_field_matches_actual_whole_result(case: dict) -> None:
    worklist_fields, gate_fields = documented_fields()
    whole = case["whole"]
    worklist = whole["knowledgeWorklist"]
    gate = whole["knowledgeGate"]
    assert set(worklist) == worklist_fields
    assert set(gate) == gate_fields
    assert len(worklist_fields) == 6
    assert len(gate_fields) == 10
    assert worklist == {
        "state": case["state"],
        "path": "reports/worklist.json" if case["refused"] else None,
        "digest": "measured-worklist-identity",
        "itemCount": 3,
        "itemsByKind": {"family": 1, "invariant": 2},
        "incomplete": [] if case["state"] == "complete" else [{"input": "unreadable-code"}],
    }
    assert gate == {
        "state": "refused" if case["refused"] else "pass",
        "owner": "leaf-owner" if case["refused"] else None,
        "worklistState": case["state"],
        "worklistDigest": "measured-worklist-identity",
        "worklistPath": worklist["path"],
        "findingCount": 3 if case["refused"] else 0,
        "openItemCount": 1 if case["refused"] else 0,
        "incompleteCount": 1 if case["refused"] else 0,
        "violationCount": 1 if case["refused"] else 0,
        "reportOnlyCount": 1,
    }


def test_absent_computations_are_absent_blocks_and_bounded_answer_names_artifacts() -> None:
    assert knowledge_briefs(None, None) == {}
    worklist = ({"state": "complete", "items": [], "digest": "empty"}, None)
    assert set(knowledge_briefs(worklist, None)) == {"knowledgeWorklist"}
    gate = GateResult(None, worklist[0], None, ())
    assert set(knowledge_briefs(None, gate)) == {"knowledgeGate"}
    for case in proof_cases():
        answer = case["answer"]
        assert isinstance(answer, dict)
        assert "knowledgeWorklist" not in answer
        assert "knowledgeGate" not in answer
        assert answer["reportPath"] == "reports/checklist.md"
        assert answer["attestationPath"] == "reports/checklist.attestation.json"
