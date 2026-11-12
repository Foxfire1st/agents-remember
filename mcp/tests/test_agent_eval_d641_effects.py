"""D641 effect regressions cut from actual failing Hermes evidence; no model calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import HERMES_ACTOR
from agent_evals.adapters.models import NativeCapture
from agent_evals.artifacts import EvidenceFile, json_digest, manifest_files, write_object
from agent_evals.fixtures import CaseBundle
from agent_evals.graders import cases as case_grader
from agent_evals.graders.models import Grade
from agent_evals.trials import (
    GradePreparationContext,
    _effect_diff,
    _judge_runtime_context,
    prepare_case_grade,
)

RECORDS = json.loads(
    (Path(__file__).parent / "fixtures/d641-effects-real-records.json").read_text()
)["rows"]


@pytest.mark.parametrize("row", RECORDS, ids=lambda row: row["attempt"][:8])
@pytest.mark.parametrize("removed", [False, True], ids=["added", "deleted"])
def test_real_empty_stderr_has_header_only_patch(tmp_path: Path, row: dict, removed: bool) -> None:
    # The captured original was an addition. Reverse the same bytes to prove deletion.
    item = next(item for item in row["emptyFiles"] if item["before"] is None)
    phase = "before" if removed else "after"
    source = tmp_path / phase / item["path"]
    source.parent.mkdir(parents=True)
    source.write_bytes(b"")
    for name in ("before", "after"):
        (tmp_path / name).mkdir(exist_ok=True)
    report = {
        name: [item.to_dict() for item in manifest_files(tmp_path / name)]
        for name in ("before", "after")
    }
    assert report[phase][0]["sha256"] == item["sha256"]
    delta = _effect_diff(report, tmp_path)
    header = f"--- before/{item['path']}\n+++ after/{item['path']}\n"
    assert delta["fullUnifiedDiff"] == header, row["source"]
    assert delta["files"][0]["diffBytes"] == len(header.encode())
    # D642 retains the complete effect inventory; no hunk-selection layer remains.
    selected = delta
    assert selected["files"][0]["path"] == item["path"]
    assert selected["files"][0]["beforeBytes"] == (0 if removed else None)
    assert selected["files"][0]["afterBytes"] == (None if removed else 0)


@pytest.mark.parametrize(
    "row",
    [row for row in RECORDS if row["codeGrade"]["status"] == "unknown"],
    ids=lambda row: row["attempt"][:8],
)
def test_real_deterministic_questions_reach_attempt_cause(tmp_path: Path, row: dict) -> None:
    recorded = row["codeGrade"]
    grade = Grade.produced(
        case_grader.__file__,
        recorded["grader_id"],
        "unknown",
        tuple(recorded["evidence"]),
        tuple(recorded["questions"]),
    )
    report = {"unknowns": []}
    capture = NativeCapture("hermes", "retained", (), (), (), ())
    case = CaseBundle(tmp_path, row["case"], {}, ())
    with (
        patch("agent_evals.trials._admitted_paths", return_value=frozenset()),
        patch("agent_evals.trials._rubric_artifacts", return_value=()),
        patch("agent_evals.trials.grade_case_code", return_value=grade),
        patch("agent_evals.trials._helper_observed", return_value=False),
    ):
        prepared = prepare_case_grade(
            case,
            report,
            tmp_path,
            capture,
            context=GradePreparationContext(lambda text: text, HERMES_ACTOR),
        )
    assert prepared.request is None
    assert grade.questions
    assert all(question in "; ".join(report["unknowns"]) for question in grade.questions)


def test_runtime_context_carries_receipts_and_bounded_completeness(tmp_path: Path) -> None:
    server = {
        "servingBuild": {"sourceDigest": "sha256:observed"},
        "evaluationToolInventory": {"sameInitializedSession": True, "complete": True, "tools": []},
    }
    path = {"path": "/trial/bin", "executables": {}}
    path["sha256"] = json_digest(path)
    write_object(tmp_path / "server-info.json", server)
    write_object(tmp_path / "path-snapshot.json", path)
    capture = NativeCapture("hermes", "retained", (), (), (), ())
    context = _judge_runtime_context({"before": [], "after": []}, tmp_path, capture)
    assert context["receiptSources"] == {
        name: EvidenceFile.capture(tmp_path, filename).to_dict()
        for name, filename in (("server", "server-info.json"), ("path", "path-snapshot.json"))
    }
    assert context["runtimeEvidence"]["mcpToolInventory"] == server["evaluationToolInventory"]
    assert context["completeness"]["actionsComplete"] is True
    assert context["completeness"]["reportsComplete"] is True
    assert context["completeness"]["unresolvedExecutablesAreNotAbsentConduct"] is True


def test_partial_capture_never_gets_a_complete_evidence_assertion(tmp_path: Path) -> None:
    capture = NativeCapture("hermes", "retained", (), (), (), ("native stream incomplete",))
    context = _judge_runtime_context({}, tmp_path, capture)
    assert context["receiptSources"] == {}
    assert context["runtimeEvidence"]["mcpToolInventory"] is None
    assert context["completeness"]["actionsComplete"] is False
    assert context["completeness"]["reportsComplete"] is False
    assert context["completeness"]["captureLimitations"] == ["native stream incomplete"]
