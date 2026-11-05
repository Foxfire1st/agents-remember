"""The real writer validator admits case inputs before any paid/native execution."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.artifacts import manifest_files, read_object, write_object
from agent_evals.fixtures import CASE_IDS, CaseBundle, reset_case
from agent_evals.preflight import (
    NativeIntakeBinding,
    preflight_case,
    preflight_cases,
    preflight_project,
)
from agent_evals.run_curator import main
from agent_evals.trial_setup import _git_baseline, _place_native_memory
from agent_evals.trials import TrialBatchRequest, run_trials
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from knowledge_validator_test_support import (
    DIRECT_LANDING,
    DIRECT_LANDING_CARD,
    INVARIANT,
    code,
    fixture_tree_files,
)
from test_agent_eval_trials import bundle


def _case(root: Path, *, broken: bool = False) -> CaseBundle:
    case = bundle(root)
    data = case.manifest
    write_object(root / data["setup"]["queue"], {"items": []})
    files = fixture_tree_files()
    if broken:
        del files[INVARIANT]
    inputs = list(data["actorInputs"])
    paired = code()
    assert paired.contents is not None
    code_bytes = dict(paired.contents)
    for path in paired.paths - code_bytes.keys():
        code_bytes[path] = b"# Existence-only fixture path with no referenced anchor.\n"
    for kind, contents in (("memory", files), ("code", code_bytes)):
        for relative, content in contents.items():
            assert content is not None
            source = f"input/{kind}/{relative}"
            path = root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            inputs.append({"source": source, "destination": f"projects/{kind}/{relative}"})
    data["actorInputs"] = inputs
    data["files"] = [item.to_dict() for item in manifest_files(root) if item.path != "case.json"]
    write_object(root / "case.json", data)
    return CaseBundle.read(root)


def test_whole_writer_tree_requires_link_closure_and_preserves_inputs(tmp_path: Path) -> None:
    valid = _case(tmp_path / "valid")
    before = manifest_files(valid.path)
    result = preflight_case(valid)
    assert result["status"] == "pass", result
    assert result["reportedViolations"]  # Report-only findings do not become writer refusals.
    assert manifest_files(valid.path) == before
    broken = _case(tmp_path / "broken", broken=True)
    before = manifest_files(broken.path)
    result = preflight_case(broken)
    assert result["status"] == "fail"
    links = [v for v in result["blockingViolations"] if v["rule"] == "R22.3-record-links"]
    assert links
    assert any(v["target"] == "INV-7K3F9Q" for v in links)
    assert all(v["path"] and v["field"] and v["message"] for v in links)
    assert manifest_files(broken.path) == before


def test_reports_all_fourteen_without_short_circuit_or_models(tmp_path: Path) -> None:
    case = _case(tmp_path / "CE01", broken=True)
    with patch("agent_evals.trials.provision_batch", side_effect=AssertionError("host started")):
        report = preflight_cases(tmp_path)
    assert report["counts"] == {"cases": 14, "pass": 0, "blocked": 14, "unknown": 0}
    assert [item["case"] for item in report["cases"]] == list(CASE_IDS)
    assert report["cases"][0]["status"] == "fail"
    assert report["cases"][1]["status"] == "invalid"
    assert report["judgeInvocationsThisRun"] == 0
    assert case.path.exists()


def test_trial_refuses_before_host_text_or_approver_and_writes_evidence(tmp_path: Path) -> None:
    case = _case(tmp_path / "case", broken=True)
    request = TrialBatchRequest(
        case.path,
        (case.case_id,),
        "codex",
        "old",
        1,
        tmp_path / "evidence",
        tmp_path / "sandbox",
        tmp_path / "code",
    )
    with (
        patch("agent_evals.trials.provision_batch", side_effect=AssertionError("host started")),
        patch("agent_evals.trials.load_trial_text", side_effect=AssertionError("text read")),
        patch("builtins.input", side_effect=AssertionError("approver asked")),
        pytest.raises(AuthorityError, match="preflight refused"),
    ):
        run_trials(request)
    assert not request.sandbox_home.exists()
    assert read_object(request.evidence / "case-preflight.json")["counts"]["blocked"] == 1


def test_cli_failure_reports_and_tamper_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    case = _case(tmp_path / "case", broken=True)
    output = tmp_path / "preflight.json"
    assert main(["preflight", "--cases", str(case.path), "--output", str(output)]) == 1
    assert json.loads(capsys.readouterr().out)["cases"][0]["status"] == "fail"
    target = case.path / case.manifest["actorInputs"][0]["source"]
    target.write_text("changed after manifest")
    result = preflight_cases(case.path, ("CE01",))
    assert result["cases"][0]["status"] == "invalid"
    assert "evidence changed" in result["cases"][0]["cause"]


def test_native_marker_and_writer_lookup_match_provisioning(tmp_path: Path) -> None:
    case = _case(tmp_path / "case")
    data = case.manifest
    data["actorInputs"] = [
        item
        for item in data["actorInputs"]
        if item["destination"] != "projects/memory/knowledge/layout.json"
    ]
    write_object(case.path / "case.json", data)
    result = preflight_case(CaseBundle.read(case.path))
    assert result["status"] == "pass", result
    assert "native layout marker" in result["materialization"]


def test_actual_trial_copy_matches_input_validation(tmp_path: Path) -> None:
    case = _case(tmp_path / "case")
    fixture = reset_case(case, tmp_path / "copies")
    memory, _ = _place_native_memory(fixture.actor_root)
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    code_root = fixture.actor_root / "projects/code"
    _git_baseline(code_root, env)
    _git_baseline(memory, env)
    predicted = preflight_case(case)
    actual = preflight_project(case, memory, code_root)
    for key in ("status", "memoryInputSha256", "codeInputSha256", "blockingViolations"):
        assert actual[key] == predicted[key]


def test_source_subject_binding_does_not_require_a_memory_markdown_copy(tmp_path: Path) -> None:
    case = _case(tmp_path / "case")
    data = case.manifest
    data["actorInputs"] = [
        item
        for item in data["actorInputs"]
        if item["destination"] != "projects/memory/" + DIRECT_LANDING_CARD
    ]
    write_object(case.path / "case.json", data)
    case = CaseBundle.read(case.path)
    result = preflight_case(case)
    assert result["status"] == "pass", result
    binding = next(x for x in result["subjectBindings"] if x["subject"] == DIRECT_LANDING)
    assert binding["status"] == "resolved" and binding["sha256"]
    assert result["rawWriterBlockingViolations"]
    data["actorInputs"] = [
        item
        for item in data["actorInputs"]
        if item["destination"] != "projects/code/" + DIRECT_LANDING
    ]
    write_object(case.path / "case.json", data)
    result = preflight_case(CaseBundle.read(case.path))
    assert result["status"] == "unknown", result
    assert any(x["subject"] == DIRECT_LANDING for x in result["bindingUnavailable"])


def test_real_memory_markdown_marker_violation_is_not_reclassified(tmp_path: Path) -> None:
    case = _case(tmp_path / "case")
    data = case.manifest
    source = next(
        x["source"]
        for x in data["actorInputs"]
        if x["destination"] == "projects/memory/" + DIRECT_LANDING_CARD
    )
    (case.path / source).write_text("# Invalid card\n\nAn unbound marker [987654].\n")
    data["files"] = [
        item.to_dict() for item in manifest_files(case.path) if item.path != "case.json"
    ]
    write_object(case.path / "case.json", data)
    result = preflight_case(CaseBundle.read(case.path))
    assert result["status"] == "fail"
    assert any(x["rule"] == "R22.3-markers" for x in result["blockingViolations"])


def test_named_absence_admits_inherited_findings_and_records_initial_writer_refusal(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path / "case")
    data = case.manifest
    data["actorInputs"] = [
        item
        for item in data["actorInputs"]
        if item["destination"]
        not in {
            "projects/memory/" + DIRECT_LANDING_CARD,
            "projects/code/" + DIRECT_LANDING,
        }
    ]
    write_object(
        case.path / data["setup"]["queue"],
        {"items": [{"source": DIRECT_LANDING, "currentSourceAbsent": True}]},
    )
    data["files"] = [
        item.to_dict() for item in manifest_files(case.path) if item.path != "case.json"
    ]
    write_object(case.path / "case.json", data)
    result = preflight_case(CaseBundle.read(case.path))
    assert result["admissionStatus"] == "pass"
    assert result["status"] == "pass"
    assert result["nativeWriterRefusesStartingTree"]
    assert result["admittedPreExistingViolations"]
    assert not result["bindingUnavailable"]
    assert all(
        item["beforeSha256"] == item["afterSha256"]
        for item in result["admittedPreExistingViolations"]
    )
    binding = next(item for item in result["subjectBindings"] if item["subject"] == DIRECT_LANDING)
    assert binding["status"] == "resolved-absent" and binding["absenceAuthority"]["sha256"]


def test_configured_intake_refusal_is_not_a_tree_pass(tmp_path: Path) -> None:
    """Retained CE11 had valid trees but native operation-location-adoption-required."""
    case = _case(tmp_path / "case")
    fixture = reset_case(case, tmp_path / "copies")
    memory, _ = _place_native_memory(fixture.actor_root)
    code_root = fixture.actor_root / "projects/code"
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    _git_baseline(code_root, env)
    _git_baseline(memory, env)
    refusal = {
        "mode": "leaf-candidate",
        "intakeAccepted": False,
        "qualityCode": "operation-location-adoption-required",
        "missingSubjects": [],
    }
    with patch("agent_evals.preflight.configured_intake", return_value=refusal) as intake:
        result = preflight_project(
            case,
            memory,
            code_root,
            intake=NativeIntakeBinding(
                cast(McpRuntimeConfig, object()), {"leafId": "CE11-L1"}, tmp_path / "intake"
            ),
        )
    intake.assert_called_once()
    assert result["treeStatus"] == "pass"
    assert result["status"] == "unknown"
    assert result["nativeIntake"] == refusal
    assert result["unknownClass"] == "binding-unavailable"
