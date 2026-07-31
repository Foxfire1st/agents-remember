from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from agents_remember.models.lifecycle_finalize import LifecycleFinalizeTaskResponse
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules.finalize import FinalizeArgs, finalize_result
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.worktree_contract import default_contract, write_contract
from test_worktree_support import commit_file, git, init_repo


def _payload(result: WorktreeCommandResult) -> dict[str, Any]:
    return cast("dict[str, Any]", result.payload)


class LifecycleFinalizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self) -> None:
        self._td.cleanup()

    def _contract(self, *, landed: bool = True, cleanup: str = "completed", **over: object):
        code_repo = self.tmp / "code"
        code_base = init_repo(code_repo, "main")
        git(code_repo, "checkout", "-b", "ar/task")
        code_commit = commit_file(code_repo, "feature.txt", "feature\n", "Add feature")
        git(code_repo, "checkout", "main")
        if landed:
            git(code_repo, "merge", "--ff-only", "ar/task")
        contract = default_contract(
            task_name="Finalize Thing",
            repo_name="repo-a",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=self.tmp / "ar-coordination",
            code_repo_path=code_repo,
            code_source_branch="main",
            code_work_branch="ar/task",
            code_base_commit=code_base,
            worktree_name="finalize-thing",
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

    def _docs(self, contract) -> tuple[Path, Path]:
        leaf = TaskDocument.model_validate(
            {
                "id": "14",
                "slug": "task",
                "title": "Finalize Thing",
                "kind": "light",
                "status": "inProgress",
                "repo": "repo-a",
                "type": "Code",
                "createdAt": "2026-06-23T22:00",
            }
        )
        leaf_json, _leaf_md = write_task_doc(contract.task_root, leaf)
        master_root = contract.coordination_root / "tasks" / "repo-a" / "master"
        master = TaskDocument.model_validate(
            {
                "id": "master",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo-a",
                "type": "Master",
                "createdAt": "2026-06-23T21:00",
                "subTasks": [
                    {
                        "number": "14",
                        "name": "Finalize Thing",
                        "file": "14_finalize",
                        "status": "inProgress",
                    }
                ],
            }
        )
        master_json, _master_md = write_task_doc(master_root, master)
        return leaf_json, master_json

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

    def test_not_finalizable_when_closeout_not_complete(self) -> None:
        contract = self._contract(
            closeout_status="not-started",
            code_commit="",
            integration_status="not-started",
            integrated_code_commit="",
        )

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        payload = _payload(result)
        self.assertEqual(payload["state"], "not-finalizable-yet")
        self.assertIn("closeout-not-complete", payload["blockers"])
        self.assertIn("integration-not-complete", payload["blockers"])

    def test_not_finalizable_when_target_branch_does_not_contain_commit(self) -> None:
        contract = self._contract(landed=False)

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        payload = _payload(result)
        self.assertEqual(payload["state"], "not-finalizable-yet")
        self.assertIn("not-landed-on-target-branch", payload["blockers"])

    def test_cleanup_blocked_does_not_mutate_task_docs(self) -> None:
        contract = self._contract(cleanup="pending")
        leaf_json, master_json = self._docs(contract)
        blocked = WorktreeCommandResult(2, {"state": "blocked", "summary": "cleanup refused"})

        with patch(
            "agents_remember.worktrees.modules.finalize.cleanup_result", return_value=blocked
        ):
            result = finalize_result(
                FinalizeArgs(
                    contract_path=contract.contract_path,
                    task_doc_path=leaf_json,
                    master_doc_path=master_json,
                    subtask_number="14",
                )
            )

        self.assertEqual(_payload(result)["state"], "cleanup-blocked")
        self.assertEqual(read_task_doc(leaf_json).status, "inProgress")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "inProgress")

    def test_dry_run_does_not_mutate_task_docs(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)

        result = finalize_result(
            FinalizeArgs(
                contract_path=contract.contract_path,
                task_doc_path=leaf_json,
                master_doc_path=master_json,
                subtask_number="14",
                dry_run=True,
            )
        )

        payload = _payload(result)
        self.assertEqual(payload["state"], "would-finalize")
        self.assertEqual(payload["taskUpdates"]["leaf"]["state"], "would-update")
        self.assertEqual(read_task_doc(leaf_json).status, "inProgress")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "inProgress")

    def test_tool_response_model_registered(self) -> None:
        self.assertIs(
            PUBLIC_TOOL_RESPONSE_MODELS["lifecycle_finalize_task"],
            LifecycleFinalizeTaskResponse,
        )


if __name__ == "__main__":
    unittest.main()
