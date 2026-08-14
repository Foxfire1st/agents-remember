from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from agents_remember.memory_quality.style.citations import (
    model,
    source_index,
    source_index_cache,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from test_memory_citation_source_index import MCP_SRC, IndexCase


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
