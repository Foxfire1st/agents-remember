from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import agents_remember.tasks.store as task_store
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)
from test_worktree_support import commit_file, git, init_repo


def _payload(result: WorktreeCommandResult) -> dict[str, Any]:
    return cast("dict[str, Any]", result.payload)


def _row(number: str = "14", file: str = "14_finalize.md") -> dict[str, str]:
    return {"number": number, "name": "Finalize Thing", "file": file, "status": "inProgress"}


def _sources(*paths: Path) -> dict[Path, bytes]:
    """Each task document's JSON and rendered Markdown bytes, to prove a refusal wrote nothing."""
    return {
        side: side.read_bytes()
        for path in paths
        for side in (path, path.with_suffix(".md"))
        if side.exists()
    }


class _FinalizeFixtures(unittest.TestCase):
    """A landed, cleaned leaf contract plus the task documents its finalizer reconciles."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _contract(
        self,
        *,
        landed: bool = True,
        cleanup: str = "completed",
        fixture_name: str = "finalize-thing",
        **over: object,
    ):
        code_repo = self.tmp / f"code-{fixture_name}"
        code_base = init_repo(code_repo, "main")
        git(code_repo, "checkout", "-b", "ar/task")
        code_commit = commit_file(code_repo, "feature.txt", "feature\n", "Add feature")
        git(code_repo, "checkout", "main")
        if landed:
            git(code_repo, "merge", "--ff-only", "ar/task")
        contract = default_contract(
            ContractTask(
                name=fixture_name,
                repo_name="repo-a",
                coordination_root=self.tmp / "ar-coordination",
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            leaf=LeafIdentity(worktree_name=fixture_name, leaf_id="14"),
            code=RepoBranchPlan(
                repo_path=code_repo,
                source_branch="main",
                work_branch="ar/task",
                base_commit=code_base,
            ),
        )
        values = {
            "human_review_status": "approved",
            "approved_for_commit": True,
            "closeout_status": "completed",
            "code_commit": code_commit,
            "integration_status": "completed",
            "integrated_code_commit": code_commit,
            "cleanup": cleanup,
            **over,
        }
        closed = replace(contract, **values)
        write_contract(closed.contract_path, closed)
        return closed

    def _docs(
        self,
        contract,
        *,
        leaf_master: str | None = "task.md",
        rows: list[dict[str, str]] | None = None,
        master_status: str = "inProgress",
    ) -> tuple[Path, Path]:
        master_json = self._folder_doc(
            contract,
            kind="master",
            status=master_status,
            subTasks=[_row()] if rows is None else rows,
        )
        return self._leaf_doc(contract, leaf_master), master_json

    def _leaf_doc(self, contract, leaf_master: str | None) -> Path:
        leaf = TaskDocument.model_validate(
            {
                "id": "14",
                "slug": "14_finalize",
                "title": "Finalize Thing",
                "kind": "subTask",
                "status": "inProgress",
                "repo": "repo-a",
                "type": "Code",
                "createdAt": "2026-06-23T22:00",
                "master": leaf_master,
            }
        )
        leaf_json, _leaf_md = write_task_doc(contract.task_root, leaf)
        return leaf_json

    def _folder_doc(self, contract, *, kind: str, doc_id: str = "master", **fields: Any) -> Path:
        """Write the folder's ``task.json``: the master, or a non-master document in its place."""
        doc = TaskDocument.model_validate(
            {
                "id": doc_id,
                "slug": "task",
                "title": "Master",
                "kind": kind,
                "status": "inProgress",
                "repo": "repo-a",
                "type": "Master" if kind == "master" else "Code",
                "createdAt": "2026-06-23T21:00",
                **fields,
            }
        )
        json_path, _markdown = write_task_doc(contract.task_root, doc)
        return json_path

    def _set_leaf_steps(self, leaf_json: Path, steps: list[dict[str, Any]]) -> None:
        leaf = read_task_doc(leaf_json)
        data = leaf.model_dump(by_alias=True)
        data["steps"] = steps
        write_task_doc(leaf_json.parent, TaskDocument.model_validate(data))


class LifecycleFinalizeTests(_FinalizeFixtures):
    def test_finalized_updates_leaf_and_immediate_parent_row(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)

        result = finalize_result(
            FinalizeArgs(
                contract_path=contract.contract_path,
                task_doc_path=leaf_json,
                master_doc_path=master_json,
                subtask_number="14",
            )
        )

        self.assertEqual(result.returncode, 0)
        payload = _payload(result)
        self.assertEqual(payload["state"], "finalized")
        self.assertEqual(payload["cleanup"]["state"], "already-completed")
        self.assertEqual(read_task_doc(leaf_json).status, "Completed")
        master = read_task_doc(master_json)
        self.assertEqual(master.subTasks[0].status, "Completed")
        self.assertEqual(master.status, "inProgress")
        self.assertEqual(master.decisions[0].decision, "Finalize task lifecycle.")

    def test_second_document_publish_failure_rolls_back_leaf_and_parent(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)
        paths = (
            leaf_json,
            leaf_json.with_suffix(".md"),
            master_json,
            master_json.with_suffix(".md"),
        )
        before = {path: path.read_bytes() for path in paths}
        real_atomic_write = task_store.atomic_write_text
        call_count = 0

        def fail_on_parent_json(path: Path, text: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise OSError("injected parent publication failure")
            real_atomic_write(path, text)

        with (
            patch.object(task_store, "atomic_write_text", side_effect=fail_on_parent_json),
            self.assertRaisesRegex(OSError, "injected parent publication failure"),
        ):
            finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertEqual(read_task_doc(leaf_json).status, "inProgress")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "inProgress")

    def test_a_named_master_the_store_would_write_elsewhere_is_refused(self) -> None:
        """Parent guard: a hand-made master ``other.json`` is written as the folder's ``task.json``.

        Completing its row would replace the series master and leave ``other.json`` stale, so the
        finalizer refuses before any write. A master at ``task.json`` finalizes as the case above.
        """
        contract = self._contract()
        series_json = self._folder_doc(contract, doc_id="series", kind="master", subTasks=[])
        other = TaskDocument.model_validate(
            {
                "id": "other",
                "slug": "other",
                "title": "Other master",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo-a",
                "type": "Master",
                "createdAt": "2026-06-23T21:00",
                "subTasks": [_row()],
            }
        )
        other_json = contract.task_root / "other.json"
        other_json.write_text(other.model_dump_json(by_alias=True), encoding="utf-8")
        leaf_json = self._leaf_doc(contract, "other.md")
        before = _sources(leaf_json, other_json, series_json)

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        self.assertEqual(result.returncode, 2, result.payload)
        payload = _payload(result)
        self.assertEqual(payload["state"], "task-document-resolution-blocked")
        self.assertIn(
            f"{other_json.resolve()} would be rewritten to {series_json.resolve()}",
            payload["blockers"][0],
        )
        self.assertEqual(_sources(leaf_json, other_json, series_json), before)


class FolderMasterFinalizeTests(_FinalizeFixtures):
    """MIK-R38: a leaf naming no ``master`` finalizes against its folder's ``task.json``.

    The task-document master sync already resolves such a leaf to that master and keeps its row
    current; the finalizer called the same leaf standalone, so its row stayed ``inProgress`` after
    the leaf landed (260928-MIK, 15 rows). Every leaf here has ``master: null``.
    """

    def test_the_listing_folder_master_row_completes_under_the_demotion_rule(self) -> None:
        """Behaviours 1-2: the row completes, and a ``Completed`` master with an open row reopens."""
        contract = self._contract()
        leaf_json, master_json = self._docs(
            contract,
            leaf_master=None,
            master_status="Completed",
            rows=[_row(), _row("15", "15_next.md")],
        )

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        self.assertEqual(result.returncode, 0, result.payload)
        self.assertEqual(_payload(result)["taskUpdates"]["parent"]["subtaskNumber"], "14")
        self.assertEqual(read_task_doc(leaf_json).status, "Completed")
        master = read_task_doc(master_json)
        self.assertEqual(
            [(row.number, row.status) for row in master.subTasks],
            [("14", "Completed"), ("15", "inProgress")],
        )
        self.assertEqual(master.status, "inProgress")

    def test_a_dry_run_reports_the_folder_master_row_and_accepts_its_assertion(self) -> None:
        """Behaviour 5, with the caller asserting that master exactly as for a named one."""
        contract = self._contract()
        leaf_json, master_json = self._docs(contract, leaf_master=None)
        before = _sources(leaf_json, master_json)

        result = finalize_result(
            FinalizeArgs(
                contract_path=contract.contract_path,
                master_doc_path=master_json,
                subtask_number="14",
                dry_run=True,
            )
        )

        self.assertEqual(result.returncode, 0, result.payload)
        payload = _payload(result)
        self.assertEqual(payload["state"], "would-finalize")
        self.assertEqual(
            payload["taskUpdates"]["parent"],
            {
                "state": "would-update",
                "docPath": master_json.resolve().as_posix(),
                "status": "inProgress",
                "subtaskNumber": "14",
            },
        )
        self.assertEqual(_sources(leaf_json, master_json), before)

    def test_without_a_listing_folder_master_the_leaf_finalizes_standalone(self) -> None:
        """Behaviour 3 and the standalone preservation boundary: nothing else is written."""
        for case in ("no-task-json", "no-row", "leaf-is-the-light-task-json"):
            with self.subTest(case=case):
                contract = self._contract(fixture_name=f"standalone-{case}")
                if case == "no-task-json":
                    leaf_json, others = self._leaf_doc(contract, None), ()
                elif case == "no-row":
                    leaf_json, master_json = self._docs(
                        contract, leaf_master=None, rows=[_row("15", "15_next.md")]
                    )
                    others = (master_json,)
                else:
                    leaf_json = self._folder_doc(contract, kind="light", doc_id="14")
                    others = ()
                before = _sources(*others)

                result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

                self.assertEqual(result.returncode, 0, result.payload)
                self.assertEqual(
                    _payload(result)["taskUpdates"]["parent"],
                    {"state": "skipped", "reason": "leaf has no immediate parent"},
                )
                self.assertEqual(read_task_doc(leaf_json).status, "Completed")
                self.assertEqual(_sources(*others), before)

    def test_a_folder_master_is_refused_exactly_as_a_named_master(self) -> None:
        """Behaviour 4: the named-master refusals, before any task document is written.

        An unreadable folder master is refused rather than crashing publication, and a caller that
        asserts a folder master without the leaf's row is told that cause (review R1 notes 2, 4).
        """
        cases = {
            "not-a-master": "immediate parent path is not a master task document",
            "unreadable": "cannot read immediate parent task document",
            "listed-twice": "immediate parent must contain exactly one row '14'; found 2",
            "row-points-elsewhere": "parent row '14' points at ",
            "asserted-other-master": "is not the leaf's immediate parent",
            "asserted-master-without-row": "lists no row '14'; the leaf finalizes standalone",
        }
        rows = {
            "listed-twice": [_row(), _row()],
            "row-points-elsewhere": [_row(file="99_other.md")],
            "asserted-master-without-row": [_row("15", "15_next.md")],
        }
        for case, refusal in cases.items():
            with self.subTest(case=case):
                contract = self._contract(fixture_name=f"refused-{case}")
                args = FinalizeArgs(contract_path=contract.contract_path)
                if case == "not-a-master":
                    master_json = self._folder_doc(contract, kind="subTask", doc_id="other")
                    leaf_json = self._leaf_doc(contract, None)
                else:
                    leaf_json, master_json = self._docs(
                        contract, leaf_master=None, rows=rows.get(case)
                    )
                if case == "unreadable":
                    master_json.write_text("{ not a task document", encoding="utf-8")
                if case == "asserted-other-master":
                    args = replace(args, master_doc_path=master_json.with_name("other.json"))
                if case == "asserted-master-without-row":
                    args = replace(args, master_doc_path=master_json)
                before = _sources(leaf_json, master_json)

                result = finalize_result(args)

                self.assertEqual(result.returncode, 2, result.payload)
                payload = _payload(result)
                self.assertEqual(payload["state"], "task-document-resolution-blocked")
                self.assertIn(refusal, payload["blockers"][0])
                self.assertEqual(_sources(leaf_json, master_json), before)

    def test_a_leaf_the_store_would_write_elsewhere_is_refused_before_any_write(self) -> None:
        """Review R1 finding 1: a hand-made ``light`` leaf is written to ``task.json``.

        ``14_finalize.json`` below is a ``light`` document, which the store writes as the folder's
        ``task.json`` -- over the master listing it. Finalize refuses it before any write.
        """
        contract = self._contract()
        master_json = self._folder_doc(contract, kind="master", subTasks=[_row()])
        leaf = TaskDocument.model_validate(
            {
                "id": "14",
                "slug": "14_finalize",
                "title": "Finalize Thing",
                "kind": "light",
                "status": "inProgress",
                "repo": "repo-a",
                "type": "Code",
                "createdAt": "2026-06-23T22:00",
            }
        )
        leaf_json = contract.task_root / "14_finalize.json"
        leaf_json.write_text(leaf.model_dump_json(by_alias=True), encoding="utf-8")
        before = _sources(leaf_json, master_json)

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        self.assertEqual(result.returncode, 2, result.payload)
        payload = _payload(result)
        self.assertEqual(payload["state"], "task-document-resolution-blocked")
        self.assertIn(f"would be rewritten to {master_json.resolve()}", payload["blockers"][0])
        self.assertEqual(_sources(leaf_json, master_json), before)


if __name__ == "__main__":
    unittest.main()
