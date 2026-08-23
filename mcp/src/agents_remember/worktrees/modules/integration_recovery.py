"""Exact external-memory proof for integration finalization recovery."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import (
    IntegrationOperationAuthority,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.integration.integration_ref_state import (
    IntegrationRefDecisionError,
    IntegrationRefState,
    classify_integration_authority_refs,
)
from agents_remember.worktrees.modules.git import branch_commit, head_commit, require_clean
from agents_remember.worktrees.worktree_contract import WorktreeContract


def classify_convergent_recovery_refs(
    authority: IntegrationOperationAuthority,
    commits: LifecycleOperationRecoveryCommits,
) -> IntegrationRefState:
    facts = classify_integration_authority_refs(authority, commits)
    if facts.state == "conflict":
        raise IntegrationRefDecisionError(facts)
    return facts


def prove_external_memory_recovery(
    contract: WorktreeContract,
    commits: LifecycleOperationRecoveryCommits,
) -> None:
    """Prove the closed task memory head still names the recovered ledger."""
    assert contract.memory_repo_path is not None
    if contract.kind == "series":
        task_memory_head = branch_commit(contract.memory_repo_path, contract.memory_work_branch)
    else:
        assert contract.memory_worktree is not None
        require_clean(contract.memory_worktree, "recovering integration memory worktree")
        task_memory_head = head_commit(contract.memory_worktree)
    if task_memory_head != commits.ledgerCommit:
        raise RuntimeError(
            "integration contract-finalization recovery requires manual reconciliation: "
            f"recorded ledger commit {commits.ledgerCommit}, found task memory HEAD "
            f"{task_memory_head}"
        )
