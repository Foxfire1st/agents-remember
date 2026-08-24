"""Direct unit tests for the publication-batch graph-title join (L12-R1/R4).

The shared application seam labels a sprint's Mermaid render from the in-memory
master documents of one topology-authoring or sprint-linkage batch. The full
flows are heavy (judgment registers, integration locks), so the join is pinned
directly at its single owner here.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agents_remember.application.task_docs.task_doc_graph_titles import (
    build_publication_batch_graph_titles,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument

REPO = "repo-a"
SPRINT_REF = TaskDocumentRef(repository=REPO, path="sprint/task.json")
MASTER_A = TaskDocumentRef(repository=REPO, path="master-a/task.json")


def _sprint() -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "SPRINT",
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


def _master() -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "MASTER-A",
            "slug": "master-a",
            "title": "Title master-a",
            "kind": "master",
            "status": "inProgress",
            "repo": REPO,
            "createdAt": "2026-08-15T00:00:00+00:00",
            "executionNature": "organizational",
            "subTasks": [{"number": "A-L1", "name": "Leaf one", "status": "inProgress"}],
        }
    )


class AuthoringBatchTitlesTests(unittest.TestCase):
    def test_joins_titles_from_in_memory_masters(self) -> None:
        documents = [
            (SPRINT_REF, Path("sprint"), _sprint()),
            (MASTER_A, Path("master-a"), _master()),
        ]
        titles = build_publication_batch_graph_titles(documents)
        self.assertIsNotNone(titles)
        assert titles is not None
        self.assertEqual(titles.master_titles, {"repo-a/master-a/task.json": "Title master-a"})
        self.assertEqual(titles.leaf_titles, {(MASTER_A, "A-L1"): "Leaf one"})

    def test_returns_none_when_no_document_carries_a_graph(self) -> None:
        plain = TaskDocument.model_validate(
            {
                "id": "PLAIN",
                "slug": "task",
                "title": "Plain",
                "kind": "light",
                "repo": REPO,
                "createdAt": "2026-08-15T00:00:00+00:00",
            }
        )
        documents = [
            (SPRINT_REF, Path("sprint"), plain),
            (MASTER_A, Path("master-a"), _master()),
        ]
        self.assertIsNone(build_publication_batch_graph_titles(documents))
