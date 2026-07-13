"""Tests for dashboard daemon supervision (260703 L2)."""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.cli import __main__ as cli_main
from agents_remember.cli import dashboard as cli_dashboard
from agents_remember.mcp.config import DashboardSettings, McpRuntimeConfig
from agents_remember.serving import daemon


def make_config(root: Path, *, auto_start: bool = False, port: int = 8765) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root / "ar-coordination",
        workspace_root=root / "workspace",
        transcript_root=root / "ar-coordination" / "logs" / "mcp",
        dashboard=DashboardSettings(auto_start=auto_start, port=port),
    )


def make_state(**overrides: object) -> daemon.DaemonState:
    base: dict = {
        "pid": 4242,
        "host": "127.0.0.1",
        "port": 8765,
        "version": "3.0.0rc1",
        "config_path": "/abs/settings.json",
        "log_path": "/abs/dashboard.log",
        "started_at": "2026-07-03T00:00:00+00:00",
    }
    base.update(overrides)
    return daemon.DaemonState(**base)


class StateFileTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = make_state()
            daemon.write_state(directory, state)
            self.assertEqual(daemon.read_state(directory), state)
            self.assertEqual([], list(directory.glob("*.tmp")))

    def test_state_file_uses_camel_case_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            daemon.write_state(directory, make_state())
            data = json.loads((directory / daemon.STATE_FILE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(data),
                ["configPath", "host", "logPath", "pid", "port", "startedAt", "version"],
            )

    def test_missing_and_malformed_states_read_as_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.assertIsNone(daemon.read_state(directory))
            path = directory / daemon.STATE_FILE_NAME
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(daemon.read_state(directory))
            path.write_text(json.dumps([1, 2]), encoding="utf-8")
            self.assertIsNone(daemon.read_state(directory))
            path.write_text(json.dumps({"pid": 1}), encoding="utf-8")
            self.assertIsNone(daemon.read_state(directory))

    def test_clear_state_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            daemon.write_state(directory, make_state())
            daemon.clear_state(directory)
            daemon.clear_state(directory)
            self.assertIsNone(daemon.read_state(directory))


class ProbeTests(unittest.TestCase):
    def test_probe_without_state_is_not_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(daemon.probe(Path(tmp)), (None, False))

    def test_probe_dead_pid_is_not_alive(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = make_state(pid=child.pid)
            daemon.write_state(directory, state)
            self.assertEqual(daemon.probe(directory), (state, False))

    def test_probe_live_pid_with_foreign_cmdline_is_stale(self) -> None:
        # pid reuse across reboots: liveness alone must not resurrect a foreign process.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = make_state(pid=1)  # init is alive but is no dashboard
            daemon.write_state(directory, state)
            with mock.patch.object(daemon, "_pid_is_dashboard", return_value=False):
                self.assertEqual(daemon.probe(directory), (state, False))

    def test_probe_live_dashboard_pid_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = make_state(pid=1)
            daemon.write_state(directory, state)
            with (
                mock.patch.object(daemon, "_pid_alive", return_value=True),
                mock.patch.object(daemon, "_pid_is_dashboard", return_value=True),
            ):
                self.assertEqual(daemon.probe(directory), (state, True))


def _spawn_sleeper(*, ignore_term: bool) -> subprocess.Popen[bytes]:
    handler = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else ""
    code = (
        "import signal, sys, time\n"
        f"{handler}"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)
    assert process.stdout is not None
    process.stdout.readline()  # handler installed before anyone signals
    return process


class StopTests(unittest.TestCase):
    def test_stop_without_state_is_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(daemon.stop(Path(tmp)), "not-running")

    def test_stop_stale_state_clears_it(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            daemon.write_state(directory, make_state(pid=child.pid))
            self.assertEqual(daemon.stop(directory), "not-running")
            self.assertIsNone(daemon.read_state(directory))

    def test_stop_terminates_a_cooperative_process(self) -> None:
        process = _spawn_sleeper(ignore_term=False)
        self.addCleanup(process.wait)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        # Registered so _wait_gone's reaping collects OUR child (a real daemon is
        # reparented to init; a test child would otherwise linger as a zombie).
        daemon._spawned.append(process)
        self.addCleanup(lambda: process in daemon._spawned and daemon._spawned.remove(process))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            daemon.write_state(directory, make_state(pid=process.pid))
            with mock.patch.object(daemon, "_pid_is_dashboard", return_value=True):
                self.assertEqual(daemon.stop(directory), "stopped")
            self.assertIsNone(daemon.read_state(directory))

    def test_stop_escalates_to_kill_when_term_is_ignored(self) -> None:
        process = _spawn_sleeper(ignore_term=True)
        self.addCleanup(process.wait)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        daemon._spawned.append(process)
        self.addCleanup(lambda: process in daemon._spawned and daemon._spawned.remove(process))
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            daemon.write_state(directory, make_state(pid=process.pid))
            with mock.patch.object(daemon, "_pid_is_dashboard", return_value=True):
                self.assertEqual(daemon.stop(directory, timeout=0.5), "killed")
            self.assertIsNone(daemon.read_state(directory))


class SpawnTests(unittest.TestCase):
    def test_spawn_launches_the_detached_cli_and_records_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            fake = mock.Mock()
            fake.pid = 777
            with mock.patch.object(daemon.subprocess, "Popen", return_value=fake) as popen:
                state = daemon.spawn(config, host="127.0.0.1", port=9100, version="9.9.9")
            command = popen.call_args.args[0]
            self.assertEqual(command[:4], [sys.executable, "-m", "agents_remember.cli", "dashboard"])
            self.assertIn("--config", command)
            self.assertEqual(command[command.index("--config") + 1], str(config.config_path))
            self.assertEqual(command[command.index("--port") + 1], "9100")
            self.assertEqual(command[command.index("--interval") + 1], "1.0")
            self.assertNotIn("--heartbeat", command)
            self.assertIn("--no-access-log", command)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(state.pid, 777)
            self.assertEqual(daemon.read_state(daemon.daemon_dir(config)), state)
            daemon._spawned.remove(fake)

    def test_spawn_forwards_an_explicit_heartbeat_to_the_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            fake = mock.Mock()
            fake.pid = 779
            with mock.patch.object(daemon.subprocess, "Popen", return_value=fake) as popen:
                daemon.spawn(config, host="127.0.0.1", port=9100, version="9.9.9", heartbeat=20.0)
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index("--heartbeat") + 1], "20.0")
            self.assertIn("--no-access-log", command)
            daemon._spawned.remove(fake)

    def test_spawn_rotates_the_previous_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            directory = daemon.daemon_dir(config)
            directory.mkdir(parents=True)
            log = directory / daemon.LOG_FILE_NAME
            log.write_text("old run\n", encoding="utf-8")
            fake = mock.Mock()
            fake.pid = 778
            with mock.patch.object(daemon.subprocess, "Popen", return_value=fake):
                daemon.spawn(config, host="127.0.0.1", port=9100, version="9.9.9")
            rotated = directory / "dashboard.log.1"
            self.assertEqual(rotated.read_text(encoding="utf-8"), "old run\n")
            daemon._spawned.remove(fake)


class EnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = make_config(Path(tmp.name))
        self.directory = daemon.daemon_dir(self.config)

    def test_absent_daemon_is_started(self) -> None:
        spawned = make_state(pid=101, port=9000, version="1.0")
        with (
            mock.patch.object(daemon, "spawn", return_value=spawned) as spawn,
            mock.patch.object(daemon, "_wait_ready", return_value=True),
            mock.patch.object(daemon, "stop") as stop,
        ):
            result = daemon.ensure(self.config, host="127.0.0.1", port=9000, version="1.0")
        self.assertEqual(result.action, "started")
        self.assertEqual(result.state, spawned)
        spawn.assert_called_once_with(
            self.config, host="127.0.0.1", port=9000, version="1.0", interval=1.0, heartbeat=None
        )
        stop.assert_not_called()

    def test_healthy_matching_daemon_is_adopted(self) -> None:
        current = make_state(pid=202, port=9000, version="1.0")
        with (
            mock.patch.object(daemon, "probe", return_value=(current, True)),
            mock.patch.object(daemon, "spawn") as spawn,
        ):
            result = daemon.ensure(self.config, host="127.0.0.1", port=9000, version="1.0")
        self.assertEqual(result.action, "adopted")
        self.assertEqual(result.state, current)
        spawn.assert_not_called()

    def test_version_mismatch_restarts(self) -> None:
        current = make_state(pid=303, port=9000, version="1.0")
        fresh = make_state(pid=304, port=9000, version="2.0")
        with (
            mock.patch.object(daemon, "probe", return_value=(current, True)),
            mock.patch.object(daemon, "stop") as stop,
            mock.patch.object(daemon, "spawn", return_value=fresh) as spawn,
            mock.patch.object(daemon, "_wait_ready", return_value=True),
        ):
            result = daemon.ensure(self.config, host="127.0.0.1", port=9000, version="2.0")
        self.assertEqual(result.action, "restarted")
        self.assertIn("version 1.0 -> 2.0", result.detail)
        stop.assert_called_once()
        spawn.assert_called_once()

    def test_port_mismatch_restarts(self) -> None:
        current = make_state(pid=305, port=9000, version="1.0")
        fresh = make_state(pid=306, port=9001, version="1.0")
        with (
            mock.patch.object(daemon, "probe", return_value=(current, True)),
            mock.patch.object(daemon, "stop") as stop,
            mock.patch.object(daemon, "spawn", return_value=fresh),
            mock.patch.object(daemon, "_wait_ready", return_value=True),
        ):
            result = daemon.ensure(self.config, host="127.0.0.1", port=9001, version="1.0")
        self.assertEqual(result.action, "restarted")
        self.assertIn("port 9000 -> 9001", result.detail)
        stop.assert_called_once()

    def test_child_dying_during_startup_fails_and_clears_state(self) -> None:
        def fake_spawn(config: McpRuntimeConfig, **kwargs: object) -> daemon.DaemonState:
            state = make_state(pid=407)
            daemon.write_state(daemon.daemon_dir(config), state)
            return state

        with (
            mock.patch.object(daemon, "spawn", side_effect=fake_spawn),
            mock.patch.object(daemon, "_wait_ready", return_value=False),
            mock.patch.object(daemon, "_pid_alive", return_value=False),
            mock.patch.object(daemon, "_log_tail", return_value="boom"),
        ):
            result = daemon.ensure(self.config, host="127.0.0.1", port=8765, version="1.0")
        self.assertEqual(result.action, "failed")
        self.assertIn("boom", result.detail)
        self.assertIsNone(daemon.read_state(self.directory))

    def test_slow_start_fails_but_keeps_state(self) -> None:
        def fake_spawn(config: McpRuntimeConfig, **kwargs: object) -> daemon.DaemonState:
            state = make_state(pid=408)
            daemon.write_state(daemon.daemon_dir(config), state)
            return state

        with (
            mock.patch.object(daemon, "spawn", side_effect=fake_spawn),
            mock.patch.object(daemon, "_wait_ready", return_value=False),
            mock.patch.object(daemon, "_pid_alive", return_value=True),
        ):
            result = daemon.ensure(self.config, host="127.0.0.1", port=8765, version="1.0")
        self.assertEqual(result.action, "failed")
        self.assertIn("may still be starting", result.detail)
        self.assertIsNotNone(daemon.read_state(self.directory))

    def test_held_lock_skips_without_spawning(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / daemon.LOCK_FILE_NAME).open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            try:
                with mock.patch.object(daemon, "spawn") as spawn:
                    result = daemon.ensure(self.config, host="127.0.0.1", port=8765)
            finally:
                fcntl.flock(held, fcntl.LOCK_UN)
        self.assertEqual(result.action, "lock-held")
        spawn.assert_not_called()


class AutostartTests(unittest.TestCase):
    def test_disabled_autostart_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), auto_start=False)
            with mock.patch.object(daemon, "ensure") as ensure:
                self.assertIsNone(daemon.maybe_autostart_dashboard(config))
            ensure.assert_not_called()

    def test_enabled_autostart_ensures_on_the_settings_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), auto_start=True, port=9321)
            outcome = daemon.EnsureResult(action="adopted", state=make_state(), detail="fine")
            stderr = io.StringIO()
            with (
                mock.patch.object(daemon, "ensure", return_value=outcome) as ensure,
                contextlib.redirect_stderr(stderr),
            ):
                worker = daemon.maybe_autostart_dashboard(config)
                self.assertIsNotNone(worker)
                assert worker is not None
                worker.join(timeout=10)
            ensure.assert_called_once_with(config, host="127.0.0.1", port=9321)
            self.assertIn("dashboard autostart: adopted", stderr.getvalue())

    def test_autostart_swallows_ensure_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp), auto_start=True)
            stderr = io.StringIO()
            with (
                mock.patch.object(daemon, "ensure", side_effect=RuntimeError("nope")),
                contextlib.redirect_stderr(stderr),
            ):
                worker = daemon.maybe_autostart_dashboard(config)
                assert worker is not None
                worker.join(timeout=10)
            self.assertIn("dashboard autostart: failed: nope", stderr.getvalue())


def _write_settings(root: Path, *, dashboard: dict | None = None) -> Path:
    (root / "ar-coordination").mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "version": 1,
        "coordinationRoot": str(root / "ar-coordination"),
        "workspaceRoot": str(root / "workspace"),
    }
    if dashboard is not None:
        payload["dashboard"] = dashboard
    path = root / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class CliDaemonDispatchTests(unittest.TestCase):
    def _run(self, root: Path, *flags: str) -> tuple[int, str]:
        settings = _write_settings(root) if not (root / "settings.json").exists() else (
            root / "settings.json"
        )
        args = cli_main.build_parser().parse_args(
            ["dashboard", "--config", str(settings), *flags]
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli_dashboard.run(args)
        return code, stdout.getvalue()

    def test_status_and_stop_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            settings = _write_settings(Path(tmp))
            with self.assertRaises(SystemExit):
                cli_main.build_parser().parse_args(
                    ["dashboard", "--config", str(settings), "--status", "--stop"]
                )

    def test_status_reports_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self._run(Path(tmp), "--status")
        self.assertEqual(code, 1)
        self.assertIn("not running", out)

    def test_status_reports_a_running_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = make_state(pid=515)
            with mock.patch.object(daemon, "probe", return_value=(state, True)):
                code, out = self._run(Path(tmp), "--status")
        self.assertEqual(code, 0)
        self.assertIn("pid 515", out)

    def test_stop_reports_the_outcome(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(daemon, "stop", return_value="stopped") as stop,
        ):
            code, out = self._run(Path(tmp), "--stop")
        self.assertEqual(code, 0)
        self.assertIn("stopped", out)
        stop.assert_called_once()

    def test_daemon_uses_the_settings_port_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_settings(root, dashboard={"port": 9555})
            outcome = daemon.EnsureResult(action="started", state=make_state(), detail="up")
            with mock.patch.object(daemon, "ensure", return_value=outcome) as ensure:
                code, out = self._run(root, "--daemon")
        self.assertEqual(code, 0)
        self.assertIn("started", out)
        self.assertEqual(ensure.call_args.kwargs["port"], 9555)

    def test_daemon_explicit_port_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_settings(root, dashboard={"port": 9555})
            outcome = daemon.EnsureResult(action="started", state=make_state(), detail="up")
            with mock.patch.object(daemon, "ensure", return_value=outcome) as ensure:
                code, _ = self._run(root, "--daemon", "--port", "9666")
        self.assertEqual(code, 0)
        self.assertEqual(ensure.call_args.kwargs["port"], 9666)

    def test_daemon_failure_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outcome = daemon.EnsureResult(action="failed", state=None, detail="no boot")
            with mock.patch.object(daemon, "ensure", return_value=outcome):
                code, out = self._run(Path(tmp), "--daemon")
        self.assertEqual(code, 1)
        self.assertIn("no boot", out)

    def test_daemon_with_sim_is_rejected(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(daemon, "ensure") as ensure,
        ):
            code, out = self._run(Path(tmp), "--daemon", "--sim", "/fixture")
        self.assertEqual(code, 1)
        self.assertIn("not supported", out)
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
