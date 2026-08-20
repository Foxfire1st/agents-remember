from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.models.queue.closeout_queue import (
    MAX_CLOSEOUT_CANDIDATES,
    MAX_CLOSEOUT_GRAPH_EDGES,
    MAX_CLOSEOUT_MASTERS,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SprintExecutionGraph, SprintExecutionNode, write_task_doc
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.worktrees.queue import closeout_queue_graph as graph_module
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
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
            nodes=[SprintExecutionNode(ref=MASTER_A)] * (MAX_CLOSEOUT_MASTERS + 1),
            edges=[],
        )
        topology.resolve.return_value = replace(
            self.sprint,
            document=self.sprint.document.model_copy(update={"executionGraph": oversized_nodes}),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "master-capacity"):
            graph_module.graph_context(topology, SPRINT)

        oversized_edges = SprintExecutionGraph.model_construct(
            nodes=[SprintExecutionNode(ref=MASTER_A)],
            edges=[mock.sentinel.edge] * (MAX_CLOSEOUT_GRAPH_EDGES + 1),
        )
        topology.resolve.return_value = replace(
            self.sprint,
            document=self.sprint.document.model_copy(update={"executionGraph": oversized_edges}),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "edge-capacity"):
            graph_module.graph_context(topology, SPRINT)

        one_node = SprintExecutionGraph.model_construct(
            nodes=[SprintExecutionNode(ref=MASTER_A)], edges=[]
        )
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
        node_b = graph.nodes[1]
        incomplete = graph_module.incomplete_predecessor_map(graph, completed=set())
        self.assertEqual(incomplete[node_b], (MASTER_A,))
        completed = graph_module.incomplete_predecessor_map(graph, completed={MASTER_A})
        self.assertEqual(completed[node_b], ())


def _segmented_master(ref: TaskDocumentRef, leaf_ids: list[str]) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": ref.path.split("/")[0].upper(),
            "slug": ref.path.split("/")[0],
            "title": ref.path.split("/")[0],
            "kind": "master",
            "status": "inProgress",
            "repo": "repo-a",
            "createdAt": "2026-08-15T00:00:00+00:00",
            "executionNature": "organizational",
            "subTasks": [
                {"number": leaf, "name": leaf, "file": f"{leaf.lower()}.md"} for leaf in leaf_ids
            ],
        }
    )


class CloseoutQueueSegmentGraphTests(unittest.TestCase):
    """L11-R2/R3: the queue graph is leaf->node aware and reports placement facts."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / "repo-a"
        self.tasks.mkdir(parents=True)
        self.topology = TaskDocumentTopology(self.coord)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_tree(self, *, leafs_a: list[str]) -> None:
        write_task_doc(self.tasks / "master-a", _segmented_master(MASTER_A, leafs_a))
        write_task_doc(self.tasks / "master-b", _segmented_master(MASTER_B, ["LEAF-B1"]))
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": "repo-a",
                    "createdAt": "2026-08-15T00:00:00+00:00",
                    "orchestrates": ["master-a", "master-b"],
                    "integrationBranch": "super",
                    "executionGraph": {
                        "nodes": [
                            {
                                "kind": "segment",
                                "ref": MASTER_A.model_dump(),
                                "leafIds": ["LEAF-A1"],
                            },
                            {
                                "kind": "segment",
                                "ref": MASTER_A.model_dump(),
                                "leafIds": ["LEAF-A2"],
                            },
                            MASTER_B.model_dump(),
                        ],
                        "edges": [
                            {
                                "predecessor": MASTER_B.model_dump(),
                                "successor": {"ref": MASTER_A.model_dump(), "leafId": "LEAF-A2"},
                                "reason": "B supplies A's second segment",
                                "judgmentId": "J-1",
                            }
                        ],
                    },
                }
            ),
        )

    def _context(self) -> graph_module.QueueGraphContext:
        return graph_module.graph_context(self.topology, SPRINT)

    def test_segment_targeted_edge_blocks_only_that_segments_leafs(self) -> None:
        self._write_tree(leafs_a=["LEAF-A1", "LEAF-A2"])
        context = self._context()
        by_leafs = {
            tuple(node.leafIds): context.incomplete_predecessors[node]
            for node in context.graph.nodes
        }
        self.assertEqual(by_leafs[("LEAF-A1",)], ())
        self.assertEqual(
            [node.ref for node in by_leafs[("LEAF-A2",)]],
            [MASTER_B],
        )
        self.assertEqual(by_leafs[()], ())  # the master-b lump has no predecessors
        leaf_a1 = TaskDocumentRef(repository="repo-a", path="master-a/leaf-a1.json")
        leaf_a2 = TaskDocumentRef(repository="repo-a", path="master-a/leaf-a2.json")
        self.assertEqual(context.leaf_nodes[leaf_a1].leafIds, ["LEAF-A1"])
        self.assertEqual(context.leaf_nodes[leaf_a2].leafIds, ["LEAF-A2"])
        self.assertEqual(context.leaf_facts, ())

    def test_unplaced_leaf_derived_placement_is_a_fact(self) -> None:
        self._write_tree(leafs_a=["LEAF-A1", "LEAF-A2", "LEAF-A3"])
        context = self._context()
        # LEAF-A2's segment is blocked by incomplete master-b; the derived target for
        # the unplaced LEAF-A3 is the latest *unblocked* segment (L11-R2).
        self.assertEqual(
            list(context.leaf_facts),
            [
                {
                    "kind": "unplaced-leaf",
                    "master": MASTER_A.key,
                    "leafId": "LEAF-A3",
                    "derivedSegmentLeafs": ["LEAF-A1"],
                    "derivedAllSegmentsBlocked": False,
                }
            ],
        )
        leaf_a3 = TaskDocumentRef(repository="repo-a", path="master-a/leaf-a3.json")
        self.assertEqual(context.leaf_nodes[leaf_a3].leafIds, ["LEAF-A1"])

    def test_unknown_leaf_is_a_fact_not_an_error(self) -> None:
        self._write_tree(leafs_a=["LEAF-A1"])
        context = self._context()
        self.assertEqual(
            list(context.leaf_facts),
            [{"kind": "unknown-leaf", "master": MASTER_A.key, "leafId": "LEAF-A2"}],
        )

    def test_candidate_predecessors_fall_back_to_master_nodes_when_leaf_is_unmapped(self) -> None:
        self._write_tree(leafs_a=["LEAF-A1", "LEAF-A2"])
        context = self._context()
        unmapped = TaskDocumentRef(repository="repo-a", path="master-a/leaf-a9.json")
        predecessors = graph_module.candidate_predecessors(context, MASTER_A, unmapped)
        self.assertEqual([node.ref for node in predecessors], [MASTER_B])

    def test_predecessor_reasons_name_the_segment_and_its_leafs(self) -> None:
        write_task_doc(self.tasks / "master-a", _segmented_master(MASTER_A, ["LEAF-A1", "LEAF-A2"]))
        write_task_doc(self.tasks / "master-b", _segmented_master(MASTER_B, ["LEAF-B1", "LEAF-B2"]))
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": "repo-a",
                    "createdAt": "2026-08-15T00:00:00+00:00",
                    "orchestrates": ["master-a", "master-b"],
                    "integrationBranch": "super",
                    "executionGraph": {
                        "nodes": [
                            {
                                "kind": "segment",
                                "ref": MASTER_A.model_dump(),
                                "leafIds": ["LEAF-A1"],
                            },
                            {
                                "kind": "segment",
                                "ref": MASTER_A.model_dump(),
                                "leafIds": ["LEAF-A2"],
                            },
                            {
                                "kind": "segment",
                                "ref": MASTER_B.model_dump(),
                                "leafIds": ["LEAF-B1"],
                            },
                            {
                                "kind": "segment",
                                "ref": MASTER_B.model_dump(),
                                "leafIds": ["LEAF-B2"],
                            },
                        ],
                        "edges": [
                            {
                                "predecessor": {"ref": MASTER_B.model_dump(), "leafId": "LEAF-B1"},
                                "successor": {"ref": MASTER_A.model_dump(), "leafId": "LEAF-A2"},
                                "reason": "B's first segment supplies A's second",
                                "judgmentId": "J-1",
                            }
                        ],
                    },
                }
            ),
        )
        context = self._context()
        leaf_a2 = TaskDocumentRef(repository="repo-a", path="master-a/leaf-a2.json")
        self.assertEqual(
            graph_module.predecessor_waiting_reasons(context, MASTER_A, leaf_a2),
            [f"predecessor-incomplete: {MASTER_B.key} (leafs: LEAF-B1)"],
        )
