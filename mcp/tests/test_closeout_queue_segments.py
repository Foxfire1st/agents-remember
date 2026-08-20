"""L11-R3/R6: queue scheduling and reporting over leaf-segment graphs.

Split from ``test_closeout_queue.py`` (file-size limit); the queue fixture and refs are
imported from it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.tasks import SprintExecutionGraph, SubTaskRef, read_task_doc, write_task_doc
from test_closeout_queue import MASTER_A, MASTER_B, QueueFixture


class SegmentGraphQueueTests(unittest.TestCase):
    """L11-R3/R6: queue scheduling and reporting over leaf-segment graphs."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _segmented_fixture(self, name: str, edge_leaf: str) -> QueueFixture:
        """Rewrite the fixture graph: master-a segmented [LEAF-A] / [LEAF-A2], edge B->edge_leaf."""
        fixture = QueueFixture(self.root / name, edge=True)
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["LEAF-A"]},
                    {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["LEAF-A2"]},
                    MASTER_B.model_dump(),
                ],
                "edges": [
                    {
                        "predecessor": MASTER_B.model_dump(),
                        "successor": {"ref": MASTER_A.model_dump(), "leafId": edge_leaf},
                        "reason": "B supplies one segment of A",
                        "judgmentId": "J-segment-edge",
                    }
                ],
            }
        )
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"executionGraph": graph}))
        master_a_path = fixture.tasks / "master-a" / "task.json"
        master_a = read_task_doc(master_a_path)
        write_task_doc(
            master_a_path.parent,
            master_a.model_copy(
                update={
                    "subTasks": [
                        *master_a.subTasks,
                        SubTaskRef(number="LEAF-A2", name="LEAF-A2", file="leaf-a2.md"),
                    ]
                }
            ),
        )
        return fixture

    def test_edge_into_a_segment_blocks_exactly_that_segments_leafs(self) -> None:
        unblocked = self._segmented_fixture("unblocked", "LEAF-A2")
        result = unblocked.declare(MASTER_A)
        self.assertEqual(result["waiting"], [])
        self.assertEqual(len(result["ready"]), 1)

        blocked = self._segmented_fixture("blocked", "LEAF-A")
        result = blocked.declare(MASTER_A)
        self.assertEqual(result["ready"], [])
        self.assertEqual(
            result["waiting"][0]["reasons"],
            ["predecessor-incomplete: repo-a/master-b/task.json"],
        )

    def test_unplaced_leaf_is_reported_as_fact_with_derived_segment(self) -> None:
        fixture = self._segmented_fixture("facts", "LEAF-A2")
        master_a_path = fixture.tasks / "master-a" / "task.json"
        master_a = read_task_doc(master_a_path)
        write_task_doc(
            master_a_path.parent,
            master_a.model_copy(
                update={
                    "subTasks": [
                        *master_a.subTasks,
                        SubTaskRef(number="LEAF-A3", name="LEAF-A3", file="leaf-a3.md"),
                    ]
                }
            ),
        )
        status = fixture.status()
        self.assertEqual(
            status["leafPlacementFacts"],
            [
                {
                    "kind": "unplaced-leaf",
                    "master": MASTER_A.key,
                    "leafId": "LEAF-A3",
                    "derivedSegmentLeafs": ["LEAF-A"],
                    "derivedAllSegmentsBlocked": False,
                }
            ],
        )
