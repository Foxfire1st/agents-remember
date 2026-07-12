"""CS-6 projection hot-path scaling regressions (260707-HFX2-L12 fix round 2).

The projection tick (`project_and_write`, 1s) previously re-walked/re-read the whole task tree twice,
double-folded every gate log, shelled out to git per leaf, and re-parsed every lifecycle log — all per
tick. These prove the caches/single-folds landed for F8/F9/F10/F11 bound that per-tick cost.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _scaling import assert_bounded_count
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer import contract_snapshot, drift_snapshots, projection_store, snapshots
from agents_remember.observer.contract_snapshot import ContractSnapshotCache
from agents_remember.observer.events import Event
from agents_remember.observer.paths import observer_logs_root
from agents_remember.observer.projection import (
    TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES,
    Analytics,
    TaskDocNode,
    WorkspaceProjection,
    task_documents_body_bytes,
)
from agents_remember.observer.snapshots import (
    TASK_DOCUMENT_SCHEMA,
    TASK_DOCUMENT_SUMMARY_LIMIT,
    read_enclosures,
    read_engine_process_facts,
    read_gates,
    read_series_documents,
    read_task_document_body,
    read_task_documents,
)
from agents_remember.observer.store import EventStore
from agents_remember.worktrees.task_resolver import ENCLOSURES_DIR, SERIES_CONTRACT_FILENAME
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    default_contract,
    write_contract,
)

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


class GateReadFoldTests(unittest.TestCase):
    """F8/CS-6 D2: the projection folds each gate log with ONE read per tick, not two."""

    def test_read_gates_reads_each_log_once_per_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp)
            store = GateStore(observer_logs_root(coordination_root))
            lifecycles = 12
            for index in range(lifecycles):
                store.append(
                    GateRecord(
                        id=f"g{index}", ts=NOW.isoformat(), kind="plan-approval", state="open",
                        lifecycleId=f"life-{index}",
                    )
                )
            reads = {"count": 0}
            original = GateStore.read

            def counting_read(self, lifecycle_id, _orig=original):  # type: ignore[no-untyped-def]
                reads["count"] += 1
                return _orig(self, lifecycle_id)

            GateStore.read = counting_read  # type: ignore[assignment]
            try:
                gates = read_gates(coordination_root, now=NOW)
            finally:
                GateStore.read = original  # type: ignore[assignment]
            self.assertEqual(len(gates), lifecycles)
            # One read per gate log (was compact()'s read + current()'s read = 2x per lifecycle).
            assert_bounded_count(reads["count"], lifecycles, label="gate-log reads per tick")


class TaskDocSharedCacheTests(unittest.TestCase):
    """F10/CS-6 D2: task + series readers share ONE cached walk of the tasks tree per tick."""

    def _seed(self, coordination_root: Path, count: int) -> None:
        tasks = coordination_root / "tasks" / "repo" / "master"
        tasks.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (tasks / f"leaf-{index}.json").write_text(
                json.dumps(
                    {
                        "schema": TASK_DOCUMENT_SCHEMA, "kind": "light", "id": f"L{index}",
                        "title": "t", "repo": "repo", "status": "planning",
                        "createdAt": NOW.isoformat(),
                    }
                ),
                encoding="utf-8",
            )

    def test_task_and_series_readers_do_not_double_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp)
            self._seed(coordination_root, 30)
            snapshots._task_doc_cache.clear()
            reads = {"count": 0}
            original = snapshots._read_json

            def counting_read_json(path, _orig=original):  # type: ignore[no-untyped-def]
                reads["count"] += 1
                return _orig(path)

            snapshots._read_json = counting_read_json  # type: ignore[assignment]
            try:
                read_task_documents(coordination_root, enclosures=[], now=NOW)
                after_first = reads["count"]
                read_series_documents(coordination_root, now=NOW)
                after_second = reads["count"]
            finally:
                snapshots._read_json = original  # type: ignore[assignment]
            # First reader walks + parses the tree once; the second reader adds ZERO parses (cache hit).
            self.assertGreaterEqual(after_first, 30)
            assert_bounded_count(
                after_second - after_first, 0, label="second-reader task-json parses"
            )


class GitStatusCacheTests(unittest.TestCase):
    """F11/CS-6 D1: the per-leaf git status probe is TTL-cached, not fired every tick."""

    def test_status_payload_cached_within_ttl(self) -> None:
        calls = {"count": 0}
        original = snapshots.projected_status_payload

        class NoLanding:
            def current(self, contract, *, now):  # type: ignore[no-untyped-def]
                _ = contract, now

        def fake_status(_contract, *, landing):  # type: ignore[no-untyped-def]
            _ = landing
            calls["count"] += 1
            return {"ok": True}

        snapshots.projected_status_payload = fake_status  # type: ignore[assignment]
        snapshots._status_payload_cache.clear()
        landing_state = NoLanding()
        try:
            snapshots._safe_status_payload(
                "c", cache_key="leaf-1", now=NOW, landing_state=landing_state
            )
            snapshots._safe_status_payload(
                "c",
                cache_key="leaf-1",
                now=NOW + timedelta(seconds=1),
                landing_state=landing_state,
            )
            snapshots._safe_status_payload(
                "c",
                cache_key="leaf-1",
                now=NOW + timedelta(seconds=30),
                landing_state=landing_state,
            )
        finally:
            snapshots.projected_status_payload = original  # type: ignore[assignment]
        # 1 cold probe + 1 refresh past the 8s TTL; the within-TTL call reused the cache.
        assert_bounded_count(calls["count"], 2, label="git status probes across 3 ticks")


class LandingProjectionHotPathTests(unittest.TestCase):
    """Remote landing observation must not serialize the recurring projection reader."""

    def test_slow_remote_landing_probes_do_not_block_engine_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp)
            for index in range(4):
                contract = default_contract(
                    task_name=f"landing-{index}",
                    repo_name=f"repo-{index}",
                    workflow_kind="light",
                    memory_mode="disabled",
                    coordination_root=coordination_root,
                    code_repo_path=coordination_root / f"repo-{index}",
                    code_source_branch=f"feat/{index}",
                    code_work_branch=f"ar/{index}",
                    code_base_commit=f"base-{index}",
                    worktree_name=f"landing-{index}",
                )
                contract = replace(contract, closeout_status="completed")
                contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
                write_contract(contract.contract_path, contract)

            def slow_remote_probe(_contract):  # type: ignore[no-untyped-def]
                time.sleep(0.075)
                return []

            started = time.perf_counter()
            with patch(
                "agents_remember.worktrees.modules.guidance.landing_refs",
                side_effect=slow_remote_probe,
            ):
                facts = snapshots.read_engine_process_facts(coordination_root, now=NOW)
            elapsed = time.perf_counter() - started

            self.assertEqual(len(facts), 4)
            self.assertLess(
                elapsed,
                0.15,
                f"projection reader waited {elapsed:.3f}s for four remote landing probes",
            )

    def test_invalid_landing_snapshot_keeps_truthful_local_status(self) -> None:
        class InvalidLanding:
            def current(self, contract, *, now):  # type: ignore[no-untyped-def]
                _ = contract, now
                raise ValueError("invalid immutable snapshot")

        with (
            patch.object(
                snapshots,
                "projected_status_payload",
                return_value={"code_worktree_exists": True},
            ),
            self.assertLogs(snapshots.logger, level="WARNING") as captured,
        ):
            status = snapshots._safe_status_payload(
                "contract",
                now=NOW,
                landing_state=InvalidLanding(),
            )

        self.assertEqual(status, {"code_worktree_exists": True})
        self.assertIn("landing snapshot merge failed", captured.output[0])


class LifecycleLogCacheTests(unittest.TestCase):
    """F9/CS-6 D2: unchanged lifecycle logs are NOT re-parsed on the next tick, at any size."""

    def _read_count_on_unchanged_second_pass(self, events_per_log: int) -> int:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / f"n{events_per_log}"
        store = EventStore(root)
        for lifecycle in range(5):
            for seq in range(events_per_log):
                store.append(
                    Event(
                        id=f"{lifecycle}-{seq}", ts=NOW.isoformat(), kind="test.event",
                        trust="observed", actor="system", data={}, lifecycleId=f"life-{lifecycle}",
                    )
                )
        projection_store._lifecycle_log_cache.clear()
        projection_store.read_lifecycle_logs(root)  # first pass populates the cache
        reads = {"count": 0}
        original = EventStore.read_log

        def counting_read_log(self, lifecycle_id, _orig=original):  # type: ignore[no-untyped-def]
            reads["count"] += 1
            return _orig(self, lifecycle_id)

        EventStore.read_log = counting_read_log  # type: ignore[assignment]
        try:
            projection_store.read_lifecycle_logs(root)  # second pass, logs unchanged
        finally:
            EventStore.read_log = original  # type: ignore[assignment]
        return reads["count"]

    def test_unchanged_logs_reparse_count_is_zero_regardless_of_size(self) -> None:
        for size in (100, 1000):
            with self.subTest(events_per_log=size):
                assert_bounded_count(
                    self._read_count_on_unchanged_second_pass(size),
                    0,
                    label=f"lifecycle-log re-reads on unchanged tick (n={size})",
                )

    def test_heartbeat_sidecar_refreshes_merged_view_without_reparsing_cached_log(self) -> None:
        """B2/F7: a heartbeat update changes the merged projection view but not the cached
        events.jsonl parse count."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = EventStore(root)
            store.append(
                Event(
                    id="started",
                    ts=NOW.isoformat(),
                    kind="lifecycle.started",
                    trust="declared",
                    actor="model",
                    lifecycleId="life-1",
                    data={"fleeting": True},
                )
            )
            projection_store._lifecycle_log_cache.clear()
            reads = {"count": 0}
            original = EventStore.read_log

            def counting_read_log(self, lifecycle_id, _orig=original):  # type: ignore[no-untyped-def]
                reads["count"] += 1
                return _orig(self, lifecycle_id)

            EventStore.read_log = counting_read_log  # type: ignore[assignment]
            try:
                first = projection_store.read_lifecycle_logs(root)
                self.assertEqual(reads["count"], 1)
                store.append(
                    Event(
                        id="beat-1",
                        ts=(NOW + timedelta(seconds=15)).isoformat(),
                        kind="lifecycle.heartbeat",
                        trust="observed",
                        actor="system",
                        lifecycleId="life-1",
                        data={"state": "running", "phase": "build"},
                    )
                )
                second = projection_store.read_lifecycle_logs(root)
            finally:
                EventStore.read_log = original  # type: ignore[assignment]
                projection_store._lifecycle_log_cache.clear()

            self.assertEqual([event.id for event in first[0]], ["started"])
            self.assertEqual([event.id for event in second[0]], ["started", "beat-1"])
            assert_bounded_count(
                reads["count"], 1, label="event-log parses across heartbeat sidecar update"
            )


class TaskDocumentsPayloadBudgetTests(unittest.TestCase):
    """F6/CS-6 D2+D1: always-on task-document payloads are bounded summaries; full bodies are
    served through the on-demand reader path.
    """

    def _doc_node(self, index: int, body_len: int) -> TaskDocNode:
        return TaskDocNode(
            id=f"L{index}", repository="repo", title="t", status="planning", kind="light",
            docPath=f"tasks/repo/leaf-{index}.json", objective="x" * body_len,
        )

    def _write_doc(self, coordination_root: Path, index: int, body_len: int) -> Path:
        task_dir = coordination_root / "tasks" / "repo" / "master"
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / f"leaf-{index:04d}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": TASK_DOCUMENT_SCHEMA,
                    "kind": "subTask",
                    "id": f"L{index}",
                    "slug": f"leaf-{index:04d}",
                    "title": f"Leaf {index}",
                    "repo": "repo",
                    "status": "inProgress",
                    "createdAt": NOW.isoformat(),
                    "objective": "x" * body_len,
                    "requirements": ["r" * body_len],
                    "design": "d" * body_len,
                    "sections": [{"heading": "Body", "body": "s" * body_len}],
                    "codeExamples": [
                        {
                            "id": "ex",
                            "title": "Example",
                            "distinctChange": "c" * body_len,
                            "why": "w" * body_len,
                            "snippet": "p" * body_len,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_master(self, coordination_root: Path, body_len: int) -> Path:
        task_dir = coordination_root / "tasks" / "repo" / "master"
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / "task.json"
        path.write_text(
            json.dumps(
                {
                    "schema": TASK_DOCUMENT_SCHEMA,
                    "kind": "master",
                    "id": "MASTER",
                    "slug": "master",
                    "title": "Master",
                    "repo": "repo",
                    "status": "inProgress",
                    "createdAt": NOW.isoformat(),
                    "objective": "m" * body_len,
                    "sections": [{"heading": "Long", "body": "s" * body_len}],
                    "decisions": [{"at": NOW.isoformat(), "decision": "d" * body_len, "rationale": "r"}],
                }
            ),
            encoding="utf-8",
        )
        return path

    def _projection(self, docs: list[TaskDocNode]) -> WorkspaceProjection:
        return WorkspaceProjection(
            generatedAt=NOW.isoformat(), analytics=Analytics(taskDocuments=docs)
        )

    def test_task_document_broadcast_summaries_are_body_free_and_windowed_at_two_sizes(self) -> None:
        body_len = 1024
        for count in (TASK_DOCUMENT_SUMMARY_LIMIT + 20, TASK_DOCUMENT_SUMMARY_LIMIT + 200):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                coordination_root = Path(tmp)
                for index in range(count):
                    self._write_doc(coordination_root, index, body_len)

                docs = read_task_documents(coordination_root, enclosures=[], now=NOW)

                assert_bounded_count(
                    len(docs), TASK_DOCUMENT_SUMMARY_LIMIT, label=f"task doc summaries n={count}"
                )
                self.assertTrue(docs)
                self.assertEqual(task_documents_body_bytes(docs), 0)
                self.assertTrue(all(doc.bodyRevision for doc in docs))
                self.assertTrue(all(doc.objective == "" for doc in docs))
                self.assertTrue(all(doc.requirements == [] for doc in docs))
                self.assertTrue(all(doc.sections == [] for doc in docs))

    def test_series_broadcast_summaries_are_body_free(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp)
            self._write_master(coordination_root, body_len=4096)

            [series] = read_series_documents(coordination_root, now=NOW)

            self.assertEqual(series.objective, "")
            self.assertEqual(series.sections, [])
            self.assertEqual(series.decisions, [])

    def test_on_demand_task_document_body_returns_full_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coordination_root = Path(tmp)
            path = self._write_doc(coordination_root, 1, body_len=512)

            body = read_task_document_body(
                coordination_root,
                doc_path=path.as_posix(),
                enclosures=[],
                now=NOW,
            )

            self.assertIsNotNone(body)
            assert body is not None
            self.assertGreater(len(body.objective), 0)
            self.assertGreater(len(body.requirements[0]), 0)
            self.assertGreater(len(body.sections[0].body), 0)

    def test_guardrail_fires_over_budget_and_is_silent_under_it(self) -> None:
        projection_store._last_task_payload_warn.clear()
        under = self._projection([self._doc_node(0, 100)])
        # Enough 4 KiB-body docs to blow past the 256 KiB budget.
        over_docs = [self._doc_node(i, 4096) for i in range(100)]
        over = self._projection(over_docs)

        with self.assertNoLogs(projection_store.logger, level="WARNING"):
            measured_under = projection_store._warn_if_task_documents_payload_over_budget(under, now=NOW)
        self.assertLessEqual(measured_under, TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES)

        with self.assertLogs(projection_store.logger, level="WARNING") as captured:
            measured_over = projection_store._warn_if_task_documents_payload_over_budget(over, now=NOW)
        self.assertGreater(measured_over, TASK_DOCUMENTS_PAYLOAD_BUDGET_BYTES)
        self.assertIn("taskDocuments", captured.output[0])

    def test_over_budget_warning_is_rate_limited_across_ticks(self) -> None:
        projection_store._last_task_payload_warn.clear()
        over = self._projection([self._doc_node(i, 4096) for i in range(100)])
        with self.assertLogs(projection_store.logger, level="WARNING"):
            projection_store._warn_if_task_documents_payload_over_budget(over, now=NOW)
        # A second tick 1s later must NOT re-log (the 1s projection cadence would otherwise spam).
        with self.assertNoLogs(projection_store.logger, level="WARNING"):
            projection_store._warn_if_task_documents_payload_over_budget(
                over, now=NOW + timedelta(seconds=1)
            )
        # Past the warn interval it may surface again.
        with self.assertLogs(projection_store.logger, level="WARNING"):
            projection_store._warn_if_task_documents_payload_over_budget(
                over, now=NOW + timedelta(seconds=400)
            )


class ContractSnapshotSharedPassTests(unittest.TestCase):
    """260712-PTS-L2: one shared contract pass per tick with a stat-identity parse cache.

    The projection previously enumerated + re-parsed EVERY leaf enclosure contract three
    times per tick (read_enclosures, read_engine_process_facts, drift-snapshot pruning).
    These prove the shared ``ContractSnapshot`` performs one pass per tick (R1), that the
    cross-tick cache parses only changed files (R2/R7), that retention is bounded to the
    live contract set (R3), and that reader outputs are behavior-identical (R4).
    """

    def _seed_contracts(self, coordination_root: Path, count: int) -> list[WorktreeContract]:
        contracts: list[WorktreeContract] = []
        for index in range(count):
            contract = default_contract(
                task_name=f"scaling task {index}",
                repo_name="repo",
                workflow_kind="light-task",
                memory_mode="disabled",
                coordination_root=coordination_root,
                code_repo_path=coordination_root / "repo",
                code_source_branch="main",
                code_work_branch=f"ar/leaf-{index}",
                code_base_commit="0" * 40,
                worktree_name=f"leaf-{index}",
                lifecycle_id=f"LC-{index}",
            )
            contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
            write_contract(contract.contract_path, contract)
            contracts.append(contract)
        return contracts

    def _seed_malformed_contract(self, coordination_root: Path) -> Path:
        path = (
            coordination_root
            / "tasks"
            / "repo"
            / "broken-task"
            / ENCLOSURES_DIR
            / "broken-leaf"
            / SERIES_CONTRACT_FILENAME
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("this is not a worktree contract\n", encoding="utf-8")
        return path

    @contextlib.contextmanager
    def _counting_parses(self) -> Iterator[list[Path]]:
        """Count load_contract calls made by the snapshot builder (its only parse site)."""
        calls: list[Path] = []
        original = contract_snapshot.load_contract

        def counting(path: Path) -> WorktreeContract:
            calls.append(path)
            return original(path)

        with patch.object(contract_snapshot, "load_contract", counting):
            yield calls

    @staticmethod
    def _bump_mtime(path: Path) -> None:
        """Force a stat-identity change even on coarse-timestamp filesystems."""
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    def test_parse_counts_n_then_zero_then_exactly_the_changed_file(self) -> None:
        """R7/R2: N parses on tick 1, zero on an unchanged tick 2, only the change on tick 3."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            contracts = self._seed_contracts(root, 5)
            tasks_root = root / "tasks"
            cache = ContractSnapshotCache()

            with self._counting_parses() as calls:
                first = cache.build(tasks_root)
            self.assertEqual(len(first.contracts), 5)
            self.assertEqual(len(calls), 5)  # tick 1: every contract parsed once, not 3x

            with self._counting_parses() as calls:
                second = cache.build(tasks_root)
            assert_bounded_count(len(calls), 0, label="unchanged-tick contract parses")
            self.assertEqual(dict(first.contracts), dict(second.contracts))

            changed = contracts[2]
            write_contract(changed.contract_path, replace(changed, lifecycle_id="LC-CHANGED"))
            self._bump_mtime(changed.contract_path)
            with self._counting_parses() as calls:
                third = cache.build(tasks_root)
            self.assertEqual(calls, [changed.contract_path])  # tick 3: exactly the changed file
            self.assertEqual(third.contracts[changed.contract_path].lifecycle_id, "LC-CHANGED")

    def test_full_projection_tick_enumerates_once_and_reparses_nothing_unchanged(self) -> None:
        """R1: project_and_write drives ONE enumeration per tick for all three consumers."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp).resolve()
            coord = tmp_path / "coord"
            coord.mkdir(parents=True)
            self._seed_contracts(coord, 4)
            config = McpRuntimeConfig(
                config_path=coord / "mcp.settings.json",
                coordination_root=coord,
                workspace_root=tmp_path / "ws",
                transcript_root=coord / "logs",
            )
            snapshots._task_doc_cache.clear()
            projection_store._repo_surface_cache.clear()
            self.addCleanup(snapshots._task_doc_cache.clear)
            self.addCleanup(projection_store._repo_surface_cache.clear)
            walks = {"count": 0}
            original_iter = contract_snapshot.iter_leaf_enclosure_contracts

            def counting_iter(tasks_root: Path) -> Iterator[Path]:
                walks["count"] += 1
                return original_iter(tasks_root)

            with (
                patch.object(projection_store, "_contract_snapshot_cache", ContractSnapshotCache()),
                patch.object(contract_snapshot, "iter_leaf_enclosure_contracts", counting_iter),
                self._counting_parses() as calls,
            ):
                projection_store.project_and_write(config, now=NOW)
                first_walks, first_parses = walks["count"], len(calls)
                projection_store.project_and_write(config, now=NOW + timedelta(seconds=1))
                second_walks = walks["count"] - first_walks
                second_parses = len(calls) - first_parses

            assert_bounded_count(first_walks, 1, label="tick-1 contract enumerations")
            self.assertEqual(first_parses, 4)  # one parse per contract on the cold tick
            assert_bounded_count(second_walks, 1, label="tick-2 contract enumerations")
            assert_bounded_count(second_parses, 0, label="tick-2 contract re-parses")

    def test_reader_outputs_equal_with_and_without_shared_snapshot(self) -> None:
        """R4: enclosure rows, engine facts, and prune keys are identical in both modes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            contracts = self._seed_contracts(root, 3)
            contracts[0].code_worktree.mkdir(parents=True)  # one live worktree for prune keys
            broken = self._seed_malformed_contract(root)
            tasks_root = root / "tasks"
            cache = ContractSnapshotCache()
            cold = cache.build(tasks_root)
            warm = cache.build(tasks_root)  # cache-hit build must be output-identical too

            self.assertEqual(len(cold.contracts), 3)
            self.assertIn(broken, cold.skipped)  # malformed: skipped, never fatal (as before)

            standalone_enclosures = read_enclosures(root)
            self.assertEqual(standalone_enclosures, read_enclosures(root, contracts=cold))
            self.assertEqual(standalone_enclosures, read_enclosures(root, contracts=warm))

            standalone_facts = read_engine_process_facts(root)
            self.assertEqual(standalone_facts, read_engine_process_facts(root, contracts=cold))
            self.assertEqual(standalone_facts, read_engine_process_facts(root, contracts=warm))

            standalone_keys = drift_snapshots._active_worktree_snapshot_keys(root)
            self.assertEqual(
                standalone_keys,
                drift_snapshots._active_worktree_snapshot_keys(root, contracts=cold),
            )
            self.assertEqual(
                standalone_keys,
                {(contracts[0].code_worktree.name, contracts[0].code_work_branch)},
            )

    def test_cache_drops_entries_for_contracts_that_left_the_enumeration(self) -> None:
        """R3: retention is bounded to the live path set across task churn."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            contracts = self._seed_contracts(root, 3)
            tasks_root = root / "tasks"
            cache = ContractSnapshotCache()
            cache.build(tasks_root)
            self.assertEqual(len(cache._entries), 3)

            contracts[1].contract_path.unlink()
            snapshot = cache.build(tasks_root)
            self.assertEqual(len(snapshot.contracts), 2)
            self.assertNotIn(contracts[1].contract_path, cache._entries)
            self.assertEqual(len(cache._entries), 2)

    def test_malformed_contract_is_retried_every_build_not_cached(self) -> None:
        """R2 containment: a parse failure degrades exactly as before -- skip + retry next tick."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._seed_contracts(root, 1)
            broken = self._seed_malformed_contract(root)
            tasks_root = root / "tasks"
            cache = ContractSnapshotCache()
            with self._counting_parses() as calls:
                first = cache.build(tasks_root)
                second = cache.build(tasks_root)
            self.assertIn(broken, first.skipped)
            self.assertIn(broken, second.skipped)
            # The broken file is re-attempted each build (a transient error self-heals);
            # the healthy contract is parsed exactly once across both builds.
            self.assertEqual(calls.count(broken), 2)
            self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
