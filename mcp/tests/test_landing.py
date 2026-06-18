"""Tests for the successful-landing arc observation (slice 5h).

The probe is best-effort and honest: a ref it cannot observe is ``planned`` (reachable, not there
yet) or ``missing`` (the probe could not run), never invented. ``subprocess.run`` is mocked so the
suite never touches the network or requires ``gh``.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents_remember.worktrees.modules.landing import landing_refs
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
        "contract_path": tmp / "contract.md",
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
                '[{"number":128,"state":"OPEN","url":"http://x/128","baseRefName":"main"}]'
            )

        run.side_effect = fake
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        self.assertEqual(by_kind["origin-feat"]["state"], "pushed")
        self.assertEqual(by_kind["origin-feat"]["factState"], "observed")
        self.assertEqual(by_kind["pr"]["label"], "PR #128")
        self.assertEqual(by_kind["pr"]["state"], "open")
        self.assertEqual(by_kind["origin-main"]["state"], "tip")

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_gh_absent_degrades_pr_to_missing(self, run: MagicMock) -> None:
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

    @patch("agents_remember.worktrees.modules.landing.subprocess.run")
    def test_offline_probe_is_missing(self, run: MagicMock) -> None:
        run.side_effect = lambda cmd, **_: _completed("", returncode=128)
        refs = landing_refs(_contract(self.tmp))
        assert refs is not None
        by_kind = {str(ref["kind"]): ref for ref in refs}
        self.assertEqual(by_kind["origin-feat"]["factState"], "missing")
        self.assertEqual(by_kind["pr"]["factState"], "missing")


if __name__ == "__main__":
    unittest.main()
