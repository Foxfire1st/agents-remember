from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest import mock

from agents_remember.memory_quality.style.citations import (
    fixer,
    model,
    source_index,
    source_index_database,
    source_index_state,
    symbol_index,
)
from test_memory_citation_source_index import IndexCase


class SnapshotReuseTests(IndexCase):
    def test_expected_snapshot_opens_without_tree_or_integrity_traversal(self) -> None:
        self.write("one.py", "def alpha():\n    return 1\n")
        _cold, snapshot = self.acquire()

        with (
            mock.patch.object(
                source_index,
                "_tree_state",
                side_effect=AssertionError("frozen acquisition inspected the source tree"),
            ),
            mock.patch.object(
                source_index,
                "_reclaim_legacy_cache_roots",
                side_effect=AssertionError("frozen acquisition ran cache reclamation"),
            ),
            mock.patch.object(
                source_index_database.Database,
                "validate_application_integrity",
                side_effect=AssertionError("frozen acquisition traversed database integrity"),
            ),
            mock.patch.object(
                source_index.Manifest,
                "from_json",
                side_effect=AssertionError("frozen acquisition parsed the full manifest"),
            ),
            source_index.open_repository_index(self.trees, expected_snapshot=snapshot) as index,
        ):
            telemetry = index.telemetry()

        self.assertEqual(telemetry["state"], "frozen")
        self.assertEqual(telemetry["snapshotId"], snapshot)
        for key in (
            "metadataFilesStat",
            "metadataDirectoriesStat",
            "metadataEntriesEnumerated",
            "metadataTreeEnumerations",
            "sourceFilesRead",
            "sourceBytesRead",
            "sourceFilesTokenized",
            "sourceFilesParsed",
            "buildSeconds",
        ):
            self.assertEqual(telemetry[key], 0)

    def test_expected_snapshot_refuses_missing_wrong_and_replaced_generations(self) -> None:
        self.write("one.py", "alpha = 1\n")
        missing = "0" * 64
        with self.assertRaisesRegex(source_index.SourceIndexError, "not published"):
            source_index.open_repository_index(self.trees, expected_snapshot=missing)

        _cold, first = self.acquire()
        with self.assertRaisesRegex(source_index.SourceIndexError, "unavailable"):
            source_index.open_repository_index(self.trees, expected_snapshot="f" * 64)

        self.write("one.py", "beta = 2\n")
        _rebuilt, second = self.acquire()
        self.assertNotEqual(second, first)
        with (
            mock.patch.object(
                source_index,
                "_tree_state",
                side_effect=AssertionError("mismatch must not fall back to source validation"),
            ),
            self.assertRaisesRegex(source_index.SourceIndexError, "unavailable"),
        ):
            source_index.open_repository_index(self.trees, expected_snapshot=first)

    def test_expected_snapshot_requires_canonical_id_before_any_cache_or_source_work(self) -> None:
        for malformed in ("", "a" * 63, "A" * 64, "g" * 64, "a" * 65):
            with (
                self.subTest(malformed=malformed),
                mock.patch.object(
                    source_index,
                    "cache_paths",
                    side_effect=AssertionError("malformed id reached cache acquisition"),
                ),
                mock.patch.object(
                    source_index,
                    "_tree_state",
                    side_effect=AssertionError("malformed id inspected source"),
                ),
                self.assertRaisesRegex(source_index.SourceIndexError, "64 lowercase hex"),
            ):
                source_index.open_repository_index(self.trees, expected_snapshot=malformed)

    def test_readiness_marker_and_database_metadata_fail_closed_without_fallback(self) -> None:
        self.write("one.py", "alpha = 1\n")
        _cold, snapshot = self.acquire()
        paths = source_index.cache_paths(self.trees)
        original = paths.readiness.read_text(encoding="utf-8")
        payload = json.loads(original)
        corruptions = (
            {**payload, "schemaVersion": -1},
            {**payload, "state": "building"},
            {**payload, "snapshotId": snapshot.upper()},
            {**payload, "filesIndexed": -1},
            {**payload, "sourceBytes": source_index.MAX_SOURCE_BYTES + 1},
            {**payload, "databaseBytes": 0},
            {**payload, "codeRoot": "relative/code"},
            {**payload, "extra": True},
        )
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                paths.readiness.write_text(json.dumps(corruption), encoding="utf-8")
                self.frozen_refusal_without_discovery(snapshot)
        paths.readiness.write_bytes(b"x" * (source_index_state.MAX_READINESS_BYTES + 1))
        self.frozen_refusal_without_discovery(snapshot)
        paths.readiness.write_text(original, encoding="utf-8")
        with sqlite3.connect(paths.database) as connection:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'application_sha256'", ("bad",)
            )
        self.frozen_refusal_without_discovery(snapshot)

    def test_old_readiness_cannot_bless_a_replaced_database(self) -> None:
        target = self.write("one.py", "alpha = 1\n")
        _cold, first = self.acquire()
        paths = source_index.cache_paths(self.trees)
        old_readiness = paths.readiness.read_bytes()
        target.write_text("beta = 2\n", encoding="utf-8")
        _rebuilt, second = self.acquire()
        self.assertNotEqual(first, second)
        paths.readiness.write_bytes(old_readiness)

        self.assertIn("identity do not match", self.frozen_refusal_without_discovery(first))

    def test_warm_process_reads_and_derives_no_source(self) -> None:
        self.write("one.py", "def alpha():\n    return 1\n")
        cold, snapshot = self.acquire()
        warm, same_snapshot = self.acquire()

        self.assertEqual(cold["state"], "built")
        self.assertEqual(warm["state"], "warm")
        self.assertEqual(same_snapshot, snapshot)
        for key in (
            "sourceFilesRead",
            "sourceBytesRead",
            "sourceFilesTokenized",
            "sourceFilesParsed",
        ):
            self.assertEqual(warm[key], 0)
        self.assertEqual(warm["metadataTreeEnumerations"], 1)

    def test_memory_edit_does_not_invalidate_code_snapshot(self) -> None:
        self.write("one.py", "def alpha():\n    return 1\n")
        _cold, snapshot = self.acquire()
        (self.memory / "card.md").write_text("changed memory\n", encoding="utf-8")

        warm, same_snapshot = self.acquire()

        self.assertEqual(warm["state"], "warm")
        self.assertEqual(same_snapshot, snapshot)

    def test_metadata_only_touch_rehashes_one_file_without_retokenizing(self) -> None:
        target = self.write("one.py", "def alpha():\n    return 1\n")
        _cold, snapshot = self.acquire()
        os.utime(target, None)

        refreshed, same_snapshot = self.acquire()
        warm, final_snapshot = self.acquire()

        self.assertEqual(refreshed["state"], "metadata-refreshed")
        self.assertEqual(refreshed["sourceFilesRead"], 1)
        self.assertEqual(refreshed["sourceFilesTokenized"], 0)
        self.assertEqual(refreshed["sourceFilesParsed"], 0)
        self.assertEqual((same_snapshot, final_snapshot), (snapshot, snapshot))
        self.assertEqual(warm["sourceFilesRead"], 0)

    def test_same_size_restored_mtime_content_change_rebuilds(self) -> None:
        target = self.write("one.py", "old_name = 1\n")
        original = target.stat()
        _cold, snapshot = self.acquire()
        target.write_text("new_name = 1\n", encoding="utf-8")
        os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))

        rebuilt, changed_snapshot = self.acquire()

        self.assertEqual(rebuilt["state"], "built")
        self.assertNotEqual(changed_snapshot, snapshot)
        anchor = model.Anchor(model.SYMBOL, "new_name")
        with source_index.open_repository_index(self.trees) as index:
            self.assertEqual(
                symbol_index.locate((anchor,), self.trees, index=index)[anchor].files, 1
            )

    def test_add_delete_and_rename_each_rebuild_once(self) -> None:
        target = self.write("one.py", "one = 1\n")
        _cold, first = self.acquire()
        added = self.write("two.py", "two = 2\n")
        add, second = self.acquire()
        warm_after_add, _ = self.acquire()
        target.unlink()
        delete, third = self.acquire()
        added.rename(self.code / "renamed.py")
        rename, fourth = self.acquire()

        self.assertEqual([add["state"], delete["state"], rename["state"]], ["built"] * 3)
        self.assertEqual(warm_after_add["state"], "warm")
        self.assertEqual(len({first, second, third, fourth}), 4)

    def test_warm_fix_and_post_fix_recheck_share_one_index_lease(self) -> None:
        source = self.write("one.py", "# preface\ndef target():\n    return 1\n")
        onboarding = self.memory / "onboarding"
        onboarding.mkdir()
        card = onboarding / "one.py.md"
        card.write_text(
            "# one.py\n\n"
            "| Field | Value |\n| --- | --- |\n| repository | test |\n"
            "| path | `one.py` |\n\n"
            "## Repo-Internal References\n\n"
            "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
            "| Target definition. | `target` | one.py:1-1 |\n",
            encoding="utf-8",
        )
        _built, snapshot = self.acquire()
        reads: list[Path] = []
        original_read_text = Path.read_text

        def read_text(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
            reads.append(path)
            return original_read_text(path, encoding=encoding, errors=errors)

        with (
            mock.patch.object(Path, "read_text", new=read_text),
            mock.patch.object(
                source_index,
                "_tree_state",
                side_effect=AssertionError("frozen fixer inspected the source tree"),
            ),
        ):
            result = fixer.fix_onboarding_root(
                onboarding,
                self.code,
                dry_run=True,
                only="one.py.md",
                expected_snapshot=snapshot,
            )

        telemetry = result["sourceIndex"]
        self.assertEqual(result["failingClaims"], 1)
        self.assertEqual(telemetry["state"], "frozen")
        self.assertEqual(telemetry["sourceFilesRead"], 0)
        self.assertEqual(telemetry["sourceFilesTokenized"], 0)
        self.assertEqual(telemetry["sourceFilesParsed"], 0)
        self.assertEqual(telemetry["metadataTreeEnumerations"], 0)
        self.assertEqual(telemetry["metadataFilesStat"], 0)
        self.assertEqual(telemetry["metadataDirectoriesStat"], 0)
        self.assertEqual(telemetry["indexQueries"], 2)
        self.assertEqual(telemetry["directAnchorQueries"], 2)
        self.assertTrue(telemetry["postFixRecheck"]["reusedLease"])
        self.assertIn(card, reads)
        self.assertIn(source, reads)
