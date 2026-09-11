"""Tests for terminal catalog liveness hysteresis."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
    ActivityState,
    AdapterSnapshot,
    ControlIdentity,
    ControlState,
)
from agents_remember.models.terminal_catalog import (
    DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS,
    TerminalCatalogEntry,
    TerminalSessionStatus,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_evidence import TerminalEvidenceRead
from agents_remember.serving.terminal_liveness import (
    DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS,
    LivenessProbe,
    SnapshotReader,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult


def _entry(session_id: str, *, status: TerminalSessionStatus = "running") -> TerminalCatalogEntry:
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
        status=status,
    )


def _snapshot(
    entry: TerminalCatalogEntry,
    *,
    control: ControlState,
    activity: ActivityState,
    acceptance: AcceptanceState,
) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control=control,
        activity=activity,
        acceptance=acceptance,
        vendor_session_id="vendor-1",
        raw={},
    )


def _ready_snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    return _snapshot(entry, control="ready", activity="idle", acceptance="immediate")


@dataclass
class _Clock:
    moment: datetime

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


class _FakeHost:
    def __init__(self, result: TmuxProbeResult) -> None:
        self.result = result
        self.calls = 0
        self.entered: threading.Event | None = None
        self.release: threading.Event | None = None

    def get(self, _sid: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return self.probe_session(tmux_name).exists

    def probe_session(self, _tmux_name: str) -> TmuxProbeResult:
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=5)
        return self.result


class _RaisingHost(_FakeHost):
    def __init__(self, result: TmuxProbeResult) -> None:
        super().__init__(result)
        self.raise_next = True

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        if self.raise_next:
            self.raise_next = False
            self.calls += 1
            raise RuntimeError("probe failed")
        return super().probe_session(tmux_name)



class TerminalCatalogLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.clock = _Clock(datetime(2026, 7, 7, tzinfo=UTC))

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _sweeper(
        self,
        host: _FakeHost,
        *,
        sweep_interval_seconds: float = 0.0,
    ) -> TerminalCatalogLivenessSweeper:
        return TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=sweep_interval_seconds,
                )
            ),
        )

    def _starting_sweeper(
        self,
        host: _FakeHost,
        *,
        snapshot_reader: SnapshotReader = _ready_snapshot,
        catalog: TerminalCatalog | None = None,
    ) -> TerminalCatalogLivenessSweeper:
        return TerminalCatalogLivenessSweeper(
            catalog or self.catalog,
            host,
            now=self.clock,
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=10.0,
                ),
                pane_capturer=lambda _tmux_name: "",
                snapshot_reader=snapshot_reader,
            ),
        )

    def _control_sweeper(
        self,
        host: _FakeHost,
        *,
        snapshot_reader: SnapshotReader,
    ) -> TerminalCatalogLivenessSweeper:
        return TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=0.0,
                ),
                pane_capturer=lambda _tmux_name: "",
                snapshot_reader=snapshot_reader,
            ),
        )

    def test_transient_failure_storm_leaves_sessions_running_until_window_elapsed(self) -> None:
        for index in range(14):
            self.catalog.upsert(_entry(f"s{index:02d}"))
        host = _FakeHost(TmuxProbeResult(exists=False, evidence="tmux-command-failed"))
        sweeper = self._sweeper(host)

        for _ in range(3):
            sweeper.refresh()
            self.clock.advance(1)

        entries = self.catalog.list()
        self.assertEqual({entry.status for entry in entries}, {"running"})
        self.assertEqual({entry.liveness_failures for entry in entries}, {3})

        self.clock.advance(2)
        sweeper.refresh()
        entries = self.catalog.list()
        self.assertEqual({entry.status for entry in entries}, {"exited"})
        self.assertEqual({entry.liveness_failures for entry in entries}, {4})
        self.assertEqual({entry.exit_evidence for entry in entries}, {"tmux-command-failed"})

        self.catalog.upsert(_entry("pane-gone"))
        host.result = TmuxProbeResult(exists=False, evidence="pane-gone")
        sweeper.refresh()
        pane_gone = self.catalog.get("pane-gone")
        assert pane_gone is not None
        self.assertEqual(pane_gone.status, "exited")
        self.assertEqual(pane_gone.liveness_failures, 1)
        self.assertEqual(pane_gone.exit_evidence, "pane-gone")

    def test_full_sweep_rate_limit_is_preserved(self) -> None:
        self.catalog.upsert(_entry("full-sweep"))
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="tmux-live"))
        sweeper = self._sweeper(
            host,
            sweep_interval_seconds=DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS,
        )

        sweeper.refresh()
        self.assertEqual(host.calls, 1)

        self.clock.advance(DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS - 1)
        sweeper.refresh()
        self.assertEqual(host.calls, 1)

        self.clock.advance(1)
        sweeper.refresh()
        self.assertEqual(host.calls, 2)

    def test_starting_rows_use_one_second_fast_path_and_four_row_cap(self) -> None:
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="tmux-live"))
        sweeper = self._starting_sweeper(host)
        sweeper.refresh()
        self.assertEqual(host.calls, 0)

        starting = [
            replace(
                _entry(f"starting-{index}"),
                control_state="starting",
                control_endpoint=self.tmp / f"control-{index}.sock",
                control_activity="unknown",
                control_acceptance="unknown",
            )
            for index in range(5)
        ]
        for entry in starting:
            self.catalog.upsert(entry)

        self.clock.advance(DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS)
        sweeper.refresh()
        entries = {entry.id: entry for entry in self.catalog.list()}
        self.assertEqual(host.calls, 4)
        self.assertEqual(
            {entries[entry.id].control_state for entry in starting[:4]},
            {"ready"},
        )
        self.assertEqual(entries[starting[4].id].control_state, "starting")

        self.clock.advance(DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS / 2)
        sweeper.refresh()
        self.assertEqual(host.calls, 4)
        self.assertEqual(
            self.catalog.get(starting[4].id).control_state,
            "starting",
        )

        self.clock.advance(DEFAULT_STARTING_SWEEP_INTERVAL_SECONDS / 2)
        sweeper.refresh()
        self.assertEqual(host.calls, 5)
        self.assertEqual(self.catalog.get(starting[4].id).control_state, "ready")


    def test_host_failure_series_survives_restart_and_success_resets(self) -> None:
        self.catalog.upsert(_entry("host-restart"))
        host = _FakeHost(TmuxProbeResult(exists=False, evidence="tmux-command-failed"))
        probe = LivenessProbe(
            hysteresis=TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0),
            pane_capturer=lambda _tmux_name: "",
        )
        sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            probe=probe,
        )

        for _ in range(2):
            sweeper.refresh()
            self.clock.advance(1)

        restarted = TerminalCatalog(self.catalog.path)
        persisted = restarted.get("host-restart")
        assert persisted is not None
        assert persisted.liveness_first_failed_at is not None
        self.assertEqual(persisted.liveness_failures, 2)
        self.assertEqual(persisted.liveness_first_failed_at, "2026-07-07T00:00:00+00:00")

        host.result = TmuxProbeResult(exists=True, evidence="alive")
        TerminalCatalogLivenessSweeper(
            restarted,
            host,
            now=self.clock,
            probe=probe,
        ).refresh()
        recovered = restarted.get("host-restart")
        assert recovered is not None
        self.assertEqual(recovered.status, "running")
        self.assertEqual(recovered.liveness_failures, 0)
        self.assertIsNone(recovered.liveness_first_failed_at)
        self.assertIsNone(recovered.liveness_last_failed_at)
        self.assertIsNone(recovered.liveness_evidence)
        self.assertIsNone(recovered.exit_evidence)

    def test_connected_control_reads_require_three_strikes_across_restart_and_reset(self) -> None:
        connected = replace(
            _entry("connected"),
            control_state="ready",
            control_endpoint=Path("/tmp/connected.sock"),
            control_activity="idle",
            control_acceptance="immediate",
        )
        self.catalog.upsert(connected)
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))

        def failed_snapshot(_entry: TerminalCatalogEntry) -> AdapterSnapshot:
            raise HarnessControlError("bridge unavailable")

        config = TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0)
        failing_probe = LivenessProbe(
            hysteresis=config,
            pane_capturer=lambda _tmux_name: "",
            snapshot_reader=failed_snapshot,
        )
        sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            probe=failing_probe,
        )
        for expected_failures in (1, 2):
            sweeper.refresh()
            row = self.catalog.get("connected")
            assert row is not None
            assert row.control_raw is not None
            self.assertEqual(row.control_state, "ready")
            self.assertEqual(row.control_raw.get("controlReadFailures"), expected_failures)
            self.clock.advance(1)

        restarted = TerminalCatalog(self.catalog.path)
        persisted = restarted.get("connected")
        assert persisted is not None
        assert persisted.control_raw is not None
        self.assertEqual(persisted.control_raw.get("controlReadFailures"), 2)

        TerminalCatalogLivenessSweeper(
            restarted,
            host,
            now=self.clock,
            probe=failing_probe,
        ).refresh()
        disconnected = restarted.get("connected")
        assert disconnected is not None
        self.assertEqual(disconnected.control_state, "disconnected")
        self.assertEqual(disconnected.turn_state, "stale")
        assert disconnected.control_raw is not None
        self.assertEqual(disconnected.control_raw.get("controlReadFailures"), 3)

        ready_probe = LivenessProbe(
            hysteresis=config,
            pane_capturer=lambda _tmux_name: "",
            snapshot_reader=_ready_snapshot,
        )
        TerminalCatalogLivenessSweeper(
            restarted,
            host,
            now=self.clock,
            probe=ready_probe,
        ).refresh()
        recovered = restarted.get("connected")
        assert recovered is not None
        self.assertEqual(recovered.control_state, "ready")
        assert recovered.control_raw is not None
        self.assertNotIn("controlReadFailures", recovered.control_raw)

    def test_alive_starting_row_survives_delayed_bridge_reads(self) -> None:
        starting = replace(
            _entry("starting"),
            control_state="starting",
            control_endpoint=Path("/tmp/starting.sock"),
        )
        self.catalog.upsert(starting)
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
        bridge_ready = False

        def delayed_snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
            if not bridge_ready:
                raise HarnessControlError("bridge still booting")
            return _ready_snapshot(entry)

        sweeper = self._starting_sweeper(host, snapshot_reader=delayed_snapshot)
        for expected_failures in range(1, 5):
            sweeper.refresh()
            row = self.catalog.get("starting")
            assert row is not None
            assert row.control_raw is not None
            self.assertEqual(row.control_state, "starting")
            self.assertEqual(row.control_raw.get("controlReadFailures"), expected_failures)
            self.clock.advance(1)

        bridge_ready = True
        sweeper.refresh()
        recovered = self.catalog.get("starting")
        assert recovered is not None
        self.assertEqual(recovered.control_state, "ready")
        assert recovered.control_raw is not None
        self.assertNotIn("controlReadFailures", recovered.control_raw)
        self.assertEqual(host.calls, 5)


    def test_contended_full_sweep_returns_committed_snapshot_without_second_probe(self) -> None:
        self.catalog.upsert(replace(_entry("full"), kind="terminal", harness=None))
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
        host.entered = threading.Event()
        host.release = threading.Event()
        sweeper = self._sweeper(host)
        first_result: list[list[TerminalCatalogEntry]] = []
        first_errors: list[BaseException] = []

        def run_first() -> None:
            try:
                first_result.append(sweeper.refresh())
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                first_errors.append(exc)

        first = threading.Thread(target=run_first)
        first.start()
        assert host.entered is not None
        self.assertTrue(host.entered.wait(timeout=1))

        contender_result: list[list[TerminalCatalogEntry]] = []
        contender = threading.Thread(target=lambda: contender_result.append(sweeper.refresh()))
        contender.start()
        contender.join(timeout=0.25)
        try:
            self.assertFalse(contender.is_alive())
            self.assertEqual(host.calls, 1)
            self.assertEqual([entry.id for entry in contender_result[0]], ["full"])
        finally:
            assert host.release is not None
            host.release.set()
            first.join(timeout=1)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_errors, [])
        self.assertEqual([entry.id for entry in first_result[0]], ["full"])

        sweeper.refresh()
        self.assertEqual(host.calls, 2)

    def test_sweep_lock_releases_after_observation_exception_for_later_retry(self) -> None:
        self.catalog.upsert(replace(_entry("retry"), kind="terminal", harness=None))
        host = _RaisingHost(TmuxProbeResult(exists=True, evidence="alive"))
        sweeper = self._sweeper(host)

        with self.assertRaisesRegex(RuntimeError, "probe failed"):
            sweeper.refresh()

        self.assertEqual(host.calls, 1)
        self.assertEqual([entry.id for entry in sweeper.refresh()], ["retry"])
        self.assertEqual(host.calls, 2)

    def test_contended_starting_sweep_reads_committed_snapshot_before_any_catalog_list(
        self,
    ) -> None:
        self.catalog.upsert(replace(_entry("starting"), control_state="starting"))
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
        host.entered = threading.Event()
        host.release = threading.Event()
        sweeper = self._starting_sweeper(host)
        sweeper._last_sweep_at = self.clock.moment
        first_result: list[list[TerminalCatalogEntry]] = []
        first_errors: list[BaseException] = []

        def run_first() -> None:
            try:
                first_result.append(sweeper.refresh())
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                first_errors.append(exc)

        first = threading.Thread(target=run_first)
        first.start()
        assert host.entered is not None
        self.assertTrue(host.entered.wait(timeout=1))

        contender_result: list[list[TerminalCatalogEntry]] = []
        contender = threading.Thread(target=lambda: contender_result.append(sweeper.refresh()))
        contender.start()
        contender.join(timeout=0.25)
        try:
            self.assertFalse(contender.is_alive())
            self.assertEqual(host.calls, 1)
            self.assertEqual([entry.id for entry in contender_result[0]], ["starting"])
        finally:
            assert host.release is not None
            host.release.set()
            first.join(timeout=1)
        self.assertFalse(first.is_alive())
        self.assertEqual(first_errors, [])
        self.assertEqual([entry.id for entry in first_result[0]], ["starting"])

        self.clock.advance(2)
        sweeper.refresh()
        self.assertEqual(host.calls, 1)

    def test_repeated_clean_hosted_sweep_does_not_replace_catalog(self) -> None:
        entry = replace(
            _entry("hosted"),
            control_state="ready",
            control_endpoint=Path("/tmp/hosted.sock"),
            control_activity="idle",
            control_acceptance="immediate",
            control_vendor_session_id="vendor-1",
            control_raw={"paneDiagnostic": "stale"},
        )
        self.catalog.upsert(entry)
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
        sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=0.0,
                ),
                pane_capturer=lambda _tmux_name: "",
                snapshot_reader=_ready_snapshot,
                terminal_reader=lambda _entry: TerminalEvidenceRead(projection=None),
            ),
        )

        with patch.object(
            self.catalog, "_write_disk", wraps=self.catalog._write_disk
        ) as write_disk:
            sweeper.refresh()
            self.assertEqual(write_disk.call_count, 1)
            write_disk.reset_mock()
            sweeper.refresh()
            self.assertEqual(write_disk.call_count, 0)


if __name__ == "__main__":
    unittest.main()
