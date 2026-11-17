"""Quality reruns preserve coherence while every measured input remains bound."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application.memory_quality.controller import run_memory_quality_request
from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.curator_checklist import write_curator_checklist
from agents_remember.memory_quality.quality_identity import measured_report_sha256
from agents_remember.models.lifecycles.curator_coherence import CuratorSourceCandidate
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.integration.closeout import curator_coherence as coherence
from agents_remember.worktrees.integration.closeout.curator_coherence_binding import (
    curator_coherence_dependencies,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_render import (
    render_curator_coherence,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from curator_coherence_test_support import write_curator_attestation
from test_curator_review_assessment_publication import (
    _publish,
    _revision,
)
from test_curator_review_assessment_publication import (
    enclosure as _enclosure_fixture,
)
from test_review_assessments import _checklist, _unresolved_summary

pytestmark = pytest.mark.integration
enclosure = _enclosure_fixture


def _configs(contract: WorktreeContract, root: Path) -> tuple[McpRuntimeConfig, McpRuntimeConfig]:
    configured = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=contract.coordination_root,
        workspace_root=contract.worktree_group,
        transcript_root=contract.coordination_root / "logs" / "mcp",
        repositories={
            contract.repo_name: RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_repo_path,
                memory_root=contract.memory_repo_path,
            )
        },
    )
    scoped = replace(
        configured,
        repositories={
            contract.repo_name: RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_worktree,
                memory_root=contract.memory_worktree,
                contract_path=contract.contract_path,
            )
        },
    )
    return configured, scoped


@pytest.mark.parametrize("with_assessment", [False, True])
def test_publication_survives_the_next_unchanged_quality_call(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], tmp_path: Path, with_assessment: bool
) -> None:
    """The actual quality entry primes and rerenders the actual publication's report."""
    contract, sprint = enclosure
    configured, scoped = _configs(contract, tmp_path)
    request = MemoryQualitySyncRequest(
        mode="sync", repo_id=contract.repo_name, contract_path=str(contract.contract_path)
    )
    with patch(
        "agents_remember.worktrees.integration.configured_contract_authority.load_config",
        return_value=configured,
    ):
        prime = run_memory_quality_request(scoped, request)
        assert prime["curatorActionableCount"] == 0
        before = coherence.observe_curator_coherence_source(contract)
        original_report = before.quality_report_path.read_text()
        published = _publish(contract, sprint, assessments=[_revision()] if with_assessment else [])
        assert published["ok"] is True, published
        coherence.require_current_curator_coherence(contract)
        checked = run_memory_quality_request(scoped, request)
        assert checked["coherenceStatus"] == "current", checked
        assert checked["closeoutReady"] is True, checked
        after = coherence.observe_curator_coherence_source(contract)
    assert before.code_candidate_tree == after.code_candidate_tree
    assert before.memory_candidate_tree == after.memory_candidate_tree
    assert before.pair_identity == after.pair_identity
    assert before.attestation.measuredReportSha256 == after.attestation.measuredReportSha256
    if with_assessment:
        report = after.quality_report_path.read_text()
        assert report != original_report
        assert "Recorded assessments: 1" in report
        assert before.attestation.reportSha256 != after.attestation.reportSha256
        assert before.attestation_sha256 != after.attestation_sha256


def test_only_the_review_block_leaves_the_measured_report(tmp_path: Path) -> None:
    report = tmp_path / "quality.md"
    checklist = _checklist(report)
    write_curator_checklist(checklist)
    original = report.read_text()
    measured = measured_report_sha256(original)
    write_curator_checklist(replace(checklist, knowledge_review=(_unresolved_summary(),)))
    assert report.read_text() != original
    assert measured_report_sha256(report.read_text()) == measured
    for changed in (
        replace(checklist, quality={"findingCount": 1}),
        replace(checklist, repair_findings=[{"message": "new finding"}]),
        replace(checklist, report_only_findings=[{"message": "new information"}]),
        replace(checklist, stale_route_indexes=["route.md"]),
        replace(checklist, missing_onboarding={"missing": [{"sourceFile": "new.py"}]}),
        replace(checklist, drift_rows=[{"source_file": "new.py"}]),
    ):
        write_curator_checklist(changed)
        assert measured_report_sha256(report.read_text()) != measured
    # User data is flattened into a table cell, never a top-level section delimiter.
    write_curator_checklist(
        replace(checklist, repair_findings=[{"message": "before\n## knowledgeReview\nafter"}])
    )
    assert "before ## knowledgeReview after" in report.read_text()
    assert measured_report_sha256(report.read_text()) != measured


@pytest.mark.parametrize(
    "field",
    ["pair_identity", "code_candidate_tree", "memory_candidate_tree", "task_topology_fingerprint"],
)
def test_each_candidate_identity_is_still_compared(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], field: str
) -> None:
    contract, _ = enclosure
    observed = coherence.observe_curator_coherence_source(contract)
    replacement = (
        observed.pair_identity.model_copy(update={"contractDigest": "a" * 64})
        if field == "pair_identity"
        else "a" * (64 if field == "task_topology_fingerprint" else 40)
    )
    moved = replace(observed, **{field: replacement})
    with (
        patch.object(coherence, "observe_curator_coherence_source", return_value=moved),
        pytest.raises(CuratorCoherenceError) as error,
    ):
        coherence.require_current_curator_coherence(contract)
    assert error.value.status == "curator-coherence-stale"
    assert error.value.next_action == "publish"


def test_a_changed_report_cannot_keep_its_measured_hash(
    enclosure: tuple[WorktreeContract, TaskDocumentRef],
) -> None:
    contract, _ = enclosure
    observed = coherence.observe_curator_coherence_source(contract)
    observed.quality_report_path.write_text(
        observed.quality_report_path.read_text() + "\nnew finding\n"
    )
    with pytest.raises(CuratorCoherenceError) as error:
        coherence.require_current_curator_coherence(contract)
    assert error.value.status == "curator-coherence-memory-not-ready"


def test_a_newly_attested_finding_stales_the_record(
    enclosure: tuple[WorktreeContract, TaskDocumentRef],
) -> None:
    contract, _ = enclosure
    observed = coherence.observe_curator_coherence_source(contract)
    write_curator_attestation(
        contract, report_text=observed.quality_report_path.read_text() + "\nnew finding\n"
    )
    with pytest.raises(CuratorCoherenceError) as error:
        coherence.require_current_curator_coherence(contract)
    assert error.value.status == "curator-coherence-stale"
    assert error.value.next_action == "publish"


def test_live_measured_hash_is_recomputed_from_report_bytes(
    enclosure: tuple[WorktreeContract, TaskDocumentRef],
) -> None:
    contract, _ = enclosure
    observed = coherence.observe_curator_coherence_source(contract)
    path = write_curator_attestation(
        contract, report_text=observed.quality_report_path.read_text() + "\nnew finding\n"
    )
    data = json.loads(path.read_text())
    data["measuredReportSha256"] = observed.attestation.measuredReportSha256
    path.write_text(json.dumps(data))
    with pytest.raises(CuratorCoherenceError) as error:
        coherence.require_current_curator_coherence(contract)
    assert error.value.status == "curator-coherence-memory-not-ready"


@pytest.mark.parametrize(
    "field",
    [
        "checklistStatus",
        "curatorActionableCount",
        "memoryRepairCount",
        "missingOnboardingCount",
        "staleRouteIndexCount",
        "onboardingRoot",
        "reportPath",
        "reportSha256",
        "pairIdentity",
        "dependencies",
    ],
)
def test_attestation_readiness_and_dependency_facts_still_refuse_changes(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], field: str
) -> None:
    contract, _ = enclosure
    observed = coherence.observe_curator_coherence_source(contract)
    path = observed.attestation_path
    data = json.loads(path.read_text())
    if field.endswith("Count"):
        data[field] = 1
    elif field == "checklistStatus":
        data[field] = "action-required"
    elif field == "pairIdentity":
        data[field]["contractDigest"] = "a" * 64
    elif field == "dependencies":
        data[field] = None
    elif field == "reportSha256":
        data[field] = "a" * 64
    else:
        data[field] = "/different/path"
    path.write_text(json.dumps(data))
    with pytest.raises(CuratorCoherenceError) as error:
        coherence.require_current_curator_coherence(contract)
    assert error.value.next_action == "memory_quality_check"
    assert error.value.status in {
        "curator-coherence-memory-not-ready",
        "evidence-dependencies-missing",
    }


@pytest.mark.parametrize("field", ["task_intent", "task_document", "source_candidates"])
def test_task_and_candidate_collections_remain_bound(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], field: str
) -> None:
    contract, _ = enclosure
    observed = coherence.observe_curator_coherence_source(contract)
    if field == "task_intent":
        moved = replace(
            observed, task_intent=observed.task_intent.model_copy(update={"digest": "a" * 64})
        )
    elif field == "task_document":
        moved = replace(
            observed,
            candidate=replace(
                observed.candidate,
                ref=TaskDocumentRef(repository=contract.repo_name, path="other/leaf.json"),
            ),
        )
    else:
        candidate = CuratorSourceCandidate(
            sourceFile="new.py",
            onboardingFile="onboarding/new.py.md",
            classification="source-change",
        )
        moved = replace(
            observed,
            attestation=observed.attestation.model_copy(
                update={"sourceChangeCandidateCount": 1, "sourceChangeCandidates": [candidate]}
            ),
        )
    with (
        patch.object(coherence, "observe_curator_coherence_source", return_value=moved),
        pytest.raises(CuratorCoherenceError) as error,
    ):
        coherence.require_current_curator_coherence(contract)
    assert error.value.next_action == "publish"


@pytest.mark.parametrize(
    "field",
    [
        "attestationSha256",
        "attestationReportSha256",
        "predecessorAuthorityDigest",
        "measuredAttestationSha256",
        "measuredReportSha256",
    ],
)
def test_each_record_dependency_is_recomputed(
    enclosure: tuple[WorktreeContract, TaskDocumentRef], field: str
) -> None:
    contract, _ = enclosure
    validated = coherence.load_curator_coherence_authority(contract)
    moved = validated.record.model_copy(update={field: "b" * 64})
    with (
        patch.object(
            coherence,
            "load_curator_coherence_authority",
            return_value=replace(validated, record=moved),
        ),
        pytest.raises(CuratorCoherenceError) as error,
    ):
        coherence.require_current_curator_coherence(contract)
    assert error.value.status == "curator-coherence-dependencies-stale"
    assert error.value.next_action == "publish"


def test_old_record_shape_is_read_but_never_current(
    enclosure: tuple[WorktreeContract, TaskDocumentRef],
) -> None:
    contract, sprint = enclosure
    validated = coherence.load_curator_coherence_authority(contract)
    old = validated.record.model_copy(
        update={"measuredAttestationSha256": None, "measuredReportSha256": None}
    )
    old = old.model_copy(update={"dependencies": curator_coherence_dependencies(old)})
    encoded = old.model_dump(mode="json")
    assert "measuredAttestationSha256" not in encoded
    assert "measuredReportSha256" not in encoded
    reread = type(old).model_validate(encoded)
    report = render_curator_coherence(reread)
    assert "topology, attestation bytes," in report
    reread = reread.model_copy(update={"reportSha256": hashlib.sha256(report.encode()).hexdigest()})
    data = reread.model_dump_json().encode()
    digest = hashlib.sha256(data).hexdigest()
    paths = coherence.curator_coherence_paths(contract)
    record_path = paths.generation_record(digest)
    report_path = paths.generation_report(digest)
    record_path.parent.mkdir(parents=True)
    record_path.write_bytes(data)
    report_path.write_text(report)
    authority = validated.authority.model_copy(
        update={
            "currentRecordDigest": digest,
            "recordPath": record_path.relative_to(contract.task_root).as_posix(),
            "reportPath": report_path.relative_to(contract.task_root).as_posix(),
            "reportSha256": reread.reportSha256,
        }
    )
    paths.canonical.write_text(authority.model_dump_json())
    retained = coherence.load_curator_coherence_authority(contract)
    assert retained.record_digest == digest
    with pytest.raises(CuratorCoherenceError) as error:
        coherence.require_current_curator_coherence(contract)
    assert error.value.status == "curator-coherence-stale"
    assert error.value.next_action == "publish"
    assert _publish(contract, sprint, assessments=[], attempt="A002")["ok"] is True
    coherence.require_current_curator_coherence(contract)
    report_path.write_text(report + "corrupt historical projection\n")
    with pytest.raises(CuratorCoherenceError) as corrupt:
        coherence.load_curator_coherence_generation(contract, digest)
    assert corrupt.value.status == "curator-coherence-projection-digest-mismatch"


def test_stored_attestation_retains_full_byte_integrity(
    enclosure: tuple[WorktreeContract, TaskDocumentRef],
) -> None:
    contract, _ = enclosure
    validated = coherence.load_curator_coherence_authority(contract)
    record = validated.record
    assert record.attestationCopyPath is not None
    path = contract.task_root / record.attestationCopyPath
    before = path.read_bytes()
    payload = json.loads(before)
    payload["reportSha256"] = "a" * 64
    path.write_text(json.dumps(payload))
    assert hashlib.sha256(path.read_bytes()).hexdigest() != record.attestationSha256
    with pytest.raises(CuratorCoherenceError) as error:
        coherence.load_curator_coherence_generation(contract, validated.record_digest)
    assert error.value.status == "curator-coherence-attestation-unreadable"
