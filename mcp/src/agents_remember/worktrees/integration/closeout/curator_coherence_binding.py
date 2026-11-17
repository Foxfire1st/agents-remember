"""The direct evidence dependencies of one immutable curator record."""

from __future__ import annotations

from agents_remember.errors import CuratorCoherenceError
from agents_remember.memory_quality.quality_identity import measured_quality_attestation_sha256
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceRecord,
    CuratorQualityAttestation,
)
from agents_remember.models.lifecycles.evidence_dependencies import (
    EVIDENCE_DEPENDENCY_VALIDATOR,
    EvidenceDependencies,
    build_evidence_dependencies,
    canonical_sha256,
    dependency,
)
from agents_remember.models.lifecycles.review_assessment_store import (
    assessment_edge_name,
    recorded_assessment_digest,
)
from agents_remember.models.task_intent import TaskIntentIdentity


def curator_coherence_dependencies(record: CuratorCoherenceRecord) -> EvidenceDependencies:
    assert isinstance(record.taskIntent, TaskIntentIdentity)
    evidence = {judgment.evidenceRef: judgment.evidenceSha256 for judgment in record.judgments}
    review_edges = {
        assessment_edge_name(assessment.assessmentId): recorded_assessment_digest(assessment)
        for assessment in record.assessments
    }
    return build_evidence_dependencies(
        "curator-coherence/v1",
        [
            dependency(
                "code-tree",
                "candidate",
                record.codeCandidateTree,
                algorithm="git-object",
            ),
            dependency(
                "memory-tree",
                "candidate",
                record.memoryCandidateTree,
                algorithm="git-object",
            ),
            dependency(
                "semantic-topology",
                "leaf-placement",
                record.taskTopologyFingerprint,
            ),
            dependency("task-intent", "leaf", record.taskIntent.digest),
            dependency(
                "memory-attestation",
                record.attestationPath,
                record.attestationSha256,
            ),
            dependency(
                "evidence-bytes",
                "memory-quality-report",
                record.attestationReportSha256,
            ),
            *(
                dependency("evidence-bytes", path, digest)
                for path, digest in sorted(evidence.items())
            ),
            *(
                dependency("review-record", name, digest)
                for name, digest in sorted(review_edges.items())
            ),
            *(
                [
                    dependency(
                        "memory-attestation", "measured-quality", record.measuredAttestationSha256
                    ),
                    dependency(
                        "evidence-bytes",
                        "measured-memory-quality-report",
                        record.measuredReportSha256,
                    ),
                ]
                if record.measuredAttestationSha256 is not None
                and record.measuredReportSha256 is not None
                else []
            ),
            dependency(
                "validator",
                EVIDENCE_DEPENDENCY_VALIDATOR,
                canonical_sha256(EVIDENCE_DEPENDENCY_VALIDATOR),
            ),
            *(
                [
                    dependency(
                        "predecessor-record",
                        "prior-curator-coherence-authority",
                        record.predecessorAuthorityDigest,
                    )
                ]
                if record.predecessorAuthorityDigest
                else []
            ),
        ],
    )


def require_current_quality_binding(
    record: CuratorCoherenceRecord, attestation: CuratorQualityAttestation
) -> None:
    """Earlier records retain integrity but require one publication of measured inputs."""
    if (
        record.measuredAttestationSha256 is None
        or record.measuredReportSha256 is None
        or attestation.measuredReportSha256 is None
        or record.measuredAttestationSha256 != measured_quality_attestation_sha256(attestation)
        or record.measuredReportSha256 != attestation.measuredReportSha256
    ):
        raise CuratorCoherenceError(
            "curator-coherence-stale",
            "the live coherence authority does not bind the exact current candidate sources",
            next_action="publish",
        )
