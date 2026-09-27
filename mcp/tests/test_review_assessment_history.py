"""Public comparison recording retains owner-authored assessments through cleanup and restart."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.review_comparison_freeze import (
    ComparisonEvidenceInput,
    ComparisonFreezeOptions,
    freeze_review_comparison,
)
from agents_remember.application.review_comparison_generation import (
    read_generation_refs,
    read_manifest,
)
from agents_remember.application.review_curator_records import CURATOR_RECORD_OWNER
from agents_remember.application.review_evidence_records import review_records_for
from agents_remember.application.review_record_rendering import EMPTY_REVIEW_RECORDS
from agents_remember.cli.__main__ import main
from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.knowledge.read import FamilyIdentitySeed
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRequest,
    CuratorSourceCandidate,
)
from agents_remember.models.lifecycles.review_assessment import AssessmentSubject
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    curator_coherence_paths,
    require_current_curator_coherence,
)
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from test_historical_committed_leaf_review import _body, _served_in_a_new_process, _subject_params
from test_knowledge_review_evidence_channels import (
    EVIDENCE_RELATIVE,
    RecordedFixture,
    _assessment_revision,
    _move_the_candidate,
    build_recorded_fixture,
    review_through_port,
)
from test_knowledge_review_source_endpoints import build_endpoint_fixture

pytestmark = pytest.mark.evidence_unit


def _publish_two(recorded: RecordedFixture, label: str = "original", *, concern: bool = True):
    """Author both subjects through the existing guarded public curator operation."""

    endpoint, contract = recorded.endpoint, recorded.contract
    family = endpoint.diff.before.fixture.direct_family
    original = _assessment_revision(endpoint)
    invariant = original.model_copy(
        update={
            "assessmentId": f"invariant-{label}",
            "disposition": "no_concern_found",
            "finding": "",
        }
    )
    family_assessment = original.model_copy(
        update={
            "assessmentId": f"family-{label}",
            "subject": AssessmentSubject(
                kind="family",
                recordId=family.family_id,
                beforeRevisionIds=(family.revision_id,),
                afterRevisionIds=(family.revision_id,),
            ),
            "disposition": "concern_found" if concern else "no_concern_found",
            "finding": "The original family concern remains part of this historical judgment."
            if concern
            else "",
        }
    )
    prepared = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="prepare", contract_path=str(contract.contract_path)),
    )
    candidates = prepared["candidates"]
    assert isinstance(candidates, list)
    published = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(
            action="publish",
            contract_path=str(contract.contract_path),
            semantic_requirement_revision="ICR-R11@v1",
            delivery_attempt=f"L41-{label}",
            judgments=[
                CuratorCoherenceJudgment(
                    **CuratorSourceCandidate.model_validate(value).model_dump(),
                    disposition="reconciled",
                    rationale="The fixture retains this exact authored evidence.",
                    evidenceRef=f"task:{EVIDENCE_RELATIVE}",
                )
                for value in candidates
            ],
            review_assessments=[invariant, family_assessment],
            expected_predecessor_digest=str(prepared["predecessorAuthorityDigest"]),
            expected_code_candidate_tree=str(prepared["codeCandidateTree"]),
            expected_memory_candidate_tree=str(prepared["memoryCandidateTree"]),
            expected_task_topology_fingerprint=str(prepared["taskTopologyFingerprint"]),
            expected_task_intent=TaskIntentIdentity.model_validate(prepared["taskIntent"]),
            expected_attestation_sha256=str(prepared["attestationSha256"]),
            caller=DeclaredCaller(role="architect", task_document_ref=recorded.sprint),
        ),
    )
    assert published["state"] == "published", published
    checked = require_current_curator_coherence(contract)
    assert len(checked.record.assessments) == 2
    return checked, published


def _argv(endpoint, *extra: str) -> list[str]:
    endpoint.config.config_path.write_text(
        json.dumps(
            {
                "workspaceRoot": str(endpoint.config.workspace_root),
                "coordinationRoot": str(endpoint.config.coordination_root),
                "repositories": {},
            }
        )
    )
    return [
        "review-record-comparison",
        "--config",
        str(endpoint.config.config_path),
        "--contract",
        str(endpoint.contract.contract_path),
        "--json",
        *extra,
    ]


def _record(endpoint, capsys, *extra: str):
    capsys.readouterr()
    code = main(_argv(endpoint, *extra))
    output = capsys.readouterr().out
    assert code == 0, output
    report = json.loads(output)
    assert report["state"] == "published", report
    refs = read_generation_refs(endpoint.contract.task_root, endpoint.contract.leaf_id)
    latest = max(refs, key=lambda ref: ref.generation_index)
    return latest, read_manifest(latest.directory / "manifest.json")


def _historical(endpoint, selector=None):
    request = endpoint.request().model_copy(update={"history": "recorded"})
    return request if selector is None else request.model_copy(update={"selector": selector})


def test_public_capture_survives_cleanup_restart_and_later_canonical_source_movement(
    tmp_path: Path, capsys
):
    recorded = build_recorded_fixture(tmp_path / "published")
    original, _ = _publish_two(recorded)
    endpoint = recorded.endpoint
    _, manifest = _record(endpoint, capsys)
    assert manifest.records.assessments == 2
    assert manifest.records.assessment_channel is not None
    assert manifest.records.assessment_channel.state == "recorded"
    pin = next(ref for ref in manifest.evidence if ref.owner == CURATOR_RECORD_OWNER)
    assert pin.sha256 == original.record_digest
    request = _historical(endpoint)
    live = review_through_port(endpoint, request).payload
    assert live is not None
    original_display = live.evidence.assessments

    later, _ = _publish_two(recorded, "later", concern=False)
    assert later.record_digest != original.record_digest
    assert later.record.assessments[1].disposition == "no_concern_found"
    (recorded.contract.worktree_group / "reports" / "curator-memory-quality.md").unlink()
    with pytest.raises(CuratorCoherenceError) as missing_live:
        require_current_curator_coherence(recorded.contract)
    assert missing_live.value.status == "curator-coherence-attestation-unreadable"
    _move_the_candidate(recorded)
    shutil.rmtree(recorded.contract.worktree_group)

    inputs = review_records_for(endpoint.config, request)
    assert inputs.assessments == tuple(original.record.assessments)
    reopened = review_through_port(endpoint, request).payload
    assert reopened is not None and reopened.evidence.assessments == original_display
    assert {
        row.assessment_id
        for row in reopened.evidence.assessments
        if row.applicability is not None and row.applicability.state == "direct"
    } == {"invariant-original"}
    family = endpoint.diff.before.fixture.direct_family
    family_view = review_through_port(
        endpoint, _historical(endpoint, FamilyIdentitySeed(family_id=family.family_id))
    ).payload
    assert family_view is not None
    direct = [
        row
        for row in family_view.evidence.assessments
        if row.applicability is not None and row.applicability.state == "direct"
    ]
    assert [(row.assessment_id, row.disposition) for row in direct] == [
        ("family-original", "concern_found")
    ]
    assert all(row.binding_state == "not-measured" for row in direct)
    restarted = _body(
        _served_in_a_new_process(endpoint, tmp_path, _subject_params(endpoint, "recorded"))
    )
    assert restarted["payload"]["evidence"]["assessments"] == [
        row.model_dump(mode="json", exclude_none=True) for row in original_display
    ]


@pytest.mark.parametrize("damaged", ["record", "published-evidence"])
def test_corrupt_bound_artifact_is_unavailable_without_erasing_other_channels(
    tmp_path: Path, capsys, damaged
):
    recorded = build_recorded_fixture(tmp_path / damaged)
    original, _ = _publish_two(recorded)
    if damaged == "record":
        supplied = freeze_review_comparison(
            recorded.endpoint.config,
            recorded.endpoint.task_request(),
            ComparisonFreezeOptions(records=recorded.records()),
        )
        assert supplied.manifest is not None
        manifest = supplied.manifest
        path = original.record_path
    else:
        _, manifest = _record(recorded.endpoint, capsys)
        reference = next(
            ref for ref in manifest.evidence if "notes/reports/evidence/" in ref.relative_path
        )
        path = recorded.contract.task_root / reference.relative_path
    path.write_bytes(b"damaged retained artifact")
    shutil.rmtree(recorded.contract.worktree_group)
    inputs = review_records_for(recorded.endpoint.config, _historical(recorded.endpoint))
    channels = {channel.records: channel for channel in inputs.channels}
    assert not inputs.assessments
    assert (
        channels["assessments"].state == channels["assessment_currentness"].state == "unavailable"
    )
    assert (
        channels["detection_signals"].state
        == channels["verification_observations"].state
        == "recorded"
    )
    assert channels["evidence_claims"].state == "recorded"
    assert inputs.signals and inputs.observations and inputs.claims
    assert (
        review_through_port(recorded.endpoint, _historical(recorded.endpoint)).payload is not None
    )


def test_new_measured_empty_is_distinct_from_legacy_not_captured(tmp_path: Path, capsys):
    endpoint = build_endpoint_fixture(tmp_path / "empty", memory_mode="external")
    legacy = freeze_review_comparison(
        endpoint.config,
        endpoint.task_request(),
        ComparisonFreezeOptions(records=EMPTY_REVIEW_RECORDS),
    )
    assert legacy.manifest is not None and legacy.manifest.records.state == "not-supplied"
    assert legacy.manifest.records.assessment_channel is None
    assert "assessment_channel" not in legacy.manifest.records.model_dump()
    assert legacy.manifest_path is not None
    before = legacy.manifest_path.read_bytes()
    legacy_inputs = review_records_for(endpoint.config, _historical(endpoint))
    assert (
        next(
            channel for channel in legacy_inputs.channels if channel.records == "assessments"
        ).state
        == "not_selected"
    )
    _, measured = _record(endpoint, capsys)
    assert measured.records.assessment_channel is not None
    assert measured.records.assessment_channel.state == "none_recorded"
    assert legacy.manifest_path.read_bytes() == before
    shutil.rmtree(endpoint.contract.worktree_group)
    supplied = review_records_for(endpoint.config, _historical(endpoint))
    channels = {channel.records: channel for channel in supplied.channels}
    assert (
        channels["assessments"].state == channels["assessment_currentness"].state == "none_recorded"
    )
    assert channels["assessments"].record_count == 0


def test_explicit_recovery_keeps_two_legacy_generations_and_publishes_exact_successor(
    tmp_path: Path, capsys
):
    recorded = build_recorded_fixture(tmp_path / "recover")
    original, publication = _publish_two(recorded)
    endpoint = recorded.endpoint
    first = freeze_review_comparison(
        endpoint.config,
        endpoint.task_request(),
        ComparisonFreezeOptions(records=EMPTY_REVIEW_RECORDS),
    )
    assert first.manifest is not None
    first_ref = read_generation_refs(recorded.contract.task_root, recorded.contract.leaf_id)[0]
    receipt = recorded.contract.task_root / "notes" / "original-publication.json"
    receipt.write_text(json.dumps(publication))
    second = freeze_review_comparison(
        endpoint.config,
        endpoint.task_request(),
        ComparisonFreezeOptions(
            records=EMPTY_REVIEW_RECORDS,
            parent=first_ref,
            evidence=(
                ComparisonEvidenceInput("curator-publication", "notes/original-publication.json"),
            ),
        ),
    )
    assert second.manifest is not None and second.manifest.generation_index == 2
    originals = {
        ref.directory / "manifest.json": (ref.directory / "manifest.json").read_bytes()
        for ref in read_generation_refs(recorded.contract.task_root, recorded.contract.leaf_id)
    }
    assert all(read_manifest(path).records.state == "not-supplied" for path in originals)
    shutil.rmtree(recorded.contract.worktree_group)
    args = (
        "--recover-generation",
        second.manifest.generation_id,
        "--curator-record-digest",
        original.record_digest,
    )
    _, successor = _record(endpoint, capsys, *args)
    assert successor.generation_index == 3
    assert successor.lineage.parent_generation_id == second.manifest.generation_id
    assert successor.lineage.parent_manifest_digest == second.manifest.manifest_digest()
    assert successor.records.assessments == 2
    assert successor.source.baseline_code_tree_id == second.manifest.source.baseline_code_tree_id
    assert successor.source.candidate_code_tree_id == second.manifest.source.candidate_code_tree_id
    assert [binding.identity for binding in successor.knowledge] == [
        binding.identity for binding in second.manifest.knowledge
    ]
    assert all(path.read_bytes() == content for path, content in originals.items())
    inputs = review_records_for(endpoint.config, _historical(endpoint))
    assert inputs.assessments == tuple(original.record.assessments)
    assert inputs.assessments[1].disposition == "concern_found"
    _, retry = _record(endpoint, capsys, *args)
    assert retry.generation_id == successor.generation_id
    assert len(read_generation_refs(recorded.contract.task_root, recorded.contract.leaf_id)) == 3


@pytest.mark.parametrize(
    "invalid",
    [
        "reserved-owner",
        "unpaired",
        "live-options",
        "foreign-record",
        "absent-parent",
        "moved-source",
        "corrupt-knowledge",
    ],
)
def test_invalid_recovery_and_impersonated_owner_refuse_without_new_generation(
    tmp_path: Path, capsys, invalid
):
    recorded = build_recorded_fixture(tmp_path / invalid)
    original, _ = _publish_two(recorded)
    if invalid == "moved-source":
        _move_the_candidate(recorded)
    legacy = freeze_review_comparison(
        recorded.endpoint.config,
        recorded.endpoint.task_request(),
        ComparisonFreezeOptions(records=EMPTY_REVIEW_RECORDS),
    )
    assert legacy.manifest is not None
    args = [
        "--recover-generation",
        legacy.manifest.generation_id,
        "--curator-record-digest",
        original.record_digest,
    ]
    if invalid == "reserved-owner":
        args = [
            "--evidence",
            f"{CURATOR_RECORD_OWNER}:{original.record_path.relative_to(recorded.contract.task_root)}",
        ]
    elif invalid == "unpaired":
        args = args[:2]
    elif invalid == "live-options":
        args.append("--unchanged-knowledge")
    elif invalid == "absent-parent":
        args[1] = str(uuid4())
    elif invalid == "foreign-record":
        foreign = build_recorded_fixture(tmp_path / "foreign")
        other, _ = _publish_two(foreign)
        destination = (
            curator_coherence_paths(recorded.contract).generation_record(other.record_digest).parent
        )
        shutil.copytree(other.record_path.parent, destination)
        args[-1] = other.record_digest
    elif invalid == "corrupt-knowledge":
        assert legacy.directory is not None
        artifact = legacy.manifest.knowledge[1].artifact
        assert artifact is not None
        (legacy.directory / artifact.relative_path).write_bytes(b"broken historical knowledge")
    capsys.readouterr()
    assert main(_argv(recorded.endpoint, *args)) == 2
    output = capsys.readouterr().out
    expected = {
        "reserved-owner": "reserved curator",
        "unpaired": "supplied together",
        "live-options": "cannot be combined",
        "foreign-record": "different leaf or contract",
        "absent-parent": "records no comparison generation",
        "moved-source": "original source",
        "corrupt-knowledge": "must all be available",
    }
    assert expected[invalid] in output, output
    assert len(read_generation_refs(recorded.contract.task_root, recorded.contract.leaf_id)) == 1
