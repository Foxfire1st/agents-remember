"""Dashboard supervision follows the code and process actually answering its port."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import io
import json
import struct
import subprocess
import urllib.error
from dataclasses import replace
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from agents_remember.cli import dashboard_daemon
from agents_remember.kernel.primitives.runtime_config import ConfigError
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.models.core import ServingBuildPayload
from agents_remember.serving import _app_routes, daemon, daemon_identity, daemon_processes
from agents_remember.serving.app import LiveProjectionInputs, create_app
from agents_remember.serving.daemon_identity import DashboardIdentity
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from fastapi.testclient import TestClient
from test_serving import _config, _projection


def _state(root: Path, *, pid: int = 12, digest: str = "old") -> daemon.DaemonState:
    (root / "config.json").write_text(json.dumps({"coordinationRoot": str(root)}))
    return daemon.DaemonState(
        pid,
        "127.0.0.1",
        8765,
        "same",
        str(root / "config.json"),
        str(root / "dashboard.log"),
        "boot",
        digest,
    )


def _identity(state: daemon.DaemonState) -> DashboardIdentity:
    return DashboardIdentity(
        state.pid,
        ServingBuildPayload(
            version=state.version, bootedAt=state.started_at, sourceDigest=state.source_digest
        ),
        state.config_path,
        None,
    )


def test_saved_pid_is_repaired_from_actual_listener_before_stop(tmp_path, monkeypatch):
    saved = _state(tmp_path)
    actual = replace(saved, pid=99, source_digest="actual")
    directory = daemon.daemon_state_directory(tmp_path)
    directory.mkdir(parents=True)
    daemon.write_state(directory, saved)
    monkeypatch.setattr(daemon, "observe_dashboard", lambda *args: _identity(actual))
    monkeypatch.setattr(daemon, "_state_alive", lambda state: True)
    monkeypatch.setattr(daemon, "_wait_gone", lambda *args, **kwargs: True)
    signaled = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: signaled.append(pid))
    observed, _ = daemon.probe(directory)
    assert observed is not None
    assert observed.pid == 99
    repaired = daemon.read_state(directory)
    assert repaired is not None
    assert repaired.source_digest == "actual"
    assert daemon.stop(directory) == "stopped"
    assert signaled == [99]


def test_unproven_saved_pid_is_never_signaled(tmp_path, monkeypatch):
    directory = daemon.daemon_state_directory(tmp_path)
    directory.mkdir(parents=True)
    daemon.write_state(directory, _state(tmp_path))
    monkeypatch.setattr(daemon, "observe_dashboard", lambda *args: None)
    kill = mock.Mock()
    monkeypatch.setattr(daemon.os, "kill", kill)
    assert daemon.stop(directory) == "not-running"
    kill.assert_not_called()


def test_equal_version_digest_change_restarts_and_matching_bytes_adopt(tmp_path, monkeypatch):
    config = _config(tmp_path)
    current = _state(tmp_path)
    target = daemon.DaemonEndpoint(current.host, current.port, current.version, "new")
    alive = True
    monkeypatch.setattr(daemon, "probe", lambda *args: (current, True))
    monkeypatch.setattr(daemon, "_state_alive", lambda state: alive)

    def stopped(directory):
        nonlocal alive
        alive = False
        return "stopped"

    monkeypatch.setattr(daemon, "stop", stopped)
    spawned = replace(current, pid=13, source_digest="new")
    spawn = mock.Mock(return_value=spawned)
    monkeypatch.setattr(daemon, "spawn", spawn)
    monkeypatch.setattr(daemon, "_wait_ready", lambda state: True)
    result = daemon._ensure_locked(
        config, tmp_path, target, cadence=daemon.DEFAULT_PROJECTION_CADENCE
    )
    assert result.action == "restarted" and "source digest old -> new" in result.detail
    current = spawned
    result = daemon._ensure_locked(
        config, tmp_path, target, cadence=daemon.DEFAULT_PROJECTION_CADENCE
    )
    assert result.action == "adopted"
    assert spawn.call_count == 1


def test_failed_stop_keeps_stale_daemon_identity_and_reports_failure(tmp_path, monkeypatch):
    current = _state(tmp_path)
    monkeypatch.setattr(daemon, "probe", lambda *args: (current, True))
    monkeypatch.setattr(daemon, "stop", lambda directory: "killed")
    monkeypatch.setattr(daemon, "_state_alive", lambda state: True)
    spawn = mock.Mock()
    monkeypatch.setattr(daemon, "spawn", spawn)
    result = daemon._ensure_locked(
        _config(tmp_path),
        tmp_path,
        daemon.DaemonEndpoint(current.host, current.port, current.version, "new"),
        cadence=daemon.DEFAULT_PROJECTION_CADENCE,
    )
    assert result.action == "failed" and result.state == current
    assert "stale daemon pid 12 still serves" in result.detail
    saved = daemon.read_state(tmp_path)
    assert saved is not None
    assert saved.pid == current.pid
    spawn.assert_not_called()
    stop = mock.Mock()
    monkeypatch.setattr(daemon, "stop", stop)
    unknown = daemon._ensure_locked(
        _config(tmp_path),
        tmp_path,
        daemon.DaemonEndpoint(current.host, current.port, current.version, None),
        cadence=daemon.DEFAULT_PROJECTION_CADENCE,
    )
    assert unknown.action == "unknown" and "could not be computed" in unknown.detail
    stop.assert_not_called()
    spawn.assert_not_called()


def test_runtime_preview_does_not_repair_saved_state(tmp_path, monkeypatch):
    config = _config(tmp_path)
    directory = daemon.daemon_dir(config)
    directory.mkdir(parents=True)
    state = _state(tmp_path)
    daemon.write_state(directory, state)
    before = (directory / daemon.STATE_FILE_NAME).read_bytes()
    monkeypatch.setattr(
        daemon, "observe_dashboard", lambda *args: _identity(replace(state, pid=99))
    )
    monkeypatch.setattr(daemon, "_installed_digest", lambda: "new")
    result = daemon.plan_running_dashboard(config)
    assert result is not None
    assert result.state is not None
    assert result.action == "would-restart" and result.state.pid == 99
    assert (directory / daemon.STATE_FILE_NAME).read_bytes() == before


def test_http_build_and_proc_socket_identity_must_agree(tmp_path, monkeypatch):
    process = tmp_path / "77"
    process.mkdir()
    (process / "cmdline").write_bytes(b"agents_remember\0dashboard\0--config\0/settings.json\0")
    monkeypatch.setattr(daemon_processes, "PROC_ROOT", tmp_path)
    monkeypatch.setattr(daemon_identity, "listening_dashboard_pid", lambda *args: 77)
    body = {
        "servingProcessId": 77,
        "servingBuild": {"version": "one", "bootedAt": "then", "sourceDigest": "loaded"},
    }
    monkeypatch.setattr(
        daemon_identity.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()),
    )
    observed = daemon_identity.observe_dashboard("127.0.0.1", 8765)
    assert observed is not None
    assert observed.build is not None
    assert observed.pid == 77 and observed.build.sourceDigest == "loaded"
    assert observed.currentness is None
    body["buildCurrentness"] = {
        "mode": "installed",
        "state": "stale",
        "loadedDigest": "loaded",
        "installedDigest": "new",
        "firstSeenAt": "2001-02-03T04:05:06Z",
        "reason": "Python source files changed since this process loaded its build.",
        "remedy": "Restart the dashboard.",
    }
    observed = daemon_identity.observe_dashboard("127.0.0.1", 8765)
    assert observed is not None
    assert observed.currentness is not None
    assert observed.currentness.state == "stale"
    assert observed.currentness.model_dump() == body["buildCurrentness"]
    body["servingProcessId"] = 78
    with pytest.raises(ConfigError, match="same process"):
        daemon_identity.observe_dashboard("127.0.0.1", 8765)


def _unresponsive_owner(root, monkeypatch, error, *, listener=77):
    root.mkdir()
    state = _state(root)
    directory = daemon.daemon_state_directory(root)
    directory.mkdir(parents=True)
    daemon.write_state(directory, state)
    monkeypatch.setattr(daemon_identity, "listening_dashboard_pid", lambda *args: listener)
    monkeypatch.setattr(
        daemon_identity,
        "process_arguments",
        lambda pid: [b"agents_remember", b"dashboard", b"--config", state.config_path.encode()],
    )
    monkeypatch.setattr(daemon_identity.urllib.request, "urlopen", mock.Mock(side_effect=error))
    return state, directory


def test_unresponsive_socket_owner_can_be_stopped_and_replaced(tmp_path, monkeypatch, subtests):
    errors = [
        TimeoutError("timed out"),
        urllib.error.HTTPError("http://local", 503, "priming", Message(), None),
    ]
    for index, error in enumerate(errors):
        with subtests.test(error=type(error).__name__):
            root = tmp_path / str(index)
            state, directory = _unresponsive_owner(root, monkeypatch, error)
            alive = {77: True, 88: True}
            signaled = []

            def kill(pid, sig, signaled=signaled, alive=alive):
                signaled.append(pid)
                alive[pid] = False

            monkeypatch.setattr(
                daemon, "_state_alive", lambda state, alive=alive: alive.get(state.pid, False)
            )
            monkeypatch.setattr(daemon.os, "kill", kill)
            monkeypatch.setattr(daemon, "_wait_gone", lambda *args, **kwargs: True)
            assert daemon.stop(directory) == "stopped"
            assert signaled == [77]
            alive[77] = True
            daemon.write_state(directory, state)
            spawned = replace(state, pid=88)
            spawn = mock.Mock(return_value=spawned)
            monkeypatch.setattr(daemon, "spawn", spawn)
            monkeypatch.setattr(daemon, "_wait_ready", lambda state: True)
            result = daemon._ensure_locked(
                _config(root),
                directory,
                daemon.DaemonEndpoint(state.host, state.port, state.version, state.source_digest),
                cadence=daemon.DEFAULT_PROJECTION_CADENCE,
            )
            assert result.action == "restarted"
            assert result.state == spawned
            assert signaled == [77, 77]
            spawn.assert_called_once()
            if isinstance(error, urllib.error.HTTPError):
                assert error.closed


def test_unresponsive_foreign_or_changed_socket_owner_is_not_signaled(
    tmp_path, monkeypatch, subtests
):
    for owner in ("foreign", "changed", "absent"):
        with subtests.test(owner=owner):
            root = tmp_path / owner
            _state_value, directory = _unresponsive_owner(
                root, monkeypatch, TimeoutError("timed out")
            )
            if owner == "foreign":
                foreign = tmp_path / "foreign-config.json"
                foreign.write_text(json.dumps({"coordinationRoot": str(tmp_path / "other")}))
                monkeypatch.setattr(
                    daemon_identity,
                    "process_arguments",
                    lambda pid, foreign=foreign: [
                        b"agents_remember",
                        b"dashboard",
                        b"--config",
                        str(foreign).encode(),
                    ],
                )
            else:
                listener = mock.Mock(side_effect=[77, 78] if owner == "changed" else [None])
                monkeypatch.setattr(daemon_identity, "listening_dashboard_pid", listener)
            kill = mock.Mock()
            monkeypatch.setattr(daemon.os, "kill", kill)
            result = daemon.stop(directory)
            assert (
                result.startswith("identity-unknown:")
                if owner != "absent"
                else result == "not-running"
            )
            kill.assert_not_called()


def test_repeated_status_prints_served_first_seen_time(tmp_path, monkeypatch, capsys):
    state, _directory = _unresponsive_owner(tmp_path / "status", monkeypatch, TimeoutError())
    build = _identity(replace(state, source_digest="loaded")).build
    assert build is not None
    currentness = BuildCurrentnessPayload(
        mode="installed",
        state="stale",
        loadedDigest="loaded",
        installedDigest="new",
        firstSeenAt="2001-02-03T04:05:06Z",
        remedy="Restart the dashboard.",
    )
    body = {
        "servingProcessId": 77,
        "servingBuild": build.model_dump(),
        "buildCurrentness": currentness.model_dump(),
    }
    monkeypatch.setattr(
        daemon_identity.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()),
    )
    monkeypatch.setattr(daemon, "_state_alive", lambda state: True)
    monkeypatch.setattr(dashboard_daemon, "_print_background_status", lambda *args: None)
    args = argparse.Namespace(sim=False, reload=False, status=True, host=state.host)

    def host(config):
        return dashboard_daemon.HostObservation("running", "host ready", {})

    expected = (
        "dashboard build: installed stale; loaded loaded; installed new; "
        "first seen 2001-02-03T04:05:06Z; Restart the dashboard."
    )
    for _ in range(2):
        assert (
            dashboard_daemon.run_daemon_command(
                args, _config(tmp_path / "status"), state.port, observe=host, start_host=host
            )
            == 0
        )
        assert expected in capsys.readouterr().out.splitlines()


def test_unresponsive_owner_does_not_prove_new_child_readiness(tmp_path, monkeypatch):
    state, _directory = _unresponsive_owner(tmp_path / "starting", monkeypatch, TimeoutError())
    state = replace(state, pid=77)
    monkeypatch.setattr(daemon, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        daemon,
        "time",
        SimpleNamespace(monotonic=mock.Mock(side_effect=[0, 0, 2]), sleep=lambda seconds: None),
    )
    # load-independent: daemon.time is the fake clock above (0, 0, 2); this bound is never wall time.
    assert not daemon._wait_ready(state, timeout=1)


def test_listener_lookup_checks_socket_inode_and_dashboard_command(tmp_path, monkeypatch):
    net = tmp_path / "net"
    net.mkdir()
    (net / "tcp").write_text("header\n 0: 0100007F:223D 00000000:0000 0A 0 0 0 0 0 456 0\n")
    for pid, command, inode in ((12, b"other", "456"), (99, b"agents_remember dashboard", "456")):
        process = tmp_path / str(pid)
        (process / "fd").mkdir(parents=True)
        (process / "cmdline").write_bytes(command)
        (process / "fd/3").symlink_to(f"socket:[{inode}]")
    monkeypatch.setattr(daemon_processes, "PROC_ROOT", tmp_path)
    assert daemon_processes.listening_dashboard_pid("127.0.0.1", 8765) == 99
    assert daemon_processes.listening_dashboard_pid("127.0.0.1", 8766) is None


def test_foreign_root_listener_is_not_adopted_or_signaled(tmp_path, monkeypatch):
    selected, foreign = tmp_path / "selected", tmp_path / "foreign"
    selected.mkdir()
    foreign.mkdir()
    config = _config(selected)
    saved = _state(selected)
    other = _state(foreign, pid=99)
    directory = daemon.daemon_dir(config)
    directory.mkdir(parents=True)
    daemon.write_state(directory, saved)
    before = (directory / daemon.STATE_FILE_NAME).read_bytes()
    monkeypatch.setattr(daemon, "observe_dashboard", lambda *args: _identity(other))
    kill, spawn = mock.Mock(), mock.Mock()
    monkeypatch.setattr(daemon.os, "kill", kill)
    monkeypatch.setattr(daemon, "spawn", spawn)
    assert "not the selected root" in daemon.stop(directory)
    result = daemon._ensure_locked(
        config,
        directory,
        daemon.DaemonEndpoint(saved.host, saved.port, "same", "old"),
        cadence=daemon.DEFAULT_PROJECTION_CADENCE,
    )
    assert result.action == "failed" and "no signal is authorized" in result.detail
    assert (directory / daemon.STATE_FILE_NAME).read_bytes() == before
    kill.assert_not_called()
    spawn.assert_not_called()
    alternate = selected / "other settings.json"
    alternate.write_text(json.dumps({"coordinationRoot": str(selected)}))
    other = replace(other, config_path=str(alternate))
    monkeypatch.setattr(daemon, "_state_alive", lambda state: True)
    observed, alive = daemon.probe(directory)
    assert observed is not None
    assert alive and observed.pid == 99 and observed.config_path == str(alternate)


def test_darwin_sysctl_arguments_preserve_spaces_and_empty_arguments(monkeypatch):
    arguments = [
        b"python",
        b"-m",
        b"agents_remember.cli",
        b"dashboard",
        b"--config",
        b"/a root/settings.json",
        b"",
    ]
    raw = struct.pack("i", len(arguments)) + b"/python\0\0\0" + b"\0".join(arguments) + b"\0ENV=x\0"
    calls = []

    class Sysctl:
        def __call__(self, *args):
            mib, count, output, size_pointer, new_value, new_size = args
            assert list(mib) == [1, 49, 99] and count == 3
            assert new_value is None and new_size == 0
            size = ctypes.cast(size_pointer, ctypes.POINTER(ctypes.c_size_t))
            size[0] = len(raw)
            if output is not None:
                ctypes.memmove(output, raw, len(raw))
            calls.append(output is None)
            return 0

    monkeypatch.setattr(
        daemon_processes.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(sysctl=Sysctl())
    )
    assert daemon_processes._darwin_process_arguments(99) == arguments
    assert calls == [True, False]


def test_darwin_owner_supports_adoption_readiness_and_stop(tmp_path, monkeypatch):
    state = _state(tmp_path, pid=99)
    config_path = tmp_path / "settings with spaces.json"
    config_path.write_text(json.dumps({"coordinationRoot": str(tmp_path)}))
    state = replace(state, config_path=str(config_path))
    arguments = [
        b"python",
        b"-m",
        b"agents_remember.cli",
        b"dashboard",
        b"--config",
        str(config_path).encode(),
    ]
    raw = struct.pack("i", len(arguments)) + b"/python\0\0" + b"\0".join(arguments) + b"\0"
    monkeypatch.setattr(daemon_processes, "PROCESS_PLATFORM", "darwin")
    monkeypatch.setattr(
        daemon_processes,
        "_darwin_process_arguments",
        lambda pid: daemon_processes._decode_darwin_arguments(raw) if pid == 99 else [b"other"],
    )

    def lsof(command, **kwargs):
        assert command == [
            "/usr/sbin/lsof",
            "-nP",
            "-a",
            "-iTCP@127.0.0.1:8765",
            "-sTCP:LISTEN",
            "-F0p",
        ]
        assert kwargs["timeout"] == 2.0
        return subprocess.CompletedProcess(command, 0, b"p12\0\np99\0\n", b"")

    monkeypatch.setattr(daemon_processes.subprocess, "run", lsof)
    build = _identity(state).build
    assert build is not None
    body = {"servingProcessId": 99, "servingBuild": build.model_dump(mode="json")}
    monkeypatch.setattr(
        daemon_identity.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()),
    )
    monkeypatch.setattr(daemon, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(daemon, "_wait_gone", lambda *args, **kwargs: True)
    directory = daemon.daemon_state_directory(tmp_path)
    directory.mkdir(parents=True)
    daemon.write_state(directory, replace(state, pid=12))
    observed, alive = daemon.probe(directory)
    assert observed is not None
    assert alive and observed.pid == 99 and observed.config_path == str(config_path)
    assert daemon._wait_ready(state, timeout=HANG_GUARD_SECONDS)
    with mock.patch.object(
        daemon, "observe_dashboard", side_effect=[ConfigError("starting"), _identity(state)]
    ):
        assert daemon._wait_ready(state, timeout=HANG_GUARD_SECONDS)
    signaled = []
    monkeypatch.setattr(daemon.os, "kill", lambda pid, sig: signaled.append(pid))
    assert daemon.stop(directory) == "stopped" and signaled == [99]


def test_native_identity_errors_are_unknown_and_never_signal(tmp_path, monkeypatch):
    state = _state(tmp_path)
    directory = daemon.daemon_state_directory(tmp_path)
    directory.mkdir(parents=True)
    daemon.write_state(directory, state)
    monkeypatch.setattr(daemon_processes, "PROCESS_PLATFORM", "darwin")
    kill = mock.Mock()
    monkeypatch.setattr(daemon.os, "kill", kill)
    for error in (
        FileNotFoundError("lsof absent"),
        PermissionError("not permitted"),
        subprocess.TimeoutExpired("lsof", 2),
    ):
        monkeypatch.setattr(daemon_processes.subprocess, "run", mock.Mock(side_effect=error))
        result = daemon.stop(directory)
        assert result.startswith("identity-unknown:") and "dashboard identity is unknown" in result
        assert daemon.read_state(directory) == state
    kill.assert_not_called()


def test_unchanged_projection_poll_observes_new_stale_currentness(tmp_path, monkeypatch):
    config = _config(tmp_path)
    current = BuildCurrentnessPayload(
        mode="installed", state="current", loadedDigest="old", installedDigest="old"
    )
    monkeypatch.setattr(_app_routes, "build_currentness", lambda *args, **kwargs: current)
    app = create_app(config, live_inputs=LiveProjectionInputs(change_watch=False))
    with (
        mock.patch(
            "agents_remember.serving.projector.project_and_write", return_value=_projection()
        ),
        TestClient(app) as client,
    ):
        first = client.get("/api/state")
        assert first.status_code == 200
        same = client.get("/api/state", headers={"If-None-Match": first.headers["etag"]})
        assert same.status_code == 304
        current = BuildCurrentnessPayload(
            mode="installed",
            state="stale",
            loadedDigest="old",
            installedDigest="new",
            firstSeenAt="now",
            remedy="Restart the dashboard.",
        )
        changed = client.get("/api/state", headers={"If-None-Match": first.headers["etag"]})
        assert changed.status_code == 200
        assert changed.json()["buildCurrentness"]["state"] == "stale"
        assert changed.json()["servingBuild"] == first.json()["servingBuild"]
        assert changed.json()["servingProcessId"] > 0
        assert changed.headers["etag"] != first.headers["etag"]

    async def check_open_stream():
        nonlocal current
        healthy = current.model_copy(
            update={"state": "current", "installedDigest": "old", "firstSeenAt": None}
        )
        stale = current
        current = healthy

        async def beats():
            nonlocal current
            yield _app_routes.ServerSentEvent(event="alive", data={})
            current = stale
            yield _app_routes.ServerSentEvent(event="alive", data={})
            yield _app_routes.ServerSentEvent(event="alive", data={})

        monkeypatch.setattr(_app_routes, "_alive_events", beats)
        events = [event async for event in _app_routes._status_events(config, healthy)]
        assert [event.event for event in events] == ["alive", "alive", "buildCurrentness", "alive"]
        assert events[2].data["state"] == "stale"

    asyncio.run(check_open_stream())
