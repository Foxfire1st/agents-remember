from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SprintExecutionGraph, SubTaskRef, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import _graph_context, _initial_state
from agents_remember.worktrees.orchestration_portfolio import (
    FrontierCandidate,
    choose,
    classify_reshape,
    manager_slice,
    recompute_frontier,
)
from test_closeout_queue import (
    LEAF_A,
    LEAF_B,
    MASTER_A,
    MASTER_B,
    NOW,
    SPRINT,
    QueueFixture,
)


class PortfolioClassifyTests(unittest.TestCase):
    def test_reshape_classification(self) -> None:
        self.assertEqual(classify_reshape(), "ordinary")
        self.assertEqual(classify_reshape(edge_changed=True), "substantial")
        self.assertEqual(classify_reshape(nature_changed=True), "substantial")
        self.assertEqual(classify_reshape(leaf_moved=True), "substantial")
        self.assertEqual(classify_reshape(new_foundation=True), "substantial")


class PortfolioChooseTests(unittest.TestCase):
    @staticmethod
    def _candidate(key: str, priority: str, node_order: int) -> FrontierCandidate:
        return FrontierCandidate(
            taskDocumentRef=TaskDocumentRef(repository="repo-a", path=f"{key}.json"),
            owningMaster=TaskDocumentRef(repository="repo-a", path="m.json"),
            priority=priority,
            nodeOrder=node_order,
        )

    def test_choose_prefers_priority_then_node_order(self) -> None:
        critical = self._candidate("critical-leaf", "critical", 5)
        normal_low = self._candidate("normal-low", "normal", 0)
        normal_high = self._candidate("normal-high", "normal", 1)
        self.assertEqual(
            choose([normal_low, critical, normal_high]).taskDocumentRef.path, "critical-leaf.json"
        )
        self.assertEqual(choose([normal_high, normal_low]).taskDocumentRef.path, "normal-low.json")

    def test_choose_refuses_empty_frontier(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty frontier"):
            choose([])


class PortfolioFrontierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _graph_and_state(self, fixture: QueueFixture):
        topology = TaskDocumentTopology(fixture.coord)
        graph = _graph_context(topology, SPRINT)
        initial = _initial_state(SPRINT, graph.revision, NOW)
        state = CloseoutQueueStore(fixture.coord, SPRINT).read(initial)
        return graph, state

    def test_recompute_frontier_excludes_downstream_and_ungraded(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True)
        fixture.declare(MASTER_A)
        fixture.declare(MASTER_B)
        graph, state = self._graph_and_state(fixture)
        frontier = recompute_frontier(graph, state)
        keys = {candidate.taskDocumentRef.key for candidate in frontier}
        self.assertEqual(keys, {LEAF_A.key})

    def test_recompute_frontier_excludes_atomic_master_without_active_blocker(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True, atomic_b=True)
        fixture.declare(MASTER_A)
        fixture.declare(MASTER_B)
        graph, state = self._graph_and_state(fixture)
        frontier = recompute_frontier(graph, state)
        keys = {candidate.taskDocumentRef.key for candidate in frontier}
        self.assertEqual(keys, {LEAF_A.key})

    def test_recompute_frontier_excludes_ungraded_candidate(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A, priority=None)
        graph, state = self._graph_and_state(fixture)
        self.assertEqual(recompute_frontier(graph, state), [])

    def test_manager_slice_scopes_to_owning_master(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True)
        fixture.declare(MASTER_A)
        fixture.declare(MASTER_B)
        graph, state = self._graph_and_state(fixture)
        slice_a = manager_slice(graph, state, MASTER_A)
        self.assertEqual(slice_a.masterRef, MASTER_A)
        self.assertEqual(slice_a.executionNature, "organizational")
        self.assertEqual(slice_a.incompletePredecessors, [])
        self.assertEqual([view.taskDocumentRef.key for view in slice_a.candidates], [LEAF_A.key])

        slice_b = manager_slice(graph, state, MASTER_B)
        self.assertEqual(slice_b.masterRef, MASTER_B)
        self.assertEqual(slice_b.incompletePredecessors, [MASTER_A])
        self.assertEqual([view.taskDocumentRef.key for view in slice_b.candidates], [LEAF_B.key])

    def test_manager_slice_unknown_master_falls_back_to_unknown_nature(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        graph, state = self._graph_and_state(fixture)
        foreign = TaskDocumentRef(repository=SPRINT.repository, path="foreign/task.json")
        slice_foreign = manager_slice(graph, state, foreign)
        self.assertEqual(slice_foreign.executionNature, "unknown")
        self.assertEqual(slice_foreign.incompletePredecessors, [])
        self.assertEqual(slice_foreign.candidates, [])

    def test_frontier_orders_an_unmapped_leaf_candidate_by_its_masters_first_node(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        graph = SprintExecutionGraph.model_validate(
            {
                "nodes": [
                    {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["LEAF-A"]},
                    {"kind": "segment", "ref": MASTER_A.model_dump(), "leafIds": ["LEAF-A2"]},
                    MASTER_B.model_dump(),
                ],
                "edges": [],
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
        fixture.declare(MASTER_A)
        graph, state = self._graph_and_state(fixture)
        # A candidate whose leaf ref maps to no segment falls back to the master's
        # earliest node order (the conservative scheduling answer).
        unmapped = TaskDocumentRef(repository="repo-a", path="master-a/leaf-a9.json")
        candidate = state.candidates[LEAF_A.key].model_copy(update={"taskDocumentRef": unmapped})
        state = state.model_copy(update={"candidates": {unmapped.key: candidate}})
        frontier = recompute_frontier(graph, state)
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0].nodeOrder, 0)


if __name__ == "__main__":
    unittest.main()
