"""The four sealed L41 baseline failures through public owners and bounded fault timing."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import review_comparison_freeze as freeze_owner
from agents_remember.application.review_comparison_freeze import ComparisonFreezeOptions
from agents_remember.application.review_comparison_generation import read_generation_refs
from agents_remember.application.review_evidence_records import review_records_for
from agents_remember.application.review_record_rendering import EMPTY_REVIEW_RECORDS
from agents_remember.cli.__main__ import main
from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRecord,
    CuratorCoherenceRequest,
    CuratorSourceCandidate,
)
from agents_remember.models.lifecycles.review_assessment import AssessmentEvidenceReference
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration.closeout import (
    curator_coherence_publication as publication,
)
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    curator_coherence_paths,
    load_curator_coherence_generation,
    require_current_curator_coherence,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_judgments import (
    read_judgment_evidence,
)
from curator_coherence_test_support import write_curator_evidence
from pydantic import ValidationError
from test_knowledge_review_evidence_channels import (
    EVIDENCE_RELATIVE,
    _assessment_revision,
    build_recorded_fixture,
    review_through_port,
)
from test_knowledge_review_source_endpoints import MODIFIED_PATH
from test_review_assessment_history import _argv, _historical, _publish_two, _record

pytestmark = pytest.mark.evidence_unit


def _nonempty_fixture(path: Path):
    recorded = build_recorded_fixture(path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=[
            {
                "sourceFile": MODIFIED_PATH,
                "onboardingFile": f"onboarding/{MODIFIED_PATH}.md",
                "classification": "source-change",
            }
        ],
    )
    return recorded


def _request(recorded, label: str, judgment_ref: str, assessment_ref=None):
    contract = recorded.contract
    prepared = publication.curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="prepare", contract_path=str(contract.contract_path)),
    )
    candidates = prepared["candidates"]
    assert isinstance(candidates, list) and candidates
    revision = _assessment_revision(recorded.endpoint).model_copy(update={"assessmentId": label})
    if assessment_ref is not None:
        revision = revision.model_copy(update={"evidenceRefs": (assessment_ref,)})
    return CuratorCoherenceRequest(
        action="publish",
        contract_path=str(contract.contract_path),
        semantic_requirement_revision="ICR-R11@v1",
        delivery_attempt=label,
        judgments=[
            CuratorCoherenceJudgment(
                **CuratorSourceCandidate.model_validate(value).model_dump(),
                disposition="reconciled",
                rationale="The original cited bytes support this exact fixture judgment.",
                evidenceRef=judgment_ref,
            )
            for value in candidates
        ],
        review_assessments=[revision],
        expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
        expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
        expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
        expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
        expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
        expected_attestation_sha256=str(prepared["attestationSha256"]),
        caller=DeclaredCaller(role="architect", task_document_ref=recorded.sprint),
    )


@pytest.mark.parametrize("namespace", ["code", "memory", "task"])
def test_each_admitted_judgment_namespace_keeps_its_authored_citation_after_cleanup(
    tmp_path: Path, capsys, namespace
):
    recorded = _nonempty_fixture(tmp_path / namespace)
    relative = {"code": MODIFIED_PATH, "memory": "memory.md", "task": EVIDENCE_RELATIVE}[namespace]
    root = {
        "code": recorded.contract.code_worktree,
        "memory": recorded.contract.memory_worktree,
        "task": recorded.contract.task_root,
    }[namespace]
    assert root is not None
    source = root / relative
    original_bytes = source.read_bytes()
    authored_ref = f"{namespace}:{relative}"
    request = _request(recorded, f"namespace-{namespace}", authored_ref)
    published = publication.curator_coherence_action(recorded.contract, request)
    assert published["state"] == "published", published
    current = require_current_curator_coherence(recorded.contract)
    judgment = current.record.judgments[0]
    assert judgment.evidenceRef == authored_ref and judgment.evidenceArtifact is not None
    assert judgment.evidenceArtifact.sha256 == judgment.evidenceSha256
    retained = recorded.contract.task_root / judgment.evidenceArtifact.path
    assert retained.read_bytes() == original_bytes
    with pytest.raises(ValidationError):
        CuratorCoherenceJudgment.model_validate(
            {
                **request.judgments[0].model_dump(),
                "evidenceArtifact": judgment.evidenceArtifact.model_dump(),
            }
        )
    source.write_bytes(original_bytes + b"\nchanged after publication\n")
    with pytest.raises(CuratorCoherenceError):
        require_current_curator_coherence(recorded.contract)
    source.write_bytes(original_bytes)
    live = review_through_port(recorded.endpoint, recorded.endpoint.request()).payload
    assert live is not None and live.evidence.assessments
    _, manifest = _record(recorded.endpoint, capsys)
    assert manifest.records.assessments == 1
    shutil.rmtree(recorded.contract.worktree_group)
    durable = load_curator_coherence_generation(recorded.contract, current.record_digest)
    assert durable.record == current.record
    assert retained.read_bytes() == original_bytes
    legacy = judgment.model_copy(update={"evidenceArtifact": None})
    assert "evidenceArtifact" not in legacy.model_dump()
    if namespace == "task":
        assert read_judgment_evidence(recorded.contract, legacy).sha256 == judgment.evidenceSha256
    else:
        with pytest.raises(CuratorCoherenceError) as missing_custody:
            read_judgment_evidence(recorded.contract, legacy)
        assert missing_custody.value.status == "curator-coherence-judgment-not-retained"
    inputs = review_records_for(recorded.endpoint.config, _historical(recorded.endpoint))
    assert inputs.assessments == tuple(current.record.assessments)
    assert (
        next(channel for channel in inputs.channels if channel.records == "assessments").state
        == "recorded"
    )


def test_confined_assessment_alias_uses_the_recorded_destination_after_alias_removal(
    tmp_path: Path, capsys
):
    recorded = _nonempty_fixture(tmp_path / "alias")
    alias_ref = "notes/reports/alias.md"
    alias = recorded.contract.task_root / alias_ref
    alias.symlink_to(recorded.contract.task_root / EVIDENCE_RELATIVE)
    request = _request(
        recorded,
        "confined-alias",
        f"task:{EVIDENCE_RELATIVE}",
        AssessmentEvidenceReference(namespace="task", ref=alias_ref),
    )
    assert publication.curator_coherence_action(recorded.contract, request)["state"] == "published"
    original = require_current_curator_coherence(recorded.contract)
    assessment = original.record.assessments[0]
    assert assessment.evidenceRefs[0].ref == alias_ref
    paths = {
        edge.name
        for edge in assessment.examinedInputs.declaration.edges
        if edge.kind == "evidence-bytes"
    }
    assert "notes/reports/evidence/confined-alias/icr-l14-evidence.md" in paths
    _record(recorded.endpoint, capsys)
    alias.unlink()
    shutil.rmtree(recorded.contract.worktree_group)
    inputs = review_records_for(recorded.endpoint.config, _historical(recorded.endpoint))
    assert inputs.assessments == tuple(original.record.assessments)


@pytest.mark.parametrize("invalid", ["escaping-alias", "corrupt-retained-byte"])
def test_integrity_rejection_precedes_canonical_authority_replacement(
    tmp_path: Path, monkeypatch, invalid
):
    recorded = _nonempty_fixture(tmp_path / invalid)
    canonical = curator_coherence_paths(recorded.contract).canonical
    before = canonical.read_bytes()
    reference = AssessmentEvidenceReference(namespace="task", ref=EVIDENCE_RELATIVE)
    if invalid == "escaping-alias":
        outside = tmp_path / "foreign.md"
        outside.write_text("outside the admitted task namespace")
        alias = recorded.contract.task_root / "notes/reports/outside.md"
        alias.symlink_to(outside)
        reference = AssessmentEvidenceReference(namespace="task", ref="notes/reports/outside.md")
    else:
        real = publication._publish_generation

        def corrupt_before_validation(record_path, report_path, record_bytes, report_bytes):
            real(record_path, report_path, record_bytes, report_bytes)
            row = CuratorCoherenceRecord.model_validate_json(record_bytes).assessments[0]
            path = next(
                edge.name
                for edge in row.examinedInputs.declaration.edges
                if edge.kind == "evidence-bytes" and edge.name.startswith("notes/reports/evidence/")
            )
            (recorded.contract.task_root / path).write_bytes(b"corrupt published evidence")

        monkeypatch.setattr(publication, "_publish_generation", corrupt_before_validation)
    request = _request(recorded, f"rejected-{invalid}", f"task:{EVIDENCE_RELATIVE}", reference)
    with pytest.raises(CuratorCoherenceError):
        publication.curator_coherence_action(recorded.contract, request)
    assert canonical.read_bytes() == before
    assert require_current_curator_coherence(recorded.contract).authority.currentRecordDigest


@pytest.mark.parametrize(
    "provenance", ["omitted", "wrong-owner", "wrong-state", "duplicate", "complete-no-currentness"]
)
def test_supplied_owner_inputs_have_complete_provenance_or_refuse_before_publication(
    tmp_path: Path, provenance
):
    recorded = build_recorded_fixture(tmp_path / provenance)
    supplied = recorded.records()
    channel = next(row for row in supplied.channels if row.records == "assessments")
    others = tuple(row for row in supplied.channels if row.records != "assessments")
    selected = {
        "omitted": (),
        "wrong-owner": (channel.model_copy(update={"owner": "another-owner"}),),
        "wrong-state": (channel.model_copy(update={"state": "none_recorded", "record_count": 0}),),
        "duplicate": (channel, channel),
        "complete-no-currentness": (channel,),
    }[provenance]
    inputs = replace(supplied, channels=(*others, *selected), currentness=None)
    result = freeze_owner.freeze_review_comparison(
        recorded.endpoint.config,
        recorded.endpoint.task_request(),
        ComparisonFreezeOptions(records=inputs),
    )
    refs = read_generation_refs(recorded.contract.task_root, recorded.contract.leaf_id)
    if provenance == "complete-no-currentness":
        assert result.manifest is not None and result.state == "published"
        assert not result.manifest.records.current_measured
        shutil.rmtree(recorded.contract.worktree_group)
        assert (
            review_records_for(recorded.endpoint.config, _historical(recorded.endpoint)).assessments
            == supplied.assessments
        )
    else:
        assert result.state == "refused" and result.refusal is not None
        assert "channel" in result.refusal.detail
        assert not refs


@pytest.mark.parametrize("side", ["before", "after"])
def test_recovery_losing_an_expected_snapshot_before_copy_refuses_and_reclaims(
    tmp_path: Path, monkeypatch, capsys, side
):
    recorded = build_recorded_fixture(tmp_path / side)
    original, _ = _publish_two(recorded)
    parent = freeze_owner.freeze_review_comparison(
        recorded.endpoint.config,
        recorded.endpoint.task_request(),
        ComparisonFreezeOptions(records=EMPTY_REVIEW_RECORDS),
    )
    assert parent.manifest is not None and parent.directory is not None
    manifest_bytes = (parent.directory / "manifest.json").read_bytes()
    snapshots = {
        parent.directory / binding.artifact.relative_path: (
            parent.directory / binding.artifact.relative_path
        ).read_bytes()
        for binding in parent.manifest.knowledge
        if binding.artifact is not None
    }
    git = [
        "git",
        "-C",
        str(recorded.contract.code_repo_path),
        "for-each-ref",
        "--format=%(refname):%(objectname)",
    ]
    original_refs = subprocess.check_output(git)
    real = freeze_owner.retain_knowledge_sides
    missing = []

    def lose_before_copy(request, stage):
        path = (
            request.resolution.baseline_database
            if side == "before"
            else request.resolution.candidate_database
        )
        content = path.read_bytes()
        missing.append(path)
        try:
            path.unlink()
            return real(request, stage)
        finally:
            path.write_bytes(content)

    monkeypatch.setattr(freeze_owner, "retain_knowledge_sides", lose_before_copy)
    args = _argv(
        recorded.endpoint,
        "--recover-generation",
        parent.manifest.generation_id,
        "--curator-record-digest",
        original.record_digest,
    )
    capsys.readouterr()
    assert main(args) == 2
    assert f"expected retained {side} snapshot" in capsys.readouterr().out
    assert (
        missing
        and len(read_generation_refs(recorded.contract.task_root, recorded.contract.leaf_id)) == 1
    )
    assert (parent.directory / "manifest.json").read_bytes() == manifest_bytes
    assert all(path.read_bytes() == content for path, content in snapshots.items())
    assert not list(parent.directory.parent.glob(".*.stage"))
    assert subprocess.check_output(git) == original_refs
