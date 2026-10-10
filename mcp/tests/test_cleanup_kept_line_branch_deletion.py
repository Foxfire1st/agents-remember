"""Kept-line branch deletion (MIK-R82 rule 5, ASSIGNMENT-SCOPE-BOUNDARIES-RULING.json).

The branch check of the cleanup takes the same kept-line proof as the carryover
question: a work branch whose tip is reachable from the landing line or the
default line is deleted even where its master's line does not hold the tip, and
the answer names the line and the commit. A tip on neither line keeps the
refusal exactly as it is and nothing is deleted.
"""

import subprocess
import sys
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.integration.integration_branch_repository import (
    repository_default_branch,
)
from agents_remember.worktrees.modules.terminal_branch_retirement import (
    _TERMINAL_MUTATION_CAPABILITY,
    RetiringBranch,
    _repository_key,
    _retire_work_branch,
    _TerminalMutationAuthority,
)
from agents_remember.worktrees.modules.terminal_validation import (
    BranchTarget,
    _cleanup_branch_preflight,
)
from test_worktree_support import git, init_repo

WORK_BRANCH = "ar/fixture-work"
SOURCE_BRANCH = "ar/fixture-master-line"


def _commit(repo: Path, name: str) -> None:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-m", name)


def _ahead_repo(root: Path) -> tuple[Path, str]:
    """Default line two commits ahead of the master line; work branch at the tip."""
    repo = root / "code"
    base = init_repo(repo, branch="main")
    assert repository_default_branch(repo) == "main"
    git(repo, "branch", SOURCE_BRANCH, base)
    _commit(repo, "landed-one")
    _commit(repo, "landed-two")
    tip = git(repo, "rev-parse", "HEAD")
    git(repo, "branch", WORK_BRANCH, tip)
    return repo, tip


def _stray_repo(root: Path) -> Path:
    """Work tip on neither the default line nor the master line."""
    repo = root / "code"
    base = init_repo(repo, branch="main")
    git(repo, "branch", SOURCE_BRANCH, base)
    _commit(repo, "landed-one")
    git(repo, "checkout", "-b", WORK_BRANCH, base)
    _commit(repo, "stray-work")
    git(repo, "checkout", "main")
    return repo


def _authority(repo: Path) -> _TerminalMutationAuthority:
    return _TerminalMutationAuthority(
        operation="worktree_cleanup",  # type: ignore[arg-type]
        worktrees=frozenset(),
        memory_worktree=None,
        branches=frozenset([(_repository_key(repo), WORK_BRANCH, SOURCE_BRANCH)]),
        remote_branches=frozenset(),
        _capability=_TERMINAL_MUTATION_CAPABILITY,
    )


def _branch_present(repo: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def test_kept_line_branch_runs_through_to_deletion(tmp_path: Path) -> None:
    """Tip on the default line but ahead of the master line: preflight proves it,
    the real retirement deletes it, and the answer names the line and commit."""
    repo, tip = _ahead_repo(tmp_path)
    pre = _cleanup_branch_preflight(
        BranchTarget("code", repo, WORK_BRANCH, SOURCE_BRANCH), {"branch": WORK_BRANCH}
    )
    assert pre.get("would_delete") is True
    assert pre["keptLine"] == "main"
    assert pre["foundIn"] == tip
    out = _retire_work_branch(
        RetiringBranch(repo, WORK_BRANCH, SOURCE_BRANCH, "main"),
        dry_run=False,
        remote=False,
        authority=_authority(repo),
    )
    assert out["deleted"] is True
    assert out["keptLine"] == "main"
    assert out["foundIn"] == tip
    assert not _branch_present(repo, WORK_BRANCH)


def test_neither_line_branch_refusal_is_protected(tmp_path: Path) -> None:
    """Tip on neither kept line: the refusal stays verbatim and the branch lives."""
    repo = _stray_repo(tmp_path)
    pre = _cleanup_branch_preflight(
        BranchTarget("code", repo, WORK_BRANCH, SOURCE_BRANCH), {"branch": WORK_BRANCH}
    )
    assert pre.get("would_delete") is not True
    assert pre["reason"] == "not-merged-into-source"
    assert pre["target"] == SOURCE_BRANCH
    out = _retire_work_branch(
        RetiringBranch(repo, WORK_BRANCH, SOURCE_BRANCH, "main"),
        dry_run=False,
        remote=False,
        authority=_authority(repo),
    )
    assert out["deleted"] is False
    assert out["reason"] == "not-merged-into-source"
    assert _branch_present(repo, WORK_BRANCH)
