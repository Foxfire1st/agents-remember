"""Byte-exact judge disk route, immutable bindings and final send guard."""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from agent_evals import package_budget as budget
from agent_evals import trials as trial_orchestration
from agent_evals.adapters.execution import CODEX_ACTOR, HERMES_ACTOR
from agent_evals.adapters.judges import JudgeRequest, judge_profile
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.artifacts import EvidenceFile
from agent_evals.evidence_selection import _read_report
from agent_evals.fixtures import CaseBundle
from agent_evals.graders.cases import (
    CaseEvidence,
    RubricSubmission,
    case_judge_request,
    parse_native_verdict,
)
from agent_evals.graders.models import Grade
from agent_evals.judge_evidence import EvidenceSelection, JudgePackageInputs, package_judge_evidence
from agent_evals.judge_outcomes import resolve_judge_outcome
from agent_evals.trials import _effect_diff, _judge_runtime_context


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fitted(tmp_path, request, bindings, **kwargs):
    result, manifest = budget.fit_package(
        request,
        bindings,
        context=budget.DiskPackageContext(tmp_path, "private-trial", "claude"),
        observation_bindings=[],
        opaque_indices=(),
        **kwargs,
    )
    (tmp_path / "judge-evidence-manifest.json").write_text(json.dumps(manifest))
    return result, manifest


def test_grading_note_is_trusted_task_text_and_changes_the_package_identity(tmp_path):
    capture = NativeCapture("hermes", "session", (), (), ({"source": "controlled"},), ())
    original = JudgeRequest(("criterion",), (), ())
    notes = [
        _judge_runtime_context({"case": "CE05", "text_signature": arm}, tmp_path, capture)[
            "helperCompletionGradingNote"
        ]
        for arm in ("old", "new")
    ]
    assert notes[0] == notes[1]
    assert all(word not in notes[0] for word in ("ACP", "broken", "undeliverable"))
    selection = EvidenceSelection("CE05", {}, {}, {}, {})
    common = {"files": [], "fullUnifiedDiff": ""}
    before = package_judge_evidence(
        original,
        capture,
        (),
        inputs=JudgePackageInputs(
            common, selection, tmp_path / "before", "controlled", HERMES_ACTOR
        ),
    )
    changed = replace(original, task=original.task + " " + notes[0])
    after = package_judge_evidence(
        changed,
        capture,
        (),
        inputs=JudgePackageInputs(
            common, selection, tmp_path / "after", "controlled", HERMES_ACTOR
        ),
    )
    old_prompt, new_prompt = json.loads(before.request.prompt()), json.loads(after.request.prompt())
    assert new_prompt["task"] == old_prompt["task"] + " " + notes[0]
    assert new_prompt["rubric"] == old_prompt["rubric"]
    assert new_prompt["response_schema"] == old_prompt["response_schema"]
    assert (
        before.manifest["diskEvidence"]["packageIdentity"]
        != after.manifest["diskEvidence"]["packageIdentity"]
    )


def test_optional_large_file_is_exact_disk_not_summary(tmp_path):
    text = "é" * 40000
    request = JudgeRequest(("criterion",), (), (text,))
    result, manifest = fitted(
        tmp_path,
        request,
        [{"index": 0, "path": "after/notes.json", "mandatory": [], "criteria": [0]}],
    )
    binding = budget.load_judge_disk_evidence(tmp_path, result, "claude")
    row = binding.rows[0]
    assert (binding.root / row["path"]).read_bytes() == text.encode()
    assert row["sha256"] == sha(text)
    assert row["inline"] is False and row["mustRead"] is False
    assert "summarised" not in result.prompt()
    assert "private-trial" not in result.prompt()
    assert str(tmp_path) not in result.prompt()
    assert manifest["trialId"] == "private-trial"


def test_mandatory_carrier_always_complete_on_disk_and_must_read(tmp_path):
    text = "whole-property-carrier" * 4000
    result, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), (text,)),
        [
            {
                "index": 0,
                "path": "after/carrier.json",
                "mandatory": ["case-named-carriers"],
                "criteria": [0],
                "citations": [{"claimId": "c", "fragment": "/property"}],
            }
        ],
    )
    first = manifest["diskEvidence"]["rows"][0]
    assert not first["inline"] and first["mustRead"]
    assert (Path(manifest["diskEvidence"]["root"]) / first["path"]).read_text() == text
    other = tmp_path / "large"
    large = "a" * (budget.MAX_PROMPT_UTF8_BYTES + 1)
    result, manifest = fitted(
        other,
        JudgeRequest(("criterion",), (), (large,)),
        [
            {
                "index": 0,
                "path": "after/carrier.json",
                "mandatory": ["changed-memory-carriers"],
                "criteria": [0],
            }
        ],
    )
    row = manifest["diskEvidence"]["rows"][0]
    assert row["mustRead"] and not row["inline"]
    assert (Path(manifest["diskEvidence"]["root"]) / row["path"]).read_text() == large
    budget.enforce_judge_limit(result, judge_harness="claude")


def test_all_actions_preserve_order_and_opaque_payload_pair(tmp_path):
    payloads = [
        ("message", {"text": "decision"}),
        (
            "tool_call",
            {
                "call_id": "a",
                "name": "exec",
                "opaque": True,
                "arguments": {"cmd": "x"},
                "native_dispatch": {"pairedOutputs": [{"payload": {"output": "full"}}]},
            },
        ),
        ("tool_result", {"call_id": "a", "output": "complete output"}),
        ("tool_call", {"call_id": "b", "name": "exec", "arguments": {"cmd": "é" * 40000}}),
        ("tool_result", {"call_id": "b", "output": "ok"}),
    ]
    events = tuple(
        NativeEvent(k, "session", i, p, "native.jsonl", "source")
        for i, (k, p) in enumerate(payloads)
    )
    capture = NativeCapture(
        "codex",
        "session",
        events,
        (SourceFile("native.jsonl", "source"),),
        ({"raw": "frozen"},),
        (),
    )
    observations = tuple(
        json.dumps({"kind": e.kind, "payload": e.payload}, ensure_ascii=False) for e in events
    )
    request = JudgeRequest(("criterion",), observations, ())
    package = package_judge_evidence(
        request,
        capture,
        (),
        inputs=JudgePackageInputs(
            {"files": [], "fullUnifiedDiff": ""},
            EvidenceSelection("case", {"a": (0,), "b": (0,)}, {}, {}, {0: (0,)}),
            tmp_path,
            "private-trial",
            CODEX_ACTOR,
        ),
    )
    assert package.request.rubric == request.rubric
    assert [r["sourceSequence"] for r in package.manifest["observations"]] == list(range(5))
    disk = package.manifest["diskEvidence"]
    rows = [r for r in disk["rows"] if r["kind"] == "action-payload" and not r["inline"]]
    assert len(rows) == 5
    for row in rows:
        original = observations[row["observationIndex"]]
        assert (Path(disk["root"]) / row["path"]).read_text() == original
        assert row["mustRead"] == (row["observationIndex"] in (1, 2, 3))
    assert json.loads(package.request.observations[0])["path"] == "actions/0000.json"
    assert json.loads(package.request.observations[-1])["identity"]["call_id"] == "b"


def test_loader_rejects_mutated_file_prompt_grant_and_kind(tmp_path):
    request, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), ("x" * 70000,)),
        [{"index": 0, "path": "notes", "mandatory": []}],
    )
    with pytest.raises(ValueError):
        budget.load_judge_disk_evidence(tmp_path, replace(request, rubric=("different",)), "claude")
    with pytest.raises(ValueError):
        budget.load_judge_disk_evidence(tmp_path, request, "codex")
    row = manifest["diskEvidence"]["rows"][0]
    (Path(manifest["diskEvidence"]["root"]) / row["path"]).write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        budget.load_judge_disk_evidence(tmp_path, request, "claude")


def test_disk_identity_binds_complete_access_contract(tmp_path):
    request = JudgeRequest(("criterion",), (), ())
    one, first = fitted(tmp_path / "one", request, [])
    two, second = budget.fit_package(
        request,
        [],
        context=budget.DiskPackageContext(tmp_path / "two", "private-second-trial", "codex"),
        observation_bindings=[],
        opaque_indices=(),
    )
    assert first["diskEvidence"]["packageIdentity"] != second["diskEvidence"]["packageIdentity"]
    assert (
        first["diskEvidence"]["accessContract"]["toolGrant"]
        == "ordinary agent, workspace = evidence folder, shell included, no AR server"
    )
    assert second["diskEvidence"]["accessContract"]["sandbox"] == "workspace-write"
    assert "private-trial" not in one.prompt() and "private-second-trial" not in two.prompt()


def test_unknown_attribution_preserves_matched_unmatched_unreasoned(tmp_path):
    _, manifest = fitted(
        tmp_path,
        JudgeRequest(("a", "b", "c", "d"), (), ("x" * 70000,)),
        [{"index": 0, "path": "after/overview.json", "mandatory": [], "criteria": [0]}],
    )
    row = manifest["diskEvidence"]["rows"][0]
    verdict = {
        "criteria": [
            {"criterion": "a", "outcome": "unknown", "evidence": "Need " + row["path"]},
            {"criterion": "b", "outcome": "unknown", "evidence": "D627 definition absent"},
            {"criterion": "c", "outcome": "unknown", "evidence": ""},
            {"criterion": "d", "outcome": "pass", "evidence": "observed"},
        ]
    }
    report = budget.attribute_unknowns(manifest, verdict)
    assert report["counts"] == {"unknown": 3, "attributed": 1, "unattributed": 2, "unreasoned": 1}
    assert report["criteria"][1]["evidence"] == "D627 definition absent"
    assert report["criteria"][2]["evidence"] == ""


def test_unrepresentable_inline_rubric_fails_named_before_send(tmp_path):
    with pytest.raises(budget.JudgePackageTooLarge, match="mandatory-evidence-over-budget"):
        fitted(tmp_path, JudgeRequest(("r" * (budget.MAX_PROMPT_UTF8_BYTES + 1),), (), ()), [])


@pytest.mark.parametrize("index", range(5))
def test_real_retained_envelopes_keep_all_bytes_and_fit(tmp_path, index):
    fixture = json.loads(
        gzip.decompress(
            (Path(__file__).parent / "fixtures/judge-package-budget-real.json.gz").read_bytes()
        )
    )
    record = fixture["records"][index]
    raw = record["request"]
    request = JudgeRequest(
        tuple(raw["rubric"]), tuple(raw["observations"]), tuple(raw["artifact_evidence"])
    )
    kind = "codex" if index == 4 else "claude"
    with pytest.raises(budget.JudgePackageTooLarge):
        budget.enforce_judge_limit(request, judge_harness=kind)
    bindings = [
        {
            "index": i,
            "path": f"retained/{i}",
            "mandatory": [],
            "criteria": list(range(len(request.rubric))),
        }
        for i in range(len(request.artifact_evidence))
    ]
    result, manifest = budget.fit_package(
        request,
        bindings,
        context=budget.DiskPackageContext(tmp_path, "private-real-trial", kind),
        observation_bindings=[
            {"index": i, "path": f"observation/{i}", "criteria": list(range(len(request.rubric)))}
            for i in range(len(request.observations))
        ],
        opaque_indices=(),
    )
    assert result.rubric == request.rubric
    budget.enforce_judge_limit(result, judge_harness=kind)
    rows = manifest["diskEvidence"]["rows"]
    for row in rows:
        if row["kind"] == "reference":
            assert row["path"] == "reference/refused-write.md"
            continue
        original = (
            request.artifact_evidence[row["artifactIndex"]]
            if row["kind"] == "artifact"
            else request.observations[row["observationIndex"]]
        )
        assert (
            Path(manifest["diskEvidence"]["root"]) / row["path"]
        ).read_bytes() == original.encode()
    assert len([row for row in rows if row["kind"] == "artifact"]) == len(request.artifact_evidence)
    assert len(result.observations) == len(request.observations)


def test_unknown_attribution_requires_exact_path_not_backup_or_prefix(tmp_path):
    _, manifest = fitted(
        tmp_path,
        JudgeRequest(("a",), (), ("x" * 70000,)),
        [{"index": 0, "path": "after/overview.json", "mandatory": []}],
    )
    for reason in (
        "after/overview.json.bak absent",
        "prefixafter/overview.json absent",
        "after/overview.json/child absent",
    ):
        report = budget.attribute_unknowns(
            manifest, {"criteria": [{"criterion": "a", "outcome": "unknown", "evidence": reason}]}
        )
        assert report["counts"]["attributed"] == 0
    for reason in (
        "Need `after/overview.json`.",
        "Need after/overview.json.",
        "Need (after/overview.json), then judge.",
    ):
        report = budget.attribute_unknowns(
            manifest, {"criteria": [{"criterion": "a", "outcome": "unknown", "evidence": reason}]}
        )
        assert report["counts"]["attributed"] == 1
        assert report["criteria"][0]["evidence"] == reason


def test_many_action_payloads_route_whole_by_fixed_small_threshold(tmp_path):
    events = tuple(
        json.dumps({"kind": "tool_result", "payload": {"call_id": str(i), "output": "e" * 60000}})
        for i in range(30)
    )
    request = JudgeRequest(("criterion",), events, ())
    result, manifest = budget.fit_package(
        request,
        [],
        context=budget.DiskPackageContext(tmp_path, "private", "claude"),
        observation_bindings=[
            {"index": i, "path": f"observation/{i}", "criteria": [0]} for i in range(30)
        ],
        opaque_indices=(),
    )
    assert len(result.observations) == 30
    moved = [
        row
        for row in manifest["diskEvidence"]["rows"]
        if row["kind"] == "action-payload" and not row["inline"]
    ]
    assert moved and all(row["routeReason"] == "over-action-threshold" for row in moved)
    for row in moved:
        assert (Path(manifest["diskEvidence"]["root"]) / row["path"]).read_text() == events[
            row["observationIndex"]
        ]
    budget.enforce_judge_limit(result, judge_harness="claude")


def test_large_claim_list_remains_inline_required(tmp_path):
    text = "claims" * 12000
    result, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), (text,)),
        [{"index": 0, "path": "claims", "mandatory": [], "inlineRequired": True}],
    )
    assert result.artifact_evidence[0] == text
    assert manifest["diskEvidence"]["rows"][0]["inline"] is True


def test_mandatory_measurements_are_payload_bytes_not_metadata(tmp_path):
    text = "a changed carrier" * 5000
    rubric = "actual rubric"
    result, manifest = fitted(
        tmp_path,
        JudgeRequest((rubric,), (), (text,)),
        [{"index": 0, "path": "after/card", "mandatory": ["changed-memory-carriers"]}],
    )
    classes = manifest["mandatoryClasses"]
    assert classes["changed-memory-carriers"]["utf8Bytes"] == len(text.encode())
    assert classes["changed-memory-carriers"]["inlineUtf8Bytes"] == 0
    assert classes["changed-memory-carriers"]["fileUtf8Bytes"] == len(text.encode())
    assert classes["rubric"]["utf8Bytes"] == len(rubric.encode())
    assert classes["in-package-manifest"]["utf8Bytes"] == len(result.artifact_evidence[-1].encode())


def test_loader_rejects_extra_embedded_digest_even_if_external_identity_rebound(tmp_path):
    request, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), ("evidence",)),
        [{"index": 0, "path": "after/card", "mandatory": []}],
    )
    facing = json.loads(request.artifact_evidence[-1])
    facing["manifestSha256"] = "0" * 64
    changed = replace(
        request, artifact_evidence=(*request.artifact_evidence[:-1], json.dumps(facing))
    )
    disk = manifest["diskEvidence"]
    disk["inlineSha256"] = sha(changed.prompt())
    disk["packageIdentity"] = budget._identity(
        disk["inlineSha256"],
        disk["manifestSha256"],
        disk["accessContract"],
        "claude",
        disk["appliedLimits"],
    )
    manifest["packagedRequestPromptSha256"] = disk["inlineSha256"]
    (tmp_path / "judge-evidence-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="embedded"):
        budget.load_judge_disk_evidence(tmp_path, changed, "claude")


def test_routed_action_index_has_short_actual_argument_values(tmp_path):
    event = json.dumps(
        {
            "kind": "tool_call",
            "payload": {
                "call_id": "id",
                "name": "exec",
                "arguments": {
                    "cmd": "verify selected subject " + "x" * 70000,
                    "cwd": "/evidence",
                    "count": 2,
                },
            },
        }
    )
    request = JudgeRequest(("criterion",), (event,), ())
    result, _ = budget.fit_package(
        request,
        [],
        context=budget.DiskPackageContext(tmp_path, "private", "claude"),
        observation_bindings=[{"index": 0, "path": "observation/0", "criteria": [0]}],
        opaque_indices=(),
    )
    index = json.loads(result.observations[0])
    assert index["shortArguments"]["cwd"] == "/evidence"
    assert index["shortArguments"]["count"] == 2
    assert index["shortArguments"]["cmd"]["prefix"].startswith("verify selected subject ")
    assert index["shortArguments"]["cmd"]["fullValueIn"] == index["path"]


def test_inline_contains_only_index_claims_and_compact_path_size_manifest(tmp_path):
    text = "changed final card" * 20
    result, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), (text,)),
        [{"index": 0, "path": "after/card", "mandatory": ["changed-memory-carriers"]}],
    )
    assert len(result.artifact_evidence) == 1
    facing = json.loads(result.artifact_evidence[0])
    assert set(facing) == {"kind", "schema", "manifestPath", "files"}
    assert all(set(row) == {"path", "sizeBytes"} for row in facing["files"])
    assert text not in result.prompt()
    assert sha(text) not in result.prompt()
    assert manifest["diskEvidence"]["rows"][0]["mustRead"] is True


def test_action_routing_is_fixed_threshold_not_judge_cap(tmp_path):
    event = json.dumps({"kind": "tool_result", "payload": {"call_id": "a", "output": "x" * 3000}})
    request = JudgeRequest(("criterion",), (event,), ())
    bindings = [{"index": 0, "path": "observation/0", "criteria": [0]}]
    _, one = budget.fit_package(
        request,
        [],
        context=budget.DiskPackageContext(tmp_path / "claude", "private", "claude"),
        observation_bindings=bindings,
        opaque_indices=(),
    )
    _, two = budget.fit_package(
        request,
        [],
        context=budget.DiskPackageContext(tmp_path / "codex", "private", "codex"),
        observation_bindings=bindings,
        opaque_indices=(),
    )
    assert one["diskEvidence"]["rows"][0]["inline"] is False
    assert one["diskEvidence"]["rows"][0]["routeReason"] == "over-action-threshold"
    assert one["diskEvidence"]["rows"][0]["inline"] == two["diskEvidence"]["rows"][0]["inline"]


@pytest.mark.parametrize("field", ["files", "manifestPath"])
def test_loader_checks_compact_file_list_against_full_folder(tmp_path, field):
    request, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), ("evidence",)),
        [{"index": 0, "path": "after/card", "mandatory": []}],
    )
    facing = json.loads(request.artifact_evidence[-1])
    if field == "files":
        facing["files"][0]["sizeBytes"] += 1
    else:
        facing["manifestPath"] = "other.json"
    text = json.dumps(facing)
    changed = replace(request, artifact_evidence=(*request.artifact_evidence[:-1], text))
    disk = manifest["diskEvidence"]
    disk["inlineSha256"] = sha(changed.prompt())
    disk["packageIdentity"] = budget._identity(
        disk["inlineSha256"],
        disk["manifestSha256"],
        disk["accessContract"],
        "claude",
        disk["appliedLimits"],
    )
    manifest["packagedRequestPromptSha256"] = disk["inlineSha256"]
    manifest["judgeFacingManifestSha256"] = sha(text)
    (tmp_path / "judge-evidence-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="embedded compact"):
        budget.load_judge_disk_evidence(tmp_path, changed, "claude")


def test_folder_manifest_supports_narrow_one_row_search(tmp_path):
    _, manifest = fitted(
        tmp_path,
        JudgeRequest(("criterion",), (), ("first", "second")),
        [
            {"index": 0, "path": "after/named-card.json", "mandatory": ["changed-memory-carriers"]},
            {"index": 1, "path": "after/unrelated.json", "mandatory": []},
        ],
    )
    disk = manifest["diskEvidence"]
    lines = Path(disk["manifestPath"]).read_text().splitlines()
    matches = [line for line in lines if "after/named-card.json" in line]
    assert len(matches) == 1
    row = json.loads(matches[0].rstrip(","))
    assert row["path"] == "artifacts/0000.txt"
    assert row["mustRead"] is True
    assert "after/unrelated.json" not in matches[0]


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_d643_ordinary_tool_grant_is_bound_without_allowlists(tmp_path, kind):
    request, manifest = budget.fit_package(
        JudgeRequest(("criterion",), (), ()),
        [],
        context=budget.DiskPackageContext(tmp_path, "private", kind),
        observation_bindings=[],
        opaque_indices=(),
    )
    (tmp_path / "judge-evidence-manifest.json").write_text(json.dumps(manifest))
    binding = budget.load_judge_disk_evidence(tmp_path, request, kind)
    grant = "ordinary agent, workspace = evidence folder, shell included, no AR server"
    assert binding.tool_grant == grant
    assert manifest["diskEvidence"]["toolGrant"] == grant
    assert manifest["diskEvidence"]["accessContract"]["toolGrant"] == grant
    assert not hasattr(binding, "allowed_tools")
    assert "allowedTools" not in json.dumps(manifest)
    assert "allowed_tools" not in json.dumps(manifest)
    assert manifest["diskEvidence"]["accessContract"]["mcpServers"] == []
    if kind == "codex":
        assert manifest["diskEvidence"]["accessContract"]["sandbox"] == "workspace-write"


def test_d643_loader_rejects_stale_grant_and_allowlist_metadata(tmp_path):
    request, manifest = fitted(tmp_path, JudgeRequest(("criterion",), (), ()), [])
    path = tmp_path / "judge-evidence-manifest.json"
    disk = manifest["diskEvidence"]
    disk["toolGrant"] = "read only"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="grant"):
        budget.load_judge_disk_evidence(tmp_path, request, "claude")
    disk["toolGrant"] = disk["accessContract"]["toolGrant"]
    disk["allowedTools"] = ["Read", "Grep", "Glob"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="binding fields"):
        budget.load_judge_disk_evidence(tmp_path, request, "claude")


def test_refused_write_reference_is_current_exact_bound_and_immutable(tmp_path):
    request = JudgeRequest(("existing criterion",), (), ())
    result, manifest = fitted(tmp_path, request, [])
    root = Path(manifest["diskEvidence"]["root"])
    reference = root / "reference/refused-write.md"
    retained = Path(budget.__file__).parent / "reference_epochs/g1-20261030/refused-write.md"
    assert reference.read_bytes() == retained.read_bytes()
    row = next(
        row
        for row in manifest["diskEvidence"]["rows"]
        if row["path"] == "reference/refused-write.md"
    )
    assert (
        row["sha256"]
        == sha(retained.read_text())
        == budget.COVERED_WRITE_POLICY["sourceBinding"]["sourceSha256"]
    )
    assert row["kind"] == "reference" and row["inline"] is False
    assert row["criteria"] == [] and row["mustRead"] is False
    epoch = manifest["coveredWritePolicy"]["referenceEpoch"]
    assert epoch["kind"] == "EVIDENCE COPY" and epoch["epoch"] == "g1-20261030"
    assert epoch["sourceSizeBytes"] == len(reference.read_bytes()) == 2760
    assert epoch["sourceSha256"] == row["sha256"]
    assert epoch["runtimeReadFrom"].endswith("reference_epochs/g1-20261030/refused-write.md")
    assert manifest["coveredWritePolicy"] == budget.covered_write_policy_trace()
    assert "reference/refused-write.md" in result.artifact_evidence[-1]
    budget.load_judge_disk_evidence(tmp_path, result, "claude")
    reference.write_text(reference.read_text() + "changed")
    with pytest.raises(ValueError, match="file bytes changed"):
        budget.load_judge_disk_evidence(tmp_path, result, "claude")


def test_fixed_grading_epoch_ignores_live_text_and_rejects_retained_mutation(tmp_path, monkeypatch):
    original = Path(budget.__file__)
    copied = tmp_path / "copy/scripts/agent_evals/package_budget.py"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(original.read_bytes())
    epoch = copied.parent / "reference_epochs/g1-20261030"
    epoch.mkdir(parents=True)
    for name in ("epoch.json", "refused-write.md"):
        (epoch / name).write_bytes(
            (original.parent / "reference_epochs/g1-20261030" / name).read_bytes()
        )
    live = copied.parents[2] / budget.COVERED_WRITE_POLICY["sourceBinding"]["sourcePath"]
    live.parent.mkdir(parents=True)
    live.write_bytes((epoch / "refused-write.md").read_bytes())
    monkeypatch.setattr(budget, "__file__", str(copied))
    request = JudgeRequest(("existing criterion",), (), ())
    before, first = fitted(tmp_path / "before", request, [])
    live.write_text(
        live.read_text() + "\nLive instruction revision independent of retained grading.\n"
    )
    after, second = fitted(tmp_path / "after", request, [])
    assert before.prompt() == after.prompt()
    assert first["diskEvidence"]["packageIdentity"] == second["diskEvidence"]["packageIdentity"]
    assert first["coveredWritePolicy"] == second["coveredWritePolicy"]
    for path in (epoch / "refused-write.md", epoch / "epoch.json"):
        original_bytes = path.read_bytes()
        path.write_bytes(original_bytes + b"changed")
        with pytest.raises(ValueError, match="retained grading reference"):
            fitted(tmp_path / path.name, request, [])
        with pytest.raises(ValueError, match="retained grading reference"):
            budget.covered_write_policy_trace()
        path.write_bytes(original_bytes)


@pytest.mark.parametrize("actor_harness", ["codex", "hermes"])
def test_binary_source_ingestion_is_reference_only_and_utf8_text_stays_exact(
    tmp_path, actor_harness
):
    payloads = {
        "valid.txt": "unchanged UTF-8: é\n".encode(),
        "nul-is-valid.txt": b"valid UTF-8\0text",
        "undecodable.md": b"\xea",  # No NUL, and a text-looking extension.
        "utf16.arbitrary": b"\xff\xfeA\0",
        "compressed.txt": gzip.compress(b"not a document"),
        "sqlite.data": b"SQLite format 3\0" + b"\0" * 15 + b"\xea",
    }
    originals = []
    for name, raw in payloads.items():
        path = tmp_path / "after" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        originals.append(EvidenceFile.capture(tmp_path, "after/" + name))
    grade = {
        "deterministicChecks": ["unchanged deterministic"],
        "semanticRubric": ["unchanged semantic"],
    }
    case = CaseBundle(tmp_path, "CE02", {"specification": {"prospectiveGrade": grade}}, ())
    inputs = CaseEvidence(case, {}, frozenset(), tmp_path, tuple(originals))
    request = case_judge_request(inputs, ())
    assert request.rubric == ("unchanged deterministic", "unchanged semantic")
    for source, text in zip(originals, request.artifact_evidence, strict=True):
        raw = payloads[source.path.removeprefix("after/")]
        try:
            expected = raw.decode("utf-8")
        except UnicodeDecodeError:
            assert json.loads(text) == {
                "kind": "binary-artifact",
                "binary": True,
                "path": source.path,
                "sizeBytes": len(raw),
                "sha256": source.sha256,
            }
        else:
            assert text == expected
    report = {
        "before": [],
        "after": [
            {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in payloads.items()
        ],
    }
    delta = _effect_diff(report, tmp_path)
    assert "+unchanged UTF-8: é\n" in delta["fullUnifiedDiff"]
    assert all(row["diffBytes"] == 0 for row in delta["files"] if row["path"] == "undecodable.md")
    capture = NativeCapture(actor_harness, "constructed-source-ingestion", (), (), (), ())
    package = package_judge_evidence(
        request,
        capture,
        tuple(originals),
        inputs=JudgePackageInputs(
            delta,
            EvidenceSelection("CE02", {}, {}, {}, {}),
            tmp_path,
            "constructed",
            CODEX_ACTOR if actor_harness == "codex" else HERMES_ACTOR,
        ),
    )
    (tmp_path / "judge-evidence-manifest.json").write_text(json.dumps(package.manifest))
    kind = "claude" if actor_harness == "codex" else "codex"
    binding = budget.load_judge_disk_evidence(tmp_path, package.request, kind)
    for index, (source, text) in enumerate(zip(originals, request.artifact_evidence, strict=True)):
        row = next(row for row in binding.rows if row.get("artifactIndex") == index + 1)
        assert row["originalFileSha256"] == source.sha256
        assert (binding.root / row["path"]).read_bytes() == text.encode()
        assert (tmp_path / source.path).read_bytes() == payloads[source.path.removeprefix("after/")]
    _assert_constructed_binary_dependent_unknown(package.request, binding, actor_harness)
    _assert_typed_report_preservation(tmp_path / "typed-preparation", actor_harness)


def _assert_constructed_binary_dependent_unknown(request, binding, actor_harness):
    """Mechanical verdict control, not a model's judgment of artifact dependence."""
    row = next(row for row in binding.rows if row["label"] == "after/undecodable.md")
    reference = json.loads((binding.root / row["path"]).read_text())
    assert row["kind"] == "artifact" and reference["binary"] is True
    reason = "Cannot read binary artifact " + row["path"] + "; only its identity is available."
    verdict = {
        "criteria": [
            {
                "criterion": request.rubric[0],
                "outcome": "pass",
                "evidence": "Independent text is present.",
            },
            {"criterion": request.rubric[1], "outcome": "unknown", "evidence": reason},
        ]
    }
    text = json.dumps(verdict)
    digest = sha(text)
    event = NativeEvent(
        "message",
        "constructed-verdict",
        0,
        {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "model": judge_profile(CODEX_ACTOR if actor_harness == "codex" else HERMES_ACTOR).model,
        },
        "constructed-verdict.json",
        digest,
    )
    capture = NativeCapture(
        binding.judge_harness,
        "constructed-verdict",
        (event,),
        (SourceFile(event.source, digest),),
        ({"constructedMechanicalVerdict": verdict},),
        (),
    )
    actor = CODEX_ACTOR if actor_harness == "codex" else HERMES_ACTOR
    result = resolve_judge_outcome(
        Grade("constructed-code-clean", "needs_rubric"),
        RubricSubmission(request, actor.model, capture),
    )
    assert result == "semantic_unknown"
    decoded, path = parse_native_verdict(capture)
    assert path == "raw_json" and decoded == verdict
    assert decoded["criteria"][1]["evidence"] == reason


def _assert_typed_report_preservation(root, actor_harness):
    """Preserve the existing typed reader's three states; this is not a new repair."""
    path = "reports/checks/memory-quality-full.json"
    target = root / "after" / path
    target.parent.mkdir(parents=True)
    parsed = {"findings": [], "ok": True}
    target.write_text(json.dumps(parsed))
    source = EvidenceFile.capture(root / "after", path)
    report: dict = {"before": [], "after": [source.to_dict()]}
    assert _read_report(report, root, "after", path) == parsed
    assert _read_report({"after": []}, root, "after", path) is None
    raw = b"\xea"
    target.write_bytes(raw)
    source = EvidenceFile.capture(root / "after", path)
    report.update(
        after=[source.to_dict()],
        executionPhase="grading",
        unknowns=[],
        case="CE02",
        identity="constructed-typed-report",
    )
    with pytest.raises(UnicodeDecodeError) as unreadable:
        _read_report(report, root, "after", path)
    manifest = {
        "setup": {"prompt": "prompt.txt", "authority": "authority.json", "queue": "queue.json"},
        "actorInputs": [],
        "allowedEffects": ["reports/**"],
        "specification": {
            "prospectiveGrade": {
                "deterministicChecks": ["unchanged deterministic"],
                "semanticRubric": ["unchanged semantic"],
            }
        },
    }
    case = CaseBundle(root, "CE02", manifest, ())
    capture = NativeCapture(actor_harness, "constructed-typed-report", (), (), (), ())

    def routed_report(_case, _capture, actual_report, actual_root, **_kwargs):
        return _read_report(actual_report, actual_root, "after", path)

    # Admission and route selection are unrelated to the typed reader/exception boundary.
    clean = Grade.produced(
        case_judge_request.__code__.co_filename, "constructed-code", "needs_rubric"
    )
    with (
        patch.object(trial_orchestration, "grade_case_code", return_value=clean),
        patch.object(
            trial_orchestration, "select_case_evidence", side_effect=routed_report
        ) as selected,
    ):
        prepared = trial_orchestration.prepare_case_grade(
            case,
            report,
            root,
            capture,
            context=trial_orchestration.GradePreparationContext(
                lambda text: text, CODEX_ACTOR if actor_harness == "codex" else HERMES_ACTOR
            ),
        )
    assert selected.call_count == 1 and prepared.code.status == "needs_rubric"
    assert prepared.request is None
    assert report["unknown_class"] == "schema-incompatible"
    assert "UnicodeDecodeError" in report["unknown_cause"]
    assert report["unknowns"] == [report["unknown_cause"]]
    stopped = json.loads((root / "evidence-selection.json").read_text())
    assert stopped == {"status": "unknown", "reason": str(unreadable.value)}
    assert stopped != parsed and stopped is not None and stopped.get("kind") != "binary-artifact"
    proof = {
        "basis": "Preservation control using actual typed reader and preparation classifier; unrelated admission/route policy mocked",
        "states": [
            {"state": "parsed", "value": parsed},
            {"state": "absent", "value": None},
            {
                "state": "unreadable",
                "reason": str(unreadable.value),
                "path": path,
                "sha256": source.sha256,
                "sizeBytes": len(raw),
            },
        ],
        "preparationCause": report["unknown_cause"],
    }
    (root / "typed-report-preservation.json").write_text(json.dumps(proof, indent=2) + "\n")
