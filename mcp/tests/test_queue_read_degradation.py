"""L13-R4: the closeout queue read path degrades to facts, never raises.

``status`` is the only read action: an absent executionGraph projects the
atomic-sequential default (mode, lane owner, legal next operations), and a
missing or malformed canonical register is reported as a fact. Mutations stay
guarded and their refusals name the recovery.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.models.closeout_queue import CloseoutQueueRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.closeout_queue import QueueActor, closeout_queue_tool
from agents_remember.worktrees.closeout_queue_errors import CloseoutQueueError
from test_closeout_queue import JUDGMENT_HEADING, MASTER_B, NOW, REPO, SPRINT, QueueFixture


class QueueReadDegradationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _status(self, fixture: QueueFixture) -> dict[str, Any]:
        return closeout_queue_tool(
            fixture.cfg,
            CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
            actor=QueueActor(role="orchestrator", task_document_ref=SPRINT),
            now=NOW,
        )

    def _remove_graph(self, fixture: QueueFixture) -> None:
        path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(path)
        write_task_doc(path.parent, sprint.model_copy(update={"executionGraph": None}))

    def test_graph_less_sprint_projects_the_atomic_sequential_default(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        self._remove_graph(fixture)
        payload = self._status(fixture)
        self.assertEqual(payload["state"], "degraded")
        self.assertEqual(payload["mode"], "atomic-sequential")
        # The fixture's series artifacts are terminal, so the lane is free.
        self.assertIsNone(payload["laneOwner"])
        self.assertTrue(payload["legalNextOperations"])
        self.assertEqual(payload["ready"], [])
        self.assertEqual(payload["registers"], {"judgmentRegister": "ok", "priorityRegister": "ok"})

    def test_graph_less_sprint_reports_the_series_lane_owner(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        self._remove_graph(fixture)
        payload = self._status(fixture)
        self.assertEqual(payload["state"], "degraded")
        # MASTER_B's live series contract owns the sequential lane (stored fact).
        self.assertEqual(payload["laneOwner"], MASTER_B.key)

    def test_absent_registers_are_reported_as_facts(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(path)
        write_task_doc(path.parent, sprint.model_copy(update={"sections": []}))
        self._remove_graph(fixture)
        payload = self._status(fixture)
        self.assertEqual(payload["state"], "degraded")
        self.assertEqual(
            payload["registers"],
            {"judgmentRegister": "absent", "priorityRegister": "absent"},
        )

    def test_malformed_register_degrades_the_projection_without_raising(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_B)
        fixture.replace_section_body(JUDGMENT_HEADING, "no canonical table here")
        payload = self._status(fixture)
        self.assertEqual(payload["state"], "degraded")
        registers = payload["registers"]
        assert isinstance(registers, dict)
        self.assertTrue(registers["judgmentRegister"].startswith("malformed:"))
        self.assertEqual(registers["priorityRegister"], "ok")
        self.assertEqual(payload["mode"], "dag")
        # The projection still reports the declared candidate.
        all_candidates = [
            item for lane in ("ready", "waiting", "blocked", "inFlight") for item in payload[lane]
        ]
        self.assertEqual(len(all_candidates), 1)

    def test_mutations_stay_guarded_and_name_the_recovery(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.replace_section_body(JUDGMENT_HEADING, "no canonical table here")
        with self.assertRaises(CloseoutQueueError) as raised:
            fixture.declare(MASTER_B)
        self.assertIn("register", str(raised.exception))
        self.assertIn("task_doc.set_section", str(raised.exception))

    def test_healthy_graph_status_reports_dag_mode(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        payload = self._status(fixture)
        self.assertEqual(payload["state"], "projected")
        self.assertEqual(payload["mode"], "dag")
        self.assertEqual(payload["registers"], {"judgmentRegister": "ok", "priorityRegister": "ok"})
        self.assertIsNone(payload["laneOwner"])

    def test_status_on_a_missing_or_non_sprint_document_fails_as_argument_fault(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        missing = TaskDocumentRef(repository=REPO, path="missing/task.json")
        with self.assertRaises(CloseoutQueueError) as raised:
            closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(action="status", sprint_task_document_ref=missing),
                actor=QueueActor(role="orchestrator", task_document_ref=SPRINT),
                now=NOW,
            )
        self.assertIn("task-document-not-found", str(raised.exception))

        plain_master = TaskDocumentRef(repository=REPO, path="master-a/task.json")
        with self.assertRaises(CloseoutQueueError) as raised:
            closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(action="status", sprint_task_document_ref=plain_master),
                actor=QueueActor(role="orchestrator", task_document_ref=SPRINT),
                now=NOW,
            )
        self.assertIn("closeout-queue-sprint-required", str(raised.exception))

    def test_status_fails_closed_when_mode_resolution_moves(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        self._remove_graph(fixture)
        with (
            mock.patch(
                "agents_remember.worktrees.closeout_queue.resolve_scheduling_mode",
                side_effect=TaskDocumentRefError("task-document-not-found", "sprint moved"),
            ),
            self.assertRaises(CloseoutQueueError) as raised,
        ):
            self._status(fixture)
        self.assertIn("task-document-not-found", str(raised.exception))

    def test_degraded_scope_authorization(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        self._remove_graph(fixture)
        # A commanded master's manager may read the degraded projection.
        payload = closeout_queue_tool(
            fixture.cfg,
            CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
            actor=QueueActor(role="manager", task_document_ref=MASTER_B),
            now=NOW,
        )
        self.assertEqual(payload["state"], "degraded")
        # An uncommanded caller is refused.
        outsider = TaskDocumentRef(repository=REPO, path="master-z/task.json")
        with self.assertRaises(CloseoutQueueError) as raised:
            closeout_queue_tool(
                fixture.cfg,
                CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
                actor=QueueActor(role="manager", task_document_ref=outsider),
                now=NOW,
            )
        self.assertIn("closeout-queue-caller-refused", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
