from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.models.closeout_queue import (
    MAX_CLOSEOUT_CANDIDATES,
    MAX_CLOSEOUT_GRAPH_EDGES,
    MAX_CLOSEOUT_MASTERS,
)
from agents_remember.tasks import SprintExecutionGraph
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees import closeout_queue_graph as graph_module
from agents_remember.worktrees.closeout_queue import CloseoutQueueError
from test_closeout_queue import MASTER_A, MASTER_B, SPRINT, QueueFixture


class CloseoutQueueGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = QueueFixture(Path(self.temp.name), edge=True)
        self.topology = TaskDocumentTopology(self.fixture.coord)
        self.sprint = self.topology.resolve(SPRINT)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_graph_context_translates_sprint_and_topology_resolution_errors(self) -> None:
        topology = mock.Mock()
        topology.resolve.side_effect = TaskDocumentRefError("missing", "sprint missing")
        with self.assertRaisesRegex(CloseoutQueueError, "sprint missing"):
            graph_module.graph_context(topology, SPRINT)
        topology.resolve.side_effect = None
        topology.resolve.return_value = self.sprint
        topology.validate_execution_topology.side_effect = TaskDocumentRefError(
            "invalid", "graph invalid"
        )
        with self.assertRaisesRegex(CloseoutQueueError, "graph invalid"):
            graph_module.graph_context(topology, SPRINT)

    def test_graph_context_requires_migration_and_enforces_all_capacity_bounds(self) -> None:
        topology = mock.Mock()
        topology.resolve.return_value = replace(
            self.sprint,
            document=self.sprint.document.model_copy(update={"executionGraph": None}),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "migration-required"):
            graph_module.graph_context(topology, SPRINT)

        oversized_nodes = SprintExecutionGraph.model_construct(
            nodes=[MASTER_A] * (MAX_CLOSEOUT_MASTERS + 1),
            edges=[],
        )
        topology.resolve.return_value = replace(
            self.sprint,
            document=self.sprint.document.model_copy(update={"executionGraph": oversized_nodes}),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "master-capacity"):
            graph_module.graph_context(topology, SPRINT)

        oversized_edges = SprintExecutionGraph.model_construct(
            nodes=[MASTER_A],
            edges=[mock.sentinel.edge] * (MAX_CLOSEOUT_GRAPH_EDGES + 1),
        )
        topology.resolve.return_value = replace(
            self.sprint,
            document=self.sprint.document.model_copy(update={"executionGraph": oversized_edges}),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "edge-capacity"):
            graph_module.graph_context(topology, SPRINT)

        one_node = SprintExecutionGraph.model_construct(nodes=[MASTER_A], edges=[])
        topology.resolve.return_value = replace(
            self.sprint,
            document=self.sprint.document.model_copy(update={"executionGraph": one_node}),
        )
        master = self.topology.resolve(MASTER_A)
        too_many_subtasks = master.document.model_copy(
            update={"subTasks": [mock.sentinel.row] * (MAX_CLOSEOUT_CANDIDATES + 1)}
        )
        topology.validate_execution_topology.return_value = [
            replace(master, document=too_many_subtasks)
        ]
        with self.assertRaisesRegex(CloseoutQueueError, "capacity-exceeded"):
            graph_module.graph_context(topology, SPRINT)

    def test_incomplete_predecessor_map_preserves_graph_order(self) -> None:
        graph = self.sprint.document.executionGraph
        assert graph is not None
        incomplete = graph_module.incomplete_predecessor_map(graph, completed=set())
        self.assertEqual(incomplete[MASTER_B], (MASTER_A,))
        completed = graph_module.incomplete_predecessor_map(graph, completed={MASTER_A})
        self.assertEqual(completed[MASTER_B], ())


if __name__ == "__main__":
    unittest.main()
