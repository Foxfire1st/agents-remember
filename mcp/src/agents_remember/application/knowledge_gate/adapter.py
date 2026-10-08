"""The composition-bound :class:`KnowledgeGatePort` of the worktree layer (MIK-R09)."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.application.knowledge_gate.direct import direct_source, direct_verdict
from agents_remember.application.knowledge_gate.gate import evaluate_leaf_gate
from agents_remember.application.knowledge_gate.landing import landing_refusal
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.worktrees.services import (
    DirectGateSource,
    DirectGateVerdict,
    LandingGateRequest,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = ["KnowledgeGate"]


@dataclass(frozen=True)
class KnowledgeGate:
    """Every route's gate: recompute over the exact trees, decide each item, validate."""

    def leaf_refusal(
        self,
        contract: WorktreeContract,
        *,
        code_tree: str,
        memory_tree: str,
        parent_memory_tip: str | None,
    ) -> str | None:
        result = evaluate_leaf_gate(
            contract,
            CandidateTrees(code=code_tree, memory=memory_tree),
            parent_memory_tip=parent_memory_tip,
        )
        return None if result is None else result.refusal()

    def direct_verdict(
        self,
        contract: WorktreeContract,
        *,
        code_commit: str,
        memory_tree: str,
        source: DirectGateSource | None = None,
    ) -> DirectGateVerdict:
        return direct_verdict(
            contract, code_commit=code_commit, memory_tree=memory_tree, source=source
        )

    def direct_source(
        self, contract: WorktreeContract, *, code_commit: str, memory_tree: str
    ) -> DirectGateVerdict:
        return direct_source(contract, code_commit=code_commit, memory_tree=memory_tree)

    def landing_refusal(self, request: LandingGateRequest) -> str | None:
        return landing_refusal(request)
