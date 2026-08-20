from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agents_remember.serving.projections.snapshots_impl._closeout_queue import (
    read_closeout_queues,
)
from test_closeout_queue import LEAF_A, LEAF_B, MASTER_A, MASTER_B, NOW, QueueFixture


class CloseoutQueueProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 8, 18, 12, 0, 0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_projects_candidates_and_grades_from_queue_artifact(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True)
        fixture.declare(MASTER_A)
        fixture.declare(MASTER_B, priority=None)
        queues = read_closeout_queues(fixture.coord, now=self.now)
        self.assertEqual(len(queues), 1)
        queue = queues[0]
        self.assertEqual(queue.sprintRef.repository, "repo-a")
        self.assertIsNone(queue.activeBlocker)
        by_key = {candidate.taskDocumentRef.key: candidate for candidate in queue.candidates}
        self.assertEqual(set(by_key), {LEAF_A.key, LEAF_B.key})
        self.assertEqual(by_key[LEAF_A.key].gradePriority, "normal")
        self.assertEqual(by_key[LEAF_A.key].candidateState, "declared")
        self.assertEqual(by_key[LEAF_B.key].gradePriority, None)
        self.assertEqual(by_key[LEAF_B.key].reasons, ["explicit-grade-required"])

    def test_projects_no_queue_when_artifact_absent(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True)
        self.assertEqual(read_closeout_queues(fixture.coord, now=self.now), [])

    def test_projects_atomic_blocker(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        fixture.declare(MASTER_A)
        fixture.declare(MASTER_B)
        state_path = fixture.tasks / "sprint" / "artifacts" / "closeout-candidates.json"
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["activeBlocker"] = {
            "master": MASTER_B.model_dump(mode="json"),
            "graphRevision": payload["graphRevision"],
            "acquiredBy": "orchestrator",
            "acquiredAt": NOW,
            "rationale": "atomic unit integration",
        }
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        queues = read_closeout_queues(fixture.coord, now=self.now)
        self.assertEqual(len(queues), 1)
        blocker = queues[0].activeBlocker
        self.assertIsNotNone(blocker)
        assert blocker is not None
        self.assertEqual(blocker.master, MASTER_B)
        self.assertEqual(blocker.rationale, "atomic unit integration")

    def test_ignores_invalid_queue_artifact(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True)
        fixture.declare(MASTER_A)
        state_path = fixture.tasks / "sprint" / "artifacts" / "closeout-candidates.json"
        state_path.write_text('{"revision": "not-an-int"}', encoding="utf-8")
        self.assertEqual(read_closeout_queues(fixture.coord, now=self.now), [])

    def test_ignores_invalid_task_document(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True)
        bad = fixture.tasks / "badtask" / "bad.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "kind": "master",
                    "executionGraph": {"nodes": [], "edges": []},
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(read_closeout_queues(fixture.coord, now=self.now), [])
