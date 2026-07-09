"""Tests for terminal catalog liveness hysteresis."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.serving.terminal import TmuxProbeResult, _tmux_probe_session
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    TerminalSessionStatus,
)
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
)


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


class _CountingCatalog(TerminalCatalog):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.read_calls = 0

    def _read(self) -> list[TerminalCatalogEntry]:
        self.read_calls += 1
        return super()._read()


class _TmuxSubprocessProbeHost:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _sid: str) -> None:
        return None

    def has_session(self, tmux_name: str) -> bool:
        return self.probe_session(tmux_name).exists

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        self.calls += 1
        return _tmux_probe_session(tmux_name)


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
            config=TerminalCatalogLivenessConfig(
                failure_threshold=3,
                minimum_failure_window_seconds=5.0,
                pane_gone_failure_threshold=1,
                sweep_interval_seconds=sweep_interval_seconds,
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

    def test_pane_gone_evidence_marks_exited_without_command_failure_window(self) -> None:
        self.catalog.upsert(_entry("gone"))
        host = _FakeHost(TmuxProbeResult(exists=False, evidence="pane-gone"))
        sweeper = self._sweeper(host)

        sweeper.refresh()

        entry = self.catalog.get("gone")
        assert entry is not None
        self.assertEqual(entry.status, "exited")
        self.assertEqual(entry.exit_evidence, "pane-gone")

    def test_non_missing_tmux_nonzero_stderr_uses_hysteresis(self) -> None:
        self.catalog.upsert(_entry("agent"))
        host = _TmuxSubprocessProbeHost()
        sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            config=TerminalCatalogLivenessConfig(
                failure_threshold=3,
                minimum_failure_window_seconds=5.0,
                pane_gone_failure_threshold=1,
                sweep_interval_seconds=0.0,
            ),
        )

        with mock.patch(
            "agents_remember.serving.terminal.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stderr="error connecting to tmux server"),
        ):
            sweeper.refresh()

        entry = self.catalog.get("agent")
        assert entry is not None
        self.assertEqual(entry.status, "running")
        self.assertEqual(entry.liveness_evidence, "tmux-command-failed")
        self.assertIsNone(entry.exit_evidence)

    def test_missing_session_stderr_uses_pane_gone_behavior(self) -> None:
        self.catalog.upsert(_entry("gone"))
        host = _TmuxSubprocessProbeHost()
        sweeper = TerminalCatalogLivenessSweeper(
            self.catalog,
            host,
            now=self.clock,
            config=TerminalCatalogLivenessConfig(
                failure_threshold=3,
                minimum_failure_window_seconds=5.0,
                pane_gone_failure_threshold=1,
                sweep_interval_seconds=0.0,
            ),
        )

        with mock.patch(
            "agents_remember.serving.terminal.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stderr="can't find session: ar-gone"),
        ):
            sweeper.refresh()

        entry = self.catalog.get("gone")
        assert entry is not None
        self.assertEqual(entry.status, "exited")
        self.assertEqual(entry.exit_evidence, "pane-gone")

    def test_alive_again_probe_clears_false_liveness_exit(self) -> None:
        self.catalog.upsert(_entry("agent"))
        host = _FakeHost(TmuxProbeResult(exists=False, evidence="tmux-command-failed"))
        sweeper = self._sweeper(host)

        sweeper.refresh()
        self.clock.advance(3)
        sweeper.refresh()
        self.clock.advance(3)
        sweeper.refresh()

        exited = self.catalog.get("agent")
        assert exited is not None
        self.assertEqual(exited.status, "exited")
        self.assertEqual(exited.exit_evidence, "tmux-command-failed")

        host.result = TmuxProbeResult(exists=True, evidence="alive")
        self.clock.advance(10)
        sweeper.refresh()

        healed = self.catalog.get("agent")
        assert healed is not None
        self.assertEqual(healed.status, "running")
        self.assertEqual(healed.liveness_failures, 0)
        self.assertIsNone(healed.exit_evidence)

    def test_fast_tick_respects_sweep_rate_limit(self) -> None:
        self.catalog.upsert(_entry("agent"))
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
        sweeper = self._sweeper(host, sweep_interval_seconds=30.0)

        sweeper.refresh()
        self.clock.advance(1)
        sweeper.refresh()
        self.clock.advance(1)
        sweeper.refresh()

        self.assertEqual(host.calls, 1)

    def test_landed_rows_do_not_add_per_row_sweep_probe_or_catalog_reads(self) -> None:
        def run_sweep(landed_count: int) -> tuple[int, int, int, int]:
            catalog = _CountingCatalog(self.tmp / f"terminal-sessions-{landed_count}.json")
            for index in range(landed_count):
                catalog.upsert(_entry(f"landed-{index:03d}", status="landed"))
            catalog.upsert(_entry("running"))
            host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
            captured: list[str] = []
            sweeper = TerminalCatalogLivenessSweeper(
                catalog,
                host,
                now=self.clock,
                config=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=0.0,
                ),
                pane_capturer=lambda tmux_name: captured.append(tmux_name) or "(esc to interrupt)",
            )

            catalog.read_calls = 0
            entries = sweeper.refresh()

            self.assertEqual(len(entries), landed_count + 1)
            self.assertEqual(sum(entry.status == "landed" for entry in entries), landed_count)
            self.assertEqual(captured, ["ar-running"])
            return host.calls, len(captured), catalog.read_calls, len(entries) - landed_count

        small = run_sweep(5)
        large = run_sweep(500)

        self.assertEqual(small, (1, 1, 3, 1))
        self.assertEqual(large, small)

    def test_overlapping_sweep_returns_current_catalog_without_second_probe(self) -> None:
        self.catalog.upsert(_entry("agent"))
        host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))
        host.entered = threading.Event()
        host.release = threading.Event()
        sweeper = self._sweeper(host)
        errors: list[BaseException] = []

        def refresh() -> None:
            try:
                sweeper.refresh()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=refresh)
        thread.start()
        self.assertTrue(host.entered.wait(timeout=5))

        self.assertEqual([entry.id for entry in sweeper.refresh()], ["agent"])
        self.assertEqual(host.calls, 1)

        host.release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]


if __name__ == "__main__":
    unittest.main()
