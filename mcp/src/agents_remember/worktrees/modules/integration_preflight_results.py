"""Typed early results for integration admission and journal recovery."""

from __future__ import annotations

from agents_remember.worktrees.integration.atomic_series_landing import AtomicLandingBlocked
from agents_remember.worktrees.integration.integration_ref_transaction import IntegratedCommits
from agents_remember.worktrees.integration.organizational_completion_integration import (
    IntegrationBoundaryFacts,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.guidance import status_payload
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import WorktreeContract


def atomic_landing_blocked_result(
    contract: WorktreeContract,
    error: AtomicLandingBlocked,
) -> WorktreeCommandResult:
    blocker = error.blocker
    return WorktreeCommandResult(
        2,
        {
            **status_payload(contract),
            "state": "blocked",
            "status": error.status,
            "summary": error.detail,
            "blocker": {
                "contractPath": str(blocker.contract_path),
                "master": blocker.master,
                "state": blocker.state,
            },
        },
    )


def prepared_integration_recovery(args: WorktreeArgs):
    if args.integration_publication is None:
        return None
    recovery = args.recovery_commits
    if recovery is None:
        raise RuntimeError("integration publication recovery has no commit tuple")
    certification = args.quality_certification
    return (
        IntegratedCommits(
            code=recovery.codeCommit,
            memory_content=recovery.memoryContentCommit,
            ledger=recovery.ledgerCommit,
        ),
        certification.result if certification is not None else {},
        certification,
        IntegrationBoundaryFacts(None, None, None),
    )


__all__ = ["atomic_landing_blocked_result", "prepared_integration_recovery"]
