"""L6 closeout coverage tests for terminal validation preflights."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules import terminal_validation
from agents_remember.worktrees.modules.terminal_validation import BranchTarget
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _done(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _target(**over: object) -> BranchTarget:
    base = {
        "key": "code",
        "repo": Path("/repo"),
        "branch": "ar/leaf",
        "source": "ar/base",
        "optional": False,
        "remote": False,
    }
    base.update(over)
    return BranchTarget(**base)


class TestWorktreePreflight:
    def test_branches(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing"
        contract = cast(
            WorktreeContract,
            SimpleNamespace(
                kind="leaf",
                code_worktree=missing,
                memory_mode="internal",
                memory_worktree=None,
            ),
        )
        previews, blockers = terminal_validation._worktree_preflight(contract, force=False)
        assert previews["code"]["reason"] == "already-absent"
        assert blockers == []

        dirty = tmp_path / "dirty"
        dirty.mkdir()
        contract = cast(
            WorktreeContract,
            SimpleNamespace(
                kind="leaf",
                code_worktree=dirty,
                memory_mode="internal",
                memory_worktree=None,
            ),
        )
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(0, stdout=" M x\n")
        ):
            previews, blockers = terminal_validation._worktree_preflight(contract, force=False)
            assert previews["code"]["reason"] == "dirty" and blockers[0]["reason"] == "dirty"
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(0, stdout=" M x\n")
        ):
            previews, blockers = terminal_validation._worktree_preflight(contract, force=True)
            assert previews["code"]["would_remove"] is True and blockers == []
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(128, stderr="boom")
        ):
            previews, blockers = terminal_validation._worktree_preflight(contract, force=False)
            assert "boom" in str(previews["code"]["reason"])


class TestBranchRefusals:
    def test_branch_refs_refusal(self) -> None:
        base: dict[str, object] = {}
        result = terminal_validation._branch_refs_refusal(_target(branch="", source=""), base)
        assert result is not None and "empty-branch-or-source" in str(result["reason"])
        with mock.patch.object(terminal_validation, "_branch_presence", return_value="absent"):
            result = terminal_validation._branch_refs_refusal(_target(), base)
            assert result is not None and result["reason"] == "source-branch-missing"
        with mock.patch.object(
            terminal_validation, "_branch_presence", return_value="query failed"
        ):
            result = terminal_validation._branch_refs_refusal(_target(), base)
            assert result is not None and result["reason"] == "query failed"
        with mock.patch.object(
            terminal_validation,
            "_branch_presence",
            side_effect=lambda repo, branch: "present" if branch == "ar/base" else "absent",
        ):
            result = terminal_validation._branch_refs_refusal(_target(), base)
            assert result is not None and result["reason"] == "already-absent"
        with mock.patch.object(
            terminal_validation,
            "_branch_presence",
            side_effect=lambda repo, branch: "present" if branch == "ar/base" else "error",
        ):
            result = terminal_validation._branch_refs_refusal(_target(), base)
            assert result is not None and result["reason"] == "error"
        with mock.patch.object(
            terminal_validation,
            "_branch_presence",
            side_effect=lambda repo, branch: "present",
        ):
            result = terminal_validation._branch_refs_refusal(
                _target(branch="ar/base", source="ar/base"), base
            )
            assert result is not None and result["reason"] == "work-branch-is-source-branch"
            assert terminal_validation._branch_refs_refusal(_target(), base) is None

    def test_branch_checkout_refusal(self) -> None:
        with mock.patch.object(
            terminal_validation, "_checked_out_paths", return_value="query failed"
        ):
            result = terminal_validation._branch_checkout_refusal(_target(), {}, set())
            assert result is not None and result["reason"] == "query failed"
        foreign = {Path("/elsewhere")}
        with mock.patch.object(terminal_validation, "_checked_out_paths", return_value=foreign):
            result = terminal_validation._branch_checkout_refusal(_target(), {}, set())
            assert result is not None and result["reason"] == "branch-checked-out-elsewhere"
        with mock.patch.object(
            terminal_validation,
            "_checked_out_paths",
            return_value={Path("/allowed")},
        ):
            result = terminal_validation._branch_checkout_refusal(_target(), {}, {Path("/allowed")})
            assert result is None


class TestCleanupAndAbandon:
    def test_cleanup_branch_preflight(self) -> None:
        with mock.patch.object(terminal_validation, "run_git", return_value=_done(1)):
            result = terminal_validation._cleanup_branch_preflight(_target(), {})
            assert result["reason"] == "not-merged-into-source"
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(2, stderr="boom")
        ):
            result = terminal_validation._cleanup_branch_preflight(_target(), {})
            assert result["reason"] == "boom"
        with mock.patch.object(terminal_validation, "run_git", return_value=_done(0)):
            result = terminal_validation._cleanup_branch_preflight(_target(), {})
            assert result["would_delete"] is True
        with (
            mock.patch.object(terminal_validation, "run_git", return_value=_done(0)),
            mock.patch.object(
                terminal_validation,
                "_remote_branch_preflight",
                return_value={"deleted": False, "reason": "remote-blocked"},
            ),
        ):
            result = terminal_validation._cleanup_branch_preflight(_target(remote=True), {})
            assert "would_delete" not in result and "remote-blocked" in str(result["reason"])

    def test_abandon_branch_preflight(self) -> None:
        result = terminal_validation._abandon_branch_preflight(_target(), {}, force=True)
        assert result["force"] is True
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(128, stderr="boom")
        ):
            result = terminal_validation._abandon_branch_preflight(_target(), {}, force=False)
            assert result["reason"] == "boom"
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(0, stdout="abc123\n")
        ):
            result = terminal_validation._abandon_branch_preflight(_target(), {}, force=False)
            assert result["reason"] == "unmerged" and result["unmergedCommits"] == ["abc123"]


class TestBranchPresenceAndCheckout:
    def test_branch_presence(self) -> None:
        with mock.patch.object(terminal_validation, "run_git", return_value=_done(0)):
            assert terminal_validation._branch_presence(Path("/r"), "b") == "present"
        with mock.patch.object(terminal_validation, "run_git", return_value=_done(1)):
            assert terminal_validation._branch_presence(Path("/r"), "b") == "absent"
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(2, stderr="boom")
        ):
            assert terminal_validation._branch_presence(Path("/r"), "b") == "boom"

    def test_checked_out_paths(self) -> None:
        with mock.patch.object(
            terminal_validation, "run_git", return_value=_done(128, stderr="boom")
        ):
            assert terminal_validation._checked_out_paths(Path("/r"), "b") == "boom"
        raw = "worktree /w1\0branch refs/heads/b\0\0worktree /w2\0branch refs/heads/other\0\0"
        with mock.patch.object(terminal_validation, "run_git", return_value=_done(0, stdout=raw)):
            found = terminal_validation._checked_out_paths(Path("/r"), "b")
            assert found == {Path("/w1").resolve()}


class TestProviderAndResultBlockers:
    def test_provider_blockers(self) -> None:
        assert terminal_validation._provider_blockers({"state": "skipped"}) == []
        blockers = terminal_validation._provider_blockers({"containers": "bad", "networks": []})
        assert blockers and blockers[0]["reason"] == "invalid-result"
        blockers = terminal_validation._provider_blockers(
            {"containers": [{"deleted": False, "reason": "busy"}], "networks": []}
        )
        assert blockers and blockers[0]["provider"] == "containers[0]"
        blockers = terminal_validation._provider_blockers(
            {"containers": [], "networks": [], "providerRuntime": {"deleted": False, "reason": "x"}}
        )
        assert blockers and blockers[0]["provider"] == "providerRuntime"

    def test_result_blockers(self) -> None:
        benign = {"code": frozenset({"ok"})}
        values = {
            "code": {"deleted": False, "reason": "ok"},
            "memory": {"deleted": False, "reason": "bad"},
            "nested": {
                "deleted": False,
                "reason": "bad",
                "child": {"remote_deleted": False, "reason": "remote-bad"},
            },
        }
        blockers = terminal_validation._result_blockers(
            "branch", values, done_key="deleted", nested="child", benign=benign
        )
        reasons = [str(b["reason"]) for b in blockers]
        assert "bad" in reasons and "remote-bad" in reasons
