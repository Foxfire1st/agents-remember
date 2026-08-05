"""Snapshot, parity, reuse, and publication tests for the citation source index."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.style.citations import (
    fixer,
    model,
    source_index,
    source_index_cache,
    source_index_database,
    source_index_state,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees


@contextmanager
def _publication_failure(boundary: str, paths: source_index.CachePaths) -> Iterator[None]:
    original_replace = source_index.atomic_replace
    original_write = source_index.atomic_write_text
    if boundary == "before_database_replace":
        with mock.patch.object(
            source_index,
            "atomic_replace",
            side_effect=RuntimeError(boundary),
        ):
            yield
        return

    def replace_database(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if boundary == "after_database_replace" and destination == paths.database:
            raise RuntimeError(boundary)

    def write_metadata(path: Path, text: str) -> None:
        original_write(path, text)
        if boundary == "after_manifest_replace" and path == paths.manifest:
            raise RuntimeError(boundary)
        if boundary == "after_readiness_publish" and path == paths.readiness:
            raise RuntimeError(boundary)
        if boundary == "before_readiness_publish" and path == paths.readiness:
            path.unlink(missing_ok=True)
            raise RuntimeError(boundary)

    with (
        mock.patch.object(source_index, "atomic_replace", new=replace_database),
        mock.patch.object(source_index, "atomic_write_text", new=write_metadata),
    ):
        yield


class IndexCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.code = self.root / "code"
        self.memory = self.root / "memory"
        self.cache = self.root / "cache"
        self.code.mkdir()
        self.memory.mkdir()
        self.trees = Trees(code_root=self.code, memory_root=self.memory)
        self.environment = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write(self, relative: str, body: str) -> Path:
        path = self.code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def acquire(self) -> tuple[dict[str, Any], str]:
        with source_index.open_repository_index(self.trees) as index:
            return index.telemetry(), index.snapshot_id

    def build(self) -> dict[str, Any]:
        return source_index.build_repository_index(self.trees)

    def frozen_refusal_without_discovery(self, snapshot: str) -> str:
        with (
            mock.patch.object(
                source_index,
                "_tree_state",
                side_effect=AssertionError("frozen refusal inspected source"),
            ),
            mock.patch.object(
                source_index,
                "_reclaim_legacy_cache_roots",
                side_effect=AssertionError("frozen refusal reclaimed cache"),
            ),
            mock.patch.object(
                source_index,
                "_build_and_publish",
                side_effect=AssertionError("frozen refusal rebuilt/fell back"),
            ),
            mock.patch.object(
                source_index_database.Database,
                "validate_application_integrity",
                side_effect=AssertionError("frozen refusal traversed integrity"),
            ),
            self.assertRaises(source_index.SourceIndexError) as raised,
        ):
            source_index.open_repository_index(self.trees, expected_snapshot=snapshot)
        return str(raised.exception)


class SemanticParityTests(IndexCase):
    def test_cached_and_direct_resolution_match_every_anchor_shape(self) -> None:
        self.write(
            "pkg/alpha.py",
            "def alpha():\n    return 'shared words', 'x', 'yz'\n\nalpha()\n",
        )
        self.write(
            "web/component.ts",
            "export function beta() {\n  sendMessage(\n    'shared words',\n+  );\n}\nconst gamma = beta;\n",
        )
        self.write("web/view.tsx", "export function Panel() { return <div>Panel</div>; }\n")
        self.write("data/config.json", '{"fallbackName": "gamma", "copy": "shared words"}\n')
        self.write(
            "README.md",
            "# Root\nfirst shared words\n## Child\nbody\n# Root\nsecond\n",
        )
        self.write("data/unicode.txt", ("λ0é0" * 128) + "\n")
        anchors = (
            model.Anchor(model.SYMBOL, "alpha"),
            model.Anchor(model.SYMBOL, "beta"),
            model.Anchor(model.SYMBOL, "Panel"),
            model.Anchor(model.SYMBOL, "fallbackName"),
            model.Anchor(model.SYMBOL, "missing"),
            model.Anchor(model.HEADING, "# Root"),
            model.Anchor(model.QUOTE, "shared words"),
            model.Anchor(model.QUOTE, "x"),
            model.Anchor(model.QUOTE, "yz"),
            model.Anchor(model.QUOTE, "0"),
            model.Anchor(model.QUOTE, "Shared Words"),
        )

        expected = symbol_index.locate_uncached(anchors, self.trees)
        with source_index.open_repository_index(self.trees) as index:
            actual = symbol_index.locate(anchors, self.trees, index=index)

        self.assertEqual(actual, expected)
        self.assertEqual(list(actual), list(dict.fromkeys(anchors)))

    def test_quote_lookup_reads_candidates_not_the_full_corpus(self) -> None:
        self.write("target.txt", "a uniquely searchable quotation\n")
        for index in range(20):
            self.write(f"fill/{index}.txt", f"ordinary filler number {index}\n")
        anchor = model.Anchor(model.QUOTE, "uniquely searchable quotation")

        with source_index.open_repository_index(self.trees) as index:
            seen = symbol_index.locate((anchor,), self.trees, index=index)[anchor]
            telemetry: dict[str, Any] = index.telemetry()

        self.assertEqual(seen.files, 1)
        self.assertEqual(telemetry["quoteFullCorpusScans"], 0)
        self.assertEqual(telemetry["quoteIndexLookups"], 1)
        self.assertLess(telemetry["quoteCandidateStreamsRead"], telemetry["quoteCorpusStreams"])


class ManagedNamespaceTests(IndexCase):
    def managed_trees(
        self,
        name: str,
        *,
        code: Path | None = None,
        memory: Path | None = None,
    ) -> Trees:
        coordination = self.root / "coordination"
        contract = coordination / "tasks" / name / "enclosures" / name / "series-contract.md"
        contract.parent.mkdir(parents=True, exist_ok=True)
        selected_code = code or self.code
        selected_memory = memory or self.memory
        authority = source_index_cache.managed_cache_authority(
            coordination_root=coordination,
            contract_path=contract,
            code_root=selected_code,
            memory_root=selected_memory,
        )
        return Trees(selected_code, selected_memory, cache_authority=authority)

    def test_same_contract_shares_exact_generation_across_processes_and_xdg_values(self) -> None:
        self.write("one.py", "def alpha():\n    return 1\n")
        trees = self.managed_trees("leaf-one")
        with (
            mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root / "xdg-a")}),
            source_index.open_repository_index(trees) as index,
        ):
            snapshot = index.snapshot_id
            path = index.paths.database
        authority = trees.cache_authority
        assert authority is not None
        self.assertTrue(path.is_relative_to(authority.managed_root))
        self.assertFalse(path.is_relative_to(self.code))
        self.assertFalse(path.is_relative_to(self.memory))
        script = """
import json, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index, source_index_cache
from agents_remember.memory_quality.style.citations.resolution import Trees
authority=source_index_cache.managed_cache_authority(
    coordination_root=Path(sys.argv[1]), contract_path=Path(sys.argv[2]),
    code_root=Path(sys.argv[3]), memory_root=Path(sys.argv[4]))
trees=Trees(Path(sys.argv[3]), Path(sys.argv[4]), cache_authority=authority)
with source_index.open_repository_index(trees, expected_snapshot=sys.argv[5]) as index:
    print(json.dumps(index.telemetry()))
"""
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                authority.coordination_root.as_posix(),
                authority.contract_path.as_posix(),
                self.code.as_posix(),
                self.memory.as_posix(),
                snapshot,
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(MCP_SRC),
                "XDG_CACHE_HOME": str(self.root / "xdg-b"),
            },
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        telemetry = json.loads(process.stdout)
        self.assertEqual(Path(telemetry["path"]), path)
        self.assertEqual(telemetry["state"], "frozen")
        self.assertTrue(telemetry["cacheManaged"])
        self.assertEqual(telemetry["cacheNamespace"], authority.namespace_id)
        self.assertEqual(telemetry["metadataTreeEnumerations"], 0)

    def test_managed_same_and_different_leaf_cold_processes_coordinate_without_eviction(
        self,
    ) -> None:
        script = """
import json, sys, time
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index, source_index_cache
from agents_remember.memory_quality.style.citations.resolution import Trees
start=Path(sys.argv[5])
deadline=time.monotonic()+10
while not start.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
authority=source_index_cache.managed_cache_authority(
    coordination_root=Path(sys.argv[1]), contract_path=Path(sys.argv[2]),
    code_root=Path(sys.argv[3]), memory_root=Path(sys.argv[4]))
trees=Trees(Path(sys.argv[3]), Path(sys.argv[4]), cache_authority=authority)
with source_index.open_repository_index(trees) as index:
    print(json.dumps(index.telemetry()))
"""
        same = self.managed_trees("same-leaf")
        self.write("one.py", "alpha = 1\n")
        authority = same.cache_authority
        assert authority is not None
        start = self.root / "same-start"
        same_processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    authority.coordination_root.as_posix(),
                    authority.contract_path.as_posix(),
                    self.code.as_posix(),
                    self.memory.as_posix(),
                    start.as_posix(),
                ],
                env={
                    **os.environ,
                    "PYTHONPATH": str(MCP_SRC),
                    "XDG_CACHE_HOME": str(self.root / f"same-xdg-{index}"),
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(4)
        ]
        start.write_text("go\n", encoding="utf-8")
        same_results = []
        for process in same_processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            same_results.append(json.loads(stdout))
        self.assertEqual(sum(one["state"] == "built" for one in same_results), 1)
        self.assertEqual(len({one["snapshotId"] for one in same_results}), 1)
        self.assertEqual(len({one["path"] for one in same_results}), 1)

        start = self.root / "different-start"
        different: list[tuple[Trees, subprocess.Popen[str]]] = []
        for index in range(3):
            code = self.root / f"different-code-{index}"
            memory = self.root / f"different-memory-{index}"
            code.mkdir()
            memory.mkdir()
            (code / "one.py").write_text(f"value_{index} = 1\n", encoding="utf-8")
            trees = self.managed_trees(f"different-{index}", code=code, memory=memory)
            leaf = trees.cache_authority
            assert leaf is not None
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    leaf.coordination_root.as_posix(),
                    leaf.contract_path.as_posix(),
                    code.as_posix(),
                    memory.as_posix(),
                    start.as_posix(),
                ],
                env={
                    **os.environ,
                    "PYTHONPATH": str(MCP_SRC),
                    "XDG_CACHE_HOME": str(self.root / f"different-xdg-{index}"),
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            different.append((trees, process))
        start.write_text("go\n", encoding="utf-8")
        paths = set()
        for trees, process in different:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result["state"], "built")
            paths.add(result["path"])
            self.assertTrue(source_index.cache_paths(trees).readiness.exists())
        self.assertEqual(len(paths), 3)
        self.assertTrue(source_index.cache_paths(same).readiness.exists())

    def test_distinct_leaf_namespaces_never_evict_and_capacity_refuses_with_occupants(self) -> None:
        trees: list[Trees] = []
        snapshots: list[str] = []
        for index in range(source_index_cache.MANAGED_NAMESPACE_LIMIT):
            code = self.root / f"managed-code-{index}"
            memory = self.root / f"managed-memory-{index}"
            code.mkdir()
            memory.mkdir()
            (code / "one.py").write_text(f"value_{index} = {index}\n", encoding="utf-8")
            one = self.managed_trees(f"leaf-{index}", code=code, memory=memory)
            trees.append(one)
            with source_index.open_repository_index(one) as acquired:
                snapshots.append(acquired.snapshot_id)

        slots = [source_index.cache_paths(one).slot for one in trees]
        self.assertEqual(len(set(slots)), source_index_cache.MANAGED_NAMESPACE_LIMIT)
        for one, snapshot in zip(trees, snapshots, strict=True):
            with source_index.open_repository_index(one, expected_snapshot=snapshot) as acquired:
                self.assertEqual(acquired.snapshot_id, snapshot)

        # Admission counts namespace ownership, not readiness. A crashed/partial publisher
        # is reclaimed only by its exact lifecycle owner, never as space for another leaf.
        slots[-1].joinpath("ready.json").unlink()

        extra_code = self.root / "managed-code-extra"
        extra_memory = self.root / "managed-memory-extra"
        extra_code.mkdir()
        extra_memory.mkdir()
        (extra_code / "one.py").write_text("extra = 1\n", encoding="utf-8")
        extra = self.managed_trees("leaf-extra", code=extra_code, memory=extra_memory)
        occupants = sorted(
            one.cache_authority.namespace_id for one in trees if one.cache_authority is not None
        )
        with self.assertRaises(source_index.SourceIndexError) as raised:
            source_index.open_repository_index(extra)
        message = str(raised.exception)
        self.assertIn(str(occupants), message)
        self.assertIn("cleanup or abandon", message)
        self.assertFalse(source_index.cache_paths(extra).slot.exists())
        for slot in slots[:-1]:
            self.assertTrue((slot / "ready.json").is_file())
        self.assertTrue(slots[-1].is_dir())
        self.assertFalse((slots[-1] / "ready.json").exists())

        published = sum(
            path.stat().st_size
            for slot in slots
            for path in (slot / "index.sqlite3", slot / "manifest.json", slot / "ready.json")
            if path.exists()
        )
        self.assertLessEqual(
            published,
            source_index_cache.MANAGED_NAMESPACE_LIMIT * source_index.MAX_INDEX_BYTES,
        )

    def test_managed_frozen_open_is_zero_walk_and_never_uses_standalone_cache(self) -> None:
        self.write("one.py", "alpha = 1\n")
        trees = self.managed_trees("leaf-frozen")
        with source_index.open_repository_index(trees) as index:
            snapshot = index.snapshot_id
        with (
            mock.patch.object(source_index, "_tree_state", side_effect=AssertionError("walk")),
            mock.patch.object(
                source_index,
                "_reclaim_legacy_cache_roots",
                side_effect=AssertionError("standalone fallback"),
            ),
            source_index.open_repository_index(trees, expected_snapshot=snapshot) as frozen,
        ):
            telemetry = frozen.telemetry()
        self.assertEqual(telemetry["state"], "frozen")
        self.assertEqual(telemetry["metadataTreeEnumerations"], 0)
        self.assertTrue(telemetry["cacheManaged"])

    def test_invalid_managed_authority_never_falls_back_to_standalone_slots(self) -> None:
        self.write("one.py", "alpha = 1\n")
        trees = self.managed_trees("leaf-invalid")
        authority = trees.cache_authority
        assert authority is not None
        wrong = Trees(
            self.code,
            self.memory,
            cache_authority=source_index_cache.ManagedCacheAuthority(
                authority.coordination_root,
                authority.contract_path,
                self.root / "foreign-code",
                authority.memory_root,
                authority.namespace_id,
            ),
        )
        with self.assertRaisesRegex(source_index_cache.CitationCacheError, "different code/memory"):
            source_index.cache_paths(wrong)
        self.assertEqual(list(source_index.cache_root().glob("slot-*")), [])

    def test_exact_reclamation_preserves_neighbor_and_live_lease_blocks(self) -> None:
        self.write("one.py", "alpha = 1\n")
        first = self.managed_trees("leaf-first")
        other_code = self.root / "neighbor-code"
        other_memory = self.root / "neighbor-memory"
        other_code.mkdir()
        other_memory.mkdir()
        (other_code / "one.py").write_text("beta = 2\n", encoding="utf-8")
        neighbor = self.managed_trees("leaf-neighbor", code=other_code, memory=other_memory)
        with source_index.open_repository_index(first) as live:
            with source_index.open_repository_index(neighbor):
                pass
            authority = first.cache_authority
            assert authority is not None
            with mock.patch.object(source_index_cache, "LOCK_TIMEOUT_SECONDS", 0.02):
                blocked = source_index_cache.reclaim_managed_namespace(authority, dry_run=False)
            self.assertEqual(blocked["reason"], "live-lease-timeout")
            self.assertTrue(live.paths.readiness.exists())

        removed = source_index_cache.reclaim_managed_namespace(authority, dry_run=False)
        self.assertTrue(removed["removed"])
        self.assertFalse(source_index.cache_paths(first).slot.exists())
        self.assertTrue(source_index.cache_paths(neighbor).readiness.exists())

    def test_direct_lookup_fetches_only_posting_referenced_file_paths(self) -> None:
        for index in range(40):
            self.write(f"fill/{index}.py", f"filler_{index} = {index}\n")
        self.write("target.py", "def uniquely_targeted_symbol():\n    return 1\n")
        anchor = model.Anchor(model.SYMBOL, "uniquely_targeted_symbol")

        with source_index.open_repository_index(self.trees) as index:
            statements: list[str] = []
            index.database.connection.set_trace_callback(statements.append)
            try:
                seen = symbol_index.locate((anchor,), self.trees, index=index)[anchor]
            finally:
                index.database.connection.set_trace_callback(None)

        self.assertEqual([one.path for one in seen.locations], ["target.py"])
        path_queries = [
            " ".join(statement.lower().split())
            for statement in statements
            if "select file_id, path from files" in statement.lower()
        ]
        self.assertTrue(path_queries)
        self.assertTrue(all("where file_id in (" in query for query in path_queries))

    def test_creation_order_and_code_root_alias_do_not_change_results(self) -> None:
        first = self.write("z.py", "def zed():\n    pass\n")
        second = self.write("a.py", "def alpha():\n    pass\n")
        expected, snapshot = self.acquire()
        alias = self.root / "code-alias"
        alias.symlink_to(self.code, target_is_directory=True)
        with source_index.open_repository_index(Trees(alias, self.memory)) as index:
            self.assertEqual(index.metrics.state, "warm")
            self.assertEqual(index.snapshot_id, snapshot)
        first.unlink()
        second.unlink()
        self.write("a.py", "def alpha():\n    pass\n")
        self.write("z.py", "def zed():\n    pass\n")
        rebuilt, same_snapshot = self.acquire()
        self.assertEqual(rebuilt["state"], "metadata-refreshed")
        self.assertEqual(same_snapshot, snapshot)
        self.assertNotEqual(expected["state"], "warm")


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


class PublicationAndBoundsTests(IndexCase):
    def test_corrupt_database_and_obsolete_manifest_rebuild(self) -> None:
        self.write("one.py", "alpha = 1\n")
        _cold, snapshot = self.acquire()
        paths = source_index.cache_paths(self.trees)
        paths.database.write_bytes(b"not sqlite")
        rebuilt, same_snapshot = self.acquire()
        self.assertEqual(rebuilt["state"], "built")
        self.assertEqual(same_snapshot, snapshot)
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        manifest["schemaVersion"] = -1
        paths.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        obsolete, final_snapshot = self.acquire()
        self.assertEqual(obsolete["state"], "built")
        self.assertEqual(final_snapshot, snapshot)

    def test_cache_is_external_fixed_slot_and_per_file_cap_fails_closed(self) -> None:
        self.write("large.txt", "12345")
        paths = source_index.cache_paths(self.trees)
        self.assertNotIn(self.code.resolve(), paths.root.parents)
        self.assertNotIn(self.memory.resolve(), paths.root.parents)
        self.assertLess(int(paths.slot.name.removeprefix("slot-")), source_index.CACHE_SLOT_COUNT)
        with (
            mock.patch.object(source_index, "MAX_SOURCE_FILE_BYTES", 4),
            self.assertRaises(source_index.SourceIndexError),
        ):
            source_index.open_repository_index(self.trees)
        self.assertFalse(paths.database.exists())
        self.assertFalse(paths.manifest.exists())
        self.assertFalse(paths.readiness.exists())

    def test_repository_slot_collision_revalidates_identity(self) -> None:
        self.write("one.py", "alpha = 1\n")
        with mock.patch.object(source_index, "CACHE_SLOT_COUNT", 1):
            _first, first_snapshot = self.acquire()
            other_code = self.root / "other-code"
            other_memory = self.root / "other-memory"
            other_code.mkdir()
            other_memory.mkdir()
            (other_code / "two.py").write_text("beta = 2\n", encoding="utf-8")
            with source_index.open_repository_index(Trees(other_code, other_memory)) as index:
                self.assertEqual(index.metrics.state, "built")
                self.assertNotEqual(index.snapshot_id, first_snapshot)
            rebuilt, restored_snapshot = self.acquire()
        self.assertEqual(rebuilt["state"], "built")
        self.assertEqual(restored_snapshot, first_snapshot)

    def test_cache_lifecycle_is_bounded_at_two_slot_and_repository_sizes(self) -> None:
        for slot_count, repository_count in ((1, 3), (2, 6)):
            with self.subTest(slot_count=slot_count, repository_count=repository_count):
                cache = self.root / f"cache-{slot_count}"
                with (
                    mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}),
                    mock.patch.object(source_index, "CACHE_SLOT_COUNT", slot_count),
                ):
                    parent = source_index.cache_root().parent
                    for version in (1, 3, 5):
                        legacy = parent / f"citation-source-index-v{version}" / "slot-0"
                        legacy.mkdir(parents=True)
                        (legacy / "index.lock").write_bytes(b"")
                        (legacy / "index.sqlite3").write_bytes(b"obsolete")
                    for repository in range(repository_count):
                        code = self.root / f"code-{slot_count}-{repository}"
                        memory = self.root / f"memory-{slot_count}-{repository}"
                        code.mkdir()
                        memory.mkdir()
                        (code / "one.py").write_text(
                            f"symbol_{repository} = {repository}\n", encoding="utf-8"
                        )
                        with source_index.open_repository_index(Trees(code, memory)) as index:
                            self.assertEqual(index.metrics.state, "built")
                    self.assertEqual(
                        [
                            path.name
                            for path in parent.iterdir()
                            if source_index.LEGACY_CACHE_NAME.fullmatch(path.name)
                        ],
                        [],
                    )
                    slots = list(source_index.cache_root().glob("slot-*"))
                    self.assertLessEqual(len(slots), slot_count)
                    published = sum(
                        path.stat().st_size
                        for slot in slots
                        for path in (
                            slot / "index.sqlite3",
                            slot / "manifest.json",
                            slot / "ready.json",
                        )
                        if path.exists()
                    )
                    self.assertLessEqual(published, slot_count * source_index.MAX_INDEX_BYTES)

    def test_concurrent_legacy_reclamation_publishes_only_stable_slots(self) -> None:
        parent = source_index.cache_root().parent
        for version in range(1, 6):
            for slot_number in range(2):
                slot = parent / f"citation-source-index-v{version}" / f"slot-{slot_number}"
                slot.mkdir(parents=True)
                (slot / "index.lock").write_bytes(b"")
                (slot / "index.sqlite3").write_bytes(b"obsolete")
        repositories: list[Trees] = []
        for repository in range(4):
            code = self.root / f"concurrent-code-{repository}"
            memory = self.root / f"concurrent-memory-{repository}"
            code.mkdir()
            memory.mkdir()
            (code / "one.py").write_text(f"symbol_{repository} = 1\n", encoding="utf-8")
            repositories.append(Trees(code, memory))
        start = self.root / "start-reclamation"
        script = """
import json, sys, time
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
start=Path(sys.argv[3])
deadline=time.monotonic()+10
while not start.exists() and time.monotonic() < deadline:
    time.sleep(0.01)
with source_index.open_repository_index(Trees(Path(sys.argv[1]), Path(sys.argv[2]))) as index:
    print(json.dumps({'state': index.metrics.state, 'snapshot': index.snapshot_id}))
"""
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(one.code_root),
                    str(one.memory_root),
                    str(start),
                ],
                env={**os.environ, "PYTHONPATH": str(MCP_SRC), "XDG_CACHE_HOME": str(self.cache)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for one in repositories
        ]
        start.write_text("go\n", encoding="utf-8")
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)["state"], "built")

        self.assertFalse(
            any(source_index.LEGACY_CACHE_NAME.fullmatch(path.name) for path in parent.iterdir())
        )
        self.assertLessEqual(
            len(list(source_index.cache_root().glob("slot-*"))), source_index.CACHE_SLOT_COUNT
        )

    def test_live_legacy_slots_are_not_deleted_and_share_one_bounded_wait(self) -> None:
        self.write("one.py", "alpha = 1\n")
        parent = source_index.cache_root().parent
        first = parent / "citation-source-index-v4" / "slot-0"
        second = parent / "citation-source-index-v5" / "slot-0"
        handles = []
        for legacy in (first, second):
            legacy.mkdir(parents=True)
            handle = (legacy / "index.lock").open("a+b")
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            handles.append(handle)

        def release_first() -> None:
            fcntl.flock(handles[0].fileno(), fcntl.LOCK_UN)

        release = threading.Timer(0.04, release_first)
        release.start()
        started = time.monotonic()
        try:
            with (
                mock.patch.object(source_index, "RECLAMATION_LOCK_TIMEOUT_SECONDS", 0.08),
                self.assertRaisesRegex(source_index.SourceIndexError, "timed out"),
            ):
                source_index.open_repository_index(self.trees)
            self.assertLess(time.monotonic() - started, 0.11)
            self.assertTrue(second.parent.exists())
        finally:
            release.join()
            for handle in handles:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()

        built, _snapshot = self.acquire()
        self.assertEqual(built["state"], "built")
        self.assertFalse(first.parent.exists())
        self.assertFalse(second.parent.exists())

    def test_anchor_digest_collision_is_rejected(self) -> None:
        self.write("one.py", "alpha = 1\nbeta = 2\n")
        with (
            mock.patch.object(source_index_database, "_anchor_key", return_value=b"same"),
            self.assertRaises(source_index_database.SourceIndexDatabaseError),
        ):
            source_index.open_repository_index(self.trees)

    def test_stale_builder_temp_is_reclaimed_by_next_publisher(self) -> None:
        self.write("one.py", "alpha = 1\n")
        paths = source_index.cache_paths(self.trees)
        paths.slot.mkdir(parents=True, exist_ok=True)
        stale = paths.slot / ".index.sqlite3.999.dead.tmp"
        stale.write_text("partial", encoding="utf-8")

        built, _snapshot = self.acquire()

        self.assertEqual(built["state"], "built")
        self.assertFalse(stale.exists())

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

        def replace(source: Path, destination: Path) -> None:
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

        def replace_database(source: Path, destination: Path) -> None:
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


class CrossProcessTests(IndexCase):
    def process_environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "PYTHONPATH": str(MCP_SRC),
            "XDG_CACHE_HOME": str(self.cache),
        }

    def test_concurrent_cold_acquisition_has_exactly_one_builder(self) -> None:
        self.write("one.py", "def alpha():\n    return 1\n")
        script = """
import json, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
trees=Trees(Path(sys.argv[1]), Path(sys.argv[2]))
with source_index.open_repository_index(trees) as index:
    print(json.dumps({'state': index.metrics.state, 'snapshot': index.snapshot_id}))
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.code), str(self.memory)],
                env=self.process_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _index in range(6)
        ]
        results: list[dict[str, str]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))

        self.assertEqual([one["state"] for one in results].count("built"), 1)
        self.assertEqual({one["snapshot"] for one in results}, {results[0]["snapshot"]})

    def test_unseen_anchor_is_queryable_in_a_later_process_without_source_work(self) -> None:
        self.write("one.py", "def never_queried_during_build():\n    return 1\n")
        self.acquire()
        script = """
import json, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import model, source_index, symbol_index
from agents_remember.memory_quality.style.citations.resolution import Trees
trees=Trees(Path(sys.argv[1]), Path(sys.argv[2]))
anchor=model.Anchor(model.SYMBOL, 'never_queried_during_build')
with source_index.open_repository_index(trees) as index:
    seen=symbol_index.locate((anchor,), trees, index=index)[anchor]
    print(json.dumps({'files': seen.files, **index.telemetry()}))
"""
        process = subprocess.run(
            [sys.executable, "-c", script, str(self.code), str(self.memory)],
            env=self.process_environment(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["state"], "warm")
        self.assertEqual(result["sourceFilesRead"], 0)
        self.assertEqual(result["sourceFilesTokenized"], 0)
        self.assertEqual(result["sourceFilesParsed"], 0)

    def test_killed_lock_owner_releases_lock_and_next_publisher_reclaims_temp(self) -> None:
        self.write("one.py", "alpha = 1\n")
        script = """
import fcntl, signal, sys
from pathlib import Path
from agents_remember.memory_quality.style.citations import source_index
from agents_remember.memory_quality.style.citations.resolution import Trees
paths=source_index.cache_paths(Trees(Path(sys.argv[1]), Path(sys.argv[2])))
paths.slot.mkdir(parents=True, exist_ok=True)
handle=paths.lock.open('a+b')
fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
(paths.slot / '.index.sqlite3.999.killed.tmp').write_text('partial', encoding='utf-8')
print('locked', flush=True)
signal.pause()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.code), str(self.memory)],
            env=self.process_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline().strip(), "locked")
        process.kill()
        process.communicate(timeout=10)

        built, _snapshot = self.acquire()

        self.assertEqual(built["state"], "built")
        paths = source_index.cache_paths(self.trees)
        self.assertFalse((paths.slot / ".index.sqlite3.999.killed.tmp").exists())


if __name__ == "__main__":
    unittest.main()
