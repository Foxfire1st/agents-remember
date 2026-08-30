"""Exact code/memory pair evidence shared by closeout preview, apply, and recovery."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.worktrees.integration.closeout.memory_candidate_pair import (
    resolve_memory_candidate_pair,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .curator_coherence import (
    CuratorCoherenceNoImpact,
    curator_coherence_no_impact,
    require_current_curator_coherence,
)


@dataclass(frozen=True)
class CloseoutMemoryPairEvidence:
    """The pair and coherence facts accepted before closeout can act."""

    pair_identity: MemoryCandidatePairIdentity | None = None
    no_impact: CuratorCoherenceNoImpact = field(default_factory=CuratorCoherenceNoImpact)
    coherence_record_digest: str = ""
    delivery_attempt: str = ""


def accepted_closeout_memory_pair(contract: WorktreeContract) -> CloseoutMemoryPairEvidence:
    """Validate the live coherence authority and expose its exact pair to closeout."""

    if contract.kind != "leaf" or contract.memory_mode != "external":
        return CloseoutMemoryPairEvidence()
    validated = require_current_curator_coherence(contract)
    return CloseoutMemoryPairEvidence(
        pair_identity=validated.record.pairIdentity,
        no_impact=curator_coherence_no_impact(validated),
        coherence_record_digest=validated.record_digest,
        delivery_attempt=validated.record.deliveryAttempt,
    )


def resolve_closeout_memory_pair(
    contract: WorktreeContract,
) -> MemoryCandidatePairIdentity | None:
    """Re-prove the exact pair for apply admission and every recovery attempt."""

    if contract.kind != "leaf" or contract.memory_mode != "external":
        return None
    return resolve_memory_candidate_pair(
        contract,
        requested_contract_path=contract.contract_path,
        requested_repo_id=contract.repo_name,
    )


def memory_candidate_pair_payload(
    pair_identity: MemoryCandidatePairIdentity | None,
) -> dict[str, object]:
    """Project a pair only where external-memory candidate acceptance applies."""

    if pair_identity is None:
        return {}
    return {"pairIdentity": pair_identity.model_dump(mode="json")}


__all__ = [
    "CloseoutMemoryPairEvidence",
    "accepted_closeout_memory_pair",
    "memory_candidate_pair_payload",
    "resolve_closeout_memory_pair",
]
