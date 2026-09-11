"""Re-prove that a landing output is the exact closeout candidate the contract recorded."""

from __future__ import annotations

from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.worktree_contract import WorktreeContract


def require_authorized_integration_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    *,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> None:
    """Prove the landing output is the exact closeout candidate recorded on the contract.

    The candidate commits are contract facts, so this reads the contract rather
    than a journal record. It refuses a replay: conflict resolution must produce
    a new leaf closeout, not reuse an older pair.
    """

    del args
    found = (code_commit, memory_content_commit, ledger_commit)
    expected = (contract.code_commit, contract.memory_content_commit, contract.ledger_commit)
    if found != expected:
        raise RuntimeError(
            "integration output is not the exact recorded closeout candidate; conflict "
            "resolution must produce a new leaf closeout before integration"
        )
