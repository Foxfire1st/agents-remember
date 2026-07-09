"""CS-6 projection hot-path scaling regressions (260707-HFX2-L12 fix round 2).

The projection tick (`project_and_write`, 1s) previously re-walked/re-read the whole task tree twice,
double-folded every gate log, shelled out to git per leaf, and re-parsed every lifecycle log — all per
tick. These prove the caches/single-folds landed for F8/F9/F10/F11 bound that per-tick cost.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _scaling import assert_bounded_count
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.observer import projection_store, snapshots
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
    read_gates,
    read_series_documents,
    read_task_document_body,
    read_task_documents,
)
from agents_remember.observer.store import EventStore

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
        original = snapshots.status_payload

        def fake_status(_contract):  # type: ignore[no-untyped-def]
            calls["count"] += 1
            return {"ok": True}

        snapshots.status_payload = fake_status  # type: ignore[assignment]
        snapshots._status_payload_cache.clear()
        try:
            snapshots._safe_status_payload("c", cache_key="leaf-1", now=NOW)
            snapshots._safe_status_payload("c", cache_key="leaf-1", now=NOW + timedelta(seconds=1))
            snapshots._safe_status_payload("c", cache_key="leaf-1", now=NOW + timedelta(seconds=30))
        finally:
            snapshots.status_payload = original  # type: ignore[assignment]
        # 1 cold probe + 1 refresh past the 8s TTL; the within-TTL call reused the cache.
        assert_bounded_count(calls["count"], 2, label="git status probes across 3 ticks")


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
        original = EventStore.read

        def counting_read(self, lifecycle_id, _orig=original):  # type: ignore[no-untyped-def]
            reads["count"] += 1
            return _orig(self, lifecycle_id)

        EventStore.read = counting_read  # type: ignore[assignment]
        try:
            projection_store.read_lifecycle_logs(root)  # second pass, logs unchanged
        finally:
            EventStore.read = original  # type: ignore[assignment]
        return reads["count"]

    def test_unchanged_logs_reparse_count_is_zero_regardless_of_size(self) -> None:
        for size in (100, 1000):
            with self.subTest(events_per_log=size):
                assert_bounded_count(
                    self._read_count_on_unchanged_second_pass(size),
                    0,
                    label=f"lifecycle-log re-reads on unchanged tick (n={size})",
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


if __name__ == "__main__":
    unittest.main()
