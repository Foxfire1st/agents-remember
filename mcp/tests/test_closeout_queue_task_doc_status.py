from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_docs.task_execution_topology import ExecutionTopologyError
from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStoreError,
    queue_store_paths,
)
from agents_remember.models.queue.closeout_queue import CloseoutQueueState
from agents_remember.tasks import read_task_doc, write_task_doc
from test_closeout_queue import LEAF_A, MASTER_A, REPO, SPRINT, QueueFixture


class CloseoutQueueTaskDocStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_task_doc_completion_uses_the_queue_quiescence_owner(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        target = TaskDocTarget(repo_id=REPO, task_name="sprint")
        edit = TaskDocEdit(fields={"status": "Completed"})
        with self.assertRaisesRegex(ExecutionTopologyError, "commanded masters remain incomplete"):
            task_doc_tool(fixture.cfg, target, operation="set_status", edit=edit)
        self.assertEqual(read_task_doc(fixture.tasks / "sprint" / "task.json").status, "inProgress")
        for ref, master in fixture.master_docs.items():
            write_task_doc(
                fixture.tasks / Path(ref.path).parent,
                master.model_copy(update={"status": "Completed"}),
            )
        with self.assertRaisesRegex(ExecutionTopologyError, "commanded masters remain incomplete"):
            task_doc_tool(fixture.cfg, target, operation="set_status", edit=edit)
        fixture.declare(MASTER_A)
        for ref, master in fixture.master_docs.items():
            completed_rows = [
                row.model_copy(update={"status": "Completed"}) for row in master.subTasks
            ]
            write_task_doc(
                fixture.tasks / Path(ref.path).parent,
                master.model_copy(update={"status": "Completed", "subTasks": completed_rows}),
            )
        with self.assertRaisesRegex(CloseoutQueueStoreError, "cannot complete"):
            task_doc_tool(fixture.cfg, target, operation="set_status", edit=edit)
        fixture.mutate("withdraw", candidate=LEAF_A)
        completed = task_doc_tool(fixture.cfg, target, operation="set_status", edit=edit)
        self.assertEqual(completed["status"], "Completed")
        state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
        self.assertTrue(
            CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8")).closed
        )
        reopened = task_doc_tool(
            fixture.cfg,
            target,
            operation="set_status",
            edit=TaskDocEdit(fields={"status": "inProgress"}),
        )
        self.assertEqual(reopened["status"], "inProgress")
        reopened_state = CloseoutQueueState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
        self.assertFalse(reopened_state.closed)
        write_task_doc(fixture.tasks / "master-a", fixture.master_docs[MASTER_A])
        declared = fixture.declare(MASTER_A)
        self.assertEqual(declared["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())
