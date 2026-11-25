"""The closing a direct landing writes: the leaf's history file with its memory review fact."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agents_remember.worktrees.knowledge_gate import HistoryClosing, close_owner_history
from agents_remember.worktrees.review_fact_source import owner_review_fact
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = ["reviewed_closing"]


def reviewed_closing(
    contract: WorktreeContract, memory_repo: Path, owner: str
) -> Callable[[], HistoryClosing]:
    """Read ``owner``'s review fact now; the returned call closes its latest history file with it.

    The fact binds the memory tree the Reviewer passed, so it is read from the checkout before the
    landing writes anything there (its cache ignore rule included); the closing comes later. The
    fact goes into the same write that sets ``closed``; a file already closed is left unchanged.
    """

    review = owner_review_fact(contract, owner, memory_repo)
    return lambda: close_owner_history(memory_repo, owner, review)
