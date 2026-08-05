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
from typing import cast
from unittest import mock

from agents_remember.application.worktree_tools import _end_ambient_lifecycle_if_anchored
from agents_remember.observer.ambient import AmbientLifecycle, AmbientTiming, install_ambient
from agents_remember.observer.store import EventStore
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.leaf_doc import (
    LeafLifecycleRestampBlocked,
    find_leaf_doc,
    restamp_leaf_doc_lifecycle,
)
from agents_remember.worktrees import git_worktree_manager as worktree_manager
from agents_remember.worktrees.modules import start as start_module
from agents_remember.worktrees.modules import start_contract as start_contract_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.task_resolver import leaf_enclosure_path, series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
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
        ContractTask(
            name="260698_demo-series",
            repo_name="repo-a",
            coordination_root=coordination_root,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id="260698-l1", lifecycle_id="LC-OLD"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="ar/01-demo-leaf",
            base_commit="abc123",
        ),
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


def _leaf_doc(
    task_root: Path,
    *,
    lifecycle_id: str | None = "LC-OLD",
    master: str | None = "task.md",
    status: str = "Completed",
    step: dict[str, object] | None = None,
) -> Path:
    doc = TaskDocument.model_validate(
        {
            "id": "260698-L1",
            "slug": "01_demo-leaf",
            "title": "L1 — Demo leaf",
            "kind": "subTask",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T10:00",
            "lifecycleId": lifecycle_id,
            "master": master,
            "steps": [step or {"id": "S1", "title": "do the thing", "status": "done"}],
        }
    )
    json_path, _ = write_task_doc(task_root, doc)
    return json_path


def _master_doc(
    task_root: Path,
    *,
    duplicate_row: bool = False,
    row_number: str = "260698-L1",
    row_file: str = "01_demo-leaf.md",
    statuses: tuple[str, str] = ("Completed", "Completed"),
) -> Path:
    status, row_status = statuses
    row = {
        "number": row_number,
        "name": "L1 — Demo leaf",
        "file": row_file,
        "status": row_status,
    }
    doc = TaskDocument.model_validate(
        {
            "id": "260698_DEMO-SERIES",
            "slug": "task",
            "title": "Demo Series",
            "kind": "master",
            "status": status,
            "repo": "repo-a",
            "createdAt": "2026-07-01T09:00",
            "subTasks": [row, dict(row)] if duplicate_row else [row],
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
            blockers = " ".join(cast("list[str]", result.payload["blockers"]))
            self.assertIn("closeout", blockers)
            self.assertIn("cleanup", blockers)

    def test_refuses_a_non_leaf_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            write_contract(contract.contract_path, replace(contract, kind="series"))
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "not a leaf enclosure", " ".join(cast("list[str]", result.payload["blockers"]))
            )

    def test_refuses_when_a_worktree_still_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            contract.code_worktree.mkdir(parents=True)
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("still exists", " ".join(cast("list[str]", result.payload["blockers"])))


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
            # The leaf identity stays on the same task-doc id; legacy stem contracts
            # load through the resolver once the doc exists.
            self.assertEqual(reopened.leaf_id, "260698-L1")

            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("planning", None))
            self.assertTrue(any("reopened" in d.decision for d in doc.decisions))

            master = read_task_doc(master_path)
            self.assertEqual(master.subTasks[0].status, "planning")
            self.assertEqual(master.status, "inProgress")
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"], "reset"
            )

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)

            result = reopen_task(contract.contract_path, dry_run=True)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "would-reopen")
            untouched = load_contract(contract.contract_path)
            self.assertEqual(
                (untouched.closeout_status, untouched.cleanup), ("completed", "completed")
            )
            doc = read_task_doc(doc_path)
            self.assertEqual((doc.status, doc.lifecycleId), ("Completed", "LC-OLD"))
            master = read_task_doc(master_path)
            self.assertEqual(master.status, "Completed")
            self.assertEqual(master.subTasks[0].status, "Completed")
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"],
                "would-reset",
            )

    def test_unreadable_parent_refuses_before_leaf_or_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            master_path = _master_doc(contract.task_root)
            contract_before = contract.contract_path.read_bytes()
            leaf_before = doc_path.read_bytes()
            master_path.write_text("{", encoding="utf-8")

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            self.assertIn(
                "cannot read parent master",
                " ".join(cast("list[str]", result.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), leaf_before)
            self.assertEqual(read_task_doc(doc_path).status, "Completed")

    def test_duplicate_parent_rows_refuse_before_leaf_or_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            _master_doc(contract.task_root, duplicate_row=True)
            contract_before = contract.contract_path.read_bytes()
            leaf_before = doc_path.read_bytes()

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "exactly one row",
                " ".join(cast("list[str]", result.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), leaf_before)

    def test_explicit_parent_requires_exact_row_number_and_leaf_path(self) -> None:
        cases = (
            ("near-case", "260698-l1", "01_demo-leaf.md", "no exact row"),
            ("whitespace", "260698-L1 ", "01_demo-leaf.md", "no exact row"),
            ("missing", "260698-L9", "01_demo-leaf.md", "no exact row"),
            ("mispointed", "260698-L1", "09_other.md", "points at"),
        )
        for mode, row_number, row_file, expected in cases:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                contract = _completed_leaf_contract(Path(tmp))
                doc_path = _leaf_doc(contract.task_root)
                _master_doc(
                    contract.task_root,
                    row_number=row_number,
                    row_file=row_file,
                )
                contract_before = contract.contract_path.read_bytes()
                leaf_before = doc_path.read_bytes()

                result = reopen_task(contract.contract_path)

                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    expected,
                    " ".join(cast("list[str]", result.payload["blockers"])),
                )
                self.assertEqual(contract.contract_path.read_bytes(), contract_before)
                self.assertEqual(doc_path.read_bytes(), leaf_before)

    def test_explicit_missing_parent_refuses_before_leaf_or_contract_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            contract_before = contract.contract_path.read_bytes()
            leaf_before = doc_path.read_bytes()

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "does not exist",
                " ".join(cast("list[str]", result.payload["blockers"])),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), leaf_before)

    def test_standalone_leaf_without_parent_still_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root, master=None)

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                cast("dict[str, object]", result.payload["doc"])["masterIndex"],
                "no-master",
            )
            self.assertEqual(read_task_doc(doc_path).status, "planning")

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

    def test_restamp_refuses_false_terminal_and_preserves_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(
                task_root,
                step={
                    "id": "S1",
                    "title": "do the thing",
                    "status": "pending",
                    "substeps": [{"id": "S1.1", "title": "nested proof", "status": "blocked"}],
                },
            )
            markdown_path = json_path.with_suffix(".md")
            before = (json_path.read_bytes(), markdown_path.read_bytes())

            with self.assertRaises(LeafLifecycleRestampBlocked) as raised:
                restamp_leaf_doc_lifecycle(task_root, "260698-l1", "LC-NEW")

            self.assertEqual(
                [blocker.model_dump() for blocker in raised.exception.plan.blockers],
                [
                    {
                        "id": "S1",
                        "parentId": None,
                        "title": "do the thing",
                        "status": "pending",
                    },
                    {
                        "id": "S1.1",
                        "parentId": "S1",
                        "title": "nested proof",
                        "status": "blocked",
                    },
                ],
            )
            self.assertEqual((json_path.read_bytes(), markdown_path.read_bytes()), before)

    def test_same_lifecycle_is_a_true_no_write_even_for_a_legacy_invalid_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(
                task_root,
                lifecycle_id="LC-SAME",
                step={"id": "S1", "title": "do the thing", "status": "pending"},
            )
            markdown_path = json_path.with_suffix(".md")
            before = (json_path.read_bytes(), markdown_path.read_bytes())

            report = restamp_leaf_doc_lifecycle(task_root, "260698-l1", "LC-SAME")

            assert report is not None
            self.assertFalse(report["changed"])
            self.assertEqual((json_path.read_bytes(), markdown_path.read_bytes()), before)

    def test_nonterminal_leaf_can_restamp_with_unresolved_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp)
            json_path = _leaf_doc(
                task_root,
                status="inProgress",
                step={"id": "S1", "title": "do the thing", "status": "inProgress"},
            )

            report = restamp_leaf_doc_lifecycle(task_root, "260698-l1", "LC-NEW")

            assert report is not None
            self.assertTrue(report["changed"])
            updated = read_task_doc(json_path)
            self.assertEqual((updated.status, updated.lifecycleId), ("inProgress", "LC-NEW"))

    def test_restamp_without_doc_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(restamp_leaf_doc_lifecycle(Path(tmp), "260698-l1", "LC-NEW"))


class AbandonAmbientLifecycleTests(unittest.TestCase):
    def test_ends_the_ambient_lifecycle_when_it_anchors_the_abandoned_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp))
            amb = AmbientLifecycle(store, timing=AmbientTiming(heartbeat_seconds=3600))
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
    def test_false_terminal_leaf_blocks_absent_and_reopened_starts_before_any_effect(self) -> None:
        for contract_state in ("absent", "reopened"):
            with self.subTest(contract_state=contract_state), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                code_repo = workspace / "repo-a"
                base_commit = init_repo(code_repo, "main")
                coordination_root = workspace / "ar-coordination"
                memory_root = coordination_root / "memory-repos" / "ar-repo-a"
                (memory_root / "system").mkdir(parents=True)
                (memory_root / "onboarding").mkdir()
                task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
                _master_doc(task_root)
                _leaf_doc(
                    task_root,
                    step={
                        "id": "S1",
                        "title": "do the thing",
                        "status": "pending",
                        "substeps": [{"id": "S1.1", "title": "nested proof", "status": "blocked"}],
                    },
                )
                contract = default_contract(
                    ContractTask(
                        name="260698_demo-series",
                        repo_name="repo-a",
                        coordination_root=coordination_root,
                        workflow_kind="light-task",
                        memory_mode="disabled",
                    ),
                    leaf=LeafIdentity(
                        worktree_name="01-demo-leaf",
                        leaf_id="260698-l1",
                        lifecycle_id="LC-OLD",
                    ),
                    code=RepoBranchPlan(
                        repo_path=code_repo,
                        source_branch="main",
                        work_branch="ar/01-demo-leaf",
                        base_commit=base_commit,
                    ),
                )
                if contract_state == "reopened":
                    write_contract(contract.contract_path, replace(contract, cleanup="reopened"))
                before = {
                    path.relative_to(coordination_root): path.read_bytes()
                    for path in coordination_root.rglob("*")
                    if path.is_file()
                }

                with (
                    mock.patch.object(start_contract_module, "_ensure_branch") as ensure_branch,
                    mock.patch.object(start_contract_module, "write_contract") as publish_contract,
                    mock.patch.object(start_module, "ensure_worktree") as ensure_worktree,
                    mock.patch.object(start_module, "prepare_memory_for_start") as prepare_memory,
                    mock.patch.object(start_module, "plan_providers_for_start") as plan_providers,
                    mock.patch.object(
                        start_module, "run_or_launch_provider_setup"
                    ) as launch_providers,
                    mock.patch.object(start_module, "_record_start_progress") as start_progress,
                    mock.patch.object(
                        start_module, "restamp_leaf_doc_lifecycle"
                    ) as persist_restamp,
                ):
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

                self.assertEqual(
                    (result.returncode, result.payload["state"]), (2, "task-steps-blocked")
                )
                self.assertEqual(
                    result.payload["blockers"],
                    [
                        {
                            "id": "S1",
                            "parentId": None,
                            "title": "do the thing",
                            "status": "pending",
                        },
                        {
                            "id": "S1.1",
                            "parentId": "S1",
                            "title": "nested proof",
                            "status": "blocked",
                        },
                    ],
                )
                after = {
                    path.relative_to(coordination_root): path.read_bytes()
                    for path in coordination_root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(after, before)
                self.assertFalse(series_contract_path(task_root).exists())
                self.assertFalse(contract.code_worktree.exists())
                ensure_branch.assert_not_called()
                publish_contract.assert_not_called()
                ensure_worktree.assert_not_called()
                prepare_memory.assert_not_called()
                plan_providers.assert_not_called()
                launch_providers.assert_not_called()
                start_progress.assert_not_called()
                persist_restamp.assert_not_called()

    def test_start_recreates_fresh_and_restamps_the_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            code_repo = workspace / "repo-a"
            init_repo(code_repo, "main")
            coordination_root = workspace / "ar-coordination"
            (coordination_root / "memory-repos" / "ar-repo-a" / "system").mkdir(parents=True)
            (coordination_root / "memory-repos" / "ar-repo-a" / "onboarding").mkdir()
            task_root = coordination_root / "tasks" / "repo-a" / "260698_demo-series"
            _master_doc(task_root, statuses=("inProgress", "planning"))
            doc_path = _leaf_doc(
                task_root,
                lifecycle_id=None,
                status="planning",
                step={"id": "S1", "title": "do the thing", "status": "pending"},
            )

            # A reopened enclosure: landed history wiped back to planning, no lifecycle.
            contract = default_contract(
                ContractTask(
                    name="260698_demo-series",
                    repo_name="repo-a",
                    coordination_root=coordination_root,
                    workflow_kind="light-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id="260698-l1"),
                code=RepoBranchPlan(
                    repo_path=code_repo,
                    source_branch="main",
                    work_branch="ar/01-demo-leaf",
                    base_commit="stale",
                ),
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
                ContractTask(
                    name="260698_demo-series",
                    repo_name="repo-a",
                    coordination_root=coordination_root,
                    workflow_kind="light-task",
                    memory_mode="disabled",
                ),
                leaf=LeafIdentity(
                    worktree_name="01-demo-leaf", leaf_id="260698-l1", lifecycle_id="LC-LIVE"
                ),
                code=RepoBranchPlan(
                    repo_path=code_repo,
                    source_branch="main",
                    work_branch="ar/01-demo-leaf",
                    base_commit="abc",
                ),
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
