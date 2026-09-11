"""Typed early results for integration admission and journal recovery."""

from __future__ import annotations

from agents_remember.worktrees.integration.atomic_series_landing import AtomicLandingBlocked
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


__all__ = ["atomic_landing_blocked_result"]
