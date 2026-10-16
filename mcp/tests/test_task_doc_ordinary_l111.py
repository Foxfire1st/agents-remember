"""MIK-R111 task_doc ordinaries, budgets and refusals (F01.8-F01.12, F01.17).

Live writes meet their targets complete with readable locations; by-file
inputs ride both transports with their digest; attach/detach dry runs carry
their aggregate diff and loss; phrases name values; removed reads refuse
before parsing; the leaf-binding recovery names its leaf.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace as _replace
from pathlib import Path
from typing import Any
from typing import cast as _cast
from unittest.mock import patch

from agents_remember.application import tool_response as response_module
from agents_remember.application.landing_answer_shapes import (
    TASK_DOC_DIFF_TRUNCATED_MARKER as marker,
)
from agents_remember.application.landing_answer_shapes import (
    _unretained_diff_marker as unretained,
)
from agents_remember.application.landing_answer_shapes import project_task_doc_answer
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.task_leaf_binding import (
    TaskLeafBindingError,
    binding_recovery_next_args,
    leaf_enclosure_binding_refusal,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_task_document import _config
from test_task_execution_topology import REPOSITORY, _master
from test_task_execution_topology import _config as _topo_config
from test_worktree_support import git, init_repo
from tool_answer_l111_test_support import (
    assert_detail_holds_whole_result,
    assert_location_separated_target,
    captured_validated_wholes,
)
from tool_entry_point_world import EntryPointWorld


def _master_doc() -> dict[str, Any]:
    return {
        "id": "series",
        "slug": "series",
        "title": "Series",
        "kind": "master",
        "repo": "agents-remember",
        "type": "Master (Code)",
        "createdAt": "2026-01-01T00:00:00Z",
        "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
    }


def _leaf_doc() -> dict[str, Any]:
    return {
        "id": "3C",
        "slug": "03c_x",
        "title": "Smoke",
        "kind": "subTask",
        "repo": "agents-remember",
        "type": "Code",
        "createdAt": "2026-01-01T00:00:00Z",
        "steps": [{"id": "S1", "title": "One", "status": "pending"}],
    }


class TaskDocLiveOrdinaryTests(unittest.TestCase):
    """F01.16: every measured write meets its location-separated target.

    Each ordinary write is measured three ways (revision 218d585d point 4):
    without its locations -- removed at every depth -- it stays within 100
    tokens; it carries at most two distinct locations; and the whole result
    stays under the unchanged R108 bound. F01.17: the detail file is
    compared field for field with the producer's whole result.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="l111-ordinary-")
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.cfg = _config(self.coord)
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="series"),
            operation="create",
            edit=TaskDocEdit(fields=_master_doc()),
        )
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="create",
            edit=TaskDocEdit(fields=_leaf_doc()),
        )

    def _delivered(self, operation: str, **kw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        whole = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation=operation,
            **kw,
        )
        args: dict[str, Any] = {"operation": operation}
        with (
            captured_validated_wholes() as cap,
            tool_call_context("task_doc", args, config=self.cfg),
        ):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        return answer, cap["task_doc"][-1]

    def _assert_ordinary(self, answer: dict[str, Any], expected_whole: dict[str, Any]) -> None:
        self.assertTrue(answer["ok"])
        self.assertIn("updated", answer)
        self.assertIn("log", answer)
        # Ruling 218d585d point 4: the target counts the answer without its
        # locations, removed at every depth; the test name says which of the
        # three measures each case takes.
        assert_location_separated_target(self, "task_doc", answer, 100)
        # F01.17: the detail file holds the complete validated whole.
        whole_back = assert_detail_holds_whole_result(self, "task_doc", answer, expected_whole)
        self.assertTrue(whole_back["ok"])
        if "renderedPath" in answer:
            self.assertEqual(whole_back.get("renderedPath"), answer["renderedPath"])

    def test_create_meets_location_separated_target_counts_distinct_locations_and_holds_whole_result(
        self,
    ) -> None:
        whole = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="second"),
            operation="create",
            edit=TaskDocEdit(
                fields={**_leaf_doc(), "id": "2C", "slug": "second", "title": "Second"}
            ),
        )
        with (
            captured_validated_wholes() as cap,
            tool_call_context("task_doc", {"operation": "create"}, config=self.cfg),
        ):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        self.assertIn("renderedPath", answer)
        self._assert_ordinary(answer, cap["task_doc"][-1])

    def test_set_step_meets_location_separated_target_counts_distinct_locations_and_holds_whole_result(
        self,
    ) -> None:
        answer, expected = self._delivered(
            "set_step", edit=TaskDocEdit(step={"id": "S1", "status": "done"})
        )
        self.assertIn("done", answer["updated"])
        self._assert_ordinary(answer, expected)

    def test_set_field_meets_location_separated_target_counts_distinct_locations_and_holds_whole_result(
        self,
    ) -> None:
        answer, expected = self._delivered(
            "set_field", edit=TaskDocEdit(fields={"title": "Renamed"})
        )
        self._assert_ordinary(answer, expected)

    def test_set_status_meets_location_separated_target_counts_distinct_locations_and_holds_whole_result(
        self,
    ) -> None:
        answer, expected = self._delivered(
            "set_status", edit=TaskDocEdit(fields={"status": "inProgress"})
        )
        self._assert_ordinary(answer, expected)


class TaskDocPhraseTests(unittest.TestCase):
    """F01.10: phrases carry the changed unit and its new value."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="l111-phrase-")
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.cfg = _config(self.coord)
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="series"),
            operation="create",
            edit=TaskDocEdit(fields=_master_doc()),
        )
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="create",
            edit=TaskDocEdit(fields=_leaf_doc()),
        )

    def _whole(self, operation: str, **kw: Any) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation=operation,
            **kw,
        )

    def test_set_field_names_value_and_ignores_unknown(self) -> None:
        whole = self._whole(
            "set_field", edit=TaskDocEdit(fields={"objective": "new", "bogus": "x"})
        )
        self.assertIn("objective=new", whole["updated"])
        self.assertNotIn("bogus", whole["updated"])

    def test_set_step_names_parent_id_and_change(self) -> None:
        whole = self._whole("set_step", edit=TaskDocEdit(step={"id": "S1", "status": "done"}))
        self.assertIn("S1", whole["updated"])
        self.assertIn("status", whole["updated"])
        self.assertIn("done", whole["updated"])

    def test_set_status_names_value(self) -> None:
        whole = self._whole("set_status", edit=TaskDocEdit(fields={"status": "inProgress"}))
        self.assertIn("inProgress", whole["updated"])

    def test_long_value_is_bounded(self) -> None:
        whole = self._whole("set_field", edit=TaskDocEdit(fields={"objective": "v" * 500}))
        self.assertLessEqual(len(whole["updated"]), 200)
        self.assertIn("objective=", whole["updated"])


class TaskDocByFileTests(unittest.TestCase):
    """F01.8: by-file inputs ride both transports with their digest, once."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="l111-byfile-")
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.cfg = _config(self.coord)
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="series"),
            operation="create",
            edit=TaskDocEdit(fields=_master_doc()),
        )
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="create",
            edit=TaskDocEdit(fields=_leaf_doc()),
        )
        self.target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x")

    def _fields_file(self, fields: dict[str, Any]) -> str:
        path = self.coord / "fields.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        return path.as_posix()

    def test_by_file_write_carries_digest(self) -> None:
        fields = {"title": "Filed"}
        digest = hashlib.sha256(json.dumps(fields).encode()).hexdigest()
        whole = task_doc_tool(
            self.cfg,
            self.target,
            operation="set_field",
            edit=TaskDocEdit(fields_file=self._fields_file(fields)),
        )
        self.assertTrue(whole["ok"])
        self.assertEqual(whole["inputFileSha256"], digest)
        with tool_call_context("task_doc", {"operation": "set_field"}, config=self.cfg):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        self.assertEqual(answer["inputFileSha256"], digest)
        self.assertTrue(answer["inputFile"].endswith("fields.json"))

    def test_by_file_dry_run_carries_digest(self) -> None:
        fields = {"title": "Filed"}
        digest = hashlib.sha256(json.dumps(fields).encode()).hexdigest()
        whole = task_doc_tool(
            self.cfg,
            self.target,
            operation="set_field",
            edit=TaskDocEdit(fields_file=self._fields_file(fields)),
            call=TaskDocCall(dry_run=True),
        )
        self.assertTrue(whole["ok"])
        with tool_call_context(
            "task_doc", {"operation": "set_field", "dry_run": True}, config=self.cfg
        ):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        self.assertEqual(answer.get("inputFileSha256"), digest)
        self.assertTrue(str(answer.get("inputFile", "")).endswith("fields.json"))

    def test_fields_and_fields_file_conflict_before_writing(self) -> None:

        before = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json")
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                self.target,
                operation="set_field",
                edit=TaskDocEdit(
                    fields={"title": "Inline"}, fields_file=self._fields_file({"title": "F"})
                ),
            )
        after = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json")
        self.assertEqual(before.title, after.title)

    def test_nonregular_file_input_refuses_before_writing(self) -> None:
        with self.assertRaises(TaskDocError):
            task_doc_tool(
                self.cfg,
                self.target,
                operation="set_field",
                edit=TaskDocEdit(fields_file=str(self.coord)),
            )

    def test_oversize_file_input_refuses_with_budget(self) -> None:
        path = self.coord / "big.json"
        with open(path, "wb") as fh:
            fh.seek(1024 * 1024)
            fh.write(b"x")
        with self.assertRaises(TaskDocError) as raised:
            task_doc_tool(
                self.cfg,
                self.target,
                operation="set_field",
                edit=TaskDocEdit(fields_file=path.as_posix()),
            )
        self.assertIn("1048576", str(raised.exception))


class TaskDocRemovedReadTests(unittest.TestCase):
    """F01.11: removed reads refuse with file names, never parsing first."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="l111-removed-")
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.cfg = _config(self.coord)
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="series"),
            operation="create",
            edit=TaskDocEdit(fields=_master_doc()),
        )
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation="create",
            edit=TaskDocEdit(fields=_leaf_doc()),
        )
        self.json_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json"

    def _refusal(self, operation: str) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation=operation,
        )

    def test_removed_read_on_valid_document_names_files(self) -> None:
        refusal = self._refusal("get")
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["code"], "operation-removed")
        self.assertIn("03c_x.md", refusal["error"])
        self.assertIn("03c_x.json", refusal["error"])

    def test_removed_read_on_missing_document_refuses(self) -> None:
        self.json_path.unlink()
        refusal = self._refusal("read_steps")
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["code"], "operation-removed")
        self.assertIn("03c_x.json", refusal["error"])
        self.assertIn("03c_x.md", refusal["error"])
        self.assertTrue(refusal["status"])

    def test_removed_read_on_corrupt_document_refuses(self) -> None:
        self.json_path.write_text("{not json", encoding="utf-8")
        refusal = self._refusal("get")
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["code"], "operation-removed")
        self.assertIn("03c_x.json", refusal["error"])


class TaskDocDiffRetentionTests(unittest.TestCase):
    """F01.9: aggregate diffs ride every dry run; a lost log never promises them."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="l111-diff-")
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.cfg = _config(self.coord)

    def test_unretained_marker_names_the_log_failure(self) -> None:

        self.assertIn("log detail file", marker)
        rewritten = ("diff " + marker).replace(marker, unretained("detail-too-large"))
        self.assertIn("was not retained", rewritten)
        self.assertIn("detail-too-large", rewritten)
        self.assertNotIn("log detail file", rewritten)

    def test_giant_dry_diff_says_not_retained(self) -> None:
        # A whole over the 8 MiB detail budget is not written: the cut marker
        # must not promise the log file. The diff is built directly -- the
        # producer path is covered elsewhere; this pins the retention promise.
        whole = {
            "ok": True,
            "operation": "create",
            "taskId": "agents-remember/3c-x",
            "slug": "big",
            "kind": "subTask",
            "status": "planning",
            "docPath": "/coord/big.json",
            "updated": "created big",
            "diff": "y\n" * 4500000,
            "renderedPath": "big.md",
        }
        with tool_call_context(
            "task_doc", {"operation": "create", "dry_run": True}, config=self.cfg
        ):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        self.assertIn("was not retained", answer.get("diff", ""))
        self.assertIn("logError", answer)
        self.assertNotIn("log", answer)


class LeafBindingRecoveryTests(unittest.TestCase):
    """F01.12: the binding recovery names its exact leaf, never the master."""

    def test_recovery_call_carries_repo_leaf_and_slug(self) -> None:

        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        # A leaf the enclosure no longer binds: the recovery names the exact
        # leaf contract and repository, never the master document.
        contract = load_contract(world.contract)
        tampered = _replace(contract, leaf_id="L9")

        result = leaf_enclosure_binding_refusal(tampered)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.returncode, 2)
        next_args = _cast(dict[str, Any], result.payload["nextArgs"])
        # A binding that names no leaf document offers no mutating replace:
        # either a file repair or a replace that never addresses the master.
        self.assertFalse(
            result.payload.get("nextTool") == "task_doc" and next_args.get("slug") == "task"
        )
        self.assertEqual(next_args["repo_id"], "repo-a")
        self.assertEqual(next_args["contract_path"], tampered.contract_path.as_posix())
        # The slug rides when the binding names a document, and is invented never.
        named = binding_recovery_next_args(
            contract,
            TaskLeafBindingError(
                "mismatch", status="s", facts={"taskDocument": "/t/m/1_leaf.json"}
            ),
        )
        self.assertEqual(named["slug"], "1_leaf")
        unnamed = binding_recovery_next_args(
            contract, TaskLeafBindingError("missing", status="s", facts={})
        )
        self.assertNotIn("slug", unnamed)
        self.assertEqual(unnamed["repo_id"], "repo-a")

    def test_unreadable_parent_offers_file_repair_never_master_replace(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        # Corrupt parent authority: no leaf slug can be derived, so the refusal
        # offers a nonmutating file repair, never a master replace.
        (world.coord / "tasks/repo-a/m/task.json").write_text("{corrupt")
        result = leaf_enclosure_binding_refusal(load_contract(world.contract))
        self.assertIsNotNone(result)
        assert result is not None
        payload = result.payload
        next_args = _cast(dict[str, Any], payload["nextArgs"])
        self.assertFalse(payload.get("nextTool") == "task_doc" and next_args.get("slug") == "task")
        self.assertIsNone(payload.get("nextTool"))
        self.assertIn("task.json", str(next_args.get("path")))
        self.assertIn("task.json", str(payload.get("nextAction")))

    def test_set_subtask_phrase_carries_new_name(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        kind, answer = world.call(
            "task_doc",
            {
                "operation": "set_subtask",
                "repo_id": "repo-a",
                "task_name": "m",
                "subtask": {
                    "number": "L1",
                    "name": "renamed fixture leaf",
                    "file": "1_leaf.md",
                    "status": "planning",
                    "scope": "sweep",
                },
            },
        )
        self.assertEqual(kind, "RETURNED")
        assert answer is not None
        self.assertTrue(answer["ok"])
        self.assertIn("renamed fixture leaf", answer["updated"])

    def test_route_review_delivery_carries_its_document(self) -> None:

        whole = {
            "ok": True,
            "operation": "record_route_review",
            "reviewState": {"round": 1},
            "taskDocumentPath": "/coord/tasks/repo-a/m/task.md",
            "updated": "review recorded",
        }
        with tool_call_context("task_doc", {"operation": "record_route_review"}):
            answer = project_task_doc_answer(whole, operation="record_route_review", dry_run=False)
        self.assertEqual(answer["taskDocumentPath"], "/coord/tasks/repo-a/m/task.md")
        for operation in ("begin_review", "record_review"):
            with self.subTest(operation=operation):
                memory_result = {
                    "ok": True,
                    "reviewKind": "memory",
                    "reviewState": {"round": 1, "remainingFindingIds": []},
                    "memoryReviewState": {"round": 2, "remainingFindingIds": ["M1"]},
                    "updated": "memory review recorded",
                    "unprojectedDebug": "not an answer field",
                }
                memory_answer = project_task_doc_answer(
                    memory_result, operation=operation, dry_run=False
                )
                for key in ("reviewState", "memoryReviewState", "reviewKind"):
                    self.assertEqual(memory_answer[key], memory_result[key])
                self.assertNotIn("unprojectedDebug", memory_answer)


class AttachDetachDryTests(unittest.TestCase):
    """F01.9: nested attach/detach previews carry their aggregate diff and loss."""

    def setUp(self) -> None:

        self.temp = tempfile.TemporaryDirectory(prefix="l111-attach-")
        self.addCleanup(self.temp.cleanup)
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.code = self.coord / "code"
        init_repo(self.code)
        git(self.code, "branch", "super", "main")
        self.cfg = _topo_config(self.coord, self.code)
        self.repository = REPOSITORY
        write_task_doc(self.tasks / "master-a", _master(identity="master-a", orchestrates=[]))
        write_task_doc(
            self.tasks / "master-b",
            _master(identity="master-b", orchestrates=[], execution_nature="atomic"),
        )
        write_task_doc(
            self.tasks / "sprint",
            _master(identity="SPRINT", orchestrates=["master-a"]).model_copy(
                update={"integrationBranch": "super"}
            ),
        )

    def _dry(self, operation: str, fields: dict[str, Any]) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=self.repository, task_name="sprint"),
            operation=operation,
            edit=TaskDocEdit(fields=fields),
            call=TaskDocCall(dry_run=True),
        )

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}

    def test_attach_dry_run_carries_aggregate_diff(self) -> None:
        before = self._snapshot()
        dry = self._dry(
            "attach_master",
            {
                "masterRef": {"repository": self.repository, "path": "master-b/task.json"},
                "number": "M2",
                "name": "master-b",
            },
        )
        self.assertTrue(dry["ok"])
        self.assertIn("diff", dry)
        self.assertIn("wouldLose", dry)
        self.assertTrue(dry["documents"])
        self.assertEqual(self._snapshot(), before)

    def test_detach_dry_run_carries_aggregate_diff(self) -> None:
        task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=self.repository, task_name="sprint"),
            operation="attach_master",
            edit=TaskDocEdit(
                fields={
                    "masterRef": {
                        "repository": self.repository,
                        "path": "master-b/task.json",
                    },
                    "number": "M2",
                    "name": "master-b",
                }
            ),
        )
        before = self._snapshot()
        dry = self._dry(
            "detach_master",
            {"masterRef": {"repository": self.repository, "path": "master-b/task.json"}},
        )
        self.assertTrue(dry["ok"])
        self.assertIn("diff", dry)
        self.assertIn("wouldLose", dry)
        self.assertTrue(dry["documents"])
        self.assertEqual(self._snapshot(), before)

    def test_attach_dry_run_writes_its_own_diff_report(self) -> None:
        whole = self._dry(
            "attach_master",
            {
                "masterRef": {"repository": self.repository, "path": "master-b/task.json"},
                "number": "M2",
                "name": "master-b",
            },
        )
        with tool_call_context(
            "task_doc", {"operation": "attach_master", "dry_run": True}, config=self.cfg
        ):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        report = answer.get("diffReport")
        self.assertIsInstance(report, str)
        assert isinstance(report, str)
        self.assertTrue(Path(report).is_file())
        self.assertEqual(Path(report).read_text(encoding="utf-8"), whole["diff"])

    def test_diff_report_survives_a_lost_detail_file(self) -> None:
        whole = self._dry(
            "attach_master",
            {
                "masterRef": {"repository": self.repository, "path": "master-b/task.json"},
                "number": "M2",
                "name": "master-b",
            },
        )
        with (
            patch.object(
                response_module,
                "prepare_call_detail",
                return_value=(None, "detail-unwritable", "abc"),
            ),
            tool_call_context(
                "task_doc", {"operation": "attach_master", "dry_run": True}, config=self.cfg
            ),
        ):
            answer = complete_tool_response("task_doc", whole, config=self.cfg)
        # No log, but the whole diff still has its own retained report: the
        # answer names it and promises nothing about the lost detail file.
        self.assertNotIn("log", answer)
        report = answer.get("diffReport")
        self.assertIsInstance(report, str)
        assert isinstance(report, str)
        self.assertTrue(Path(report).is_file())
        self.assertEqual(Path(report).read_text(encoding="utf-8"), whole["diff"])
