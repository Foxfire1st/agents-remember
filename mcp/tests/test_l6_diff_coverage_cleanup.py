"""L6 closeout coverage tests for worktree cleanup branch helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

from agents_remember.worktrees.modules import cleanup

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


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
    def test_branches(self) -> None:
        with mock.patch.object(
            cleanup,
            "local_branch_presence",
            return_value=cleanup.LocalBranchPresence("error", "bad"),
        ):
            assert cleanup.delete_branch_force(Path("/x"), "b", dry_run=False)["reason"] == "bad"
        with mock.patch.object(
            cleanup,
            "local_branch_presence",
            return_value=cleanup.LocalBranchPresence("absent"),
        ):
            result = cleanup.delete_branch_force(Path("/x"), "b", dry_run=False)
            assert result["reason"] == "already-absent"
        with mock.patch.object(
            cleanup,
            "local_branch_presence",
            return_value=cleanup.LocalBranchPresence("present"),
        ):
            result = cleanup.delete_branch_force(Path("/x"), "b", dry_run=True)
            assert result["would_delete"] is True
        with (
            mock.patch.object(
                cleanup,
                "local_branch_presence",
                return_value=cleanup.LocalBranchPresence("present"),
            ),
            mock.patch.object(cleanup, "run_git", return_value=_done(128, stderr="locked")),
        ):
            result = cleanup.delete_branch_force(Path("/x"), "b", dry_run=False)
            assert result["deleted"] is False and "locked" in str(result["reason"])
        with (
            mock.patch.object(
                cleanup,
                "local_branch_presence",
                return_value=cleanup.LocalBranchPresence("present"),
            ),
            mock.patch.object(cleanup, "run_git", return_value=_done(0)),
        ):
            result = cleanup.delete_branch_force(Path("/x"), "b", dry_run=False)
            assert result["deleted"] is True


class TestRepoDefaultBranch:
    def test_default_branch(self) -> None:
        with mock.patch.object(cleanup, "run_git", return_value=_done(0, stdout="main\n")):
            assert cleanup._repo_default_branch(Path("/x")) == "main"
        with mock.patch.object(cleanup, "run_git", return_value=_done(1)):
            assert cleanup._repo_default_branch(Path("/x")) == "main"
