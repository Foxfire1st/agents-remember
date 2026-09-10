"""Behavioral proof for the terminal catalog's post-commit deferred work.

These cases exercise the R28 boundary at the real ``TerminalCatalog`` and
``TerminalCatalogLivenessSweeper`` seams.  The observer is deliberately a
small stand-in for the hosted-interaction store: it records whether the
catalog batch has committed before the downstream side effect runs.
"""

from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.models.conversations.control_wire import (
    ActivityState,
    AdapterSnapshot,
    ControlIdentity,
    ControlState,
)
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_evidence import TerminalEvidenceRead
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    TerminalLivenessActions,
    TerminalLivenessObservation,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)

_HYSTERESIS = TerminalCatalogLivenessConfig(
    failure_threshold=3,
    minimum_failure_window_seconds=5.0,
    pane_gone_failure_threshold=1,
    sweep_interval_seconds=10.0,
)


def _entry(session_id: str, *, control_state: ControlState = "ready") -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-08-31T00:00:00+00:00",
        last_attached_at="2026-08-31T00:00:00+00:00",
        status="running",
        control_state=control_state,
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
        control_activity="idle",
        control_acceptance="immediate",
    )


def _snapshot(
    entry: TerminalCatalogEntry,
    *,
    activity: ActivityState = "running",
) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity=activity,
        acceptance="immediate",
        vendor_session_id="vendor-1",
        raw={"source": "deferred-work-test"},
    )


class _LiveHost:
    def get(self, _session_id: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name.startswith("ar-")

    def probe_session(self, _tmux_name: str) -> TmuxProbeResult:
        return TmuxProbeResult(exists=True, evidence="alive")


def _empty_terminal_read(_entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
    return TerminalEvidenceRead(projection=None)


class TerminalLivenessDeferredWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self._root = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _sweeper(
        self,
        catalog: TerminalCatalog,
        *,
        snapshot_reader,
        on_control_snapshot=None,
        actions: TerminalLivenessActions | None = None,
    ) -> TerminalCatalogLivenessSweeper:
        return TerminalCatalogLivenessSweeper(
            catalog,
            _LiveHost(),
            now=lambda: NOW,
            probe=LivenessProbe(
                hysteresis=_HYSTERESIS,
                pane_capturer=lambda _tmux_name: "",
                snapshot_reader=snapshot_reader,
                terminal_reader=_empty_terminal_read,
                on_control_snapshot=on_control_snapshot,
            ),
            actions=actions,
        )

    def _assert_committed_row(self, catalog: TerminalCatalog, session_id: str) -> None:
        row = catalog.get(session_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.control_activity, "running")
        self.assertEqual(row.turn_state, "working")

    def test_full_sweep_commits_before_registration_compaction_sync_and_callback(self) -> None:
        catalog = TerminalCatalog(self._root / "full.json")
        catalog.upsert(_entry("full"))
        events: list[str] = []

        def register(_rows: tuple[TerminalCatalogEntry, ...]) -> frozenset[str]:
            events.append("register")
            self.assertIsNone(catalog._batch)
            self._assert_committed_row(catalog, "full")
            return frozenset()

        def compact(*, now: datetime, registered_execution_ids: frozenset[str]) -> int:
            events.append("compact")
            self.assertIsNone(catalog._batch)
            return real_compact(now=now, registered_execution_ids=registered_execution_ids)

        def sync(entry: TerminalCatalogEntry, _snapshot: AdapterSnapshot) -> None:
            events.append("sync")
            self.assertIsNone(catalog._batch)
            self._assert_committed_row(catalog, entry.id)

        def callback(observation: TerminalLivenessObservation) -> None:
            events.append("callback")
            self.assertIsNone(catalog._batch)
            self.assertEqual(observation.entry.id, "full")
            self._assert_committed_row(catalog, observation.entry.id)

        def recording_batch(self: TerminalCatalog) -> Iterator[None]:
            events.append("batch-enter")
            with real_batch(self):
                yield
            events.append("batch-exit")

        real_batch = TerminalCatalog.batch
        real_compact = catalog.compact
        sweeper = self._sweeper(
            catalog,
            snapshot_reader=_snapshot,
            on_control_snapshot=sync,
            actions=TerminalLivenessActions(
                on_turn_state_change=callback,
                register_execution_evidence=register,
            ),
        )
        with (
            mock.patch.object(TerminalCatalog, "batch", contextlib.contextmanager(recording_batch)),
            mock.patch.object(catalog, "compact", side_effect=compact),
        ):
            sweeper.refresh()

        self.assertEqual(
            events,
            ["batch-enter", "batch-exit", "register", "compact", "sync", "callback"],
        )

    def test_starting_sweep_commits_before_sync_and_callback(self) -> None:
        catalog = TerminalCatalog(self._root / "starting.json")
        catalog.upsert(_entry("starting", control_state="starting"))
        events: list[str] = []

        def sync(entry: TerminalCatalogEntry, _snapshot: AdapterSnapshot) -> None:
            events.append("sync")
            self.assertIsNone(catalog._batch)
            self._assert_committed_row(catalog, entry.id)

        def callback(observation: TerminalLivenessObservation) -> None:
            events.append("callback")
            self.assertIsNone(catalog._batch)
            self._assert_committed_row(catalog, observation.entry.id)

        def recording_batch(self: TerminalCatalog) -> Iterator[None]:
            events.append("batch-enter")
            with real_batch(self):
                yield
            events.append("batch-exit")

        real_batch = TerminalCatalog.batch
        sweeper = self._sweeper(
            catalog,
            snapshot_reader=_snapshot,
            on_control_snapshot=sync,
            actions=TerminalLivenessActions(on_turn_state_change=callback),
        )
        # Enter the rate-limited branch without waiting for wall clock time. This
        # is the existing in-memory cadence seam, not a production behavior change.
        sweeper._last_sweep_at = NOW
        with mock.patch.object(
            TerminalCatalog, "batch", contextlib.contextmanager(recording_batch)
        ):
            sweeper.refresh()

        self.assertEqual(events, ["batch-enter", "batch-exit", "sync", "callback"])

    def test_aborted_batch_commits_partial_catalog_truth_and_skips_deferred_work(self) -> None:
        catalog = TerminalCatalog(self._root / "aborted.json")
        catalog.upsert(_entry("abort-1"))
        catalog.upsert(_entry("abort-2"))
        sync_calls: list[str] = []
        callback_calls: list[str] = []

        def snapshot_reader(entry: TerminalCatalogEntry) -> AdapterSnapshot:
            if entry.id == "abort-2":
                raise RuntimeError("snapshot failed")
            return _snapshot(entry)

        def sync(entry: TerminalCatalogEntry, _snapshot: AdapterSnapshot) -> None:
            sync_calls.append(entry.id)

        def callback(observation: TerminalLivenessObservation) -> None:
            callback_calls.append(observation.entry.id)

        sweeper = self._sweeper(
            catalog,
            snapshot_reader=snapshot_reader,
            on_control_snapshot=sync,
            actions=TerminalLivenessActions(on_turn_state_change=callback),
        )
        with self.assertRaisesRegex(RuntimeError, "snapshot failed"):
            sweeper.refresh()

        self._assert_committed_row(catalog, "abort-1")
        second = catalog.get("abort-2")
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.control_activity, "idle")
        self.assertEqual(sync_calls, [])
        self.assertEqual(callback_calls, [])

    def test_one_sync_failure_is_quarantined_and_drain_continues(self) -> None:
        catalog = TerminalCatalog(self._root / "quarantine.json")
        catalog.upsert(_entry("guard-bad"))
        catalog.upsert(_entry("guard-good"))
        observed: list[str] = []

        def sync(entry: TerminalCatalogEntry, _snapshot: AdapterSnapshot) -> None:
            observed.append(entry.id)
            if entry.id == "guard-bad":
                raise RuntimeError("poisoned sync")

        sweeper = self._sweeper(
            catalog,
            snapshot_reader=_snapshot,
            on_control_snapshot=sync,
        )
        sweeper.refresh()

        self.assertEqual(observed, ["guard-bad", "guard-good"])
        bad = catalog.get("guard-bad")
        good = catalog.get("guard-good")
        self.assertIsNotNone(bad)
        self.assertIsNotNone(good)
        assert bad is not None and good is not None
        self.assertEqual((bad.control_raw or {}).get("interactionSyncError"), "poisoned sync")
        self.assertNotIn("interactionSyncError", good.control_raw or {})
        self.assertEqual(good.control_activity, "running")

    def test_post_commit_failures_escape_without_rolling_back_durable_truth(self) -> None:
        cases = ("callback", "catalog-get", "quarantine-upsert")
        for case in cases:
            with self.subTest(case=case):
                catalog = TerminalCatalog(self._root / f"{case}.json")
                catalog.upsert(_entry(case))

                if case == "callback":

                    def callback(_observation: TerminalLivenessObservation) -> None:
                        raise RuntimeError("turn callback failed")

                    sweeper = self._sweeper(
                        catalog,
                        snapshot_reader=_snapshot,
                        actions=TerminalLivenessActions(on_turn_state_change=callback),
                    )
                    with self.assertRaisesRegex(RuntimeError, "turn callback failed"):
                        sweeper.refresh()
                elif case == "catalog-get":
                    sweeper = self._sweeper(
                        catalog,
                        snapshot_reader=_snapshot,
                        on_control_snapshot=lambda _entry, _snapshot: None,
                    )
                    real_get = catalog.get

                    def fail_get_after_commit(
                        session_id: str,
                        *,
                        _catalog: TerminalCatalog = catalog,
                        _real_get=real_get,
                    ) -> TerminalCatalogEntry | None:
                        if _catalog._batch is None:
                            raise RuntimeError("catalog get failed")
                        return _real_get(session_id)

                    with (
                        mock.patch.object(catalog, "get", side_effect=fail_get_after_commit),
                        self.assertRaisesRegex(RuntimeError, "catalog get failed"),
                    ):
                        sweeper.refresh()
                else:

                    def sync_failure(
                        _entry: TerminalCatalogEntry, _snapshot: AdapterSnapshot
                    ) -> None:
                        raise RuntimeError("poisoned sync")

                    sweeper = self._sweeper(
                        catalog,
                        snapshot_reader=_snapshot,
                        on_control_snapshot=sync_failure,
                    )
                    real_upsert = catalog.upsert

                    def fail_upsert_after_commit(
                        entry: TerminalCatalogEntry,
                        *,
                        _catalog: TerminalCatalog = catalog,
                        _real_upsert=real_upsert,
                    ) -> None:
                        if _catalog._batch is None:
                            raise RuntimeError("quarantine upsert failed")
                        _real_upsert(entry)

                    with (
                        mock.patch.object(catalog, "upsert", side_effect=fail_upsert_after_commit),
                        self.assertRaisesRegex(RuntimeError, "quarantine upsert failed"),
                    ):
                        sweeper.refresh()

                self._assert_committed_row(catalog, case)


if __name__ == "__main__":
    unittest.main()
