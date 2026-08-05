"""Tests for 05m: carryover-before-cleanup ordering + task work-branch retirement.

The lifecycle must carry the parked memory home (via the existing ``memory_carryover_apply``) *before*
cleanup deletes the worktree it reads from, and cleanup must then retire the just-finalized task work
branches. Parent/source branches belong to the next edge up the task tree and must survive this
child-edge cleanup. The "carryover done" signal is the official ledger itself (no contract stamp).
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents_remember.errors import CitationCacheError
from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    ledger_to_text,
    prepend_mapping,
)
from agents_remember.memory_quality.style.citations import source_index, source_index_cache
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.observer.drift_snapshots import drift_snapshot_path
from agents_remember.observer.paths import DRIFT_SNAPSHOT_SCHEMA
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules import terminal_validation
from agents_remember.worktrees.modules.abandon import abandon_result
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    LocalBranchPresence,
    cleanup_result,
    delete_branch_if_merged,
    delete_branch_if_merged_into,
    delete_remote_branch_if_present,
)
from agents_remember.worktrees.modules.guidance import carryover_done, lifecycle_guidance
from agents_remember.worktrees.modules.terminal_validation import TerminalPreflight
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)
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
        "contract_path": tmp / "series-contract.md",
        "leaf_id": "demo",
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
        "lifecycle_id": "LC-T",
    }
    base.update(over)
    return WorktreeContract(**base)  # type: ignore[arg-type]


def _write_drift_snapshot(coordination_root: Path, *, repository: str, branch: str) -> Path:
    path = drift_snapshot_path(coordination_root, repository=repository, branch=branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": DRIFT_SNAPSHOT_SCHEMA,
                "repository": repository,
                "branch": branch,
                "checkedAt": "2026-06-13T18:00:00+00:00",
                "counts": {"drifted": 1},
                "actionableCount": 1,
                "rows": [],
            }
        ),
        encoding="utf-8",
    )
    return path


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
        self.assertEqual(guidance.get("nextTool"), "memory_carryover_apply")

    @patch("agents_remember.worktrees.modules.guidance.carryover_done")
    def test_routes_cleanup_pending_with_done_at_when_carried(self, cd: MagicMock) -> None:
        cd.return_value = (True, "2026-06-21T09:00:00+02:00")
        guidance = lifecycle_guidance(_contract(self.tmp))
        self.assertEqual(guidance["phase"], "cleanup-pending")
        self.assertEqual(guidance.get("nextTool"), "worktree_cleanup")
        self.assertEqual(guidance.get("carryoverDoneAt"), "2026-06-21T09:00:00+02:00")


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
        task_doc_path, _task_markdown = write_task_doc(
            contract.task_root,
            TaskDocument.model_validate(
                {
                    "id": "demo",
                    "slug": "task",
                    "title": "Pending cleanup step",
                    "kind": "light",
                    "status": "inProgress",
                    "repo": "repo-a",
                    "createdAt": "2026-08-03T12:00:00+00:00",
                    "steps": [
                        {
                            "id": "S-final",
                            "title": "Clean up the worktree",
                            "status": "pending",
                        }
                    ],
                }
            ),
        )
        task_before = task_doc_path.read_bytes()

        with (
            patch.object(
                terminal_validation,
                "_remote_branch_preflight",
                return_value={"remote_deleted": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.delete_remote_branch_if_present",
                return_value={"remote_deleted": False, "reason": "already-absent"},
            ),
        ):
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
        assert isinstance(branches, dict)
        self.assertEqual(sorted(branches.keys()), ["code", "memory", "memory_integration"])
        self.assertEqual(branches["memory_integration"]["reason"], "already-absent")
        self.assertEqual(git(code_repo, "branch", "--list", "ar/task").strip(), "")
        self.assertEqual(git(memory_repo, "branch", "--list", "ar/task").strip(), "")
        self.assertIn("feat/dashboard", git(code_repo, "branch", "--list", "feat/dashboard"))
        self.assertIn("feat/dashboard", git(memory_repo, "branch", "--list", "feat/dashboard"))
        self.assertEqual(task_doc_path.read_bytes(), task_before)
        self.assertEqual(read_task_doc(task_doc_path).steps[0].status, "pending")
        self.assertEqual(read_task_doc(task_doc_path).status, "inProgress")

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done")
    def test_cleanup_deletes_remote_branch_when_local_branch_is_already_absent(
        self, carryover: MagicMock
    ) -> None:
        carryover.return_value = (True, "2026-08-03T09:00:00+02:00")
        code_repo, code_worktree = self._repo_with_landed_task("remote-code")
        memory_repo, memory_worktree = self._repo_with_landed_task("remote-memory")
        origin = self.tmp / "origin.git"
        origin.mkdir()
        subprocess.run(
            ["git", "init", "--bare"],
            cwd=origin,
            text=True,
            capture_output=True,
            check=True,
        )
        git(code_repo, "remote", "add", "origin", origin.as_posix())
        git(code_repo, "push", "origin", "ar/task")
        git(code_repo, "worktree", "remove", code_worktree.as_posix())
        git(code_repo, "branch", "-D", "ar/task")
        self.assertEqual(git(code_repo, "branch", "--list", "ar/task"), "")
        self.assertIn("refs/heads/ar/task", git(code_repo, "ls-remote", "origin", "ar/task"))

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
        assert isinstance(branches, dict)
        self.assertEqual(branches["code"]["reason"], "already-absent")
        self.assertTrue(branches["code"]["remote"]["remote_deleted"])
        self.assertEqual(git(code_repo, "ls-remote", "origin", "ar/task"), "")
        self.assertEqual(load_contract(contract.contract_path).cleanup, "completed")


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


class CleanupDriftSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done")
    def test_cleanup_dry_run_reports_worktree_drift_snapshot_removal(
        self, carryover: MagicMock
    ) -> None:
        carryover.return_value = (True, "2026-06-24T09:00:00+02:00")
        code_repo = self.tmp / "code"
        memory_repo = self.tmp / "mem"
        init_repo(code_repo, "main")
        init_repo(memory_repo, "main")
        git(code_repo, "branch", "feat/x")
        git(memory_repo, "branch", "feat/x")
        contract = _contract(self.tmp, code_repo_path=code_repo, memory_repo_path=memory_repo)
        write_contract(contract.contract_path, contract)
        snapshot = _write_drift_snapshot(
            contract.coordination_root,
            repository=contract.code_worktree.name,
            branch=contract.code_work_branch,
        )

        result = cleanup_result(WorktreeArgs(contract_path=contract.contract_path, dry_run=True))
        drift_snapshots = result.payload["drift_snapshots"]  # type: ignore[index]

        self.assertTrue(snapshot.exists())
        self.assertTrue(drift_snapshots["code"]["would_remove"])  # type: ignore[index]
        self.assertFalse(drift_snapshots["code"]["removed"])  # type: ignore[index]

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done")
    def test_cleanup_removes_exact_worktree_drift_snapshot(self, carryover: MagicMock) -> None:
        carryover.return_value = (True, "2026-06-24T09:00:00+02:00")
        code_repo = self.tmp / "code"
        memory_repo = self.tmp / "mem"
        init_repo(code_repo, "main")
        init_repo(memory_repo, "main")
        git(code_repo, "branch", "feat/x")
        git(memory_repo, "branch", "feat/x")
        contract = _contract(self.tmp, code_repo_path=code_repo, memory_repo_path=memory_repo)
        write_contract(contract.contract_path, contract)
        snapshot = _write_drift_snapshot(
            contract.coordination_root,
            repository=contract.code_worktree.name,
            branch=contract.code_work_branch,
        )
        other = _write_drift_snapshot(
            contract.coordination_root,
            repository="other-worktree",
            branch=contract.code_work_branch,
        )

        result = cleanup_result(
            WorktreeArgs(
                contract_path=contract.contract_path,
                approved=True,
                dry_run=False,
                teardown_providers=False,
            )
        )
        drift_snapshots = result.payload["drift_snapshots"]  # type: ignore[index]

        self.assertEqual(result.returncode, 0)
        self.assertFalse(snapshot.exists())
        self.assertTrue(other.exists())
        self.assertTrue(drift_snapshots["code"]["removed"])  # type: ignore[index]


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
            SimpleNamespace(returncode=0, stdout="origin\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="sha1234\trefs/heads/feat/x\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        out = delete_remote_branch_if_present(Path("/x"), "feat/x", dry_run=False)
        self.assertTrue(out["remote_deleted"])

    @patch("agents_remember.worktrees.modules.cleanup.run_git")
    def test_remote_query_failure_is_not_reported_absent(self, run_git: MagicMock) -> None:
        run_git.return_value = SimpleNamespace(
            returncode=128, stdout="", stderr="remote config exploded"
        )
        out = delete_remote_branch_if_present(Path("/x"), "feat/x", dry_run=False)
        self.assertEqual(out["reason"], "remote config exploded")

    def test_terminal_remote_preflight_reports_present_and_absent_refs(self) -> None:
        configured = SimpleNamespace(returncode=0, stdout="origin\n", stderr="")
        for ref_output, expected in (
            ("sha\trefs/heads/feat/x\n", {"remote_deleted": False, "would_delete": True}),
            ("", {"remote_deleted": False, "reason": "already-absent"}),
        ):
            with (
                self.subTest(ref_output=ref_output),
                patch.object(
                    terminal_validation,
                    "run_git",
                    side_effect=[
                        configured,
                        SimpleNamespace(returncode=0, stdout=ref_output, stderr=""),
                    ],
                ),
            ):
                self.assertEqual(
                    terminal_validation._remote_branch_preflight(Path("/x"), "feat/x"),
                    expected,
                )

    def test_terminal_remote_preflight_reports_query_and_probe_failures(self) -> None:
        cases = (
            (
                [SimpleNamespace(returncode=128, stdout="", stderr="config exploded")],
                "config exploded",
            ),
            (
                [
                    SimpleNamespace(returncode=0, stdout="origin\n", stderr=""),
                    SimpleNamespace(returncode=128, stdout="", stderr="probe exploded"),
                ],
                "probe exploded",
            ),
            (
                [
                    SimpleNamespace(returncode=0, stdout="origin\n", stderr=""),
                    subprocess.TimeoutExpired(["git", "ls-remote"], 30),
                ],
                "remote-unreachable",
            ),
        )
        for side_effect, reason in cases:
            with (
                self.subTest(reason=reason),
                patch.object(terminal_validation, "run_git", side_effect=side_effect),
            ):
                result = terminal_validation._remote_branch_preflight(Path("/x"), "feat/x")
                self.assertEqual(result["reason"], reason)


class LocalBranchDeleteTests(unittest.TestCase):
    def test_presence_error_and_absence_are_truthful_without_deletion(self) -> None:
        cases = (
            (LocalBranchPresence("error", "ref query exploded"), "ref query exploded"),
            (LocalBranchPresence("absent"), "already-absent"),
        )
        for presence, reason in cases:
            with (
                self.subTest(reason=reason),
                patch(
                    "agents_remember.worktrees.modules.cleanup.local_branch_presence",
                    return_value=presence,
                ),
                patch("agents_remember.worktrees.modules.cleanup.run_git") as run_git_mock,
            ):
                result = delete_branch_if_merged(Path("/x"), "ar/task", dry_run=False)
                self.assertFalse(result["deleted"])
                self.assertEqual(result["reason"], reason)
                run_git_mock.assert_not_called()

    def test_present_branch_dry_run_success_and_failure_are_distinct(self) -> None:
        outcomes = (
            (True, None, {"deleted": False, "would_delete": True}),
            (False, SimpleNamespace(returncode=0, stdout="", stderr=""), {"deleted": True}),
            (
                False,
                SimpleNamespace(returncode=1, stdout="", stderr="delete refused"),
                {"deleted": False, "reason": "delete refused"},
            ),
        )
        for dry_run, git_result, expected in outcomes:
            with (
                self.subTest(dry_run=dry_run, expected=expected),
                patch(
                    "agents_remember.worktrees.modules.cleanup.local_branch_presence",
                    return_value=LocalBranchPresence("present"),
                ),
                patch(
                    "agents_remember.worktrees.modules.cleanup.run_git",
                    return_value=git_result,
                ) as run_git_mock,
            ):
                result = delete_branch_if_merged(Path("/x"), "ar/task", dry_run=dry_run)
                self.assertEqual(
                    {key: result[key] for key in expected},
                    expected,
                )
                if dry_run:
                    run_git_mock.assert_not_called()
                else:
                    run_git_mock.assert_called_once_with(Path("/x"), ["branch", "-d", "ar/task"])


class CitationCacheLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)

    def contract(self, name: str) -> WorktreeContract:
        code = self.tmp / f"{name}-code"
        memory = self.tmp / f"{name}-memory"
        code.mkdir()
        memory.mkdir()
        contract = _contract(
            self.tmp,
            task_name=name,
            contract_path=self.tmp / "tasks" / name / "series-contract.md",
            code_worktree=code,
            memory_worktree=memory,
            worktree_group=self.tmp / f"{name}-group",
        )
        write_contract(contract.contract_path, contract)
        return contract

    def preflight(self, *blockers: dict[str, object]) -> TerminalPreflight:
        return TerminalPreflight(
            worktrees={
                "code": {"removed": False, "would_remove": True},
                "memory": {"removed": False, "would_remove": True},
            },
            branches={
                "code": {"deleted": False, "would_delete": True},
                "memory": {"deleted": False, "would_delete": True},
                "memory_integration": {"deleted": False, "reason": "already-absent"},
            },
            blockers=tuple(blockers),
        )

    def cache(self, contract: WorktreeContract) -> Path:
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        authority.namespace.mkdir(parents=True)
        authority.control_dir.mkdir(parents=True)
        authority.control_lock.write_bytes(b"")
        (authority.namespace / "ready.json").write_text("ready\n", encoding="utf-8")
        (authority.namespace / "index.sqlite3").write_bytes(b"exact-ready-generation")
        return authority.namespace

    def cache_bytes(self, cache: Path) -> dict[str, bytes]:
        return {
            path.relative_to(cache).as_posix(): path.read_bytes()
            for path in sorted(cache.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def trees(contract: WorktreeContract, authority: object) -> Trees:
        assert contract.memory_worktree is not None
        assert isinstance(authority, source_index_cache.ManagedCacheAuthority)
        return Trees(
            contract.code_worktree,
            contract.memory_worktree,
            cache_authority=authority,
        )

    @staticmethod
    def fail_publication() -> None:
        raise RuntimeError("publication exploded")

    def test_cleanup_dirty_refusal_leaves_cache_intact(self) -> None:
        contract = self.contract("dirty-cleanup")
        cache = self.cache(contract)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight({"worktree": "code", "reason": "dirty"}),
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(cache.exists())
        self.assertNotIn("citation_source_index", result.payload)

    def test_cleanup_integration_refusal_precedes_cache_reclamation(self) -> None:
        contract = _contract(
            self.tmp,
            integration_status="not-started",
            contract_path=self.tmp / "tasks" / "pending" / "series-contract.md",
            code_worktree=self.tmp / "pending-code",
            memory_worktree=self.tmp / "pending-memory",
        )
        cache = self.cache(contract)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            self.assertRaisesRegex(RuntimeError, "integration.status completed"),
        ):
            cleanup_result(WorktreeArgs(contract_path=contract.contract_path, approved=True))
        self.assertTrue(cache.exists())

    def test_successful_cleanup_removes_only_exact_namespace(self) -> None:
        contract = self.contract("cleanup-target")
        neighbor = self.contract("cleanup-neighbor")
        target_cache = self.cache(contract)
        neighbor_cache = self.cache(neighbor)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={"code": {"deleted": True}, "memory": {"deleted": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories",
                return_value={},
            ),
            patch("agents_remember.worktrees.modules.cleanup.write_contract"),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.payload["citation_source_index"]["removed"])  # type: ignore[index]
        self.assertFalse(target_cache.exists())
        self.assertTrue(neighbor_cache.exists())

    def test_cleanup_live_lease_blocks_before_worktree_removal(self) -> None:
        contract = self.contract("live-cleanup")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = authority.control_lock.open("r+b")
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            with (
                patch(
                    "agents_remember.worktrees.modules.cleanup.load_contract",
                    return_value=contract,
                ),
                patch(
                    "agents_remember.worktrees.modules.cleanup.carryover_done",
                    return_value=(True, "now"),
                ),
                patch(
                    "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                    return_value=False,
                ),
                patch(
                    "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                    return_value=self.preflight(),
                ),
                patch.object(source_index_cache, "LOCK_TIMEOUT_SECONDS", 0.02),
                patch("agents_remember.worktrees.modules.cleanup._removed_worktrees") as removed,
            ):
                result = cleanup_result(
                    WorktreeArgs(contract_path=contract.contract_path, approved=True)
                )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.payload["citation_source_index"]["reason"],  # type: ignore[index]
                "live-lease-timeout",
            )
            removed.assert_not_called()
            self.assertTrue(cache.exists())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def test_abandon_refusal_leaves_cache_intact(self) -> None:
        contract = self.contract("dirty-abandon")
        cache = self.cache(contract)
        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.abandon.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight({"worktree": "memory", "reason": "dirty"}),
            ),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertTrue(cache.exists())
        self.assertNotIn("citation_source_index", result.payload)

    def test_abandon_approval_refusal_precedes_cache_reclamation(self) -> None:
        contract = self.contract("unapproved-abandon")
        cache = self.cache(contract)
        with self.assertRaisesRegex(RuntimeError, "requires --approved"):
            abandon_result(WorktreeArgs(contract_path=contract.contract_path))
        self.assertTrue(cache.exists())

    def test_successful_force_abandon_removes_only_exact_namespace(self) -> None:
        contract = self.contract("abandon-target")
        neighbor = self.contract("abandon-neighbor")
        target_cache = self.cache(contract)
        neighbor_cache = self.cache(neighbor)
        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                    "memory_integration": {"deleted": False, "reason": "already-absent"},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_directories",
                return_value={},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.teardown_worktree_providers",
                return_value={"state": "removed"},
            ),
            patch("agents_remember.worktrees.modules.abandon.write_contract"),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True, force=True)
            )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.payload["citation_source_index"]["removed"])  # type: ignore[index]
        self.assertFalse(target_cache.exists())
        self.assertTrue(neighbor_cache.exists())

    def test_cleanup_worktree_failure_after_preflight_preserves_contract_and_cache(self) -> None:
        contract = self.contract("cleanup-worktree-failure")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        before_contract = contract.contract_path.read_bytes()
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": False, "reason": "remove refused"}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches"
            ) as deleted_branches,
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ) as remove_drift,
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories"
            ) as remove_directories,
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertTrue(result.payload["citation_source_index"]["preserved"])  # type: ignore[index]
        deleted_branches.assert_not_called()
        remove_drift.assert_not_called()
        remove_directories.assert_not_called()

    def test_cleanup_provider_failure_stops_before_worktree_and_branch_mutation(self) -> None:
        contract = self.contract("cleanup-provider-failure")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        providers = {
            "state": "blocked",
            "containers": [{"removed": False, "reason": "container refused"}],
            "networks": [],
            "providerRuntime": {"removed": False, "reason": "already-absent"},
        }
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.teardown_worktree_providers",
                return_value=providers,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees"
            ) as removed_worktrees,
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches"
            ) as deleted_branches,
        ):
            result = cleanup_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["providers"], providers)
        self.assertEqual(result.payload["removed_worktrees"], {})
        self.assertEqual(result.payload["branches"], {})
        self.assertEqual(self.cache_bytes(cache), before_cache)
        removed_worktrees.assert_not_called()
        deleted_branches.assert_not_called()

    def test_abandon_branch_refusal_after_passing_preview_is_nonzero_and_preserves_cache(
        self,
    ) -> None:
        contract = self.contract("abandon-branch-refusal")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        before_contract = contract.contract_path.read_bytes()
        providers = {
            "state": "torn-down",
            "containers": [],
            "networks": [],
            "providerRuntime": {"removed": False, "reason": "already-absent"},
        }
        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.abandon.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.teardown_worktree_providers",
                return_value=providers,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_branches",
                return_value={"code": {"deleted": False, "reason": "git branch -d refused"}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_directories", return_value={}
            ),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "abandon-blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_cleanup_late_remote_branch_failure_is_nonzero_and_preserves_cache(self) -> None:
        contract = self.contract("cleanup-remote-refusal")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={
                    "code": {
                        "deleted": True,
                        "remote": {"remote_deleted": False, "reason": "remote refused"},
                    },
                    "memory": {"deleted": True},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["blockers"][0]["reason"], "remote refused")  # type: ignore[index]
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_post_preflight_ref_query_error_is_not_already_absent(self) -> None:
        contract = self.contract("post-preview-query-error")
        contract.code_repo_path.mkdir()
        assert contract.memory_repo_path is not None
        contract.memory_repo_path.mkdir()
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.local_branch_presence",
                return_value=LocalBranchPresence("error", "ref query exploded"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        branches = result.payload["branches"]  # type: ignore[index]
        assert isinstance(branches, dict)
        self.assertTrue(
            all(
                isinstance(item, dict) and item["reason"] == "ref query exploded"
                for item in branches.values()
            )
        )
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_unexpected_terminal_helper_exception_is_a_blocked_result(self) -> None:
        contract = self.contract("terminal-helper-exception")
        cache = self.cache(contract)
        before_cache = self.cache_bytes(cache)
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                side_effect=RuntimeError("helper exploded"),
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertIn("helper exploded", result.payload["blockers"][0]["reason"])  # type: ignore[index]
        self.assertEqual(self.cache_bytes(cache), before_cache)

    def test_absent_namespace_concurrent_first_builder_refuses_after_terminal_success(
        self,
    ) -> None:
        contract = self.contract("absent-concurrent-builder")
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        trees = self.trees(contract, authority)
        started = threading.Event()
        outcome: list[str] = []
        updated = amend_contract(contract, ContractCells(cleanup="completed"))
        with source_index_cache.terminal_namespace_guard(
            contract, requested_contract_path=contract.contract_path
        ) as guard:
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            result = guard.complete(
                outcome="completed",
                publish=lambda: write_contract(contract.contract_path, updated),
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["reason"], "already-absent")
        self.assertEqual(len(outcome), 1)
        self.assertIn("terminal", outcome[0])
        self.assertFalse(authority.namespace.exists())

    def test_existing_namespace_queued_builder_refuses_after_terminal_success(self) -> None:
        contract = self.contract("existing-concurrent-builder")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        trees = self.trees(contract, authority)
        started = threading.Event()
        outcome: list[str] = []
        updated = amend_contract(contract, ContractCells(cleanup="completed"))
        with source_index_cache.terminal_namespace_guard(
            contract, requested_contract_path=contract.contract_path
        ) as guard:
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            result = guard.complete(
                outcome="completed",
                publish=lambda: write_contract(contract.contract_path, updated),
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result["removed"])
        self.assertEqual(len(outcome), 1)
        self.assertIn("terminal", outcome[0])
        self.assertFalse(cache.exists())

    def test_terminal_publication_failure_restores_generation_and_releases_waiter(self) -> None:
        contract = self.contract("failed-terminal-publication")
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        trees = self.trees(contract, authority)
        with source_index.open_repository_index(trees):
            pass
        before_cache = self.cache_bytes(authority.namespace)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()
        started = threading.Event()
        outcome: list[str] = []
        with (
            self.assertRaisesRegex(RuntimeError, "publication exploded"),
            source_index_cache.terminal_namespace_guard(
                contract, requested_contract_path=contract.contract_path
            ) as guard,
        ):
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
            guard.complete(
                outcome="completed",
                publish=self.fail_publication,
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, ["opened"])
        self.assertEqual(self.cache_bytes(authority.namespace), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)

    def test_terminal_tombstone_reserves_capacity_until_failed_publication_restores(self) -> None:
        contracts = [self.contract(f"capacity-{index}") for index in range(4)]
        target = contracts[0]
        target_cache = self.cache(target)
        for contract in contracts[1:]:
            self.cache(contract)
        target_before = self.cache_bytes(target_cache)
        newcomer = self.contract("capacity-newcomer")
        newcomer_authority = source_index_cache.contract_cache_authority(newcomer)
        assert newcomer_authority is not None
        newcomer_trees = self.trees(newcomer, newcomer_authority)
        publication_started = threading.Event()
        release_publication = threading.Event()
        terminal_outcome: list[str] = []

        def blocked_publication() -> None:
            publication_started.set()
            if not release_publication.wait(5):
                raise RuntimeError("test did not release publication")
            raise RuntimeError("publication failed")

        def terminal_attempt() -> None:
            try:
                with source_index_cache.terminal_namespace_guard(
                    target, requested_contract_path=target.contract_path
                ) as guard:
                    guard.complete(
                        outcome="completed",
                        publish=blocked_publication,
                        rollback_publish=lambda: write_contract(target.contract_path, target),
                    )
            except Exception as error:
                terminal_outcome.append(str(error))

        thread = threading.Thread(target=terminal_attempt)
        thread.start()
        self.assertTrue(publication_started.wait(2))
        with self.assertRaisesRegex(source_index.SourceIndexError, "capacity is full"):
            source_index.open_repository_index(newcomer_trees)
        self.assertFalse(newcomer_authority.namespace.exists())
        release_publication.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(terminal_outcome, ["publication failed"])
        self.assertEqual(self.cache_bytes(target_cache), target_before)
        self.assertEqual(
            len(source_index_cache._namespace_ids(target_cache.parent)),
            source_index_cache.MANAGED_NAMESPACE_LIMIT,
        )

    def test_cleanup_state_publication_failure_rolls_back_contract_cache_and_fence(self) -> None:
        contract = self.contract("cleanup-publication-rollback")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = source_index_cache.open_shared_namespace(authority, create=True)
        handle.close()
        before_cache = self.cache_bytes(cache)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()
        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                    "memory_integration": {"deleted": False, "reason": "already-absent"},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
            patch.object(
                source_index_cache,
                "_write_control_state",
                side_effect=RuntimeError("state publication exploded"),
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)

    def test_cleanup_publisher_write_then_raise_rolls_back_exact_live_state(self) -> None:
        contract = self.contract("cleanup-write-then-raise")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = source_index_cache.open_shared_namespace(authority, create=True)
        handle.close()
        before_cache = self.cache_bytes(cache)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()

        def write_then_raise(path: Path, candidate: WorktreeContract) -> None:
            write_contract(path, candidate)
            if candidate.cleanup == "completed":
                raise RuntimeError("publisher raised after terminal contract write")

        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                    "memory_integration": {"deleted": False, "reason": "already-absent"},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.write_contract",
                side_effect=write_then_raise,
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)
        self.assertEqual(load_contract(contract.contract_path).cleanup, "pending")

    def test_abandon_publisher_write_then_raise_rolls_back_exact_live_state(self) -> None:
        contract = self.contract("abandon-write-then-raise")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        handle = source_index_cache.open_shared_namespace(authority, create=True)
        handle.close()
        before_cache = self.cache_bytes(cache)
        before_state = authority.control_state.read_bytes()
        before_contract = contract.contract_path.read_bytes()

        def write_then_raise(path: Path, candidate: WorktreeContract) -> None:
            write_contract(path, candidate)
            if candidate.cleanup == "abandoned":
                raise RuntimeError("publisher raised after abandoned contract write")

        with (
            patch("agents_remember.worktrees.modules.abandon.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.abandon.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.teardown_worktree_providers",
                return_value={"state": "removed"},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                    "memory_integration": {"deleted": False, "reason": "already-absent"},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.abandon._abandon_directories", return_value={}
            ),
            patch(
                "agents_remember.worktrees.modules.abandon.write_contract",
                side_effect=write_then_raise,
            ),
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True, force=True)
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], "abandon-blocked")
        self.assertEqual(contract.contract_path.read_bytes(), before_contract)
        self.assertEqual(self.cache_bytes(cache), before_cache)
        self.assertEqual(authority.control_state.read_bytes(), before_state)
        self.assertEqual(load_contract(contract.contract_path).cleanup, "pending")

    def test_partial_retired_cleanup_is_terminal_success_and_never_restores_live_bytes(
        self,
    ) -> None:
        contract = self.contract("partial-retired-cleanup")
        cache = self.cache(contract)
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None

        def partial_delete(path: Path) -> None:
            (path / "ready.json").unlink()
            raise CitationCacheError("deterministic partial recursive deletion")

        with (
            patch("agents_remember.worktrees.modules.cleanup.load_contract", return_value=contract),
            patch(
                "agents_remember.worktrees.modules.cleanup.carryover_done",
                return_value=(True, "now"),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.terminal_preflight",
                return_value=self.preflight(),
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_worktrees",
                return_value={"code": {"removed": True}, "memory": {"removed": True}},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._deleted_branches",
                return_value={
                    "code": {"deleted": True},
                    "memory": {"deleted": True},
                    "memory_integration": {"deleted": False, "reason": "already-absent"},
                },
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup.remove_drift_snapshot",
                return_value={"removed": False, "reason": "already-absent"},
            ),
            patch(
                "agents_remember.worktrees.modules.cleanup._removed_directories", return_value={}
            ),
            patch.object(source_index_cache, "_remove_tree", side_effect=partial_delete),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )

        citation_cache = result.payload["citation_source_index"]  # type: ignore[index]
        assert isinstance(citation_cache, dict)
        retired_cleanup = citation_cache["retired_cleanup"]
        assert isinstance(retired_cleanup, dict)
        retired = Path(str(retired_cleanup["path"]))
        self.assertEqual(result.returncode, 0)
        self.assertTrue(citation_cache["removed"])
        self.assertFalse(retired_cleanup["removed"])
        self.assertIn("partial recursive deletion", retired_cleanup["reason"])
        self.assertFalse(cache.exists())
        self.assertTrue(retired.exists())
        self.assertFalse((retired / "ready.json").exists())
        self.assertTrue((retired / "index.sqlite3").exists())
        self.assertNotIn(
            authority.namespace_id,
            source_index_cache._namespace_ids(authority.managed_root),
        )
        self.assertEqual(load_contract(contract.contract_path).cleanup, "completed")
        self.assertEqual(
            json.loads(authority.control_state.read_text(encoding="utf-8"))["phase"],
            "terminal",
        )

    def test_empty_legacy_lifecycle_cannot_acquire_terminal_fence(self) -> None:
        contract = replace(self.contract("empty-lifecycle"), lifecycle_id="")
        write_contract(contract.contract_path, contract)
        with (
            self.assertRaisesRegex(CitationCacheError, "nonempty lifecycle"),
            source_index_cache.terminal_namespace_guard(
                contract, requested_contract_path=contract.contract_path
            ),
        ):
            self.fail("empty lifecycle unexpectedly acquired terminal authority")

    def test_neighbor_build_completes_while_target_terminal_fence_is_held(self) -> None:
        target = self.contract("fenced-target")
        neighbor = self.contract("fenced-neighbor")
        neighbor_authority = source_index_cache.contract_cache_authority(neighbor)
        assert neighbor_authority is not None
        trees = self.trees(neighbor, neighbor_authority)
        outcome: list[str] = []
        started = threading.Event()
        with source_index_cache.terminal_namespace_guard(
            target, requested_contract_path=target.contract_path
        ):
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(trees, started, outcome),
            )
            thread.start()
            self.assertTrue(started.wait(2))
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome, ["opened"])

    def test_old_authority_stays_refused_after_fresh_lifecycle_reopens(self) -> None:
        contract = self.contract("fresh-lifecycle")
        old_authority = source_index_cache.contract_cache_authority(contract)
        assert old_authority is not None
        old_trees = self.trees(contract, old_authority)
        with source_index.open_repository_index(old_trees):
            pass
        terminal = amend_contract(contract, ContractCells(cleanup="completed"))
        with source_index_cache.terminal_namespace_guard(
            contract, requested_contract_path=contract.contract_path
        ) as guard:
            guard.complete(
                outcome="completed",
                publish=lambda: write_contract(contract.contract_path, terminal),
                rollback_publish=lambda: write_contract(contract.contract_path, contract),
            )
        fresh = amend_contract(
            replace(terminal, lifecycle_id="LC-FRESH"),
            ContractCells(cleanup="pending"),
        )
        write_contract(fresh.contract_path, fresh)
        fresh_authority = source_index_cache.contract_cache_authority(fresh)
        assert fresh_authority is not None
        fresh_trees = self.trees(fresh, fresh_authority)
        with self.assertRaisesRegex(source_index.SourceIndexError, "stale|terminal"):
            source_index.open_repository_index(old_trees)
        with source_index.open_repository_index(fresh_trees):
            pass
        queued_started = threading.Event()
        queued_outcome: list[str] = []
        with source_index_cache.terminal_namespace_guard(
            fresh, requested_contract_path=fresh.contract_path
        ):
            thread = threading.Thread(
                target=self._open_after_signal,
                args=(old_trees, queued_started, queued_outcome),
            )
            thread.start()
            self.assertTrue(queued_started.wait(2))
            time.sleep(0.05)
            self.assertTrue(thread.is_alive())
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(queued_outcome), 1)
        self.assertRegex(queued_outcome[0], "stale|terminal")
        self.assertLessEqual(fresh_authority.control_state.stat().st_size, 4096)
        self.assertEqual(
            sorted(path.name for path in fresh_authority.control_dir.iterdir()),
            ["lease.lock", "state.json"],
        )

    @staticmethod
    def _open_after_signal(trees: Trees, started: threading.Event, outcome: list[str]) -> None:
        started.set()
        try:
            with source_index.open_repository_index(trees):
                outcome.append("opened")
        except Exception as error:
            outcome.append(str(error))


class TerminalPreflightFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name)

    def contract(self, name: str, *, missing_source: bool = False) -> WorktreeContract:
        code = self.tmp / f"{name}-code"
        memory = self.tmp / f"{name}-memory"
        init_repo(code, "main")
        init_repo(memory, "main")
        git(code, "checkout", "-b", "ar/task")
        (code / "change.txt").write_text("unmerged\n", encoding="utf-8")
        git(code, "add", "change.txt")
        git(code, "commit", "-m", "unmerged")
        git(memory, "branch", "ar/task")
        contract = _contract(
            self.tmp,
            task_name=name,
            contract_path=self.tmp / "tasks" / name / "series-contract.md",
            code_repo_path=code,
            code_worktree=code,
            code_source_branch="missing" if missing_source else "main",
            code_work_branch="ar/task",
            memory_repo_path=memory,
            memory_worktree=memory,
            memory_source_branch="main",
            memory_work_branch="ar/task",
        )
        write_contract(contract.contract_path, contract)
        return contract

    def cache(self, contract: WorktreeContract) -> tuple[Path, bytes]:
        authority = source_index_cache.contract_cache_authority(contract)
        assert authority is not None
        authority.namespace.mkdir(parents=True)
        payload = b"preflight-must-preserve"
        (authority.namespace / "ready.json").write_bytes(payload)
        return authority.namespace, payload

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done", return_value=(True, "now"))
    def test_cleanup_unmerged_preflight_preserves_exact_cache(self, _carryover: MagicMock) -> None:
        contract = self.contract("cleanup-unmerged")
        cache, payload = self.cache(contract)
        with patch(
            "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
            return_value=False,
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["blockers"][0]["reason"], "not-merged-into-source")  # type: ignore[index]
        self.assertEqual((cache / "ready.json").read_bytes(), payload)

    def test_abandon_missing_source_is_not_safe_empty_history(self) -> None:
        contract = self.contract("abandon-missing-source", missing_source=True)
        cache, payload = self.cache(contract)
        with patch(
            "agents_remember.worktrees.modules.abandon.provider_async.provider_setup_running",
            return_value=False,
        ):
            result = abandon_result(
                WorktreeArgs(contract_path=contract.contract_path, approved=True)
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["blockers"][0]["reason"], "source-branch-missing")  # type: ignore[index]
        self.assertEqual((cache / "ready.json").read_bytes(), payload)

    @patch("agents_remember.worktrees.modules.cleanup.carryover_done", return_value=(True, "now"))
    def test_cleanup_ancestry_query_failure_is_explicit(self, _carryover: MagicMock) -> None:
        contract = self.contract("cleanup-query-failure")
        cache, payload = self.cache(contract)
        original = terminal_validation.run_git

        def failed(repo: Path, args: list[str], **kwargs: object):
            if args[0] == "merge-base":
                return SimpleNamespace(returncode=128, stdout="", stderr="ancestry exploded")
            return original(repo, args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.object(terminal_validation, "run_git", new=failed),
            patch(
                "agents_remember.worktrees.modules.cleanup.provider_async.provider_setup_running",
                return_value=False,
            ),
        ):
            result = cleanup_result(
                WorktreeArgs(
                    contract_path=contract.contract_path,
                    approved=True,
                    teardown_providers=False,
                )
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ancestry exploded", result.payload["blockers"][0]["reason"])  # type: ignore[index]
        self.assertEqual((cache / "ready.json").read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
