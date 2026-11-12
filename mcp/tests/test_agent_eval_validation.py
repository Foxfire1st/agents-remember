"""Differential eval gates use real whole-tree writer validation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.artifacts import json_digest, manifest_files, read_object
from agent_evals.fixtures import CaseBundle
from agent_evals.graders.cases import CaseEvidence, grade_case_code
from agent_evals.validation import compare_validation, validation_snapshot
from knowledge_validator_test_support import (
    DIRECT_LANDING_CARD,
    DIRECT_LANDING_SIDECAR,
    INTEGRATE_CARD,
    INVARIANT,
    code,
    encode,
    fixture_tree_files,
    tree,
)


def _broken_card() -> dict[str, bytes]:
    files = fixture_tree_files()
    files[DIRECT_LANDING_CARD] += b"\nMissing citation [987654].\n"
    return files


def _compare(before: dict[str, bytes], after: dict[str, bytes], **kwargs):
    return compare_validation(
        tree(before, "before"), tree(after, "after"), code(), code(), **kwargs
    )


def test_untouched_inherited_fault_is_recorded_with_exact_hashes() -> None:
    before = _broken_card()
    after = {**before, INTEGRATE_CARD: before[INTEGRATE_CARD] + b"\nStill the same code.\n"}
    result = _compare(before, after)
    assert result["status"] == "pass", result
    inherited = result["preExistingViolations"]
    assert inherited and all(item["path"] == DIRECT_LANDING_CARD for item in inherited)
    assert all(item["beforeSha256"] == item["afterSha256"] for item in inherited)
    assert result["writtenPaths"] == [INTEGRATE_CARD]


@pytest.mark.parametrize("explicit", [False, True])
def test_touched_inheritor_refuses_even_identical_diagnostic(explicit: bool) -> None:
    before = _broken_card()
    after = dict(before)
    if not explicit:
        after[DIRECT_LANDING_CARD] += b"\nUnrelated prose on the same card.\n"
    result = _compare(
        before, after, written_paths=frozenset({DIRECT_LANDING_CARD}) if explicit else frozenset()
    )
    assert result["status"] == "fail"
    assert result["beforeBlockingViolations"] == result["afterBlockingViolations"]
    assert result["blockingViolations"]
    assert not result["preExistingViolations"]


def test_repair_of_inherited_fault_is_allowed() -> None:
    result = _compare(_broken_card(), fixture_tree_files())
    assert result["beforeBlockingViolations"]
    assert not result["afterBlockingViolations"]
    assert result["status"] == "pass"


def test_deleted_target_blocks_on_untouched_referrer() -> None:
    before = fixture_tree_files()
    after = dict(before)
    del after[INVARIANT]
    result = _compare(before, after)
    missing = [
        item for item in result["blockingViolations"] if item["rule"] == "R22.3-record-links"
    ]
    assert missing
    assert any(item["path"] == DIRECT_LANDING_SIDECAR for item in missing)
    assert DIRECT_LANDING_SIDECAR not in result["writtenPaths"]
    assert any(item["target"] == "INV-7K3F9Q" for item in missing)


def test_changed_missing_target_is_not_the_same_inherited_finding() -> None:
    before = fixture_tree_files()
    value = json.loads(before[DIRECT_LANDING_SIDECAR])
    value["realizes"][0]["invariant"] = "INV-AAAAAA"
    before[DIRECT_LANDING_SIDECAR] = encode(value)
    value["realizes"][0]["invariant"] = "INV-BBBBBB"
    after = {**before, DIRECT_LANDING_SIDECAR: encode(value)}
    result = _compare(before, after)
    changed = [
        item for item in result["blockingViolations"] if item["rule"] == "R22.3-record-links"
    ]
    assert any(item["target"] == "INV-BBBBBB" for item in changed)
    assert not any(item["target"] == "INV-BBBBBB" for item in result["preExistingViolations"])


def test_report_only_findings_never_become_differential_blockers() -> None:
    files = fixture_tree_files()
    result = _compare(files, files)
    assert result["reportedViolations"]
    assert all(item["report_only"] for item in result["reportedViolations"])
    assert result["status"] == "pass"


@pytest.mark.parametrize("marker", [None, b"{}", b"not json"])
def test_absent_or_invalid_layout_is_not_synthesized(marker: bytes | None) -> None:
    files = fixture_tree_files()
    if marker is None:
        del files["knowledge/layout.json"]
    else:
        files["knowledge/layout.json"] = marker
    snapshot = validation_snapshot(tree(files), code())
    assert any(item["rule"] == "R22.1-shape" for item in snapshot["blockingViolations"])


@pytest.mark.parametrize("written", [False, True])
def test_case_grader_records_inherited_fault_and_blocks_native_write(
    tmp_path: Path, written: bool
) -> None:
    files = _broken_card()
    paired = code()
    assert paired.contents is not None
    for phase in ("before", "after"):
        for prefix, contents in (("projects/memory", files), ("projects/code", paired.contents)):
            for name, content in contents.items():
                target = tmp_path / phase / prefix / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
    manifest = {
        "specification": {"prospectiveGrade": {"deterministicChecks": [], "semanticRubric": []}}
    }
    event = {
        "kind": "tool_result",
        "session_id": "test",
        "sequence": 0,
        "source": "native",
        "source_sha256": "0" * 64,
        "payload": {
            "native_item": {
                "type": "FileChange",
                "status": "completed",
                "changes": {"projects/memory/" + DIRECT_LANDING_CARD: {"type": "update"}},
            }
        },
    }
    attempt = {
        "case": "CE01",
        "caseDigest": json_digest(manifest),
        "unknowns": [],
        "capture": {
            "harness": "codex",
            "session_id": "test",
            "raw_records": [{}],
            "source_files": [{"path": "native", "sha256": "0" * 64}],
            "unknowns": [],
            "events": [event] if written else [],
        },
        **{
            phase: [item.to_dict() for item in manifest_files(tmp_path / phase)]
            for phase in ("before", "after")
        },
    }
    evidence = CaseEvidence(
        CaseBundle(tmp_path, "CE01", manifest, ()), attempt, frozenset(), tmp_path, ()
    )
    grade = grade_case_code(evidence)
    assert grade.status == ("fail" if written else "needs_rubric"), grade
    report = read_object(tmp_path / "validation-grade.json")
    assert bool(report["blockingViolations"]) == written
    if not written:
        assert report["preExistingViolations"]
