"""Task-documents projection wiring for the render-ready graph view (L12-R4)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.projections.snapshots_impl._task_documents import (
    _master_docs_by_ref,
    read_task_documents,
)
from agents_remember.tasks import TaskDocument, write_task_doc
from test_observer_projection import FRESH

REPO = "repo-a"


def _doc(**over: object) -> TaskDocument:
    base: dict[str, object] = {
        "id": "D",
        "slug": "task",
        "title": "Demo",
        "kind": "light",
        "repo": REPO,
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


class TaskDocumentsGraphViewProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)

    def _master(self, name: str, *, status: str, nature: str, rows: list[dict[str, str]]) -> None:
        write_task_doc(
            self.coord / "tasks" / REPO / name,
            TaskDocument.model_validate(
                {
                    "id": name.upper(),
                    "slug": name,
                    "title": f"Title {name}",
                    "kind": "master",
                    "status": status,
                    "repo": REPO,
                    "createdAt": "2026-08-15T00:00:00+00:00",
                    "executionNature": nature,
                    "subTasks": [
                        {
                            "number": row["number"],
                            "name": row["name"],
                            "status": row["status"],
                        }
                        for row in rows
                    ],
                }
            ),
        )

    def test_sprint_doc_carries_render_ready_graph_view(self) -> None:
        write_task_doc(
            self.coord / "tasks" / REPO / "sprint",
            _doc(
                id="SPRINT",
                kind="master",
                title="Sprint",
                orchestrates=["master-a", "master-b"],
                executionGraph={
                    "nodes": [
                        {"ref": {"repository": REPO, "path": "master-a/task.json"}},
                        {"ref": {"repository": REPO, "path": "master-b/task.json"}},
                    ],
                    "edges": [],
                },
            ),
        )
        self._master(
            "master-a",
            status="inProgress",
            nature="organizational",
            rows=[{"number": "A-L1", "name": "Leaf one", "status": "inProgress"}],
        )
        self._master(
            "master-b",
            status="Completed",
            nature="atomic",
            rows=[{"number": "B-L1", "name": "Lump leaf", "status": "Completed"}],
        )
        write_task_doc(self.coord / "tasks" / REPO / "plain", _doc())

        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        sprint = next(node for node in nodes if node.id == "SPRINT")
        view = sprint.executionGraphView
        self.assertIsNotNone(view)
        assert view is not None
        # zero-edge graph: one wave, both masters independent
        self.assertEqual([node.waveIndex for node in view.nodes], [1, 1])
        self.assertEqual(
            [(node.masterTitle, node.frontierState) for node in view.nodes],
            [("Title master-a", "in-flight"), ("Title master-b", "landed")],
        )
        # non-sprint docs carry no graph view
        plain = next(node for node in nodes if node.id == "D")
        self.assertIsNone(plain.executionGraphView)

    def test_segmented_master_scenario_projects_titles_and_predecessors(self) -> None:
        write_task_doc(
            self.coord / "tasks" / REPO / "sprint",
            _doc(
                id="SPRINT",
                kind="master",
                title="Sprint",
                orchestrates=["master-a", "atomic-f"],
                executionGraph={
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
                            "successor": {
                                "ref": {"repository": REPO, "path": "atomic-f/task.json"}
                            },
                            "reason": "early segment gates the atomic block",
                        },
                        {
                            "predecessor": {
                                "ref": {"repository": REPO, "path": "atomic-f/task.json"}
                            },
                            "successor": {
                                "ref": {"repository": REPO, "path": "master-a/task.json"},
                                "leafId": "A-L3",
                            },
                            "reason": "the atomic block gates the late segment",
                        },
                    ],
                },
            ),
        )
        self._master(
            "master-a",
            status="inProgress",
            nature="organizational",
            rows=[
                {"number": "A-L1", "name": "Leaf one", "status": "Completed"},
                {"number": "A-L2", "name": "Leaf two", "status": "inProgress"},
                {"number": "A-L3", "name": "Leaf three", "status": "planning"},
            ],
        )
        self._master(
            "atomic-f",
            status="planning",
            nature="atomic",
            rows=[{"number": "F-L1", "name": "F leaf", "status": "planning"}],
        )

        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        sprint = next(node for node in nodes if node.id == "SPRINT")
        view = sprint.executionGraphView
        self.assertIsNotNone(view)
        assert view is not None
        self.assertEqual([node.waveIndex for node in view.nodes], [1, 2, 3])
        early, atomic, late = view.nodes
        self.assertEqual(early.leafTitles, ["Leaf one", "Leaf two"])
        self.assertEqual(atomic.frontierState, "waiting")
        self.assertEqual(
            [(p.predecessorTitle, p.reason) for p in atomic.predecessors],
            [("Title master-a", "early segment gates the atomic block")],
        )
        # The late segment is waiting: its atomic predecessor is not landed (planning).
        self.assertEqual(late.frontierState, "waiting")
        self.assertEqual(
            [(p.predecessorTitle, p.reason) for p in late.predecessors],
            [("Title atomic-f", "the atomic block gates the late segment")],
        )

    def test_duplicate_local_leaf_numbers_keep_master_qualified_titles(self) -> None:
        write_task_doc(
            self.coord / "tasks" / REPO / "sprint",
            _doc(
                id="SPRINT",
                kind="master",
                title="Sprint",
                orchestrates=["master-a", "master-b"],
                executionGraph={
                    "nodes": [
                        {
                            "kind": "segment",
                            "ref": {"repository": REPO, "path": "master-a/task.json"},
                            "leafIds": ["L1"],
                        },
                        {"ref": {"repository": REPO, "path": "master-b/task.json"}},
                    ],
                    "edges": [
                        {
                            "predecessor": {
                                "ref": {"repository": REPO, "path": "master-a/task.json"},
                                "leafId": "L1",
                            },
                            "successor": {
                                "repository": REPO,
                                "path": "master-b/task.json",
                            },
                            "reason": "A leaf gates B",
                        }
                    ],
                },
            ),
        )
        self._master(
            "master-a",
            status="inProgress",
            nature="organizational",
            rows=[{"number": "L1", "name": "Title from A", "status": "inProgress"}],
        )
        self._master(
            "master-b",
            status="planning",
            nature="atomic",
            rows=[{"number": "L1", "name": "Title from B", "status": "planning"}],
        )

        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        sprint = next(node for node in nodes if node.id == "SPRINT")
        view = sprint.executionGraphView
        assert view is not None
        segment = next(node for node in view.nodes if node.kind == "segment")
        self.assertEqual(segment.leafTitles, ["Title from A"])

    def test_master_docs_by_ref_skips_invalid_master_payloads(self) -> None:
        # The invalid-master skip branch: a kind=master payload that fails validation is
        # dropped from the join table (a corrupted master must not break the projection).
        bad_path = self.coord / "tasks" / REPO / "bad" / "task.json"
        bad_payload = {
            "schema": "ar-task-document/v1",
            "kind": "master",
            "executionGraph": {"nodes": []},
        }
        self.assertEqual(_master_docs_by_ref([(bad_path, bad_payload)]), {})
        # a valid master is indexed under its repo-relative reference
        self._master(
            "master-a",
            status="planning",
            nature="organizational",
            rows=[{"number": "A-L1", "name": "Leaf one", "status": "planning"}],
        )
        valid_path = self.coord / "tasks" / REPO / "master-a" / "task.json"
        with open(valid_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        indexed = _master_docs_by_ref([(valid_path, payload)])
        self.assertEqual(
            list(indexed),
            [TaskDocumentRef(repository=REPO, path="master-a/task.json")],
        )
