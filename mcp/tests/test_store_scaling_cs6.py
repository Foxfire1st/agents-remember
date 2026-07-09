"""CS-6 store scaling + reclamation regressions (260707-HFX2-L12 R7/R9).

Store-level proofs for the inline fixes this leaf landed, all inheriting the shared
``tests/_scaling`` helper so every future durable store gets the same floor:

- ``SupervisorSignalCooldownStore``: read-once snapshot (``records=``), a named
  ``compact()`` that bounds the on-disk log, and corrupt-line-tolerant ``read()``.
- ``ProviderMetricsStore.read_recent``: bounded tail read, not O(filesize).
- ``EventStore.read``: one torn line no longer poisons the projection tick.

The supervisor-sweep *integration* proofs (read-count per sweep, escalation budget,
expectation snapshot) live in ``test_supervisor.py`` next to the sweep harness.
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

from _scaling import assert_bounded_count, assert_bounded_file_size, assert_subquadratic
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.expectation_rows import (
    ExpectationRow,
    ExpectationRowStore,
    write_expectation_row,
)
from agents_remember.controlplane.orchestration_nudges import (
    OrchestrationNudgeRecord,
    OrchestrationNudgeStore,
)
from agents_remember.controlplane.supervisor_signals import (
    SupervisorSignalCooldownStore,
    SupervisorSignalRecord,
)
from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_TTL_SECONDS,
    compact_workspace_river,
    initial_event_offsets,
)
from agents_remember.observer.events import Event
from agents_remember.observer.served_store import ServedRecord, ServedStore
from agents_remember.observer.store import EventStore
from agents_remember.providers.degradation import ProviderDegradationStore
from agents_remember.providers.metrics import PROVIDER_METRICS_SCHEMA, ProviderMetricsStore
from agents_remember.serving.events import read_new_events
from agents_remember.serving.terminal import TmuxProbeResult
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
)
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
)

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


def _signal(record_id: str, *, ts: datetime, detail: str = "turn-state-stale") -> SupervisorSignalRecord:
    return SupervisorSignalRecord(
        id=record_id,
        ts=ts.isoformat(),
        targetAgentId="manager-1",
        targetLifecycleId=None,
        targetRole="manager",
        leafKey="repo/260707_master/leaf-1",
        findingKind="seat-liveness",
        detail=detail,
        deliveryState="delivered",
    )


class SupervisorSignalStoreScalingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SupervisorSignalCooldownStore(Path(self.tmp.name))

    def test_compact_reclaims_stale_records_and_bounds_file_at_two_sizes(self) -> None:
        """CS-6 D3: a named compactor drops records past the retention window and keeps the
        on-disk log bounded no matter how many stale rows accumulated (>= 2 sizes)."""

        def compacted_size(stale_count: int) -> float:
            store = SupervisorSignalCooldownStore(Path(self.tmp.name) / f"n{stale_count}")
            for index in range(stale_count):
                # Far outside the 900s window -> can never suppress a signal again.
                store.append(_signal(f"old-{index}", ts=NOW - timedelta(days=30, seconds=index)))
            for index in range(3):
                store.append(_signal(f"fresh-{index}", ts=NOW - timedelta(seconds=index)))
            removed, kept = store.compact(now=NOW, retain_seconds=900.0)
            self.assertEqual(removed, stale_count)
            self.assertEqual({r.id for r in kept}, {"fresh-0", "fresh-1", "fresh-2"})
            # The file must now hold only the 3 fresh rows, regardless of stale_count.
            assert_bounded_file_size(store.log_path(), 4000, label="supervisor-signals.jsonl")
            return store.log_path().stat().st_size

        # Post-compaction size is ~constant (only the 3 fresh rows survive) across a 10x seed.
        assert_subquadratic(compacted_size, [1000, 10000], label="post-compact signal-log bytes")

    def test_in_cooldown_with_snapshot_does_not_read_the_file(self) -> None:
        """CS-6 D2: the sweep passes its one-read snapshot, so per-finding cooldown checks are
        O(1) in-memory lookups and never re-parse the log."""
        snapshot = [_signal("recent", ts=NOW - timedelta(seconds=60))]
        reads = {"count": 0}
        original = self.store.read

        def counting_read() -> list[SupervisorSignalRecord]:
            reads["count"] += 1
            return original()

        self.store.read = counting_read  # type: ignore[method-assign]
        for _ in range(50):
            self.store.in_cooldown(
                target_agent_id="manager-1",
                target_lifecycle_id=None,
                target_role="manager",
                leaf_key="repo/260707_master/leaf-1",
                finding_kind="seat-liveness",
                detail="turn-state-stale",
                now=NOW,
                cooldown_seconds=900.0,
                records=snapshot,
            )
        assert_bounded_count(reads["count"], 0, label="signal-store reads across 50 cooldown checks")

    def test_read_is_corrupt_line_tolerant(self) -> None:
        """CS-6 D3 robustness: a torn/legacy line is skipped, not raised -- one bad append cannot
        freeze the supervisor sweep that folds this non-authoritative cooldown log."""
        self.store.append(_signal("good-1", ts=NOW))
        path = self.store.log_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"schema": "ar-supervisor-signal/v1", "id": "torn"\n')  # truncated JSON
        self.store.append(_signal("good-2", ts=NOW))
        records = self.store.read()
        self.assertEqual({r.id for r in records}, {"good-1", "good-2"})


class ProviderMetricsTailReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProviderMetricsStore(Path(self.tmp.name))
        self.store._root.mkdir(parents=True, exist_ok=True)

    def _seed(self, count: int, *, corrupt_first: bool = False) -> None:
        path = self.store.log_path
        with path.open("w", encoding="utf-8") as handle:
            if corrupt_first:
                handle.write("this-is-not-json{{{\n")
            for index in range(count):
                # ~pad each line past the tail block so the tail must span multiple reads.
                handle.write(
                    json.dumps(
                        {"schema": PROVIDER_METRICS_SCHEMA, "seq": index, "pad": "x" * 200}
                    )
                    + "\n"
                )

    def test_tail_returns_last_limit_rows_in_order_across_block_boundary(self) -> None:
        self._seed(2000)
        rows = self.store.read_recent(limit=50)
        self.assertEqual(len(rows), 50)
        self.assertEqual([row["seq"] for row in rows], list(range(1950, 2000)))

    def test_tail_ignores_corrupt_leading_content_it_never_reads(self) -> None:
        """A corrupt row at the FILE HEAD must not affect a small tail read -- proving the reader
        only touches the end of the log, not the whole (unbounded) file."""
        self._seed(2000, corrupt_first=True)
        rows = self.store.read_recent(limit=10)
        self.assertEqual([row["seq"] for row in rows], list(range(1990, 2000)))

    def test_read_recent_cost_is_flat_in_file_size(self) -> None:
        """CS-6 D2: a fixed-``limit`` tail read costs ~the same on a 1k-row and a 20k-row log; the
        old ``read_text().splitlines()`` was O(filesize) and would blow the sub-quadratic ceiling."""

        def cost(rows: int) -> None:
            store = ProviderMetricsStore(Path(self.tmp.name) / f"n{rows}")
            store._root.mkdir(parents=True, exist_ok=True)
            with store.log_path.open("w", encoding="utf-8") as handle:
                for index in range(rows):
                    handle.write(
                        json.dumps(
                            {"schema": PROVIDER_METRICS_SCHEMA, "seq": index, "pad": "x" * 200}
                        )
                        + "\n"
                    )
            for _ in range(20):
                store.read_recent(limit=120)

        assert_subquadratic(cost, [1000, 20000], repeat=3, label="metrics read_recent wall-clock")


class ExpectationRowSnapshotTests(unittest.TestCase):
    def test_mark_missed_with_snapshot_does_not_read_the_file(self) -> None:
        """CS-6 D2 (Z4b): the supervisor threads its one-read snapshot into per-finding
        mark_missed, so K missed-transitions in a sweep cost 0 additional full-file folds."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ExpectationRowStore(Path(tmp))
            rows = [
                write_expectation_row(
                    store,
                    row_id=f"exp-{index}",
                    now=NOW - timedelta(minutes=10),
                    kind="briefed-by",
                    sla_seconds=60.0,
                    source_id=f"seat-{index}",
                    subject_agent_id="worker-1",
                )
                for index in range(20)
            ]
            snapshot = store.current()
            reads = {"count": 0}
            original = store.read

            def counting_read() -> list:
                reads["count"] += 1
                return original()

            store.read = counting_read  # type: ignore[method-assign]
            for row in rows:
                store.mark_missed(row.id, now=NOW.isoformat(), current=snapshot)
            assert_bounded_count(reads["count"], 0, label="expectation reads across 20 marks")
            self.assertTrue(all(r.state == "missed" for r in store.current().values()))


class EventStoreToleranceTests(unittest.TestCase):
    def test_read_skips_one_torn_line_instead_of_poisoning_the_projection(self) -> None:
        """CS-6 D3: one malformed event line no longer raises out of EventStore.read (the read the
        shared projection tick folds), so a single torn append cannot freeze projection fleet-wide."""
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp))
            path = store.log_path("life-1")
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write to a lifecycle log: one good, one torn, one good.
            store.append(
                Event(
                    id="g1", ts=NOW.isoformat(), kind="test.b", trust="observed",
                    actor="system", data={}, lifecycleId="life-1",
                )
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"id": "torn", "ts": "oops"\n')  # truncated + bad ts
            store.append(
                Event(
                    id="g2", ts=NOW.isoformat(), kind="test.c", trust="observed",
                    actor="system", data={}, lifecycleId="life-1",
                )
            )
            events = store.read("life-1")
            self.assertEqual([event.id for event in events], ["g1", "g2"])


class ExpectationCompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _terminal(self, row_id: str, *, state: str, at: datetime) -> ExpectationRow:
        return ExpectationRow(
            id=row_id, ts=at.isoformat(), kind="briefed-by", state=state,  # type: ignore[arg-type]
            createdAt=(at - timedelta(minutes=5)).isoformat(), dueAt=at.isoformat(),
            sourceId=f"src-{row_id}",
            metAt=at.isoformat() if state == "met" else None,
            missedAt=at.isoformat() if state == "missed" else None,
        )

    def test_compact_drops_stale_terminal_keeps_pending_and_bounds_file(self) -> None:
        """F4/CS-6 D3: `pending` rows + terminal rows within the window survive; stale met/missed
        rows are reclaimed, so the file stays bounded regardless of accumulated history (>=2 sizes)."""

        def compacted_size(stale_count: int) -> float:
            store = ExpectationRowStore(Path(self.tmp.name) / f"n{stale_count}")
            for index in range(stale_count):
                store.append(self._terminal(f"old-{index}", state="met", at=NOW - timedelta(days=2)))
            for index in range(3):
                write_expectation_row(
                    store, row_id=f"pending-{index}", now=NOW - timedelta(minutes=1),
                    kind="briefed-by", sla_seconds=3600.0, source_id=f"seat-{index}",
                )
            store.append(self._terminal("recent", state="missed", at=NOW - timedelta(seconds=30)))
            removed, kept = store.compact(now=NOW)
            self.assertEqual(removed, stale_count)
            self.assertEqual(
                {r for r, row in kept.items() if row.state == "pending"},
                {"pending-0", "pending-1", "pending-2"},
            )
            self.assertIn("recent", kept)  # within retention window
            assert_bounded_file_size(store.log_path(), 4000, label="expectation-rows.jsonl")
            return store.log_path().stat().st_size

        assert_subquadratic(compacted_size, [1000, 10000], label="post-compact expectation bytes")


class ProviderMetricsCompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_compact_bounds_metrics_file_at_two_sizes(self) -> None:
        """F5/CS-6 D3: past its byte budget, the log is reclaimed to its newest `retain_rows` from a
        bounded tail, so on-disk size is ~constant regardless of accumulated rows (>=2 sizes)."""

        def compacted_size(rows: int) -> float:
            store = ProviderMetricsStore(Path(self.tmp.name) / f"n{rows}")
            store._root.mkdir(parents=True, exist_ok=True)
            with store.log_path.open("w", encoding="utf-8") as handle:
                for index in range(rows):
                    handle.write(
                        json.dumps(
                            {"schema": PROVIDER_METRICS_SCHEMA, "seq": index, "pad": "x" * 200}
                        )
                        + "\n"
                    )
            kept = store.compact(retain_rows=100, max_bytes=100 * 300)
            self.assertEqual(kept, 100)
            surviving = store.read_recent(limit=10_000)
            self.assertEqual(len(surviving), 100)
            self.assertEqual(surviving[-1]["seq"], rows - 1)  # newest survive
            assert_bounded_file_size(store.log_path, 100 * 400, label="metrics.jsonl")
            return store.log_path.stat().st_size

        assert_subquadratic(compacted_size, [1000, 20000], label="post-compact metrics bytes")


class ProviderDegradationCompactTests(unittest.TestCase):
    def test_compact_events_bounds_the_audit_log(self) -> None:
        """F5/CS-6 D3: the append-only degradation-events log gets a bounded cap."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ProviderDegradationStore(Path(tmp))
            for index in range(2000):
                store.append_event({"schema": "ar-provider-degradation-event/v1", "seq": index})
            dropped = store.compact_events(retain_rows=100)
            self.assertEqual(dropped, 1900)
            remaining = [
                line
                for line in store.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(remaining), 100)
            self.assertIn('"seq": 1999', remaining[-1])


class ToleranceBatchTests(unittest.TestCase):
    """F12: the remaining dashboard-tolerant readers skip a torn line instead of raising."""

    def test_attention_dismissal_read_tolerant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = AttentionDismissalStore(Path(tmp))
            store.dismiss(AttentionDismissalRecord(itemId="a1", dismissedAt=NOW.isoformat()))
            with store.log_path().open("a", encoding="utf-8") as handle:
                handle.write('{"schema": "ar-attention-dismissal/v1", "itemId":\n')
            store.dismiss(AttentionDismissalRecord(itemId="a2", dismissedAt=NOW.isoformat()))
            self.assertEqual({r.itemId for r in store.read()}, {"a1", "a2"})

    def test_orchestration_nudge_read_tolerant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OrchestrationNudgeStore(Path(tmp))
            store.append(
                OrchestrationNudgeRecord(
                    id="n1", ts=NOW.isoformat(), state="sent", reason="inactive", message="m"
                )
            )
            with store.log_path().open("a", encoding="utf-8") as handle:
                handle.write("not json at all\n")
            store.append(
                OrchestrationNudgeRecord(
                    id="n2", ts=NOW.isoformat(), state="sent", reason="inactive", message="m"
                )
            )
            self.assertEqual({r.id for r in store.read()}, {"n1", "n2"})

    def test_served_store_read_tolerant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ServedStore(Path(tmp))
            store.append("life-1", ServedRecord(kind="overview", path="a", hash="h1", ts=NOW.isoformat()))
            with store.log_path("life-1").open("a", encoding="utf-8") as handle:
                handle.write('{"kind": "overview", "path"\n')
            store.append("life-1", ServedRecord(kind="overview", path="b", hash="h2", ts=NOW.isoformat()))
            self.assertEqual({r.path for r in store.read("life-1")}, {"a", "b"})


class _DiskCountingCatalog(TerminalCatalog):
    """A catalog that counts genuine disk reads/writes (buffer ops are free) for the F1 sweep proof."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.disk_read_calls = 0
        self.disk_write_calls = 0

    def _read_disk(self) -> list[TerminalCatalogEntry]:
        self.disk_read_calls += 1
        return super()._read_disk()

    def _write_disk(self, entries: list[TerminalCatalogEntry]) -> None:
        self.disk_write_calls += 1
        super()._write_disk(entries)


def _running_harness_entry(session_id: str, *, status: str = "running") -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Terminal {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-07T00:00:00+00:00",
        last_attached_at="2026-07-07T00:00:00+00:00",
        status=status,  # type: ignore[arg-type]
    )


class _AllAliveHost:
    """A liveness host that reports every tmux session alive with no subprocess work."""

    def get(self, _sid: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return self.probe_session(tmux_name).exists

    def probe_session(self, _tmux_name: str) -> TmuxProbeResult:
        return TmuxProbeResult(exists=True, evidence="alive")


class TerminalCatalogSweepScalingTests(unittest.TestCase):
    """F1/CS-6 D2+D3: the liveness sweep and its reclamation are bounded in disk work, at >=2 sizes.

    Every RUNNING row exercises the mutator read-modify-write path (``record_liveness_probe`` +
    ``record_turn_state``) that used to re-read and rewrite the whole catalog file per row -- the
    O(n^2) disk signature. The ``batch()`` unit of work collapses that to one disk read + one disk
    write per sweep regardless of the row count; ``compact()`` reclaims aged ``terminated`` tombstones.
    """

    def _sweep_disk_ops(self, rows: int) -> tuple[int, int]:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = _DiskCountingCatalog(Path(tmp) / f"terminal-sessions-{rows}.json")
            with catalog.batch():  # seed once (the batch itself is what makes this O(n), not O(n^2))
                for index in range(rows):
                    catalog.upsert(_running_harness_entry(f"s{index:05d}"))
            sweeper = TerminalCatalogLivenessSweeper(
                catalog,
                _AllAliveHost(),
                now=lambda: NOW,
                config=TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0),
                pane_capturer=lambda _tmux_name: "(esc to interrupt)",
            )
            catalog.disk_read_calls = 0
            catalog.disk_write_calls = 0
            entries = sweeper.refresh()
            self.assertEqual(len(entries), rows)
            return catalog.disk_read_calls, catalog.disk_write_calls

    def test_sweep_disk_io_is_constant_regardless_of_row_count(self) -> None:
        for rows in (200, 2000):
            with self.subTest(rows=rows):
                disk_reads, disk_writes = self._sweep_disk_ops(rows)
                # One read (batch begin), at most one write (batch commit) -- independent of ``rows``.
                assert_bounded_count(disk_reads, 1, label=f"sweep disk reads (n={rows})")
                assert_bounded_count(disk_writes, 1, label=f"sweep disk writes (n={rows})")

    def test_sweep_wall_clock_is_subquadratic(self) -> None:
        assert_subquadratic(
            lambda n: float(sum(self._sweep_disk_ops(n))),
            (200, 2000),
            label="terminal-catalog sweep disk ops",
        )

    def test_compact_reclaims_aged_terminated_rows_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            catalog = TerminalCatalog(Path(tmp) / "terminal-sessions.json")
            old_at = (NOW - timedelta(days=3)).isoformat()
            recent_at = (NOW - timedelta(minutes=5)).isoformat()
            with catalog.batch():
                for index in range(500):
                    catalog.upsert(
                        _running_harness_entry(f"old-{index:04d}", status="terminated").with_status(
                            "terminated", at=old_at
                        )
                    )
                catalog.upsert(
                    _running_harness_entry("recent", status="terminated").with_status(
                        "terminated", at=recent_at
                    )
                )
                catalog.upsert(_running_harness_entry("live"))

            reclaimed = catalog.compact(now=NOW)

            self.assertEqual(reclaimed, 500)
            surviving = {entry.id for entry in catalog.list(include_terminated=True)}
            self.assertEqual(surviving, {"recent", "live"})
            # The always-re-read catalog file is bounded to the survivors, not the full tombstone history.
            assert_bounded_file_size(
                catalog.path, 4096, label="terminal catalog after tombstone reclamation"
            )
            # A second compaction is a no-op (nothing left aged out) -- idempotent reclamation.
            self.assertEqual(catalog.compact(now=NOW), 0)


class EventRiverCompactionTests(unittest.TestCase):
    """F3/CS-6 D3: the workspace event river is bounded on disk after compaction, with a reconnecting
    client seeing exactly the retained tail -- no lost, torn, or duplicated events across the rotation.
    """

    def _seed_river(self, root: Path, *, old_rows: int, recent_rows: int) -> list[str]:
        store = EventStore(root)
        old_ts = (NOW - timedelta(seconds=WORKSPACE_EVENT_TTL_SECONDS + 3600)).isoformat()
        recent_ts = (NOW - timedelta(minutes=5)).isoformat()
        for index in range(old_rows):
            store.append(
                Event(id=f"old-{index:06d}", ts=old_ts, kind="supervisor.redeliver",
                      trust="observed", actor="system", data={"n": index})
            )
        recent_ids: list[str] = []
        for index in range(recent_rows):
            event_id = f"recent-{index:04d}"
            recent_ids.append(event_id)
            store.append(
                Event(id=event_id, ts=recent_ts, kind="orchestration.nudge",
                      trust="observed", actor="system", data={"n": index})
            )
        return recent_ids

    def test_river_bounded_after_compaction_at_two_sizes(self) -> None:
        recent_rows = 40
        for old_rows in (1_000, 10_000):
            with self.subTest(old_rows=old_rows), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                recent_ids = self._seed_river(root, old_rows=old_rows, recent_rows=recent_rows)
                river = root / "workspace" / "events.jsonl"
                pre_size = river.stat().st_size

                reclaimed = compact_workspace_river(root, now=NOW)

                self.assertEqual(reclaimed, old_rows)
                # On-disk river is bounded by the RETAINED window (recent_rows), not the full history --
                # the same cap regardless of how many aged-out rows preceded it (D3 reclamation, @2 sizes).
                assert_bounded_file_size(river, 16_384, label=f"workspace river (old={old_rows})")
                self.assertLess(river.stat().st_size, pre_size)

                # Reconnecting-client property: a fresh connect resumes at the retained window and streams
                # exactly the recent events -- none lost, none duplicated, and every line parses (no torn
                # read from a mid-line byte offset). This is the F3 done-when across a rotation.
                offsets = initial_event_offsets(root, now=NOW)
                events, _ = read_new_events(root, offsets)
                streamed_ids = [json.loads(event.data)["id"] for event in events]
                self.assertEqual(streamed_ids, recent_ids)

    def test_compaction_is_noop_when_nothing_aged_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._seed_river(root, old_rows=0, recent_rows=25)
            river = root / "workspace" / "events.jsonl"
            before = river.read_bytes()
            self.assertEqual(compact_workspace_river(root, now=NOW), 0)
            # A no-op compaction never rewrites the file (byte-identical) -- so it can never perturb a
            # live client's byte offset when there is nothing to reclaim.
            self.assertEqual(river.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
