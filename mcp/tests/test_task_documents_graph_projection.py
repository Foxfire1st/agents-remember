"""Task-documents projection wiring for the render-ready graph view (L12-R4)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.observer.projection import TaskDocNode
from agents_remember.serving.projections.snapshots_impl._task_documents import (
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

    def _segmented_scenario(self, *, atomic_status: str) -> None:
        """Write the sprint and its two masters; only ``atomic-f``'s status varies."""

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
            status=atomic_status,
            nature="atomic",
            rows=[{"number": "F-L1", "name": "F leaf", "status": "planning"}],
        )

    def test_segmented_master_scenario_projects_titles_and_predecessors(self) -> None:
        self._segmented_scenario(atomic_status="planning")

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

    def test_abandoned_master_reads_abandoned_and_stops_gating_its_successor(self) -> None:
        self._segmented_scenario(atomic_status="abandoned")

        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        sprint = next(node for node in nodes if node.id == "SPRINT")
        view = sprint.executionGraphView
        self.assertIsNotNone(view)
        assert view is not None
        _early, atomic, late = view.nodes

        # Named for what it is: the block was dropped, so it must not read as ready work even
        # though its own gate never landed.
        self.assertEqual(atomic.frontierState, "abandoned")
        # And it stops gating the segment that waited on it. Without that, abandoning a master
        # would leave its dependents waiting forever -- worse than not abandoning it.
        self.assertEqual(late.frontierState, "ready")

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


def _index_doc(nodes: list[TaskDocNode], master_doc_path: str, file: str) -> TaskDocNode | None:
    """The dashboard's own index rule (``sliceForRef``): the master's folder, the row's file stem.

    A row drills in only when the projected pool holds a document in the master's own directory
    whose file stem is the row's ``file``; a document withheld from the pool leaves the row as
    dead text, so this is the exact property the projection owes every authored row.
    """
    directory = Path(master_doc_path).parent
    stem = Path(file).stem
    return next(
        (
            node
            for node in nodes
            if Path(node.docPath).parent == directory and Path(node.docPath).stem == stem
        ),
        None,
    )


class SubTaskIndexReachabilityTests(unittest.TestCase):
    """A master's sub-task row stays reachable whatever build wrote the leaf document.

    The projection reads durable documents written by other, independently versioned processes,
    so one of them may carry a field this reader's schema has never heard of. Strict-only parsing
    deleted that whole document, and the master's row then rendered as "not authored as a task
    document yet" instead of drilling in -- completed leaves first, because a step is where a
    newer writer records what actually happened.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.coord = Path(self._dir.name)
        self.root = self.coord / "tasks" / REPO / "series"

    def _leaf(self, slug: str, *, status: str, step_status: str) -> TaskDocument:
        return _doc(
            id=slug.upper(),
            slug=slug,
            kind="subTask",
            title=f"Leaf {slug}",
            status=status,
            seriesContractPath="enclosures/leaf/contract.md",
            steps=[{"id": "S1", "title": "One", "status": step_status}],
        )

    def _write_master(self) -> None:
        write_task_doc(
            self.root,
            TaskDocument.model_validate(
                {
                    "id": "SERIES",
                    "slug": "series",
                    "title": "Series",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": "2026-08-15T00:00:00+00:00",
                    "executionNature": "atomic",
                    "subTasks": [
                        {
                            "number": "D-L1",
                            "name": "Done leaf",
                            "file": "01_done.md",
                            "status": "Completed",
                        },
                        {
                            "number": "D-L2",
                            "name": "Planned leaf",
                            "file": "02_planned.md",
                            "status": "planning",
                        },
                        {
                            "number": "D-L3",
                            "name": "Broken leaf",
                            "file": "03_broken.md",
                            "status": "planning",
                        },
                    ],
                }
            ),
        )

    def test_completed_leaf_written_by_another_build_stays_reachable_from_the_index(self) -> None:
        write_task_doc(self.root, self._leaf("01_done", status="Completed", step_status="done"))
        write_task_doc(
            self.root, self._leaf("02_planned", status="planning", step_status="pending")
        )
        # Republish the completed leaf as a build that knows MORE than this reader: the field sits
        # on the step, which is where the live skew landed. ``write_task_doc`` refuses it (the
        # authoring model is strict on purpose), so the durable file is written directly.
        payload = json.loads((self.root / "01_done.json").read_text(encoding="utf-8"))
        payload["steps"][0]["checkpoint"] = "recorded by a newer build"
        (self.root / "01_done.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        # A genuinely broken document (a required field never reached the file) is still withheld:
        # tolerating an unknown key must not become projecting anything.
        broken = json.loads((self.root / "02_planned.json").read_text(encoding="utf-8"))
        broken["slug"] = "03_broken"
        del broken["title"]
        (self.root / "03_broken.json").write_text(
            json.dumps(broken, indent=2) + "\n", encoding="utf-8"
        )
        self._write_master()

        nodes = read_task_documents(self.coord, enclosures=[], now=FRESH)
        master = next(node for node in nodes if node.kind == "master")
        resolved = {
            row.number: _index_doc(nodes, master.docPath, row.file) for row in master.subTasks
        }
        self.assertIsNotNone(resolved["D-L1"])  # completed, written by a newer build
        self.assertIsNotNone(resolved["D-L2"])  # unstarted, written by this build
        self.assertIsNone(resolved["D-L3"])  # broken: withheld exactly as before
        completed = resolved["D-L1"]
        assert completed is not None
        self.assertEqual(
            (completed.id, completed.status, completed.stepsDone, completed.stepsTotal),
            ("01_DONE", "Completed", 1, 1),
        )
