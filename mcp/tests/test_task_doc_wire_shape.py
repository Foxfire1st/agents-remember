"""Wire-shape lock for the task_doc special ops (260815-DAG envelope fix).

The special ops -- attach_master, detach_master, linkage_report,
author_execution_graph and get-on-a-sprint -- must satisfy the TaskDocResponse
envelope (extra=forbid). Same bug class as 260703-L18 finding 1 (remove_subtask):
before the fix they returned raw operation payloads that lacked the standard
task_doc identity (taskId/slug/kind/status/docPath/renderedPath/...) and carried
undeclared extras, so the envelope REJECTED them after their writes. Each op's
real and dry-run payload must validate through TaskDocResponse.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, write_task_doc
from test_task_execution_topology import (
    MASTER_A,
    MASTER_B,
    MASTER_C,
    REPOSITORY,
    _config,
    _master,
)
from test_task_sprint_linkage import _register_section
from test_worktree_support import git, init_repo


class TaskDocSpecialOpWireShapeTests(unittest.TestCase):
    """The special ops must satisfy the TaskDocResponse envelope (extra=forbid).

    Same bug class as 260703-L18 finding 1 (remove_subtask): attach_master,
    detach_master, linkage_report and author_execution_graph returned raw
    operation payloads that lacked the standard task_doc identity and carried
    undeclared extras, so the envelope REJECTED them after their writes. Each
    op's real and dry-run payload must validate through TaskDocResponse.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.code = self.coord / "code"
        init_repo(self.code)
        git(self.code, "branch", "super", "main")
        self.cfg = _config(self.coord, self.code)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_master(
        self,
        ref: TaskDocumentRef,
        *,
        nature: str | None = None,
        status: str = "planning",
    ) -> None:
        folder = Path(ref.path).parent.name
        write_task_doc(
            self.tasks / folder,
            _master(identity=folder, execution_nature=nature).model_copy(update={"status": status}),
        )

    def _write_sprint(
        self,
        *,
        graph: dict[str, Any] | None = None,
        rows: list[SubTaskRef] | None = None,
        orchestrates: list[str] | None = None,
    ) -> None:
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=(
                    orchestrates if orchestrates is not None else ["master-a", "master-b"]
                ),
                execution_graph=graph,
            ).model_copy(
                update={
                    "integrationBranch": "super",
                    "sections": [_register_section("J-1", "J-2")],
                    "subTasks": rows or [],
                }
            ),
        )

    def _graph_ful_sprint(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []}
        )

    def _op(
        self,
        operation: str,
        fields: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation=operation,
            edit=TaskDocEdit(fields=fields),
            call=TaskDocCall(dry_run=dry_run),
        )

    def _attach(self, fields: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        return self._op("attach_master", fields, dry_run=dry_run)

    def _detach(self, fields: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        return self._op("detach_master", fields, dry_run=dry_run)

    def _assert_validates(self, payload: dict[str, Any]) -> None:
        # Would raise ValidationError before the R5 fix (missing identity + extras).
        TaskDocResponse.model_validate(payload)
        self.assertEqual(payload["taskId"], "SPRINT")
        self.assertEqual(payload["slug"], "SPRINT")
        self.assertEqual(payload["kind"], "master")
        self.assertTrue(payload["docPath"].endswith(".json"))
        self.assertTrue(payload["renderedPath"].endswith(".md"))

    def test_attach_master_payloads_validate(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C)
        real = self._attach(
            {
                "masterRef": MASTER_C.model_dump(),
                "number": "M3",
                "executionNature": "organizational",
                "judgmentId": "J-1",
            }
        )
        self._assert_validates(real)
        # The real attach above published the row; reset the sprint so the dry run
        # starts from the same pre-attach shape (a second attach would be refused).
        self._graph_ful_sprint()
        self._write_master(MASTER_C)
        preview = self._attach(
            {
                "masterRef": MASTER_C.model_dump(),
                "number": "M3",
                "executionNature": "atomic",
                "judgmentId": "J-2",
            },
            dry_run=True,
        )
        self._assert_validates(preview)

    def test_detach_master_payloads_validate(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        real = self._detach({"masterRef": MASTER_C.model_dump()})
        self._assert_validates(real)
        self._write_master(MASTER_C, nature="atomic")
        self._attach({"masterRef": MASTER_C.model_dump(), "number": "M3"})
        preview = self._detach({"masterRef": MASTER_C.model_dump()}, dry_run=True)
        self._assert_validates(preview)

    def test_linkage_report_payload_validates(self) -> None:
        self._graph_ful_sprint()
        payload = self._op("linkage_report", {})
        TaskDocResponse.model_validate(payload)
        self.assertEqual(payload["taskId"], "SPRINT")
        # A sprint whose commanded masters have no typed rows yet surfaces
        # membership-without-row facts; the facts list is present and shaped.
        self.assertIsInstance(payload["linkageFacts"], list)
        self.assertEqual(payload["linkageFacts"][0]["kind"], "membership-without-row")

    def test_author_execution_graph_payloads_validate(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(graph=None)
        mutations = {
            "mutations": [
                {"op": "add_node", "ref": MASTER_A.model_dump()},
                {"op": "add_node", "ref": MASTER_B.model_dump()},
                {
                    "op": "set_nature",
                    "ref": MASTER_A.model_dump(),
                    "executionNature": "organizational",
                    "judgmentId": "J-1",
                },
                {
                    "op": "set_nature",
                    "ref": MASTER_B.model_dump(),
                    "executionNature": "atomic",
                    "judgmentId": "J-2",
                },
            ]
        }
        real = self._op("author_execution_graph", mutations)
        self._assert_validates(real)
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(graph=None)
        preview = self._op("author_execution_graph", mutations, dry_run=True)
        self._assert_validates(preview)

    def test_get_on_a_sprint_with_linkage_facts_validates(self) -> None:
        self._graph_ful_sprint()
        self._write_master(MASTER_C, nature="atomic")
        self._write_sprint(
            graph={
                "nodes": [
                    MASTER_A.model_dump(),
                    MASTER_B.model_dump(),
                    MASTER_C.model_dump(),
                ],
                "edges": [],
            },
            orchestrates=["master-a", "master-b", "master-c"],
            rows=[
                SubTaskRef(number="M1", name="master-a", masterRef=MASTER_A),
                SubTaskRef(number="M2", name="master-b", masterRef=MASTER_B),
                SubTaskRef(number="M3", name="master-c", masterRef=MASTER_C),
            ],
        )
        payload = task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation="get",
        )
        self.assertIn("linkageFacts", payload)
        TaskDocResponse.model_validate(payload)  # would raise before the R5 fix
