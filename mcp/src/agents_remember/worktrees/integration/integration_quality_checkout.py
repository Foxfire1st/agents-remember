"""Materialize the exact accepted integration candidate for the master quality gate."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract


@contextmanager
def integration_quality_checkout(
    contract: WorktreeContract,
    *,
    commit: str | None = None,
) -> Iterator[Path]:
    """Yield a detached exact candidate, or the ordinary leaf worktree when unpinned."""

    if contract.kind == "leaf" and commit is None:
        yield contract.code_worktree
        return
    exact_commit = commit or contract.code_commit
    with tempfile.TemporaryDirectory(prefix="agents-remember-master-gate-") as root:
        checkout = Path(root) / "candidate"
        require_git(
            contract.code_repo_path,
            ["worktree", "add", "--detach", checkout.as_posix(), exact_commit],
        )
        try:
            yield checkout
        finally:
            require_git(
                contract.code_repo_path,
                ["worktree", "remove", "--force", checkout.as_posix()],
            )
