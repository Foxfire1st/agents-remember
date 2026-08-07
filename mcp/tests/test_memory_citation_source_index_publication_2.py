from __future__ import annotations

import os
import sqlite3
import struct
import zlib
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.memory_quality.style.citations import (
    model,
    source_index,
    source_index_database,
    source_index_state,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from test_memory_citation_source_index import IndexCase, _publication_failure


class PublicationAndBoundsTests2(IndexCase):
    def test_explicit_build_rebuilds_every_packed_payload_corruption_class(self) -> None:
        self.write("one.ts", "function alpha() { sendMessage('xy'); }\n")
        self.acquire()
        paths = source_index.cache_paths(self.trees)
        corruptions: dict[str, tuple[str, tuple[object, ...]]] = {
            "posting-header": ("UPDATE direct_postings SET postings = ?", (b"x",)),
            "posting-body": (
                "UPDATE direct_postings SET postings = ?",
                (struct.pack("<IH", 1, 1),),
            ),
            "extent-kind": (
                "UPDATE direct_postings SET postings = ?",
                (struct.pack("<IH", 1, 1) + struct.pack("<IIB", 1, 1, 255),),
            ),
            "extent-coordinates": (
                "UPDATE direct_postings SET postings = ?",
                (struct.pack("<IH", 1, 1) + struct.pack("<IIB", 0, 1, 0),),
            ),
            "posting-file-reference": (
                "UPDATE direct_postings SET postings = ?",
                (struct.pack("<IH", 999999, 0),),
            ),
            "short-posting-shape": (
                "UPDATE quote_short_postings SET stream_ids = ?",
                (b"x",),
            ),
            "short-posting-reference": (
                "UPDATE quote_short_postings SET stream_ids = ?",
                (struct.pack("<I", 999999),),
            ),
            "quote-text": ("UPDATE quote_streams SET text = ?", (b"not-zlib",)),
            "word-map-compression": ("UPDATE quote_streams SET marks = ?", (b"not-zlib",)),
            "word-map-shape": (
                "UPDATE quote_streams SET marks = ?",
                (zlib.compress(b"x"),),
            ),
            "quote-stream-reference": (
                "UPDATE quote_streams SET file_id = 999999 WHERE stream_id = 1",
                (),
            ),
            "quote-stream-count": (
                "UPDATE metadata SET value = '999999' WHERE key = 'quote_stream_count'",
                (),
            ),
            "quote-text-count": (
                "UPDATE metadata SET value = '999999' WHERE key = 'quote_text_bytes'",
                (),
            ),
            "anchor-posting-pair": ("DELETE FROM anchor_names", ()),
            "anchor-key-content": (
                "UPDATE anchor_names SET anchor_text = anchor_text || 'corrupt'",
                (),
            ),
            "short-posting-valid-delete": (
                "DELETE FROM quote_short_postings WHERE gram = "
                "(SELECT gram FROM quote_short_postings LIMIT 1)",
                (),
            ),
            "call-literal-reference": ("UPDATE call_literals SET file_id = 999999", ()),
        }
        for name, (statement, parameters) in corruptions.items():
            with self.subTest(name=name):
                with sqlite3.connect(paths.database) as connection:
                    connection.execute(statement, parameters)
                result = self.build()
                self.assertTrue(result["ok"])
                self.assertEqual(result["sourceIndex"]["state"], "built")
                warm = self.build()
                self.assertEqual(warm["sourceIndex"]["state"], "warm")

        with self.subTest(name="quote-search-valid-delete"):
            with sqlite3.connect(paths.database) as connection:
                stream_id, raw_text = connection.execute(
                    "SELECT stream_id, text FROM quote_streams ORDER BY stream_id LIMIT 1"
                ).fetchone()
                text = source_index_database._unpack_text(bytes(raw_text))
                connection.execute(
                    "INSERT INTO quote_search(quote_search, rowid, text) VALUES ('delete', ?, ?)",
                    (stream_id, text),
                )
            result = self.build()
            self.assertEqual(result["sourceIndex"]["state"], "built")

        with self.subTest(name="matching-anchor-and-posting-valid-delete"):
            with sqlite3.connect(paths.database) as connection:
                (key,) = connection.execute(
                    "SELECT anchor_key FROM direct_postings ORDER BY anchor_key LIMIT 1"
                ).fetchone()
                connection.execute("DELETE FROM direct_postings WHERE anchor_key = ?", (key,))
                connection.execute("DELETE FROM anchor_names WHERE anchor_key = ?", (key,))
            result = self.build()
            self.assertEqual(result["sourceIndex"]["state"], "built")

    def test_source_change_during_publication_retries_then_publishes_one_snapshot(self) -> None:
        target = self.write("one.py", "alpha = 1\n")
        original = source_index_database.Database.insert_file
        changed = False

        def insert(database: source_index_database.Database, path: str, lines: list[str]) -> None:
            nonlocal changed
            original(database, path, lines)
            if not changed:
                changed = True
                target.write_text("bravo_name = 2\n", encoding="utf-8")

        with mock.patch.object(source_index_database.Database, "insert_file", new=insert):
            built, snapshot = self.acquire()

        self.assertEqual(built["state"], "built")
        with source_index.open_repository_index(self.trees) as index:
            anchor = model.Anchor(model.SYMBOL, "bravo_name")
            self.assertEqual(
                symbol_index.locate((anchor,), self.trees, index=index)[anchor].files, 1
            )
            self.assertEqual(index.snapshot_id, snapshot)

    def test_source_change_at_atomic_publication_boundary_is_retried(self) -> None:
        target = self.write("one.py", "old_boundary_name = 1\n")
        original = source_index.atomic_replace
        replacements = 0

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_memory_citation_source_index_publication_2.py:144).
        def replace(source: Path, destination: Path) -> None:  # pragma: no cover
            nonlocal replacements
            original(source, destination)
            if destination.name == "index.sqlite3":
                replacements += 1
                if replacements == 1:
                    target.write_text("new_boundary_name = 2\n", encoding="utf-8")

        with mock.patch.object(source_index, "atomic_replace", new=replace):
            built, snapshot = self.acquire()

        self.assertEqual(built["state"], "built")
        self.assertEqual(replacements, 2)
        anchors = (
            model.Anchor(model.SYMBOL, "old_boundary_name"),
            model.Anchor(model.SYMBOL, "new_boundary_name"),
        )
        with source_index.open_repository_index(self.trees) as index:
            seen = symbol_index.locate(anchors, self.trees, index=index)
            self.assertEqual(seen[anchors[0]].files, 0)
            self.assertEqual(seen[anchors[1]].files, 1)
            self.assertEqual(index.snapshot_id, snapshot)

    def test_temp_generation_is_fully_validated_before_database_publication(self) -> None:
        self.write("one.py", "alpha = 1\n")
        validated = False
        original_validate = source_index_database.Database.validate_application_integrity
        original_replace = source_index.atomic_replace

        def validate(database: source_index_database.Database) -> None:
            nonlocal validated
            original_validate(database)
            validated = True

        # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_memory_citation_source_index_publication_2.py:178).
        def replace_database(source: Path, destination: Path) -> None:  # pragma: no cover
            if destination.name == "index.sqlite3":
                self.assertTrue(validated)
            original_replace(source, destination)

        with (
            mock.patch.object(
                source_index_database.Database,
                "validate_application_integrity",
                new=validate,
            ),
            mock.patch.object(source_index, "atomic_replace", new=replace_database),
        ):
            self.acquire()

        self.assertTrue(validated)

    def test_publication_crash_boundaries_never_leave_a_leasable_mixed_generation(self) -> None:
        target = self.write("one.py", "value_0 = 0\n")
        _cold, stable_snapshot = self.acquire()
        paths = source_index.cache_paths(self.trees)
        for iteration, boundary in enumerate(
            (
                "before_database_replace",
                "after_database_replace",
                "after_manifest_replace",
                "before_readiness_publish",
            ),
            start=1,
        ):
            target.write_text(f"value_{iteration} = {iteration}\n", encoding="utf-8")
            with self.subTest(boundary=boundary):
                with (
                    _publication_failure(boundary, paths),
                    self.assertRaisesRegex(RuntimeError, boundary),
                ):
                    self.acquire()
                self.assertFalse(paths.readiness.exists())
                self.frozen_refusal_without_discovery(stable_snapshot)
                _repaired, stable_snapshot = self.acquire()

        target.write_text("value_after_ready = 99\n", encoding="utf-8")
        with (
            _publication_failure("after_readiness_publish", paths),
            self.assertRaisesRegex(RuntimeError, "after_readiness_publish"),
        ):
            self.acquire()
        self.assertTrue(paths.readiness.exists())
        ready = source_index_state.ReadyGeneration.from_json(paths.readiness)
        with source_index.open_repository_index(
            self.trees, expected_snapshot=ready.snapshot_id
        ) as index:
            self.assertEqual(index.snapshot_id, ready.snapshot_id)

    def test_temp_validation_failure_preserves_the_previous_ready_generation(self) -> None:
        target = self.write("one.py", "alpha = 1\n")
        _cold, stable_snapshot = self.acquire()
        paths = source_index.cache_paths(self.trees)
        old_readiness = paths.readiness.read_bytes()
        target.write_text("beta = 2\n", encoding="utf-8")

        with (
            mock.patch.object(
                source_index,
                "_validate_temporary_database",
                side_effect=RuntimeError("validation crash"),
            ),
            self.assertRaisesRegex(RuntimeError, "validation crash"),
        ):
            self.acquire()

        self.assertEqual(paths.readiness.read_bytes(), old_readiness)
        with source_index.open_repository_index(
            self.trees, expected_snapshot=stable_snapshot
        ) as index:
            self.assertEqual(index.snapshot_id, stable_snapshot)

    def test_cold_and_warm_bounds_hold_at_two_corpus_sizes(self) -> None:
        measurements: list[tuple[int, int, int]] = []
        for file_count in (8, 32):
            with self.subTest(file_count=file_count):
                cache = self.root / f"scale-cache-{file_count}"
                code = self.root / f"scale-code-{file_count}"
                memory = self.root / f"scale-memory-{file_count}"
                code.mkdir()
                memory.mkdir()
                for index in range(file_count):
                    (code / f"file-{index}.py").write_text(
                        f"def symbol_{index}():\n    return 'quote number {index}'\n",
                        encoding="utf-8",
                    )
                with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}):
                    trees = Trees(code, memory)
                    with source_index.open_repository_index(trees) as index:
                        cold: dict[str, Any] = index.telemetry()
                    with source_index.open_repository_index(trees) as index:
                        warm: dict[str, Any] = index.telemetry()
                self.assertEqual(cold["state"], "built")
                self.assertLessEqual(cold["sourceBytesIndexed"], source_index.MAX_SOURCE_BYTES)
                self.assertLessEqual(cold["indexBytes"], source_index.MAX_INDEX_BYTES)
                self.assertEqual(warm["state"], "warm")
                self.assertEqual(warm["sourceFilesRead"], 0)
                self.assertEqual(warm["sourceFilesTokenized"], 0)
                self.assertEqual(warm["sourceFilesParsed"], 0)
                measurements.append(
                    (file_count, int(cold["sourceBytesIndexed"]), int(cold["indexBytes"]))
                )
        self.assertLess(measurements[0][1], measurements[1][1])
        self.assertLess(measurements[0][2], measurements[1][2])
