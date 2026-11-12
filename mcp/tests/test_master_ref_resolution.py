"""MIK-R61 rule 2: one rule for a named master across the three writers.

The task-document master sync, the finalizer and the reopen resolve a leaf's
``master`` reference through one function; a reference that leaves the task folder,
that cannot name a task document, or that names no existing document is refused by
all three with one message (leaf, reference, fix).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.leaf_doc import require_task_document_in_place
from agents_remember.tasks.master_sync import MasterSyncError, master_json_path_from_ref
from agents_remember.worktrees.modules.finalize_task_documents import (
    FinalizeTaskDocumentError,
    _expected_parent_path,
)
from agents_remember.worktrees.reopen import ReopenTaskDocumentError, _reopen_master_path


def _doc(leaf_id: str = "3C", master: str | None = "task.md") -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": leaf_id,
            "slug": "03_leaf",
            "title": "leaf",
            "kind": "subTask",
            "status": "Completed",
            "repo": "repo-a",
            "createdAt": "2026-07-01T10:00",
            "master": master,
            "steps": [{"id": "S1", "title": "do the thing", "status": "done"}],
        }
    )


class MasterRefResolutionTests(unittest.TestCase):
    def test_task_taskmd_and_taskjson_name_the_same_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.json").write_text("{}", encoding="utf-8")
            for ref in ("task", "task.md", "task.json"):
                self.assertEqual(
                    master_json_path_from_ref(root, leaf_id="3C", master_ref=ref),
                    (root / "task.json").resolve(strict=False),
                )

    def test_three_writers_refuse_a_reference_outside_the_folder_with_one_message(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(MasterSyncError) as sync_refused:
                master_json_path_from_ref(root, leaf_id="3C", master_ref="../other-series/task.md")
            with self.assertRaises(FinalizeTaskDocumentError) as finalize_refused:
                _expected_parent_path(root, "../other-series/task.md", "3C")
            with self.assertRaises(ReopenTaskDocumentError) as reopen_refused:
                _reopen_master_path(root, _doc(master="../other-series/task.md"))
            for raised in (sync_refused, finalize_refused, reopen_refused):
                message = str(raised.exception)
                self.assertIn("3C", message)
                self.assertIn("../other-series/task.md", message)
                self.assertIn("name a document of this folder", message)

    def test_three_writers_refuse_a_missing_document_with_one_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(MasterSyncError) as sync_refused:
                master_json_path_from_ref(root, leaf_id="3C", master_ref="gone.md")
            with self.assertRaises(FinalizeTaskDocumentError) as finalize_refused:
                _expected_parent_path(root, "gone.md", "3C")
            with self.assertRaises(ReopenTaskDocumentError) as reopen_refused:
                _reopen_master_path(root, _doc(master="gone.md"))
            for raised in (sync_refused, finalize_refused, reopen_refused):
                message = str(raised.exception)
                self.assertIn("gone.md", message)
                self.assertIn("does not exist", message)

    def test_three_writers_refuse_a_reference_that_names_no_task_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(MasterSyncError) as sync_refused:
                master_json_path_from_ref(root, leaf_id="3C", master_ref="notes.txt")
            with self.assertRaises(FinalizeTaskDocumentError) as finalize_refused:
                _expected_parent_path(root, "notes.txt", "3C")
            with self.assertRaises(ReopenTaskDocumentError) as reopen_refused:
                _reopen_master_path(root, _doc(master="notes.txt"))
            for raised in (sync_refused, finalize_refused, reopen_refused):
                message = str(raised.exception)
                self.assertIn("notes.txt", message)
                self.assertIn("name a document of this folder", message)

    def test_master_sync_writes_in_place(self) -> None:
        # MIK-R61 rule 3: a master document whose read path differs from the store's
        # write target is refused before any write.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = TaskDocument.model_validate(
                {
                    "id": "series",
                    "slug": "series",
                    "title": "Series",
                    "kind": "master",
                    "repo": "agents-remember",
                    "type": "Master (Code)",
                    "createdAt": "2026-01-01T00:00",
                    "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
                }
            )
            json_path, _ = write_task_doc(root, doc)
            require_task_document_in_place(json_path, read_task_doc(json_path), ValueError)
            other = root / "other.json"
            other.write_bytes(json_path.read_bytes())
            with self.assertRaises(ValueError) as refused:
                require_task_document_in_place(other, read_task_doc(other), ValueError)
            message = str(refused.exception)
            self.assertIn("other.json", message)
            self.assertIn("task.json", message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
