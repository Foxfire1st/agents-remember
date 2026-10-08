"""The four sealed L41 baseline failures through public owners and bounded fault timing."""

from __future__ import annotations

from pathlib import Path

import pytest
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
    require_current_curator_coherence,
)
from curator_coherence_test_support import write_curator_evidence
from test_knowledge_review_evidence_channels import (
    EVIDENCE_RELATIVE,
    _assessment_revision,
    build_recorded_fixture,
)
from test_review_git_trees import CODE_FILE

pytestmark = [pytest.mark.evidence_unit, pytest.mark.usefixtures("worktree_services")]


def _nonempty_fixture(path: Path):
    recorded = build_recorded_fixture(path)
    write_curator_evidence(
        recorded.contract,
        caller_ref=recorded.sprint,
        source_candidates=[
            {
                "sourceFile": CODE_FILE,
                "onboardingFile": f"onboarding/{CODE_FILE}.md",
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
