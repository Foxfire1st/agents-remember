"""MIK-R111 copies, refresh and resources (F01.6, F01.7, F01.20).

Typed persisted requirement refs copy like links and warn when absent;
refresh reconciles the exact file set with rollback; admitted work and
storage stay inside explicit budgets, loudly, never silently.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.task_docs.task_sprint_linkage import (
    _reclaim_old_linkage_reports,
)
from agents_remember.application.tool_answer_log import detail_file_for
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks import write_task_doc as _write
from agents_remember.worktrees import task_document_copies as copies
from agents_remember.worktrees.worktree_contract import load_contract
from tool_entry_point_world import EntryPointWorld


def _leaf_fixture(root: Path, requirements: Any) -> tuple[Path, Path, TaskDocumentRef]:
    tasks = root / "coord" / "tasks" / "demo"
    doc = TaskDocument.model_validate(
        {
            "id": "LEAF",
            "slug": "leaf",
            "title": "leaf",
            "repo": "demo",
            "createdAt": "2026-10-08T00:00:00Z",
            "kind": "subTask",
            "requirements": requirements,
        }
    )
    path, _ = write_task_doc(tasks / "m", doc)
    ref = TaskDocumentRef(repository="demo", path=path.relative_to(tasks).as_posix())
    group = root / "group"
    group.mkdir(exist_ok=True)
    return tasks, group, ref


class TypedRequirementRefTests(unittest.TestCase):
    """F01.6: persisted approved-packet dictionaries copy and warn like links."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _spec(self, tasks: Path, text: str = "spec\n") -> None:
        spec = tasks / "m" / "requirements" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(text, encoding="utf-8")

    def test_typed_persisted_refs_are_copied(self) -> None:
        tasks, group, ref = _leaf_fixture(
            self.root,
            [
                {
                    "kind": "approved-requirement-packet",
                    "path": "requirements/spec.md",
                    "stableId": "spec",
                    "version": "v1",
                }
            ],
        )
        self._spec(tasks)
        rows = copies.prepare_task_document_copies(self.root / "coord", group, ref)
        copied = group / "task-documents" / "m" / "requirements" / "spec.md"
        self.assertTrue(copied.is_file())
        self.assertEqual(copied.read_text(encoding="utf-8"), "spec\n")
        self.assertTrue(
            any(row.get("path", "").endswith("spec.md") and not row.get("missing") for row in rows)
        )

    def test_absent_typed_ref_is_a_missing_row(self) -> None:
        _tasks, group, ref = _leaf_fixture(
            self.root,
            [
                {
                    "kind": "approved-requirement-packet",
                    "path": "requirements/gone.md",
                    "stableId": "gone",
                    "version": "v1",
                }
            ],
        )
        rows = copies.prepare_task_document_copies(self.root / "coord", group, ref)
        missing = [row for row in rows if row.get("missing")]
        self.assertEqual(len(missing), 1)
        self.assertFalse((group / "task-documents" / "m" / "requirements" / "gone.md").exists())


class RefreshReconciliationTests(unittest.TestCase):
    """F01.7: refresh reconciles the exact file set, with rollback."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tasks = self.root / "coord" / "tasks" / "demo"

    def _requirements(self, *names: str) -> None:
        folder = self.tasks / "m" / "requirements"
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            (folder / name).write_text(f"{name}\n", encoding="utf-8")

    def _publish(self, group: Path, ref: TaskDocumentRef, requirements: Any) -> None:
        doc = TaskDocument.model_validate(
            {
                "id": "LEAF",
                "slug": "leaf",
                "title": "leaf",
                "repo": "demo",
                "createdAt": "2026-10-08T00:00:00Z",
                "kind": "subTask",
                "requirements": requirements,
            }
        )
        write_task_doc(self.tasks / "m", doc)
        writes, _rows = copies._copy_writes(self.root / "coord", group, ref)
        stale = copies._stale_copy_files(group / "task-documents", set(writes))
        copies._publish_copy_writes(writes, deletions=stale)

    def test_removed_requirement_copy_is_reclaimed(self) -> None:
        _tasks, group, ref = _leaf_fixture(self.root, [])
        self._requirements("a.md", "b.md")
        self._publish(group, ref, ["[a](requirements/a.md)", "[b](requirements/b.md)"])
        self.assertTrue((group / "task-documents" / "m" / "requirements" / "b.md").is_file())
        self._publish(group, ref, ["[a](requirements/a.md)"])
        self.assertFalse((group / "task-documents" / "m" / "requirements" / "b.md").exists())
        self.assertTrue((group / "task-documents" / "m" / "requirements" / "a.md").is_file())

    def test_deleted_source_copy_is_reclaimed(self) -> None:
        _tasks, group, ref = _leaf_fixture(self.root, [])
        self._requirements("a.md")
        self._publish(group, ref, ["[a](requirements/a.md)"])
        (self.tasks / "m" / "requirements" / "a.md").unlink()
        self._publish(group, ref, ["[a](requirements/a.md)"])
        self.assertFalse((group / "task-documents" / "m" / "requirements" / "a.md").exists())

    def test_amended_source_is_updated(self) -> None:
        _tasks, group, ref = _leaf_fixture(self.root, [])
        self._requirements("a.md")
        self._publish(group, ref, ["[a](requirements/a.md)"])
        (self.tasks / "m" / "requirements" / "a.md").write_text("amended\n", encoding="utf-8")
        self._publish(group, ref, ["[a](requirements/a.md)"])
        self.assertEqual(
            (group / "task-documents" / "m" / "requirements" / "a.md").read_text(encoding="utf-8"),
            "amended\n",
        )

    def test_failed_publication_rolls_back(self) -> None:
        _tasks, group, ref = _leaf_fixture(self.root, [])
        self._requirements("a.md")
        self._publish(group, ref, ["[a](requirements/a.md)"])
        victim = group / "task-documents" / "m" / "requirements" / "a.md"
        victim.unlink()
        victim.mkdir()
        try:
            with self.assertRaises(OSError):
                self._publish(group, ref, ["[a](requirements/a.md)"])
        finally:
            victim.rmdir()
        # Nothing was published over the failure: the index still names the copy.
        index = json.loads((group / "task-documents" / "INDEX.json").read_text(encoding="utf-8"))
        self.assertTrue(any("a.md" in row.get("path", "") for row in index["documents"]))


class CopyBudgetTests(unittest.TestCase):
    """F01.20: admitted copy work and storage stay inside explicit budgets."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_oversize_file_refuses_loudly(self) -> None:
        tasks, group, ref = _leaf_fixture(self.root, ["[big](requirements/big.md)"])
        spec = tasks / "m" / "requirements" / "big.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        with open(spec, "wb") as fh:
            fh.seek(copies.COPY_FILE_MAX_BYTES)
            fh.write(b"x")
        with self.assertRaises(ValueError) as raised:
            copies.prepare_task_document_copies(self.root / "coord", group, ref)
        self.assertIn(str(copies.COPY_FILE_MAX_BYTES), str(raised.exception))

    def test_many_targets_all_copy(self) -> None:
        folder = self.root / "coord" / "tasks" / "demo" / "m" / "requirements"
        folder.mkdir(parents=True, exist_ok=True)
        names = [f"req-{index:03d}.md" for index in range(200)]
        for name in names:
            (folder / name).write_text(f"{name}\n", encoding="utf-8")
        _tasks, group, ref = _leaf_fixture(self.root, [])
        requirements = [f"[r{i}](requirements/{name})" for i, name in enumerate(names)]
        requirements += requirements[:50]
        doc = TaskDocument.model_validate(
            {
                "id": "LEAF",
                "slug": "leaf",
                "title": "leaf",
                "repo": "demo",
                "createdAt": "2026-10-08T00:00:00Z",
                "kind": "subTask",
                "requirements": requirements,
            }
        )
        path, _ = write_task_doc(self.root / "coord" / "tasks" / "demo" / "m", doc)
        ref = TaskDocumentRef(
            repository="demo",
            path=path.relative_to(self.root / "coord" / "tasks" / "demo").as_posix(),
        )
        targets = copies.requirement_copy_targets(
            self.root / "coord" / "tasks" / "demo", path, requirements
        )
        self.assertEqual(len(targets), 200)
        rows = copies.prepare_task_document_copies(self.root / "coord", group, ref)
        self.assertEqual(
            len(
                [row for row in rows if row.get("kind") == "requirement" and not row.get("missing")]
            ),
            200,
        )

    def test_zero_requirements_are_quiescent(self) -> None:
        _tasks, group, ref = _leaf_fixture(self.root, [])
        rows = copies.prepare_task_document_copies(self.root / "coord", group, ref)
        published = [path for path in (group / "task-documents").rglob("*") if path.is_file()]
        # Only the leaf and the index: no requirement was read or written.
        self.assertEqual(len(published), 2)
        self.assertTrue(any("reference" in row for row in rows))

    def test_large_identical_files_show_no_drift(self) -> None:
        tasks, group, ref = _leaf_fixture(self.root, ["[big](requirements/big.md)"])
        spec = tasks / "m" / "requirements" / "big.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_bytes(b"y" * (copies.STATUS_HASH_MAX_BYTES + 1024))
        copies.prepare_task_document_copies(self.root / "coord", group, ref)
        self.assertIsNone(copies.stale_copy_line(self.root / "coord", group))

    def test_large_changed_file_is_noted(self) -> None:
        tasks, group, ref = _leaf_fixture(self.root, ["[big](requirements/big.md)"])
        spec = tasks / "m" / "requirements" / "big.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_bytes(b"y" * (copies.STATUS_HASH_MAX_BYTES + 1024))
        copies.prepare_task_document_copies(self.root / "coord", group, ref)
        spec.write_bytes(b"y" * (copies.STATUS_HASH_MAX_BYTES + 2048))
        line = copies.stale_copy_line(self.root / "coord", group)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("large file differs by size or mtime", line)


class LinkageReportReclamationTests(unittest.TestCase):
    """F01.20: two-size reclamation keeps 50 newest reports, never the file just written."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "reports"
        self.folder.mkdir()

    def _fill(self, size: int) -> str:
        last = ""
        for index in range(size):
            path = detail_file_for(self.folder, "linkage-report", detail_id=f"{index:08x}")
            path.write_text("{}", encoding="utf-8")
            last = path.name
        return last

    def test_reclamation_at_55_reports(self) -> None:
        last = self._fill(55)
        _reclaim_old_linkage_reports(self.folder, last)
        remaining = list(self.folder.iterdir())
        self.assertLessEqual(len(remaining), 50)
        self.assertIn(last, [entry.name for entry in remaining])

    def test_reclamation_at_80_reports(self) -> None:
        last = self._fill(80)
        _reclaim_old_linkage_reports(self.folder, last)
        remaining = list(self.folder.iterdir())
        self.assertLessEqual(len(remaining), 50)
        self.assertIn(last, [entry.name for entry in remaining])


class ScanBudgetTests(unittest.TestCase):
    """F01.20: reconciliation scans count every visited entry, not just files.

    A chain of empty directories costs visits too: with a lowered entry
    budget the walk sheds before reaching a file no file-counting bound
    would miss, and the call still completes.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._old_budget = copies.COPY_SCAN_MAX_ENTRIES
        self.addCleanup(setattr, copies, "COPY_SCAN_MAX_ENTRIES", self._old_budget)

    def _chain(self, depth: int) -> Path:
        current = self.root / "tree"
        current.mkdir()
        for level in range(depth):
            current = current / f"dir-{level:03d}"
            current.mkdir()
        leaf = current / "leaf.md"
        leaf.write_text("leaf\n", encoding="utf-8")
        return leaf

    def test_empty_directories_count_toward_the_entry_budget(self) -> None:
        self._chain(200)
        copies.COPY_SCAN_MAX_ENTRIES = 100
        # 201 visits to reach the file against a 100-visit budget: the walk
        # sheds and the file is never visited. A bound counting files only
        # would walk all 201 entries and return the one file.
        self.assertEqual(copies._copy_tree_files(self.root / "tree"), [])

    def test_generous_budget_still_finds_the_file(self) -> None:
        leaf = self._chain(200)
        copies.COPY_SCAN_MAX_ENTRIES = 300
        self.assertEqual(copies._copy_tree_files(self.root / "tree"), [leaf])


class StatusRowBudgetTests(unittest.TestCase):
    """F01.20: status index work stays inside explicit byte and row budgets."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.coord = self.root / "coord"
        self.group = self.root / "group"
        (self.group / "task-documents").mkdir(parents=True)

    def _write_index(self, count: int) -> None:
        rows = [
            {
                "kind": "requirement",
                "reference": f"requirements/req-{index:04d}.md",
                "repository": "demo",
                "path": f"requirements/req-{index:04d}.md",
                "missing": True,
                "copiedAt": "2026-10-08T00:00:00+00:00",
            }
            for index in range(count)
        ]
        (self.group / "task-documents" / "INDEX.json").write_text(
            json.dumps({"schema": "ar-task-documents/v1", "documents": rows}),
            encoding="utf-8",
        )

    def test_row_budget_marks_unchecked_rows_loudly(self) -> None:
        self._write_index(copies.COPY_STATUS_MAX_ROWS + 500)
        line = copies.stale_copy_line(self.coord, self.group)
        # No drift in the examined rows, yet the line is present: 500 rows
        # went unchecked and the status call says so instead of passing.
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("+500 further row(s) unchecked", line)

    def test_small_index_with_no_drift_is_quiet(self) -> None:
        self._write_index(100)
        self.assertIsNone(copies.stale_copy_line(self.coord, self.group))

    def test_oversize_index_reports_unknown_not_absent(self) -> None:
        index = self.group / "task-documents" / "INDEX.json"
        with open(index, "wb") as fh:
            fh.seek(copies.COPY_INDEX_MAX_BYTES)
            fh.write(b"x")
        line = copies.stale_copy_line(self.coord, self.group)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("too large to check", line)
        self.assertIn("unknown, not absent", line)


class RollbackSnapshotTests(unittest.TestCase):
    """F01.7/F01.20: rollback covers every admitted copy; the rest is refused.

    The snapshot of a file is as large as a copy may be, so an admitted
    change always restores. A change whose snapshots do not fit the budgets
    is refused before the first write, by name with the sizes: nothing is
    written, and nothing is skipped silently.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._old_file_budget = copies.COPY_ROLLBACK_FILE_MAX_BYTES
        self._old_total_budget = copies.COPY_ROLLBACK_TOTAL_MAX_BYTES
        self.addCleanup(setattr, copies, "COPY_ROLLBACK_FILE_MAX_BYTES", self._old_file_budget)
        self.addCleanup(setattr, copies, "COPY_ROLLBACK_TOTAL_MAX_BYTES", self._old_total_budget)
        copies.COPY_ROLLBACK_FILE_MAX_BYTES = 10

    def test_over_budget_snapshot_refuses_before_any_write(self) -> None:
        small = self.root / "small.md"
        small.write_bytes(b"orig")
        big = self.root / "big.md"
        big.write_bytes(b"x" * 1024)
        gone = self.root / "gone.md"
        gone.write_bytes(b"gone")
        third = self.root / "third.md"
        with self.assertRaises(ValueError) as refused:
            copies._publish_copy_writes(
                {small: b"new-small", big: b"new-big", third: b"new-third"},
                deletions=[gone],
            )
        # The refusal names the over-budget file with its size and both
        # budgets; nothing was written and nothing was deleted.
        self.assertIn("big.md", str(refused.exception))
        self.assertIn("1024 bytes", str(refused.exception))
        self.assertIn("10 bytes per file", str(refused.exception))
        self.assertEqual(small.read_bytes(), b"orig")
        self.assertEqual(big.read_bytes(), b"x" * 1024)
        self.assertEqual(gone.read_bytes(), b"gone")
        self.assertFalse(third.exists())

    def test_over_total_snapshot_refuses_before_any_write(self) -> None:
        copies.COPY_ROLLBACK_FILE_MAX_BYTES = 1024
        copies.COPY_ROLLBACK_TOTAL_MAX_BYTES = 10
        first = self.root / "first.md"
        first.write_bytes(b"1" * 8)
        second = self.root / "second.md"
        second.write_bytes(b"2" * 8)
        with self.assertRaises(ValueError) as refused:
            copies._publish_copy_writes({first: b"a" * 8, second: b"b" * 8})
        # Each file fits alone, but the two snapshots do not fit the change
        # budget together: the refusal names the file that broke the total,
        # with its size, and nothing was written.
        self.assertNotIn("first.md", str(refused.exception))
        self.assertIn("second.md", str(refused.exception))
        self.assertIn("8 bytes", str(refused.exception))
        self.assertIn("10 bytes per change", str(refused.exception))
        self.assertEqual(first.read_bytes(), b"1" * 8)
        self.assertEqual(second.read_bytes(), b"2" * 8)

    def test_file_at_snapshot_cap_restores_after_a_late_failure(self) -> None:
        # Real budgets for this case: a file exactly at the snapshot cap.
        copies.COPY_ROLLBACK_FILE_MAX_BYTES = self._old_file_budget
        copies.COPY_ROLLBACK_TOTAL_MAX_BYTES = self._old_total_budget
        cap = copies.COPY_ROLLBACK_FILE_MAX_BYTES
        self.assertEqual(cap, copies.COPY_FILE_MAX_BYTES)
        full = self.root / "full.md"
        full.write_bytes(b"o" * cap)
        third = self.root / "third.md"
        real_write = copies.atomic_write_bytes

        def fail_on_third(path: Path, raw: bytes) -> None:
            if path == third:
                raise OSError("boom")
            real_write(path, raw)

        copies.atomic_write_bytes = fail_on_third  # type: ignore[method-assign]
        self.addCleanup(setattr, copies, "atomic_write_bytes", real_write)
        with self.assertRaises(OSError):
            copies._publish_copy_writes({full: b"n" * cap, third: b"new-third"})
        # A file exactly at the snapshot cap is covered: the failure after
        # the first write restores it, byte for byte.
        self.assertEqual(full.read_bytes(), b"o" * cap)
        self.assertFalse(third.exists())


class AdmittedLargeCopyRollbackTests(unittest.TestCase):
    """F01.7: an admitted large copy restores after a failure past the first write.

    A 5MiB requirement is admitted (under the 16MiB copy cap) and therefore
    snapshotted. When the INDEX write then fails, the whole copy tree comes
    back: the new copy bytes too, not just the index.
    """

    def test_admitted_large_requirement_restores_after_index_failure(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        leaf = world.coord / "tasks/repo-a/m/1_leaf.json"
        req = leaf.parent / "requirements/spec.md"
        req.parent.mkdir(parents=True, exist_ok=True)
        old = b"a" * (5 * 1024 * 1024)
        new = b"b" * len(old)
        self.assertLess(len(old), copies.COPY_FILE_MAX_BYTES)
        req.write_bytes(old)
        fields = json.loads(leaf.read_text(encoding="utf-8"))
        fields["requirements"] = [
            {
                "kind": "approved-requirement-packet",
                "path": "requirements/spec.md",
                "stableId": "spec",
                "version": "v1",
            }
        ]
        address = {"repo_id": "repo-a", "task_name": "m", "slug": "1_leaf"}
        kind, added = world.call("task_doc", {**address, "operation": "replace", "fields": fields})
        self.assertEqual(kind, "RETURNED")
        assert added is not None
        self.assertTrue(added["ok"], added.get("error"))
        root = load_contract(world.contract).worktree_group / "task-documents"
        self.assertEqual((root / "m/requirements/spec.md").read_bytes(), old)

        def digest_tree() -> dict[str, str]:
            return {
                str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*")
                if path.is_file()
            }

        before = digest_tree()
        req.write_bytes(new)
        real_write = copies.atomic_write_bytes

        def fail_once_index(path: Path, raw: bytes) -> None:
            if path == root / "INDEX.json" and not fail_once_index.hits:  # type: ignore[attr-defined]
                fail_once_index.hits.append(str(path))  # type: ignore[attr-defined]
                raise OSError("controlled copy-index write failure")
            real_write(path, raw)

        fail_once_index.hits = []  # type: ignore[attr-defined]
        with patch.object(copies, "atomic_write_bytes", fail_once_index):
            kind, outcome = world.call(
                "task_doc",
                {**address, "operation": "set_field", "fields": {"title": "trigger refresh"}},
            )
        self.assertTrue(fail_once_index.hits)  # type: ignore[attr-defined]
        # The injected fault is an I/O failure past the first write, not a
        # task state, so the call raises; what this case proves is that the
        # copy tree the failed call leaves behind is whole.
        self.assertEqual(kind, "RAISED ToolError")
        assert outcome is not None
        self.assertIn("controlled copy-index write failure", outcome.get("detail", ""))
        self.assertEqual(digest_tree(), before)


class ParentWriteKeepsLeafCopiesTests(unittest.TestCase):
    """F01.7: a parent write refreshes without evicting the bound leaf's copies."""

    def test_parent_row_rename_keeps_leaf_and_requirement_copies(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        source = world.coord / "tasks/repo-a/m/requirements/spec.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Spec\n", encoding="utf-8")
        leaf = world.coord / "tasks/repo-a/m/1_leaf.json"
        fields = json.loads(leaf.read_text(encoding="utf-8"))
        fields["requirements"] = [
            {
                "kind": "approved-requirement-packet",
                "path": "requirements/spec.md",
                "stableId": "spec",
                "version": "v1",
            }
        ]
        kind, replaced = world.call(
            "task_doc",
            {
                "repo_id": "repo-a",
                "task_name": "m",
                "slug": "1_leaf",
                "operation": "replace",
                "fields": fields,
            },
        )
        self.assertEqual(kind, "RETURNED")
        assert replaced is not None
        self.assertTrue(replaced["ok"], replaced.get("error"))
        group = load_contract(world.contract).worktree_group
        leaf_copy = group / "task-documents/m/1_leaf.md"
        spec_copy = group / "task-documents/m/requirements/spec.md"
        self.assertTrue(leaf_copy.is_file())
        self.assertEqual(spec_copy.read_bytes(), b"# Spec\n")
        # The verdict's scenario: a valid parent row rename returns ok...
        kind, renamed = world.call(
            "task_doc",
            {
                "repo_id": "repo-a",
                "task_name": "m",
                "operation": "set_subtask",
                "subtask": {
                    "number": "L1",
                    "name": "renamed fixture leaf",
                    "file": "1_leaf.md",
                    "status": "planning",
                    "scope": "sweep",
                },
            },
        )
        self.assertEqual(kind, "RETURNED")
        assert renamed is not None
        self.assertTrue(renamed["ok"], renamed.get("error"))
        # ...and the enclosure still holds the leaf and its requirement copy.
        self.assertTrue(leaf_copy.is_file(), "parent write evicted the leaf copy")
        self.assertEqual(
            spec_copy.read_bytes(), b"# Spec\n", "parent write evicted the requirement copy"
        )
        index = json.loads((group / "task-documents/INDEX.json").read_text(encoding="utf-8"))
        paths = [row.get("path", "") for row in index.get("documents", [])]
        self.assertIn("m/1_leaf.md", paths)
        self.assertTrue(any("spec.md" in path for path in paths))

    def test_leaf_clear_still_reclaims_through_union_refresh(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()
        leaf = world.coord / "tasks/repo-a/m/1_leaf.json"
        req = leaf.parent / "requirements/spec.md"
        req.parent.mkdir(exist_ok=True)
        req.write_text("# Requirement\n", encoding="utf-8")
        fields = json.loads(leaf.read_text(encoding="utf-8"))
        fields["requirements"] = ["[spec](requirements/spec.md)"]
        kind, _replaced = world.call(
            "task_doc",
            {
                "repo_id": "repo-a",
                "task_name": "m",
                "slug": "1_leaf",
                "operation": "replace",
                "fields": fields,
            },
        )
        self.assertEqual(kind, "RETURNED")
        group = load_contract(world.contract).worktree_group
        spec_copy = group / "task-documents/m/requirements/spec.md"
        self.assertTrue(spec_copy.is_file())
        kind, cleared = world.call(
            "task_doc",
            {
                "repo_id": "repo-a",
                "task_name": "m",
                "slug": "1_leaf",
                "operation": "set_field",
                "fields": {"requirements": []},
            },
        )
        self.assertEqual(kind, "RETURNED")
        assert cleared is not None
        self.assertTrue(cleared["ok"], cleared.get("error"))
        self.assertFalse(spec_copy.exists(), "cleared requirement copy was not reclaimed")
        self.assertTrue((group / "task-documents/m/1_leaf.md").is_file())
        # A leaf root holds the leaf's copies only: no master copy beside it.
        self.assertFalse((group / "task-documents/m/task.md").exists())


class StatusDriftLiveTests(unittest.TestCase):
    """F01.7: a drifted enclosure reports its stale line under the snake key."""

    def test_status_with_drift_carries_task_document_copies(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        world.build()

        contract = load_contract(world.contract)
        leaf_path = world.coord / "tasks" / "repo-a" / "m" / "1_leaf.json"
        document = read_task_doc(leaf_path)
        spec = world.coord / "tasks" / "repo-a" / "m" / "requirements" / "spec.md"
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("spec\n", encoding="utf-8")
        document = document.model_copy(update={"requirements": ["[spec](requirements/spec.md)"]})
        _write(leaf_path.parent, document)
        ref = TaskDocumentRef(repository="repo-a", path="m/1_leaf.json")
        copies.prepare_task_document_copies(world.coord, contract.worktree_group, ref)
        spec.write_text("drifted\n", encoding="utf-8")
        kind, payload = world.call(
            "worktree_status",
            {"contract_path": world.contract.as_posix(), "repo_id": "repo-a"},
        )
        self.assertEqual(kind, "RETURNED")
        assert payload is not None
        self.assertIn("task_document_copies", payload)
        self.assertIn("spec.md", payload["task_document_copies"])
