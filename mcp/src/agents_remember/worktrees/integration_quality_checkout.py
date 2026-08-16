"""Materialize the exact accepted integration candidate for the master quality gate."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract


@contextmanager
def integration_quality_checkout(contract: WorktreeContract) -> Iterator[Path]:
    """Yield a detached view of the exact atomic candidate, never ambient branch state."""

    if contract.kind == "leaf":
        yield contract.code_worktree
        return
    with tempfile.TemporaryDirectory(prefix="agents-remember-master-gate-") as root:
        checkout = Path(root) / "candidate"
        require_git(
            contract.code_repo_path,
            ["worktree", "add", "--detach", checkout.as_posix(), contract.code_commit],
        )
        try:
            yield checkout
        finally:
            require_git(
                contract.code_repo_path,
                ["worktree", "remove", "--force", checkout.as_posix()],
            )
