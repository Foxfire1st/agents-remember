"""Exact curator judgment-set and evidence-byte validation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agents_remember.errors import CuratorCoherenceError
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRecordedJudgment,
    CuratorSourceCandidate,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_coherence import resolve_curator_evidence_ref


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


__all__ = ["exact_curator_judgments", "require_recorded_judgments_current"]
