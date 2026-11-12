"""Integrity reading of the curator's existing immutable generations; no live readiness check."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import ValidationError

from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.closeout.source import EvidenceFact
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherencePaths,
    CuratorCoherenceRecord,
    CuratorQualityAttestation,
    ValidatedCuratorCoherenceGeneration,
    require_memory_quality_attestation_dependencies,
)
from agents_remember.models.lifecycles.evidence_dependencies import EvidenceDependencyError
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_assessment_evidence import AssessmentEvidenceBlockedError, read_assessment_evidence
from .curator_coherence_judgments import read_judgment_evidence
from .curator_coherence_paths import resolve_curator_evidence_ref
from .curator_coherence_render import render_curator_coherence


def read_curator_coherence_generation(
    contract: WorktreeContract,
    paths: CuratorCoherencePaths,
    record_digest: str,
) -> ValidatedCuratorCoherenceGeneration:
    """Read one exact owner address and verify its typed content and declared retained artifacts."""

    if re.fullmatch(r"[0-9a-f]{64}", record_digest) is None:
        raise CuratorCoherenceError(
            "curator-coherence-record-digest-invalid",
            "a generation requires an exact SHA-256 digest",
        )
    record_path = paths.generation_record(record_digest)
    report_path = paths.generation_report(record_digest)
    try:
        record_bytes = record_path.read_bytes()
        report_bytes = report_path.read_bytes()
        record = CuratorCoherenceRecord.model_validate_json(record_bytes)
    except (OSError, ValidationError) as error:
        raise CuratorCoherenceError(
            "curator-coherence-generation-unreadable",
            "the named content-addressed generation is absent or invalid",
            next_action="restore the exact retained generation",
        ) from error
    if _digest(record_bytes) != record_digest:
        raise CuratorCoherenceError(
            "curator-coherence-record-digest-mismatch",
            "record bytes do not match the named immutable generation digest",
        )
    if (record.leafId, record.contractPath) != (
        contract.leaf_id,
        contract.contract_path.as_posix(),
    ):
        raise CuratorCoherenceError(
            "curator-coherence-record-identity-mismatch",
            "the selected record belongs to a different leaf or contract",
        )
    if _digest(report_bytes) != record.reportSha256 or report_bytes != render_curator_coherence(
        record
    ).encode("utf-8"):
        raise CuratorCoherenceError(
            "curator-coherence-projection-digest-mismatch",
            "generated Markdown does not match the structured record and its digest",
        )
    evidence = [_fact(record_path, record_bytes), _fact(report_path, report_bytes)]
    evidence.append(_attestation(contract, paths, record))
    evidence.extend(read_judgment_evidence(contract, judgment) for judgment in record.judgments)
    try:
        for assessment in record.assessments:
            evidence.extend(read_assessment_evidence(contract, assessment))
    except AssessmentEvidenceBlockedError as error:
        raise CuratorCoherenceError(error.status, error.detail) from error
    return ValidatedCuratorCoherenceGeneration(
        record=record,
        record_path=record_path,
        report_path=report_path,
        record_digest=record_digest,
        evidence=evidence,
    )


def _attestation(
    contract: WorktreeContract,
    paths: CuratorCoherencePaths,
    record: CuratorCoherenceRecord,
) -> EvidenceFact:
    """Read the explicit durable copy, never a replacement found in the reclaimed enclosure."""

    expected = paths.attestation_copy(record.attestationSha256).relative_to(contract.task_root)
    if record.attestationCopyPath != expected.as_posix():
        raise CuratorCoherenceError(
            "curator-coherence-attestation-reference-invalid",
            "the record does not name its exact content-addressed durable attestation copy",
        )
    try:
        path = resolve_curator_evidence_ref(contract, f"task:{record.attestationCopyPath}")
        payload = path.read_bytes()
        attestation = CuratorQualityAttestation.model_validate_json(payload)
        if (
            _digest(payload) != record.attestationSha256
            or attestation.reportSha256 != record.attestationReportSha256
            or attestation.pairIdentity != record.pairIdentity
            or attestation.sourceChangeCandidates != record.sourceCandidates
        ):
            raise ValueError("retained attestation does not match the immutable record")
        require_memory_quality_attestation_dependencies(
            attestation,
            code_candidate_tree=record.codeCandidateTree,
            memory_candidate_tree=record.memoryCandidateTree,
        )
    except (OSError, ValidationError, ValueError, EvidenceDependencyError) as error:
        raise CuratorCoherenceError(
            "curator-coherence-attestation-unreadable",
            f"the retained attestation is missing, invalid or mismatched: {error}",
        ) from error
    return _fact(path, payload)


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fact(path: Path, payload: bytes) -> EvidenceFact:
    return EvidenceFact(path=path.resolve().as_posix(), sha256=_digest(payload))
