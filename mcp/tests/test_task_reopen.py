"""Tests for task_reopen (L11): reopen a completed leaf under its exact same leaf id.

Covers the novel logic in isolation: the guard set (only a fully landed leaf reopens),
the contract + doc reset, the leaf-doc lookup/restamp helpers, and the start-side
recreate-fresh path for ``cleanup=reopened`` including the doc lifecycle restamp.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.controllers.worktree_tools import _end_ambient_lifecycle_if_anchored
from agents_remember.observer.ambient import AmbientLifecycle, install_ambient
from agents_remember.observer.store import EventStore
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.leaf_doc import (
    find_leaf_doc,
    restamp_leaf_doc_lifecycle,
)
from agents_remember.tasks.reopen import reopen_task
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import (
    default_contract,
    load_contract,
    write_contract,
)
from test_worktree_support import init_repo


def _completed_leaf_contract(workspace: Path):
    """A fully landed leaf enclosure (closeout+integration+cleanup completed, worktrees gone)."""
    coordination_root = workspace / "ar-coordination"
    code_repo = workspace / "repo-a"
    code_repo.mkdir(parents=True, exist_ok=True)
    contract = default_contract(
        task_name="260698_demo-series",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=coordination_root,
        code_repo_path=code_repo,
        code_source_branch="main",
        code_work_branch="ar/01-demo-leaf",
        code_base_commit="abc123",
        worktree_name="01-demo-leaf",
        leaf_id="260698-l1",
        lifecycle_id="LC-OLD",
    )
    contract = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit="c1",
        integration_status="completed",
        integrated_code_commit="c1",
        cleanup="completed",
    )
    write_contract(contract.contract_path, contract)
    return contract


def _leaf_doc(task_root: Path, *, lifecycle_id: str | None = "LC-OLD") -> Path:
    doc = TaskDocument.model_validate(
        {
            "id": "260698-L1",
            "slug": "01_demo-leaf",
            "title": "L1 — Demo leaf",
            "kind": "subTask",
            "status": "Completed",
            "repo": "repo-a",
            "createdAt": "2026-07-01T10:00",
            "lifecycleId": lifecycle_id,
            "steps": [{"id": "S1", "title": "do the thing", "status": "done"}],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


def _master_doc(task_root: Path) -> Path:
    doc = TaskDocument.model_validate(
        {
            "id": "260698_DEMO-SERIES",
            "slug": "task",
            "title": "Demo Series",
            "kind": "master",
            "status": "inProgress",
            "repo": "repo-a",
            "createdAt": "2026-07-01T09:00",
            "subTasks": [
                {
                    "number": "260698-L1",
                    "name": "L1 — Demo leaf",
                    "file": "01_demo-leaf.md",
                    "status": "Completed",
                }
            ],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


class ReopenGuardTests(unittest.TestCase):
    def test_refuses_a_leaf_that_is_not_fully_landed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            write_contract(
                contract.contract_path,
                replace(contract, closeout_status="not-started", cleanup="pending"),
            )
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            blockers = " ".join(result.payload["blockers"])
            self.assertIn("closeout", blockers)
            self.assertIn("cleanup", blockers)

    def test_refuses_a_non_leaf_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            write_contract(contract.contract_path, replace(contract, kind="series"))
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("not a leaf enclosure", " ".join(result.payload["blockers"]))

    def test_refuses_when_a_worktree_still_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            contract.code_worktree.mkdir(parents=True)
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("still exists", " ".join(result.payload["blockers"]))


class ReopenResetTests(unittest.TestCase):
    def test_resets_contract_doc_and_master_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "reopened")
            self.assertEqual(result.payload["nextOperation"], "worktree_start")

            reopened = load_contract(contract.contract_path)
            self.assertEqual(
                (
                    reopened.human_review_status,
                    reopened.approved_for_commit,
                    reopened.closeout_status,
                    reopened.integration_status,
                    reopened.cleanup,
                    reopened.lifecycle_id,
                    reopened.code_commit,
                    reopened.integrated_code_commit,
                ),
                ("pending-review", False, "not-started", "not-started", "reopened", "", "", ""),
            )
            # The leaf id NEVER changes — that is the whole point.
            self.assertEqual(reopened.leaf_id, "260698-l1")

            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("planning", None))
            self.assertTrue(any("reopened" in d.decision for d in doc.decisions))

            master = read_task_doc(master_path)
            self.assertEqual(master.subTasks[0].status, "planning")

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)

            result = reopen_task(contract.contract_path, dry_run=True)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "would-reopen")
            untouched = load_contract(contract.contract_path)
            self.assertEqual(
                (untouched.closeout_status, untouched.cleanup), ("completed", "completed")
            )
            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("Completed", "LC-OLD"))

    def test_reopens_a_leaf_without_a_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 0)
            self.assertIsNone(result.payload["doc"])
            self.assertEqual(load_contract(contract.contract_path).cleanup, "reopened")


class LeafDocLookupTests(unittest.TestCase):
    def test_finds_by_doc_id_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            _leaf_doc(task_root)
            _master_doc(task_root)
            found = find_leaf_doc(task_root, "260698-L1".lower())
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found[1].id, "260698-L1")

    def test_finds_by_enclosure_ref_and_stem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            doc = TaskDocument.model_validate(
                {
                    "id": "unrelated-id",
                    "slug": "02_other-leaf",
                    "title": "Other",
                    "kind": "subTask",
                    "repo": "repo-a",
                    "createdAt": "2026-07-01T10:00",
                    "enclosures": [{"leafId": "260698-L2", "enclosurePath": "/e.md"}],
                }
            )
            write_task_doc(task_root, doc)
            by_ref = find_leaf_doc(task_root, "260698-l2")
            self.assertIsNotNone(by_ref)
            by_stem = find_leaf_doc(task_root, "02_OTHER-LEAF")
            self.assertIsNotNone(by_stem)
            self.assertIsNone(find_leaf_doc(task_root, "260698-l9"))

    def test_restamp_overwrites_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(task_root, lifecycle_id=None)
            report = restamp_leaf_doc_lifecycle(task_root, "260698-l1", "LC-NEW")
            self.assertIsNotNone(report)
            assert report is not None
            self.assertTrue(report["changed"])
            self.assertEqual(read_task_doc(json_path).lifecycleId, "LC-NEW")
            again = restamp_leaf_doc_lifecycle(task_root, "260698-l1", "LC-NEW")
            assert again is not None
            self.assertFalse(again["changed"])

    def test_restamp_without_doc_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(restamp_leaf_doc_lifecycle(Path(tmp), "260698-l1", "LC-NEW"))


class AbandonAmbientLifecycleTests(unittest.TestCase):
    def test_ends_the_ambient_lifecycle_when_it_anchors_the_abandoned_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp))
            amb = AmbientLifecycle(store, heartbeat_seconds=3600)
            install_ambient(amb)
            try:
                lc = amb.start()
                _end_ambient_lifecycle_if_anchored("SOME-OTHER-LIFECYCLE")
                self.assertIsNotNone(amb.current)  # unrelated id: untouched
                _end_ambient_lifecycle_if_anchored(lc.id)
                self.assertIsNone(amb.current)  # anchored id: owner-written ended
                kinds = [event.kind for event in store.read(lc.id)]
                self.assertEqual(kinds[-1], "lifecycle.ended")
            finally:
                install_ambient(None)  # type: ignore[arg-type]


class StartAfterReopenTests(unittest.TestCase):
    def test_start_recreates_fresh_and_restamps_the_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            init_repo(code_repo, "main")
            coordination_root = workspace / "ar-coordination"
            (coordination_root / "memory-repos" / "ar-repo-a" / "system").mkdir(parents=True)
            (coordination_root / "memory-repos" / "ar-repo-a" / "onboarding").mkdir()
            task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
            _master_doc(task_root)
            doc_path = _leaf_doc(task_root, lifecycle_id=None)

            # A reopened enclosure: landed history wiped back to planning, no lifecycle.
            contract = default_contract(
                task_name="260698_demo-series",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=coordination_root,
                code_repo_path=code_repo,
                code_source_branch="main",
                code_work_branch="ar/01-demo-leaf",
                code_base_commit="stale",
                worktree_name="01-demo-leaf",
                leaf_id="260698-l1",
            )
            contract = replace(contract, cleanup="reopened")
            write_contract(contract.contract_path, contract)

            result = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260698_demo-series",
                    worktree_name="01-demo-leaf",
                    leaf_id="260698-l1",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-NEW",
                )
            )

            self.assertEqual(result.returncode, 0)
            # NOT attached to the dead binding: the reopened tombstone recreates fresh.
            self.assertNotEqual(result.payload.get("state"), "attached-existing-contract")
            recreated = load_contract(leaf_enclosure_path(task_root, "260698-l1"))
            self.assertEqual(recreated.lifecycle_id, "LC-NEW")
            self.assertEqual(recreated.cleanup, "pending")
            self.assertEqual(recreated.closeout_status, "not-started")
            # The doc followed the enclosure onto the fresh lifecycle (explicit restamp).
            self.assertEqual(read_task_doc(doc_path).lifecycleId, "LC-NEW")

    def test_start_still_attaches_a_live_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            init_repo(code_repo, "main")
            coordination_root = workspace / "ar-coordination"
            (coordination_root / "memory-repos" / "ar-repo-a" / "system").mkdir(parents=True)
            (coordination_root / "memory-repos" / "ar-repo-a" / "onboarding").mkdir()
            task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
            _master_doc(task_root)

            contract = default_contract(
                task_name="260698_demo-series",
                repo_name="repo-a",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=coordination_root,
                code_repo_path=code_repo,
                code_source_branch="main",
                code_work_branch="ar/01-demo-leaf",
                code_base_commit="abc",
                worktree_name="01-demo-leaf",
                leaf_id="260698-l1",
                lifecycle_id="LC-LIVE",
            )
            write_contract(contract.contract_path, contract)

            result = worktree_manager.start_result(
                WorktreeArgs(
                    code_repository_name="repo-a",
                    workspace_root=workspace,
                    coordination_root=coordination_root,
                    code_repository_root=code_repo,
                    topology="external",
                    task_name="260698_demo-series",
                    worktree_name="01-demo-leaf",
                    leaf_id="260698-l1",
                    workflow_kind="light-task",
                    memory_mode="disabled",
                    skip_provider_setup=True,
                    lifecycle_id="LC-NEW",
                )
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "attached-existing-contract")
            self.assertEqual(
                load_contract(leaf_enclosure_path(task_root, "260698-l1")).lifecycle_id,
                "LC-LIVE",
            )


if __name__ == "__main__":
    unittest.main()
