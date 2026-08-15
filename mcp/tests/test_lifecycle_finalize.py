from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import agents_remember.tasks.store as task_store
from agents_remember.application.worktree_tools import FinalizeTaskDocs
from agents_remember.mcp.tools.lifecycle_finalize import lifecycle_finalize_task_payload
from agents_remember.models.lifecycles.finalize import LifecycleFinalizeTaskResponse
from agents_remember.models.tool_registry import PUBLIC_TOOL_RESPONSE_MODELS
from agents_remember.tasks import CompletionBlocker, TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.closeout_queue_errors import CloseoutQueueError
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


class LifecycleFinalizeTests(unittest.TestCase):
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

    def _docs(self, contract) -> tuple[Path, Path]:
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
                        "file": "14_finalize.md",
                        "status": "inProgress",
                    }
                ],
            }
        )
        master_json, _master_md = write_task_doc(contract.task_root, master)
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
                "master": "task.md",
            }
        )
        leaf_json, _leaf_md = write_task_doc(contract.task_root, leaf)
        return leaf_json, master_json

    def _set_leaf_steps(self, leaf_json: Path, steps: list[dict[str, Any]]) -> None:
        leaf = read_task_doc(leaf_json)
        data = leaf.model_dump(by_alias=True)
        data["steps"] = steps
        write_task_doc(leaf_json.parent, TaskDocument.model_validate(data))

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

    def test_queue_refusal_after_cleanup_does_not_mutate_task_docs(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)
        with patch(
            "agents_remember.worktrees.modules.finalize._reconcile_task_documents",
            side_effect=CloseoutQueueError("closeout-queue-blocked", "lane is owned"),
        ):
            result = finalize_result(
                FinalizeArgs(
                    contract_path=contract.contract_path,
                    task_doc_path=leaf_json,
                    master_doc_path=master_json,
                    subtask_number="14",
                )
            )
        payload = _payload(result)
        self.assertEqual(payload["state"], "task-queue-blocked")
        self.assertIn("closeout-queue-blocked", payload["blockers"][0])
        self.assertIn("sprint closeout queue", payload["summary"])
        self.assertEqual(read_task_doc(leaf_json).status, "inProgress")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "inProgress")

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

    def test_unresolved_parent_and_child_refuse_before_cleanup(self) -> None:
        contract = self._contract(cleanup="pending")
        leaf_json, master_json = self._docs(contract)
        self._set_leaf_steps(
            leaf_json,
            [
                {
                    "id": "S1",
                    "title": "Parent",
                    "status": "pending",
                    "substeps": [{"id": "C1", "title": "Child", "status": "blocked"}],
                }
            ],
        )

        with patch("agents_remember.worktrees.modules.finalize._run_or_verify_cleanup") as cleanup:
            result = finalize_result(
                FinalizeArgs(
                    contract_path=contract.contract_path,
                    task_doc_path=leaf_json,
                    master_doc_path=master_json,
                    subtask_number="14",
                )
            )

        cleanup.assert_not_called()
        self.assertEqual(result.returncode, 2)
        payload = _payload(result)
        self.assertEqual(payload["state"], "task-steps-blocked")
        self.assertEqual(
            payload["blockers"],
            [
                {"id": "S1", "parentId": None, "title": "Parent", "status": "pending"},
                {"id": "C1", "parentId": "S1", "title": "Child", "status": "blocked"},
            ],
        )
        self.assertEqual(read_task_doc(leaf_json).status, "inProgress")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "inProgress")

    def test_dry_run_and_already_clean_contract_do_not_bypass_step_preflight(self) -> None:
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                contract = self._contract(
                    cleanup="completed",
                    fixture_name=f"already-clean-{dry_run}",
                )
                leaf_json, _master_json = self._docs(contract)
                self._set_leaf_steps(
                    leaf_json,
                    [{"id": "S1", "title": "Open", "status": "inProgress"}],
                )
                with patch(
                    "agents_remember.worktrees.modules.finalize._run_or_verify_cleanup"
                ) as cleanup:
                    result = finalize_result(
                        FinalizeArgs(
                            contract_path=contract.contract_path,
                            task_doc_path=leaf_json,
                            dry_run=dry_run,
                        )
                    )
                cleanup.assert_not_called()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(_payload(result)["state"], "task-steps-blocked")

    def test_omitted_document_arguments_adopt_leaf_and_immediate_parent(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)
        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(read_task_doc(leaf_json).status, "Completed")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "Completed")

    def test_parent_path_and_number_are_independent_identity_assertions(self) -> None:
        for assertion in ("path", "number"):
            with self.subTest(assertion=assertion):
                contract = self._contract(fixture_name=f"assert-{assertion}")
                leaf_json, master_json = self._docs(contract)
                args = (
                    FinalizeArgs(
                        contract_path=contract.contract_path,
                        master_doc_path=master_json,
                    )
                    if assertion == "path"
                    else FinalizeArgs(
                        contract_path=contract.contract_path,
                        subtask_number="14",
                    )
                )

                result = finalize_result(args)

                self.assertEqual(result.returncode, 0)
                self.assertEqual(read_task_doc(leaf_json).status, "Completed")
                self.assertEqual(read_task_doc(master_json).subTasks[0].status, "Completed")

    def test_automatic_parent_resolution_refuses_mispoint_and_duplicates_before_cleanup(
        self,
    ) -> None:
        for mode in ("mispoint", "duplicate"):
            with self.subTest(mode=mode):
                contract = self._contract(cleanup="pending", fixture_name=f"auto-{mode}")
                leaf_json, master_json = self._docs(contract)
                master = read_task_doc(master_json)
                data = master.model_dump(by_alias=True)
                if mode == "mispoint":
                    data["subTasks"][0]["file"] = "15_sibling.md"
                else:
                    data["subTasks"].append(dict(data["subTasks"][0]))
                write_task_doc(contract.task_root, TaskDocument.model_validate(data))

                with patch(
                    "agents_remember.worktrees.modules.finalize._run_or_verify_cleanup"
                ) as cleanup:
                    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

                cleanup.assert_not_called()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(_payload(result)["state"], "task-document-resolution-blocked")
                self.assertEqual(read_task_doc(leaf_json).status, "inProgress")

    def test_standalone_leaf_finalizes_without_touching_unreferenced_master(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)
        leaf = read_task_doc(leaf_json)
        data = leaf.model_dump(by_alias=True)
        data["master"] = None
        write_task_doc(contract.task_root, TaskDocument.model_validate(data))

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(read_task_doc(leaf_json).status, "Completed")
        self.assertEqual(read_task_doc(master_json).subTasks[0].status, "inProgress")
        self.assertEqual(_payload(result)["taskUpdates"]["parent"]["state"], "skipped")

    def test_completed_parent_is_demoted_when_a_sibling_remains_unresolved(self) -> None:
        contract = self._contract()
        leaf_json, master_json = self._docs(contract)
        master = read_task_doc(master_json)
        data = master.model_dump(by_alias=True)
        data["status"] = "Completed"
        data["subTasks"].append(
            {
                "number": "15",
                "name": "Sibling",
                "file": "15_sibling.md",
                "status": "planning",
            }
        )
        write_task_doc(contract.task_root, TaskDocument.model_validate(data))

        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))

        self.assertEqual(result.returncode, 0)
        parent = read_task_doc(master_json)
        self.assertEqual(parent.status, "inProgress")
        self.assertEqual(
            [(row.number, row.status) for row in parent.subTasks],
            [("14", "Completed"), ("15", "planning")],
        )
        self.assertEqual(read_task_doc(leaf_json).status, "Completed")

    def test_mispointed_leaf_and_wrong_parent_row_refuse_before_cleanup(self) -> None:
        contract = self._contract(cleanup="pending")
        leaf_json, master_json = self._docs(contract)
        sibling = TaskDocument.model_validate(
            {
                "id": "15",
                "slug": "15_sibling",
                "title": "Sibling",
                "kind": "subTask",
                "status": "inProgress",
                "repo": "repo-a",
                "createdAt": "2026-06-23T22:00",
            }
        )
        sibling_json, _ = write_task_doc(contract.task_root, sibling)
        cases = (
            FinalizeArgs(contract_path=contract.contract_path, task_doc_path=sibling_json),
            FinalizeArgs(
                contract_path=contract.contract_path,
                task_doc_path=leaf_json,
                master_doc_path=master_json,
                subtask_number="15",
            ),
        )
        for final_args in cases:
            with (
                self.subTest(task_doc_path=str(final_args.task_doc_path)),
                patch(
                    "agents_remember.worktrees.modules.finalize._run_or_verify_cleanup"
                ) as cleanup,
            ):
                result = finalize_result(final_args)
                cleanup.assert_not_called()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(_payload(result)["state"], "task-document-resolution-blocked")

    def test_duplicate_and_unreadable_leaf_identity_refuse_before_cleanup(self) -> None:
        for mode in ("duplicate", "unreadable"):
            with self.subTest(mode=mode):
                contract = self._contract(cleanup="pending", fixture_name=mode)
                leaf_json, _master_json = self._docs(contract)
                if mode == "duplicate":
                    duplicate = read_task_doc(leaf_json).model_copy(update={"slug": "14_duplicate"})
                    write_task_doc(contract.task_root, duplicate)
                else:
                    (contract.task_root / "14.json").write_text("{", encoding="utf-8")
                with patch(
                    "agents_remember.worktrees.modules.finalize._run_or_verify_cleanup"
                ) as cleanup:
                    result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
                cleanup.assert_not_called()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(_payload(result)["state"], "task-document-resolution-blocked")

    def test_traversal_and_absolute_parent_refs_refuse_before_cleanup(self) -> None:
        for mode in ("traversal", "absolute"):
            with self.subTest(mode=mode):
                contract = self._contract(cleanup="pending", fixture_name=f"parent-{mode}")
                leaf_json, _master_json = self._docs(contract)
                outside = contract.task_root.parent / f"outside-{mode}" / "task.json"
                leaf = read_task_doc(leaf_json)
                data = leaf.model_dump(by_alias=True)
                data["master"] = (
                    f"../outside-{mode}/task.md"
                    if mode == "traversal"
                    else outside.with_suffix(".md").as_posix()
                )
                write_task_doc(contract.task_root, TaskDocument.model_validate(data))

                with patch(
                    "agents_remember.worktrees.modules.finalize._run_or_verify_cleanup"
                ) as cleanup:
                    result = finalize_result(
                        FinalizeArgs(
                            contract_path=contract.contract_path,
                            task_doc_path=leaf_json,
                            master_doc_path=outside,
                            subtask_number="14",
                        )
                    )
                cleanup.assert_not_called()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(_payload(result)["state"], "task-document-resolution-blocked")

    def test_true_no_document_contract_finalizes_without_parent_update(self) -> None:
        contract = self._contract()
        result = finalize_result(FinalizeArgs(contract_path=contract.contract_path))
        self.assertEqual(result.returncode, 0)
        payload = _payload(result)
        self.assertEqual(payload["state"], "finalized")
        self.assertEqual(payload["taskUpdates"]["leaf"]["state"], "skipped")
        self.assertEqual(payload["taskUpdates"]["parent"]["state"], "skipped")

    def test_public_payload_preserves_typed_step_blockers(self) -> None:
        contract = self._contract()
        leaf_json, _master_json = self._docs(contract)
        self._set_leaf_steps(
            leaf_json,
            [{"id": "S1", "title": "Open", "status": "pending"}],
        )
        config = cast(
            Any,
            SimpleNamespace(coordination_root=contract.coordination_root),
        )
        payload = lifecycle_finalize_task_payload(
            config,
            contract.contract_path.as_posix(),
            docs=FinalizeTaskDocs(task_doc_path=leaf_json.as_posix()),
        )
        validated = LifecycleFinalizeTaskResponse.model_validate(payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["blockers"][0]["id"], "S1")
        blocker = validated.blockers[0]
        self.assertIsInstance(blocker, CompletionBlocker)
        self.assertEqual(cast(CompletionBlocker, blocker).id, "S1")

    def test_tool_response_model_registered(self) -> None:
        self.assertIs(
            PUBLIC_TOOL_RESPONSE_MODELS["lifecycle_finalize_task"],
            LifecycleFinalizeTaskResponse,
        )


if __name__ == "__main__":
    unittest.main()
