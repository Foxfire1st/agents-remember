"""Tests for the successful-landing arc observation (slice 5h; hardened 5l P2).

The probe is best-effort and honest: a ref it cannot observe is ``planned`` (reachable, not there
yet) or ``missing`` (the probe could not run), never invented. ``subprocess.run`` is mocked so the
suite never touches the network or requires ``gh``.

Slice 5l P2 hardens it: ``origin/main`` is probed directly (visible across the whole landing window,
not only inferred from a PR base), its ``state`` tracks whether *this* work landed (PR merged) rather
than merely whether ``origin/main`` exists, and the PR ref carries gh's open/merge timestamp.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents_remember.kernel.git_command import GIT_REPOSITORY_SELECTOR_ENV
from agents_remember.worktrees.modules.landing import _default_branch, landing_refs
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _contract(tmp: Path, **over: object) -> WorktreeContract:
    base: dict[str, object] = {
        "task_id": "T",
        "task_name": "demo",
        "repo_name": "repo-a",
        "workflow_kind": "chat",
        "memory_mode": "external",
        "coordination_root": tmp,
        "task_root": tmp,
        "contract_path": tmp / "series-contract.md",
        "leaf_id": "demo",
        "task_artifact": tmp / "task.md",
        "worktree_group": tmp / "grp",
        "code_repo_path": tmp,
        "code_source_branch": "feat/x",
        "code_work_branch": "ar/x",
        "code_base_commit": "abc1234",
        "code_worktree": tmp / "wt",
        "memory_repo_path": tmp,
        "memory_source_branch": "main",
        "closeout_status": "completed",
    }
    base.update(over)
    return WorktreeContract(**base)  # type: ignore[arg-type]


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], returncode, stdout=stdout, stderr="")


class LandingRefsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_inactive_before_closeout_returns_none(self) -> None:
        contract = _contract(
            self.tmp,
            closeout_status="not-started",
            integration_status="not-started",
            cleanup="pending",
        )
        self.assertIsNone(landing_refs(contract))

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_observed_pushed_branch_and_open_pr(self, run: MagicMock) -> None:
        def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if "ls-remote" in cmd:
                return _completed("deadbeef1234\trefs/heads/feat/x\n")
            return _completed(
                '[{"number":128,"state":"OPEN","url":"http://x/128","baseRefName":"main",'
                '"createdAt":"2026-06-20T10:00:00Z","mergedAt":null}]'
            )

        run.side_effect = fake
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        self.assertEqual(by_kind["origin-feat"]["state"], "pushed")
        self.assertEqual(by_kind["origin-feat"]["factState"], "observed")
        self.assertEqual(by_kind["pr"]["label"], "PR #128")
        self.assertEqual(by_kind["pr"]["state"], "open")
        # gh's createdAt rides along on an open PR (5l P2)
        self.assertEqual(by_kind["pr"]["at"], "2026-06-20T10:00:00Z")
        # an open PR has not landed yet: the target reads honestly as planned, not done
        self.assertEqual(by_kind["origin-main"]["state"], "planned")
        self.assertEqual(by_kind["origin-main"]["factState"], "observed")

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_merged_pr_marks_origin_main_merged_with_merged_at(self, run: MagicMock) -> None:
        def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if "ls-remote" in cmd:
                return _completed("deadbeef1234\trefs/heads/main\n")
            return _completed(
                '[{"number":128,"state":"MERGED","url":"http://x/128","baseRefName":"main",'
                '"createdAt":"2026-06-20T10:00:00Z","mergedAt":"2026-06-21T09:30:00Z"}]'
            )

        run.side_effect = fake
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        self.assertEqual(by_kind["pr"]["state"], "merged")
        # the milestone time is mergedAt once merged (5l P2)
        self.assertEqual(by_kind["pr"]["at"], "2026-06-21T09:30:00Z")
        # the PR merged -> this work landed in the protected target
        self.assertEqual(by_kind["origin-main"]["state"], "merged")
        self.assertEqual(by_kind["origin-main"]["factState"], "observed")

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_origin_main_probed_directly_before_any_pr(self, run: MagicMock) -> None:
        """The protected target is visible from a direct ls-remote even with no PR yet (5l P2)."""

        def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if "--symref" in cmd:  # _default_branch resolves origin HEAD
                return _completed("ref: refs/heads/main\tHEAD\ncafe1234abcd\tHEAD\n")
            if "ls-remote" in cmd and "main" in cmd:  # origin/main tip
                return _completed("cafe1234abcd\trefs/heads/main\n")
            if "ls-remote" in cmd:  # origin/feat pushed
                return _completed("deadbeef1234\trefs/heads/feat/x\n")
            return _completed("[]")  # gh ran, no PR opened yet

        run.side_effect = fake
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        # origin-main exists before any PR, named from the remote default branch
        self.assertEqual(by_kind["origin-main"]["label"], "origin/main")
        self.assertEqual(by_kind["origin-main"]["state"], "planned")
        self.assertEqual(by_kind["origin-main"]["factState"], "observed")
        self.assertEqual(by_kind["origin-main"]["detail"], "cafe1234ab")
        self.assertEqual(by_kind["pr"]["state"], "planned")  # gh ran, no PR

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_gh_absent_still_shows_origin_main_via_ls_remote(self, run: MagicMock) -> None:
        def fake(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if "ls-remote" in cmd:
                return _completed("")  # origin reachable, branch not pushed yet
            raise FileNotFoundError("gh not installed")

        run.side_effect = fake
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        self.assertEqual(by_kind["origin-feat"]["state"], "planned")
        self.assertEqual(by_kind["pr"]["factState"], "missing")
        # origin-main is observed via ls-remote independent of gh (5l P2)
        self.assertEqual(by_kind["origin-main"]["state"], "planned")
        self.assertEqual(by_kind["origin-main"]["factState"], "observed")

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_offline_probe_is_missing(self, run: MagicMock) -> None:
        run.side_effect = lambda cmd, **_: _completed("", returncode=128)
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        self.assertEqual(by_kind["origin-feat"]["factState"], "missing")
        self.assertEqual(by_kind["pr"]["factState"], "missing")
        # the protected-target probe could not run either -> honest missing/unknown
        self.assertEqual(by_kind["origin-main"]["factState"], "missing")
        self.assertEqual(by_kind["origin-main"]["state"], "unknown")

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_the_gh_probe_does_not_inherit_the_repository_selectors(self, run: MagicMock) -> None:
        # `gh` is not git, but it resolves the repository through git, so an exported GIT_DIR
        # would have it list another repository's pull requests under this branch's name and
        # the landing arc would report a PR that has nothing to do with this worktree.
        # `cwd=repo` does not outrank the selectors for gh any more than it does for git.
        captured: list[dict[str, object]] = []

        def fake(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured.append({"cmd": cmd, **kwargs})
            return _completed("[]")

        run.side_effect = fake
        selectors = {name: str(self.tmp / "decoy") for name in GIT_REPOSITORY_SELECTOR_ENV}
        with patch.dict(os.environ, selectors):
            landing_refs(_contract(self.tmp))

        gh_calls = [call for call in captured if str(call["cmd"][0]) == "gh"]  # type: ignore[index]
        self.assertTrue(gh_calls)  # the probe really ran; the assertions below are not vacuous
        for call in gh_calls:
            environment = call.get("env")
            self.assertIsInstance(environment, dict)
            assert isinstance(environment, dict)
            self.assertTrue(set(GIT_REPOSITORY_SELECTOR_ENV).isdisjoint(environment))
            self.assertIn("PATH", environment)  # only the selectors go; gh still has to run


class DefaultBranchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_parses_head_symref(self, run: MagicMock) -> None:
        run.return_value = _completed("ref: refs/heads/trunk\tHEAD\nabcd1234\tHEAD\n")
        self.assertEqual(_default_branch(self.tmp), "trunk")

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_falls_back_to_main_on_probe_failure(self, run: MagicMock) -> None:
        run.return_value = _completed("", returncode=128)
        self.assertEqual(_default_branch(self.tmp), "main")


if __name__ == "__main__":
    unittest.main()
