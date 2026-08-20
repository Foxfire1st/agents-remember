"""Render-ready sprint graph projection builder tests (L12-R4).

The builder under test is primitives-only (the observer package must not
import tasks); the tests build a real persisted graph and walk it the same way
the serving layer does -- derived waves, resolved edge endpoints, facts, and
titles -- then feed the builder plain data.
"""

from __future__ import annotations

import unittest

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection_graph import (
    GraphPredecessorFacts,
    MasterGraphFacts,
    build_execution_graph_view,
    node_identity,
)
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.execution_graph_titles import SprintGraphTitles

REPO = "repo-a"
MASTER_A = TaskDocumentRef(repository=REPO, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPO, path="master-b/task.json")
ATOMIC_F = TaskDocumentRef(repository=REPO, path="atomic-f/task.json")


def _doc(
    ref: TaskDocumentRef,
    *,
    status: str = "planning",
    nature: str = "organizational",
    rows: list[dict[str, str]] | None = None,
) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": ref.path.split("/")[0].upper(),
            "slug": ref.path.split("/")[0],
            "title": f"Title of {ref.path.split('/')[0]}",
            "kind": "master",
            "status": status,
            "repo": REPO,
            "createdAt": "2026-08-15T00:00:00+00:00",
            "executionNature": nature,
            "subTasks": [
                {"number": row["number"], "name": row["name"], "status": row["status"]}
                for row in (rows or [])
            ],
        }
    )


def _facts(doc: TaskDocument) -> MasterGraphFacts:
    return MasterGraphFacts(
        status=doc.status,
        executionNature=doc.executionNature,
        leaf_statuses={row.number: row.status for row in doc.subTasks},
    )


def _segmented_graph() -> dict[str, object]:
    """Wave 1: Master A early segment; wave 2: atomic lump F; wave 3: Master A late segment."""
    return {
        "nodes": [
            {
                "kind": "segment",
                "ref": {"repository": REPO, "path": "master-a/task.json"},
                "leafIds": ["A-L1", "A-L2"],
            },
            {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
            {
                "kind": "segment",
                "ref": {"repository": REPO, "path": "master-a/task.json"},
                "leafIds": ["A-L3"],
            },
        ],
        "edges": [
            {
                "predecessor": {
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafId": "A-L1",
                },
                "successor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                "reason": "early segment lands before the atomic block",
                "judgmentId": "J-1",
            },
            {
                "predecessor": {"ref": {"repository": REPO, "path": "atomic-f/task.json"}},
                "successor": {
                    "ref": {"repository": REPO, "path": "master-a/task.json"},
                    "leafId": "A-L3",
                },
                "reason": "the atomic block gates the late segment",
            },
        ],
    }


def _graph(payload: dict[str, object]):
    """Validate a sprint doc payload and return its asserted execution graph."""
    graph = TaskDocument.model_validate(payload).executionGraph
    assert graph is not None
    return graph


def _walk(graph, masters_facts: dict[TaskDocumentRef, MasterGraphFacts], titles=None):
    """The serving-layer walk: resolve edges, then feed the primitives-only builder."""
    predecessor_edges = {node: [] for node in graph.nodes}
    for edge in graph.edges:
        predecessor = graph.resolve_endpoint(edge.predecessor)
        successor = graph.resolve_endpoint(edge.successor)
        predecessor_edges[successor].append(
            GraphPredecessorFacts(
                predecessor=predecessor,
                reason=edge.reason,
                judgmentId=edge.judgmentId,
            )
        )
    return build_execution_graph_view(
        graph.nodes,
        graph.derived_waves(),
        predecessor_edges,
        masters_facts,
        titles,
    )


class ExecutionGraphViewBuilderTests(unittest.TestCase):
    def test_zero_edge_graph_projects_one_wave_of_independent_nodes(self) -> None:
        graph = _graph(
            {
                "id": "S",
                "slug": "task",
                "kind": "master",
                "title": "Sprint",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
                "orchestrates": ["master-a", "master-b"],
                "executionGraph": {
                    "nodes": [
                        {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                        {"ref": {"repository": REPO, "path": "master-b/task.json"}},
                    ],
                    "edges": [],
                },
            }
        )
        masters = {
            MASTER_A: _facts(
                _doc(
                    MASTER_A,
                    status="inProgress",
                    rows=[{"number": "A-L1", "name": "One", "status": "inProgress"}],
                )
            ),
            MASTER_B: _facts(_doc(MASTER_B, status="Completed", nature="atomic")),
        }
        view = _walk(graph, masters)
        self.assertEqual([node.waveIndex for node in view.nodes], [1, 1])
        self.assertEqual([node.kind for node in view.nodes], ["lump", "lump"])
        self.assertEqual([node.frontierState for node in view.nodes], ["in-flight", "landed"])
        self.assertEqual(view.nodes[0].masterTitle, "repo-a/master-a/task.json")
        self.assertEqual(view.nodes[1].executionNature, "atomic")

    def test_segmented_master_projects_wave_ordered_nodes_with_titles_and_reasons(self) -> None:
        graph = _graph(
            {
                "id": "S",
                "slug": "task",
                "kind": "master",
                "title": "Sprint",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
                "orchestrates": ["master-a", "atomic-f"],
                "executionGraph": _segmented_graph(),
            }
        )
        masters = {
            MASTER_A: _facts(
                _doc(
                    MASTER_A,
                    status="inProgress",
                    rows=[
                        {"number": "A-L1", "name": "Leaf one", "status": "Completed"},
                        {"number": "A-L2", "name": "Leaf two", "status": "inProgress"},
                        {"number": "A-L3", "name": "Leaf three", "status": "planning"},
                    ],
                )
            ),
            ATOMIC_F: _facts(_doc(ATOMIC_F, nature="atomic")),
        }
        titles = SprintGraphTitles(
            master_titles={
                "repo-a/master-a/task.json": "Title of master-a",
                "repo-a/atomic-f/task.json": "Title of atomic-f",
            },
            leaf_titles={"A-L1": "Leaf one", "A-L2": "Leaf two", "A-L3": "Leaf three"},
        )
        view = _walk(graph, masters, titles=titles)
        self.assertEqual([node.waveIndex for node in view.nodes], [1, 2, 3])
        self.assertEqual([node.kind for node in view.nodes], ["segment", "lump", "segment"])
        # The late segment is waiting: its atomic predecessor is not landed (planning).
        self.assertEqual(
            [node.frontierState for node in view.nodes], ["in-flight", "waiting", "waiting"]
        )
        early, atomic, late = view.nodes
        self.assertEqual(early.nodeId, "repo-a/master-a/task.json#seg1")
        self.assertEqual(late.nodeId, "repo-a/master-a/task.json#seg2")
        self.assertEqual(early.masterTitle, "Title of master-a")
        self.assertEqual(early.leafTitles, ["Leaf one", "Leaf two"])
        # atomic lump carries no leaf list
        self.assertEqual(atomic.leafIds, [])
        # predecessors carry the master title plus the recorded reason
        self.assertEqual(
            [(p.predecessorTitle, p.reason) for p in atomic.predecessors],
            [("Title of master-a", "early segment lands before the atomic block")],
        )
        self.assertEqual(
            [(p.predecessorTitle, p.reason, p.judgmentId) for p in late.predecessors],
            [("Title of atomic-f", "the atomic block gates the late segment", None)],
        )
        self.assertEqual(atomic.predecessors[0].judgmentId, "J-1")

    def test_missing_master_falls_back_to_ref_key_and_conservative_state(self) -> None:
        graph = _graph(
            {
                "id": "S",
                "slug": "task",
                "kind": "master",
                "title": "Sprint",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
                "orchestrates": ["master-a"],
                "executionGraph": {
                    "nodes": [{"ref": {"repository": REPO, "path": "master-a/task.json"}}],
                    "edges": [],
                },
            }
        )
        view = _walk(graph, {})
        [node] = view.nodes
        self.assertEqual(node.masterTitle, "repo-a/master-a/task.json")
        self.assertIsNone(node.executionNature)
        self.assertEqual(node.frontierState, "ready")  # never landed, never in-flight

    def test_node_identity_is_stable_for_lump_and_segments(self) -> None:
        graph = _graph(
            {
                "id": "S",
                "slug": "task",
                "kind": "master",
                "title": "Sprint",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
                "orchestrates": ["master-a"],
                "executionGraph": _segmented_graph(),
            }
        )
        lump = graph.nodes_for(ATOMIC_F)[0]
        first_segment = graph.nodes_for(MASTER_A)[0]
        self.assertEqual(node_identity(graph.nodes, lump), "repo-a/atomic-f/task.json")
        self.assertEqual(
            node_identity(graph.nodes, first_segment), "repo-a/master-a/task.json#seg1"
        )
