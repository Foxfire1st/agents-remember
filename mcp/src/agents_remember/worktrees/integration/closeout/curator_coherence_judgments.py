"""Exact curator judgment-set and evidence-byte validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.models.closeout.source import EvidenceFact
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRecordedJudgment,
    CuratorSourceCandidate,
)
from agents_remember.models.lifecycles.review_assessment import AssessmentEvidenceByte
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_assessment_evidence import AssessmentEvidenceBlockedError, read_retained_evidence_byte
from .curator_coherence_paths import curator_coherence_paths, resolve_curator_evidence_ref


def exact_curator_judgments(
    contract: WorktreeContract,
    candidates: list[CuratorSourceCandidate],
    supplied: list[CuratorCoherenceJudgment],
) -> list[CuratorCoherenceRecordedJudgment]:
    """Require exactly one supplied judgment per candidate and bind its evidence bytes."""

    expected = [candidate.identity for candidate in candidates]
    observed = [judgment.identity for judgment in supplied]
    if len(observed) != len(set(observed)):
        raise CuratorCoherenceError(
            "curator-coherence-judgment-duplicate",
            "publication contains duplicate candidate judgments",
            next_action="publish",
        )
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    if missing or extra:
        raise CuratorCoherenceError(
            "curator-coherence-judgment-set-mismatch",
            "publication requires exactly one judgment for every attested candidate identity",
            expected={"missing": missing},
            observed={"extra": extra},
            next_action="publish",
        )
    by_identity = {judgment.identity: judgment for judgment in supplied}
    return [
        CuratorCoherenceRecordedJudgment(
            **by_identity[identity].model_dump(mode="json"),
            evidenceSha256=_evidence_digest(
                resolve_curator_evidence_ref(contract, by_identity[identity].evidenceRef),
                by_identity[identity].evidenceRef,
            ),
        )
        for identity in expected
    ]


def retain_judgment_evidence(
    contract: WorktreeContract, judgments: list[CuratorCoherenceRecordedJudgment]
) -> list[CuratorCoherenceRecordedJudgment]:
    """The publication owner retains admitted bytes without changing the authored citation."""

    retained = []
    for judgment in judgments:
        source = resolve_curator_evidence_ref(contract, judgment.evidenceRef)
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != judgment.evidenceSha256:
            raise CuratorCoherenceError(
                "curator-coherence-evidence-raced", "judgment bytes moved before durable custody"
            )
        destination = curator_coherence_paths(contract).judgment_evidence(digest)
        if destination.exists():
            if destination.read_bytes() != payload:
                raise CuratorCoherenceError(
                    "curator-coherence-content-address-collision",
                    "the retained judgment address holds different bytes",
                )
        else:
            atomic_write_bytes(destination, payload)
        artifact = AssessmentEvidenceByte(
            path=destination.resolve().relative_to(contract.task_root.resolve()).as_posix(),
            sha256=digest,
            size=len(payload),
        )
        stamped = judgment.model_copy(update={"evidenceArtifact": artifact})
        read_judgment_evidence(contract, stamped)
        retained.append(stamped)
    return retained


def read_judgment_evidence(
    contract: WorktreeContract, judgment: CuratorCoherenceRecordedJudgment
) -> EvidenceFact:
    """Use owner-stamped custody, or an old explicit durable task citation; never current code/memory."""

    artifact = judgment.evidenceArtifact
    if artifact is None:
        if not judgment.evidenceRef.startswith("task:"):
            raise CuratorCoherenceError(
                "curator-coherence-judgment-not-retained",
                "this historical code/memory judgment has no retained evidence artifact",
            )
        path = resolve_curator_evidence_ref(contract, judgment.evidenceRef)
        observed = _evidence_digest(path, judgment.evidenceRef)
        if observed != judgment.evidenceSha256:
            raise CuratorCoherenceError(
                "curator-coherence-evidence-stale",
                f"judgment evidence bytes changed: {judgment.evidenceRef}",
            )
        return EvidenceFact(path=path.as_posix(), sha256=observed)
    expected = curator_coherence_paths(contract).judgment_evidence(judgment.evidenceSha256)
    if (
        artifact.path != expected.relative_to(contract.task_root).as_posix()
        or artifact.sha256 != judgment.evidenceSha256
    ):
        raise CuratorCoherenceError(
            "curator-coherence-judgment-artifact-invalid",
            "judgment custody does not name this owner's exact recorded evidence bytes",
        )
    try:
        return read_retained_evidence_byte(contract, artifact)
    except AssessmentEvidenceBlockedError as error:
        raise CuratorCoherenceError(error.status, error.detail) from error


def require_recorded_judgments_current(
    contract: WorktreeContract,
    judgments: list[CuratorCoherenceRecordedJudgment],
) -> None:
    """Refuse a publication when any referenced evidence changed during its CAS window."""

    for judgment in judgments:
        observed = _evidence_digest(
            resolve_curator_evidence_ref(contract, judgment.evidenceRef),
            judgment.evidenceRef,
        )
        if observed != judgment.evidenceSha256:
            raise CuratorCoherenceError(
                "curator-coherence-evidence-raced",
                f"judgment evidence changed during publication: {judgment.evidenceRef}",
                expected={"evidenceSha256": judgment.evidenceSha256},
                observed={"evidenceSha256": observed},
                next_action="prepare",
            )


def _evidence_digest(path: Path, reference: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CuratorCoherenceError(
            "curator-coherence-evidence-unreadable",
            f"judgment evidence cannot be read: {reference}",
            next_action="publish",
        ) from exc


__all__ = [
    "exact_curator_judgments",
    "read_judgment_evidence",
    "require_recorded_judgments_current",
    "retain_judgment_evidence",
]
