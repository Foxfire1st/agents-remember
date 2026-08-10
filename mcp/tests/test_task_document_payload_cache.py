"""Scaled task-document cache tests for runtime-churn projection ticks."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_remember.serving.projections.snapshots_impl import _common as snapshots_common
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class TaskDocumentPayloadCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        snapshots_common._task_doc_cache.clear()

    def test_two_corpus_sizes_reparse_only_changed_and_new_files(self) -> None:
        for count in (32, 320):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                coordination_root = Path(tmp)
                task_root = coordination_root / "tasks" / "repo" / "series"
                task_root.mkdir(parents=True)
                paths = [self._write_doc(task_root, index) for index in range(count)]
                calls: list[Path] = []
                original = snapshots_common._read_json

                def counting_read(  # type: ignore[no-untyped-def]
                    path: Path,
                    _calls: list[Path] = calls,
                    _original=original,
                ):
                    _calls.append(path)
                    return _original(path)

                snapshots_common._task_doc_cache.clear()
                snapshots_common._read_json = counting_read  # type: ignore[assignment]
                try:
                    first = snapshots_common._iter_task_document_payloads(
                        coordination_root / "tasks", now=NOW
                    )
                    self.assertEqual(len(first), count)
                    self.assertEqual(calls, paths)

                    calls.clear()
                    unchanged = snapshots_common._iter_task_document_payloads(
                        coordination_root / "tasks", now=NOW + timedelta(minutes=30)
                    )
                    self.assertEqual(len(unchanged), count)
                    self.assertEqual(calls, [])

                    changed = paths[count // 2]
                    time.sleep(0.05)
                    changed.write_text(
                        changed.read_text(encoding="utf-8").replace('"title": "t"', '"title": "u"'),
                        encoding="utf-8",
                    )
                    added = self._write_doc(task_root, count)
                    deleted = paths[0]
                    deleted.unlink()
                    calls.clear()
                    updated = snapshots_common._iter_task_document_payloads(
                        coordination_root / "tasks", now=NOW + timedelta(hours=1)
                    )
                    self.assertEqual(len(updated), count)
                    self.assertEqual(calls, [changed, added])
                    self.assertEqual(
                        snapshots_common._task_doc_cache.entry_count(coordination_root / "tasks"),
                        count,
                    )
                finally:
                    snapshots_common._read_json = original  # type: ignore[assignment]

    @staticmethod
    def _write_doc(root: Path, index: int) -> Path:
        path = root / f"{index:04d}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": TASK_DOCUMENT_SCHEMA,
                    "kind": "light",
                    "id": f"L{index}",
                    "title": "t",
                    "repo": "repo",
                    "status": "planning",
                    "createdAt": NOW.isoformat(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return path


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
