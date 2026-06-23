"""Tests for 05m: carryover-before-cleanup ordering + task work-branch retirement.

The lifecycle must carry the parked memory home (via the existing ``memory_carryover_apply``) *before*
cleanup deletes the worktree it reads from, and cleanup must then retire the just-finalized task work
branches. Parent/source branches belong to the next edge up the task tree and must survive this
child-edge cleanup. The "carryover done" signal is the official ledger itself (no contract stamp).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    ledger_to_text,
    prepend_mapping,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    cleanup_result,
    delete_branch_if_merged_into,
    delete_remote_branch_if_present,
)
from agents_remember.worktrees.modules.guidance import carryover_done, lifecycle_guidance
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from test_worktree_support import git, init_repo


def _contract(tmp: Path, **over: object) -> WorktreeContract:
    base: dict[str, object] = {
        "task_id": "T",
        "task_name": "demo",
        "repo_name": "repo-a",
        "workflow_kind": "light-task",
        "memory_mode": "external",
        "coordination_root": tmp,
        "task_root": tmp,
        "contract_path": tmp / "contract.md",
        "task_artifact": tmp / "task.md",
        "worktree_group": tmp / "grp",
        "code_repo_path": tmp / "code",
        "code_source_branch": "feat/x",
        "code_work_branch": "ar/x",
        "code_base_commit": "base1234",
        "code_worktree": tmp / "wt",
        "memory_repo_path": tmp / "mem",
        "memory_source_branch": "feat/x",
        "memory_work_branch": "ar/x",
        "memory_base_commit": "mbase1234",
        "memory_worktree": tmp / "memwt",
        "ledger_path": tmp / "memwt" / "memory.md",
        "integration_status": "completed",
        "integrated_code_commit": "LANDED1234",
        "code_commit": "CLOSE1234",
        "cleanup": "pending",
    }
    base.update(over)
    return WorktreeContract(**base)  # type: ignore[arg-type]


class CarryoverDoneTests(unittest.TestCase):
    """The carryover signal is the official ledger mapping the landed code commit (no contract stamp)."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _official_memory(self, *mapped_codes: str) -> Path:
        mem = self.tmp / "official-mem"
        init_repo(mem, "main")
        ledger = create_initial_ledger("repo-a", "BASECODE", "BASEMEM")
        (mem / "memory.md").write_text(ledger_to_text(ledger), encoding="utf-8")
        git(mem, "add", "memory.md")
        git(mem, "commit", "-m", "base ledger")
        for code in mapped_codes:
            head = git(mem, "rev-parse", "HEAD").strip()  # a real commit, so %cI resolves
            ledger = prepend_mapping(ledger, code, head)
            (mem / "memory.md").write_text(ledger_to_text(ledger), encoding="utf-8")
            git(mem, "add", "memory.md")
            git(mem, "commit", "-m", f"carry {code}")
        return mem

    def test_external_done_when_ledger_maps_landed_commit(self) -> None:
        mem = self._official_memory("LANDED1234")
        done, done_at = carryover_done(_contract(self.tmp, memory_repo_path=mem))
        self.assertTrue(done)
        self.assertTrue(done_at)  # a real ISO timestamp from `git show %cI`

    def test_external_not_done_when_ledger_lacks_landed_commit(self) -> None:
        mem = self._official_memory("SOMETHINGELSE")
        done, done_at = carryover_done(_contract(self.tmp, memory_repo_path=mem))
        self.assertFalse(done)
        self.assertEqual(done_at, "")

    def test_internal_memory_is_vacuously_done(self) -> None:
        done, done_at = carryover_done(
            _contract(self.tmp, memory_mode="internal", memory_repo_path=None)
        )
        self.assertTrue(done)
        self.assertEqual(done_at, "")


class GuidanceCarryoverRoutingTests(unittest.TestCase):
    """integration-completed routes carryover *before* cleanup, keyed on the ledger signal."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    @patch("agents_remember.worktrees.modules.guidance.carryover_done")
    def test_routes_carryover_pending_when_not_carried(self, cd: MagicMock) -> None:
        cd.return_value = (False, "")
        guidance = lifecycle_guidance(_contract(self.tmp))
        self.assertEqual(guidance["phase"], "carryover-pending")
        self.assertEqual(guidance["nextTool"], "memory_carryover_apply")

    @patch("agents_remember.worktrees.modules.guidance.carryover_done")
    def test_routes_cleanup_pending_with_done_at_when_carried(self, cd: MagicMock) -> None:
        cd.return_value = (True, "2026-06-21T09:00:00+02:00")
        guidance = lifecycle_guidance(_contract(self.tmp))
        self.assertEqual(guidance["phase"], "cleanup-pending")
        self.assertEqual(guidance["nextTool"], "worktree_cleanup")
        self.assertEqual(guidance["carryoverDoneAt"], "2026-06-21T09:00:00+02:00")


class CleanupCarryoverGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done")
    def test_cleanup_refuses_before_carryover(self, cd: MagicMock) -> None:
        cd.return_value = (False, "")
        contract = _contract(self.tmp)
        write_contract(contract.contract_path, contract)
        args = WorktreeArgs(contract_path=contract.contract_path, approved=True, dry_run=False)
        with self.assertRaises(RuntimeError) as caught:
            cleanup_result(args)
        self.assertIn("carryover", str(caught.exception).lower())


class SourceBranchProofTests(unittest.TestCase):
    """Cleanup proves work-branch reachability against the recorded source branch."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.repo = Path(self._td.name) / "repo"
        init_repo(self.repo, "main")
        git(self.repo, "checkout", "-b", "feat/dashboard")
        (self.repo / "dashboard.txt").write_text("dashboard\n", encoding="utf-8")
        git(self.repo, "add", "dashboard.txt")
        git(self.repo, "commit", "-m", "dashboard source")

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_deletes_work_branch_merged_into_recorded_source_while_main_is_checked_out(
        self,
    ) -> None:
        git(self.repo, "checkout", "-b", "ar/task")
        (self.repo / "task.txt").write_text("task\n", encoding="utf-8")
        git(self.repo, "add", "task.txt")
        git(self.repo, "commit", "-m", "task work")
        git(self.repo, "checkout", "feat/dashboard")
        git(self.repo, "merge", "--ff-only", "ar/task")
        git(self.repo, "checkout", "main")

        out = delete_branch_if_merged_into(self.repo, "ar/task", "feat/dashboard", dry_run=False)

        self.assertTrue(out["deleted"])
        self.assertEqual(out["target"], "feat/dashboard")
        self.assertEqual(git(self.repo, "branch", "--list", "ar/task").strip(), "")

    def test_keeps_work_branch_not_merged_into_recorded_source(self) -> None:
        git(self.repo, "checkout", "main")
        git(self.repo, "checkout", "-b", "ar/unmerged")
        (self.repo / "unmerged.txt").write_text("unmerged\n", encoding="utf-8")
        git(self.repo, "add", "unmerged.txt")
        git(self.repo, "commit", "-m", "unmerged work")
        git(self.repo, "checkout", "main")

        out = delete_branch_if_merged_into(
            self.repo, "ar/unmerged", "feat/dashboard", dry_run=False
        )

        self.assertFalse(out["deleted"])
        self.assertEqual(out["reason"], "not-merged-into-source")
        self.assertEqual(out["target"], "feat/dashboard")
        self.assertIn("ar/unmerged", git(self.repo, "branch", "--list", "ar/unmerged"))


class CleanupChildEdgeTests(unittest.TestCase):
    """Full cleanup removes the finalized child task branches, not their parent/source branches."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _repo_with_landed_task(self, name: str) -> tuple[Path, Path]:
        repo = self.tmp / name
        init_repo(repo, "main")
        git(repo, "checkout", "-b", "feat/dashboard")
        (repo / "dashboard.txt").write_text(f"{name} dashboard\n", encoding="utf-8")
        git(repo, "add", "dashboard.txt")
        git(repo, "commit", "-m", "dashboard source")
        git(repo, "checkout", "-b", "ar/task")
        (repo / "task.txt").write_text(f"{name} task\n", encoding="utf-8")
        git(repo, "add", "task.txt")
        git(repo, "commit", "-m", "task work")
        git(repo, "checkout", "feat/dashboard")
        git(repo, "merge", "--ff-only", "ar/task")
        git(repo, "checkout", "main")
        worktree = self.tmp / "grp" / name
        git(repo, "worktree", "add", str(worktree), "ar/task")
        return repo, worktree

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done")
    def test_cleanup_removes_child_branches_and_preserves_parent_sources(
        self, carryover: MagicMock
    ) -> None:
        carryover.return_value = (True, "2026-06-24T09:00:00+02:00")
        code_repo, code_worktree = self._repo_with_landed_task("code")
        memory_repo, memory_worktree = self._repo_with_landed_task("memory")
        contract = _contract(
            self.tmp,
            code_repo_path=code_repo,
            memory_repo_path=memory_repo,
            code_source_branch="feat/dashboard",
            code_work_branch="ar/task",
            memory_source_branch="feat/dashboard",
            memory_work_branch="ar/task",
            worktree_group=self.tmp / "grp",
            code_worktree=code_worktree,
            memory_worktree=memory_worktree,
            ledger_path=memory_worktree / "memory.md",
        )
        write_contract(contract.contract_path, contract)

        result = cleanup_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=True,
                dry_run=False,
                teardown_providers=False,
            )
        )
        branches = result.payload["branches"]  # type: ignore[index]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(branches), ["code", "memory"])
        self.assertEqual(git(code_repo, "branch", "--list", "ar/task").strip(), "")
        self.assertEqual(git(memory_repo, "branch", "--list", "ar/task").strip(), "")
        self.assertIn("feat/dashboard", git(code_repo, "branch", "--list", "feat/dashboard"))
        self.assertIn("feat/dashboard", git(memory_repo, "branch", "--list", "feat/dashboard"))


class CleanupDryRunDirectoryTests(unittest.TestCase):
    """Dry-run directory reporting models paths cleanup has already scheduled."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    @patch("agents_remember.worktrees.modules.cleanup.teardown_worktree_providers")
    @patch("agents_remember.worktrees.modules.cleanup.carryover_done")
    def test_worktree_group_would_remove_when_only_scheduled_paths_remain(
        self, carryover: MagicMock, teardown: MagicMock
    ) -> None:
        carryover.return_value = (True, "2026-06-23T09:00:00+02:00")
        code_repo = self.tmp / "code"
        memory_repo = self.tmp / "mem"
        init_repo(code_repo, "main")
        init_repo(memory_repo, "main")
        worktree_group = self.tmp / "grp"
        code_worktree = worktree_group / "code"
        memory_worktree = worktree_group / "memory"
        provider_runtime = worktree_group / "provider-runtime"
        code_worktree.mkdir(parents=True)
        memory_worktree.mkdir()
        provider_runtime.mkdir()
        teardown.return_value = {
            "state": "would-teardown",
            "providerRuntime": {
                "path": provider_runtime.as_posix(),
                "removed": False,
                "would_remove": True,
            },
        }
        contract = _contract(
            self.tmp,
            code_repo_path=code_repo,
            memory_repo_path=memory_repo,
            worktree_group=worktree_group,
            code_worktree=code_worktree,
            memory_worktree=memory_worktree,
            ledger_path=memory_worktree / "memory.md",
        )
        write_contract(contract.contract_path, contract)

        result = cleanup_result(WorktreeArgs(contract_path=contract.contract_path, dry_run=True))
        directories = result.payload["directories"]  # type: ignore[index]

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.payload["state"], "would-cleanup")  # type: ignore[index]
        self.assertIn("Cleanup would reclaim", result.payload["summary"])  # type: ignore[index]
        self.assertTrue(directories["worktree_group"]["would_remove"])  # type: ignore[index]


class RemoteBranchDeleteTests(unittest.TestCase):
    @patch("agents_remember.worktrees.modules.cleanup.run_git")
    def test_absent_remote_is_not_pushed(self, run_git: MagicMock) -> None:
        run_git.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        out = delete_remote_branch_if_present(Path("/x"), "feat/x", dry_run=False)
        self.assertFalse(out["remote_deleted"])
        self.assertEqual(out["reason"], "already-absent")

    @patch("agents_remember.worktrees.modules.cleanup.run_git")
    def test_present_remote_is_deleted(self, run_git: MagicMock) -> None:
        run_git.side_effect = [
            SimpleNamespace(returncode=0, stdout="sha1234\trefs/heads/feat/x\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        out = delete_remote_branch_if_present(Path("/x"), "feat/x", dry_run=False)
        self.assertTrue(out["remote_deleted"])


if __name__ == "__main__":
    unittest.main()
