"""L6 closeout coverage tests for worktree cleanup branch helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

from agents_remember.worktrees.modules import cleanup
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
)

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _abandon_authority(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.email", "terminal-tests@example.invalid"),
        ("config", "user.name", "Terminal Tests"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "branch", "b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", head], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo,
        check=True,
    )
    contract = default_contract(
        ContractTask("terminal", "repo", tmp_path / "coordination", "light-task", "disabled"),
        leaf=LeafIdentity("terminal", leaf_id="T"),
        code=RepoBranchPlan(repo, "main", "b", head),
    )
    return repo, cleanup._terminal_mutation_authority(contract, operation="worktree_abandon")


class TestLocalBranchPresence:
    def test_branches(self) -> None:
        assert cleanup.local_branch_presence(Path("/x"), "").state == "error"
        with mock.patch.object(cleanup, "run_git", return_value=_done(0)):
            assert cleanup.local_branch_presence(Path("/x"), "b").state == "present"
        with mock.patch.object(cleanup, "run_git", return_value=_done(1)):
            assert cleanup.local_branch_presence(Path("/x"), "b").state == "absent"
        with mock.patch.object(cleanup, "run_git", return_value=_done(2, stderr="boom")):
            presence = cleanup.local_branch_presence(Path("/x"), "b")
            assert presence.state == "error" and "boom" in presence.reason


class TestDeleteBranchForce:
    def test_branches(self, tmp_path: Path) -> None:
        repo, authority = _abandon_authority(tmp_path)
        with mock.patch.object(
            cleanup,
            "local_branch_presence",
            return_value=cleanup.LocalBranchPresence("error", "bad"),
        ):
            assert (
                cleanup.delete_branch_force(repo, "b", dry_run=False, authority=authority)["reason"]
                == "bad"
            )
        with mock.patch.object(
            cleanup,
            "local_branch_presence",
            return_value=cleanup.LocalBranchPresence("absent"),
        ):
            result = cleanup.delete_branch_force(repo, "b", dry_run=False, authority=authority)
            assert result["reason"] == "already-absent"
        with mock.patch.object(
            cleanup,
            "local_branch_presence",
            return_value=cleanup.LocalBranchPresence("present"),
        ):
            result = cleanup.delete_branch_force(repo, "b", dry_run=True, authority=authority)
            assert result["would_delete"] is True
        with (
            mock.patch.object(
                cleanup,
                "local_branch_presence",
                return_value=cleanup.LocalBranchPresence("present"),
            ),
            mock.patch.object(cleanup, "run_git", return_value=_done(128, stderr="locked")),
        ):
            result = cleanup.delete_branch_force(repo, "b", dry_run=False, authority=authority)
            assert result["deleted"] is False and "locked" in str(result["reason"])
        with (
            mock.patch.object(
                cleanup,
                "local_branch_presence",
                return_value=cleanup.LocalBranchPresence("present"),
            ),
            mock.patch.object(cleanup, "run_git", return_value=_done(0)),
        ):
            result = cleanup.delete_branch_force(repo, "b", dry_run=False, authority=authority)
            assert result["deleted"] is True
