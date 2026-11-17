"""Construction of immutable curator records and their deterministic projection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agents_remember.memory_quality.quality_identity import measured_quality_attestation_sha256
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceRecord,
    CuratorCoherenceRecordedJudgment,
    CuratorCoherenceRequest,
)
from agents_remember.models.lifecycles.review_assessment import ReviewAssessment
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_coherence import CuratorCoherenceObservation
from .curator_coherence_binding import curator_coherence_dependencies
from .curator_coherence_render import render_curator_coherence


@dataclass(frozen=True)
class RecordPublication:
    judgments: list[CuratorCoherenceRecordedJudgment]
    assessments: list[ReviewAssessment]
    fingerprint: str
    predecessor: str
    attestation_copy_path: str


def build_curator_coherence_record(
    contract: WorktreeContract,
    request: CuratorCoherenceRequest,
    observation: CuratorCoherenceObservation,
    publication: RecordPublication,
    *,
    caller: DeclaredCaller,
) -> tuple[CuratorCoherenceRecord, str]:
    assert request.semantic_requirement_revision is not None
    assert request.delivery_attempt is not None
    provisional = CuratorCoherenceRecord(
        leafId=contract.leaf_id,
        contractPath=contract.contract_path.as_posix(),
        taskDocumentRef=observation.candidate.ref,
        semanticRequirementRevision=request.semantic_requirement_revision.strip(),
        deliveryAttempt=request.delivery_attempt.strip(),
        pairIdentity=observation.pair_identity,
        codeCandidateTree=observation.code_candidate_tree,
        memoryCandidateTree=observation.memory_candidate_tree,
        taskTopologyFingerprint=observation.task_topology_fingerprint,
        taskIntent=observation.task_intent,
        attestationPath=observation.attestation_path.resolve().as_posix(),
        attestationSha256=observation.attestation_sha256,
        attestationReportSha256=observation.attestation.reportSha256,
        attestationCopyPath=publication.attestation_copy_path,
        sourceCandidates=observation.source_candidates,
        judgments=publication.judgments,
        assessments=publication.assessments,
        dependencies=None,
        measuredAttestationSha256=measured_quality_attestation_sha256(observation.attestation),
        measuredReportSha256=observation.attestation.measuredReportSha256,
        predecessorAuthorityDigest=publication.predecessor,
        publicationFingerprint=publication.fingerprint,
        publishedBy=f"{caller.role}@{caller.task_document_ref.key}",
        reportSha256="0" * 64,
    )
    provisional = provisional.model_copy(
        update={"dependencies": curator_coherence_dependencies(provisional)}
    )
    report = render_curator_coherence(provisional)
    record = provisional.model_copy(update={"reportSha256": _digest(report.encode("utf-8"))})
    rendered = render_curator_coherence(record)
    if rendered != report:
        raise RuntimeError("curator-coherence projection unexpectedly depends on its own digest")
    return record, rendered


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
