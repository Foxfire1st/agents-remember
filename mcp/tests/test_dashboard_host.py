from __future__ import annotations

import threading
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

from agents_remember.cli import dashboard
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import daemon
from agents_remember.serving.paseo.paseo_settings import daemon_settings
from agents_remember.serving.paseo.paseo_start import HostObservation, ensure_host
from paseo_runtime_test_support import FakePaseo, free, runtime_settings, write_shared_runtime


def config(root):
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "logs",
    )


def args(**overrides):
    values = dict(
        status=False,
        stop=False,
        daemon=False,
        sim=None,
        reload=False,
        host="127.0.0.1",
        interval=1.0,
        heartbeat=None,
        no_access_log=False,
        port=None,
        config=None,
        host_start_owner=None,
    )
    values.update(overrides)
    return Namespace(**values)


def test_status_exit_codes_print_both_and_do_not_start(monkeypatch, tmp_path, capsys):
    cfg = config(tmp_path)
    start = Mock()
    monkeypatch.setattr(dashboard, "ensure_host", start)
    state = SimpleNamespace(pid=42, host="127.0.0.1", port=8765, version="build", log_path="log")
    for alive, host_state, expected in (
        (True, "running", 0),
        (True, "not configured", 0),
        (True, "not running", 3),
        (False, "running", 1),
    ):
        monkeypatch.setattr(daemon, "probe", lambda root, alive=alive: (state, alive))
        monkeypatch.setattr(
            dashboard,
            "observe_host",
            lambda cfg, host_state=host_state: HostObservation(
                host_state, f"host {host_state}", {}
            ),
        )
        assert dashboard._run_daemon_command(args(status=True), cfg, 8765) == expected
        output = capsys.readouterr().out
        assert "dashboard daemon" in output and f"host {host_state}" in output
    start.assert_not_called()


def test_adoption_and_autostart_ensure_host_while_stop_does_not(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    monkeypatch.setattr(
        daemon, "ensure", lambda *a, **k: daemon.EnsureResult("adopted", None, "existing")
    )
    outcome = HostObservation("not running", "host not running", {})
    host = Mock(return_value=outcome)
    monkeypatch.setattr(dashboard, "ensure_host", host)
    monkeypatch.setattr(daemon, "record_host_outcome", Mock())
    assert dashboard._run_daemon_command(args(daemon=True), cfg, 8765) == 3
    host.assert_called_once_with(cfg)
    host.reset_mock()
    monkeypatch.setattr(daemon, "stop", lambda root: "stopped")
    assert dashboard._run_daemon_command(args(stop=True), cfg, 8765) == 0
    host.assert_not_called()
    monkeypatch.setattr(daemon, "ensure_host", host)
    daemon._autostart(cfg)
    host.assert_called_once_with(cfg)


def test_foreground_serves_without_waiting_for_host_and_sim_skips_it(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    events = []
    monkeypatch.setattr(dashboard, "declare_process_role", lambda role: None)
    monkeypatch.setattr(dashboard, "bind_worktree_services", lambda services: None)
    monkeypatch.setattr(dashboard, "build_default_worktree_services", lambda: None)
    monkeypatch.setattr(
        dashboard, "_resolve_settings", lambda arguments: (str(cfg.config_path), cfg)
    )
    monkeypatch.setattr(
        dashboard, "_build_app", lambda arguments, cfg: dashboard._DashboardApp(object(), None)
    )
    monkeypatch.setattr(
        dashboard, "_start_host_background", lambda cfg: events.append("host background")
    )
    monkeypatch.setattr(dashboard.uvicorn, "run", lambda *a, **k: events.append("serve"))
    assert dashboard.run(args()) == 0
    assert events == ["host background", "serve"]
    events.clear()
    assert dashboard.run(args(sim="fixture")) == 0
    assert events == ["serve"]
    events.clear()
    monkeypatch.setattr(
        dashboard, "_run_reload_server", lambda *a: events.append("reload serve") or 0
    )
    assert dashboard.run(args(reload=True)) == 0
    assert events == ["host background", "reload serve"]


def test_real_background_function_serves_before_host_outcome_and_logs(
    monkeypatch, tmp_path, capsys
):
    cfg = config(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    settings = runtime_settings(tmp_path)
    write_shared_runtime(tmp_path, settings)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    fake.port_held = True

    def runner(argv, timeout):
        if list(argv)[2:4] == ["daemon", "start"]:
            entered.set()
            assert release.wait(2)
        return fake(argv, timeout)

    def pending(actual):
        assert actual is cfg
        return ensure_host(actual, runner=runner, reader=fake.reader, probe=free)

    def logged(actual, line):
        assert actual is cfg and "listen_port_in_use" in line
        finished.set()

    monkeypatch.setattr(dashboard, "ensure_host", pending)
    monkeypatch.setattr(daemon, "read_state", lambda root: None)
    monkeypatch.setattr(daemon, "record_host_outcome", logged)
    dashboard._start_host_background(cfg)
    try:
        assert entered.wait(1)
        assert not finished.is_set()
    finally:
        release.set()
    assert finished.wait(1)
    assert "host not running" in capsys.readouterr().out
    assert fake.mutations() == [("daemon", "start")]


def test_supervised_child_does_not_start_host_again(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    monkeypatch.setattr(dashboard, "declare_process_role", lambda role: None)
    monkeypatch.setattr(dashboard, "bind_worktree_services", lambda services: None)
    monkeypatch.setattr(dashboard, "build_default_worktree_services", lambda: None)
    monkeypatch.setattr(dashboard, "_resolve_settings", lambda args: (str(cfg.config_path), cfg))
    monkeypatch.setattr(
        dashboard, "_build_app", lambda args, config: dashboard._DashboardApp(object(), None)
    )
    background = Mock()
    serve = Mock()
    monkeypatch.setattr(dashboard, "_start_host_background", background)
    monkeypatch.setattr(dashboard.uvicorn, "run", serve)
    assert dashboard.run(args(host_start_owner=4242)) == 0
    background.assert_not_called()
    serve.assert_called_once()


def test_discovered_config_path_is_named_in_normal_and_transition_outcomes(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    path = workspace / ".claude/mcp/agents-remember-settings.json"
    path.parent.mkdir(parents=True)
    (tmp_path / "coordination").mkdir()
    path.write_text(
        __import__("json").dumps(
            {"coordinationRoot": str(tmp_path / "coordination"), "workspaceRoot": str(workspace)}
        )
    )
    nested = workspace / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    resolution = dashboard._resolve_settings(args())
    assert resolution is not None
    resolved, cfg = resolution
    assert resolved == str(path)
    settings = runtime_settings(tmp_path)
    write_shared_runtime(tmp_path, settings)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    fake.start()
    first = ensure_host(cfg, runner=fake, reader=fake.reader, probe=free)
    assert str(path) in first.line
    fake.running()["version"] = "0.10.2"
    changed = ensure_host(cfg, runner=fake, reader=fake.reader, probe=free)
    assert "--config " + str(path) in changed.line
    assert "<harness" not in changed.line and not fake.mutations()
