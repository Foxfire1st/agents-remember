"""D641 regressions bind the real rejected inputs, without executing a model."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.adapters.execution import ACTOR_PROFILES, HERMES_GPT_ACTOR
from agent_evals.adapters.judges import JudgeRequest, judge_profile
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.artifacts import EvidenceFile, json_digest, read_object
from agent_evals.evidence_selection import _grading_context
from agent_evals.fixtures import CaseBundle
from agent_evals.graders.cases import CaseEvidence, RubricSubmission, _git_effects, resolve_rubric
from agent_evals.graders.models import Grade
from agent_evals.hermes_profiles import absolute_compaction, effort_declaration, hermes_route
from agent_evals.judge_evidence import _artifact_entries
from agent_evals.judge_outcomes import resolve_judge_outcome
from agent_evals.package_budget import load_judge_disk_evidence
from agent_evals.regrade import RegradeRequest, plan_regrades, run_regrades
from agent_evals.run_curator import parser
from agents_remember.errors import AuthorityError
from test_agent_eval_regrade import source_attempt
from test_agent_eval_trials import capture

DATA = json.loads((Path(__file__).parent / "fixtures/d641-judge-inputs.json").read_bytes())


@pytest.mark.parametrize("row", DATA["resolution"], ids=lambda row: row["attempt"])
def test_real_firm_fail_wins_and_unknown_only_stays_unknown(row: dict) -> None:
    data = row["capture"]
    capture = NativeCapture(
        data["harness"],
        data["session_id"],
        tuple(NativeEvent(**event) for event in data["events"]),
        tuple(SourceFile(**source) for source in data["source_files"]),
        (),
        (),
    )
    # Construct identity-routing controls from the retained verdict; no new model run.
    for actor in ACTOR_PROFILES:
        judge = judge_profile(actor)
        routed = replace(
            capture,
            harness=judge.harness,
            events=tuple(
                replace(event, payload={**event.payload, "model": judge.model})
                for event in capture.events
            ),
        )
        submission = RubricSubmission(
            JudgeRequest(tuple(row["rubric"]), (), ()), actor.model, routed
        )
        code = Grade(row["case"], "needs_rubric")
        assert resolve_rubric(code, submission) == row["expected"]
        expected = "semantic_unknown" if row["expected"] == "missing_capture" else row["expected"]
        assert resolve_judge_outcome(code, submission) == expected


@pytest.mark.parametrize("row", DATA["scratchRepositories"], ids=lambda row: row["attempt"])
def test_real_new_scratch_repository_is_content_not_an_existing_repository_change(
    row: dict, tmp_path: Path
) -> None:
    evidence = CaseEvidence(
        attempt={"before": row["before"], "after": row["after"]},
        artifacts_root=tmp_path,
        case=CaseBundle(tmp_path, row["case"], {}, ()),
        admitted_paths=frozenset(),
        rubric_files=(),
    )
    changed = {item["path"] for item in row["after"]}
    benign, failure = _git_effects(evidence, changed)
    assert failure is None
    # Scratch exemption does not bypass the ordinary admitted-path check.
    assert not benign


def test_real_changed_carrier_keeps_every_final_byte_in_judge_input() -> None:
    row = DATA["carrier"]
    before, after = row["before"], row["after"]
    digest = hashlib.sha256(after.encode()).hexdigest()
    request = JudgeRequest(("Every current claim is checked",), (), (after,))
    texts, manifest = _artifact_entries(
        request,
        (EvidenceFile("after/" + row["path"], digest),),
        {
            "files": [
                {
                    "path": row["path"],
                    "before": hashlib.sha256(before.encode()).hexdigest(),
                    "after": digest,
                }
            ]
        },
    )
    assert after in texts or any(json.loads(text).get("content") == after for text in texts)
    assert manifest[0]["fullUtf8Bytes"] == len(after.encode())


def test_judge_instructions_distinguish_absence_from_missing_collection_and_resolution() -> None:
    task = json.loads(JudgeRequest((), (), ()).prompt())["task"]
    assert "complete" in task and "absent" in task
    assert "unresolved executable" in task
    assert "not evidence" in task


@pytest.mark.parametrize("case_id", ["CE01", "CE02"])
def test_case_context_is_hash_bound_blinded_and_keeps_runtime_proof_limits(
    tmp_path: Path, case_id: str
) -> None:
    inputs = {
        "construction.json": json.dumps({"delta": f"{case_id} change"}),
        "claims.json": json.dumps(f"{case_id} false claim"),
    }
    for path, text in inputs.items():
        (tmp_path / path).write_text(text)
    files = tuple(EvidenceFile.capture(tmp_path, path) for path in inputs)
    case = CaseBundle(
        tmp_path,
        case_id,
        {"constructedDelta": "construction.json", "setup": {"gradingContext": "claims.json"}},
        files,
    )
    runtime = {
        "runtimeEvidence": {
            "commands": [{"status": "unresolved", "observationID": f"{case_id}-call"}],
            "proofLimits": "Unresolved is not evidence of stale build or absent conduct.",
        },
        "completeness": {"complete": False, "reason": "capture partial"},
    }
    result = _grading_context(
        case, {"judgeContext": runtime}, neutralize=lambda x: x.replace(case_id, "[case]")
    )
    assert result["caseInputs"][0]["purpose"] == "construction"
    assert [item["sourceSha256"] for item in result["caseInputs"][1:]] == [
        item.sha256 for item in files
    ]
    assert [item["sourcePath"] for item in result["caseInputs"][1:]] == list(inputs)
    assert all(case_id not in item["content"] for item in result["caseInputs"])
    assert (
        result["runtime"]["runtimeEvidence"]["proofLimits"]
        == runtime["runtimeEvidence"]["proofLimits"]
    )
    assert result["runtime"]["completeness"]["complete"] is False
    assert result["runtime"]["runtimeEvidence"]["commands"][0]["observationID"] == "[case]-call"
    (tmp_path / "construction.json").write_text("changed")
    with pytest.raises(AuthorityError, match="evidence changed"):
        _grading_context(case, {}, neutralize=lambda x: x)


def test_unbound_context_rejected_and_absent_runtime_is_explicit(tmp_path: Path) -> None:
    case = CaseBundle(tmp_path, "CE01", {"setup": {}}, ())
    result = _grading_context(case, {}, neutralize=lambda x: x)
    assert result["runtime"]["available"] is False
    assert "recorded" in result["runtime"]["reason"]
    case.manifest["setup"]["gradingContext"] = "unbound.json"
    with pytest.raises(AuthorityError, match="not a hash-bound case file"):
        _grading_context(case, {}, neutralize=lambda x: x)


@pytest.mark.parametrize(
    "case_id,paths",
    [
        ("CE04", ("evidence/construction.json", "d641-construction-delta.json")),
        ("CE05", ("private/constructed-delta.json",)),
    ],
)
def test_explicit_construction_carriers_and_manifest_are_bound(
    case_id: str, paths: tuple[str, ...], tmp_path: Path
) -> None:
    for path in paths:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text('{"construction":"recorded source"}')
    case = CaseBundle(
        tmp_path,
        case_id,
        {"construction": {"kind": "cut from the record (D637)"}},
        tuple(EvidenceFile.capture(tmp_path, path) for path in paths),
    )
    result = _grading_context(case, {}, neutralize=lambda x: x)
    assert result["caseInputs"][0]["caseManifestDigest"] == json_digest(case.manifest)
    assert result["caseInputs"][0]["valueSha256"] == json_digest(case.manifest["construction"])
    assert [row["sourcePath"] for row in result["caseInputs"][1:]] == list(paths)
    assert [row["sourcePath"] for row in result["sourceBindings"][1:]] == list(paths)


@pytest.mark.parametrize("existing", [False, "directory", "gitfile"])
def test_scratch_git_exemption_never_applies_outside_scratch_or_to_existing_repo(
    existing: bool | str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = "reports/scratch/repo/.git/HEAD" if existing else "projects/repo/.git/HEAD"
    calls = []

    def compare(before, after, *, scratch_root):
        calls.append((before, after))
        return SimpleNamespace(status="pass", benign_metadata_paths=(), to_dict=lambda: {})

    monkeypatch.setattr("agent_evals.graders.cases.compare_git_semantics", compare)
    evidence = CaseEvidence(
        attempt={
            "before": [{"path": path.removesuffix("/HEAD") if existing == "gitfile" else path}]
            if existing
            else [],
            "after": [{"path": path}],
        },
        artifacts_root=tmp_path,
        case=CaseBundle(tmp_path, "CE03", {}, ()),
        admitted_paths=frozenset(),
        rubric_files=(),
    )
    assert _git_effects(evidence, {path}) == (set(), None)
    assert len(calls) == 1


def test_hermes_gpt_retained_regrade_binds_opus_package_and_execution(tmp_path: Path) -> None:
    """Synthetic Hermes transcript control exercises actual retained-regrade routing."""
    for selector in ("hermes-gpt", HERMES_GPT_ACTOR.model):
        args = parser().parse_args(
            [
                "trials",
                "--case",
                "case",
                "--evidence",
                "evidence",
                "--sandbox-home",
                "sandbox",
                "--model",
                selector,
                "--text",
                "old",
                "--codex-access",
                "supplied-access-only.json",
            ]
        )
        assert args.model == selector and args.codex_access == Path("supplied-access-only.json")
    route = hermes_route(HERMES_GPT_ACTOR)
    assert (route["provider"], route["model"], route["expectedHost"]) == (
        "openai-codex",
        "gpt-6.1-sol-900k",
        "chatgpt.com",
    )
    assert route["seedNames"] == [] and route["effortPolicy"] == "high"
    declaration = effort_declaration(HERMES_GPT_ACTOR)
    assert declaration is not None and declaration["requestCarrierObserved"] is False
    compaction = absolute_compaction(900_000)
    assert compaction["fraction"] == 400_000 / 900_000
    assert compaction["absoluteThreshold"] == 400_000
    batch, original = source_attempt(tmp_path, model=HERMES_GPT_ACTOR.model)
    manifest = plan_regrades(
        RegradeRequest(
            batch=batch, evidence=tmp_path / "regrade", judge_executable=Path(sys.executable)
        )
    )
    row = read_object(manifest)["attempts"][0]
    assert row["ready"], row["preparationCauses"]
    assert row["resultIdentity"]["components"]["judgeModel"] == "claude-opus-5-5"
    target = manifest.parent / row["prepared"]
    request = read_object(target / "judge-request.json")
    disk = load_judge_disk_evidence(
        target,
        JudgeRequest(
            tuple(request["rubric"]),
            tuple(request["observations"]),
            tuple(request["artifact_evidence"]),
            task=request["task"],
        ),
        "claude",
    )
    assert disk.judge_harness == "claude"
    assert read_object(target / "actor-capture.json")["harness"] == "hermes"
    with patch(
        "agent_evals.regrade.run_native_judge", return_value=capture(tmp_path, judge=True)
    ) as judge:
        result = run_regrades(RegradeRequest(manifest=manifest, execute=True, tooling_check=True))
    assert judge.call_args.args[1] == HERMES_GPT_ACTOR
    record = read_object(result)["regrades"][0]
    assert record["status"] == "right"
    assert record["resultIdentity"] == row["resultIdentity"]
    assert read_object(original / "attempt.json")["capture"]["harness"] == "hermes"
