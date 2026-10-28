from __future__ import annotations

import subprocess
import sys
import threading
import uuid
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import h11
import pytest
from agents_remember.cli import dashboard
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import daemon
from agents_remember.serving.paseo.paseo_settings import daemon_settings
from agents_remember.serving.paseo.paseo_start import HostObservation, ensure_host
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from paseo_runtime_test_support import FakePaseo, free, runtime_settings, write_runtime_block


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
    monkeypatch.setattr(
        dashboard, "build_default_worktree_services", lambda *, leaf_agent_archive: None
    )
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


def _card_read_head() -> bytes:
    """The request head of one card read naming the route's maximum of 500 invariants."""

    query = urlencode(
        {
            "repo": "agents-remember",
            "master": "260928_maintained-invariant-knowledge",
            "leaf": "260928-MIK-L181",
            "comparison": "8",
            "invariants": ",".join(str(uuid.UUID(int=number)) for number in range(500)),
        }
    )
    return f"GET /api/review/trees?{query} HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n".encode()


def _parsed_over_two_reads(head: bytes, connection: h11.Connection) -> object:
    """What one server connection makes of a head that arrives over two reads, as uvicorn feeds it."""

    connection.receive_data(head[:-4])
    assert connection.next_event() is h11.NEED_DATA
    connection.receive_data(head[-4:])
    return connection.next_event()


def test_a_500_invariant_card_read_fits_the_dashboard_servers_request_head_limit(
    monkeypatch, tmp_path
):
    """MIK-R89 v2 rule 2 (W6): every serving mode admits the card read's ~19.5 KB head."""

    cfg = config(tmp_path)
    served: list[dict] = []
    monkeypatch.setattr(dashboard, "declare_process_role", lambda role: None)
    monkeypatch.setattr(dashboard, "bind_worktree_services", lambda services: None)
    monkeypatch.setattr(
        dashboard, "build_default_worktree_services", lambda *, leaf_agent_archive: None
    )
    monkeypatch.setattr(dashboard, "_resolve_settings", lambda args: (str(cfg.config_path), cfg))
    monkeypatch.setattr(
        dashboard, "_build_app", lambda args, config: dashboard._DashboardApp(object(), None)
    )
    monkeypatch.setattr(dashboard.uvicorn, "run", lambda *a, **k: served.append(k))
    for name in (dashboard._DEV_CONFIG_ENV, dashboard._DEV_INTERVAL_ENV):
        monkeypatch.setenv(name, "")  # restored after the reload path writes them
    # The foreground serve is also the daemon child's (``--host-start-owner``); then reload.
    assert dashboard.run(args(host_start_owner=4242)) == 0
    assert dashboard._run_reload_server(args(reload=True), str(cfg.config_path), 9785) == 0
    limit = dashboard.DASHBOARD_H11_MAX_INCOMPLETE_EVENT_SIZE
    assert [kwargs["h11_max_incomplete_event_size"] for kwargs in served] == [limit, limit]
    head = _card_read_head()
    assert len(head) < limit
    # h11's own default refuses the head (uvicorn then answers 400); the dashboard's limit reads it.
    with pytest.raises(h11.RemoteProtocolError):
        _parsed_over_two_reads(head, h11.Connection(h11.SERVER))
    ours = h11.Connection(h11.SERVER, max_incomplete_event_size=limit)
    assert isinstance(_parsed_over_two_reads(head, ours), h11.Request)


def test_real_background_function_serves_before_host_outcome_and_logs(
    monkeypatch, tmp_path, capsys
):
    cfg = config(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    settings = runtime_settings(tmp_path)
    write_runtime_block(cfg.config_path, settings)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    fake.port_held = True

    def runner(argv, timeout):
        if list(argv)[2:4] == ["daemon", "start"]:
            entered.set()
            assert release.wait(HANG_GUARD_SECONDS)
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
        assert entered.wait(HANG_GUARD_SECONDS)
        assert not finished.is_set()
    finally:
        release.set()
    assert finished.wait(HANG_GUARD_SECONDS)
    assert "host not running" in capsys.readouterr().out
    assert fake.mutations() == [("daemon", "start")]


def test_supervised_child_does_not_start_host_again(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    monkeypatch.setattr(dashboard, "declare_process_role", lambda role: None)
    monkeypatch.setattr(dashboard, "bind_worktree_services", lambda services: None)
    monkeypatch.setattr(
        dashboard, "build_default_worktree_services", lambda *, leaf_agent_archive: None
    )
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
    write_runtime_block(cfg.config_path, settings)
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


def test_both_serve_paths_hand_uvicorn_the_package_log_config(monkeypatch, tmp_path):
    cfg = config(tmp_path)
    served = []
    monkeypatch.setattr(dashboard, "declare_process_role", lambda role: None)
    monkeypatch.setattr(dashboard, "bind_worktree_services", lambda services: None)
    monkeypatch.setattr(
        dashboard, "build_default_worktree_services", lambda *, leaf_agent_archive: None
    )
    monkeypatch.setattr(
        dashboard, "_resolve_settings", lambda arguments: (str(cfg.config_path), cfg)
    )
    monkeypatch.setattr(
        dashboard, "_build_app", lambda arguments, cfg: dashboard._DashboardApp(object(), None)
    )
    monkeypatch.setattr(dashboard, "_start_host_background", lambda cfg: None)
    monkeypatch.setattr(dashboard.uvicorn, "run", lambda *a, **k: served.append(k["log_config"]))
    for name in (
        dashboard._DEV_CONFIG_ENV,
        dashboard._DEV_INTERVAL_ENV,
        dashboard._DEV_HEARTBEAT_ENV,
    ):
        monkeypatch.setenv(name, "")  # the reload path writes these; restored afterwards
    assert dashboard.run(args(host_start_owner=4242)) == 0
    assert dashboard.run(args(reload=True, host_start_owner=4242)) == 0
    assert served == [dashboard.dashboard_log_config()] * 2


def test_the_package_log_config_prints_the_packages_info_lines_once():
    """Applied in a child interpreter: the suite's own logging is never configured here."""

    script = (
        "import logging, logging.config\n"
        "from agents_remember.cli.dashboard import dashboard_log_config\n"
        "logging.config.dictConfig(dashboard_log_config())\n"
        "logging.getLogger().addHandler(logging.StreamHandler())\n"
        "logging.getLogger('agents_remember.serving.leaf_worktree_watch').info('the plan')\n"
        "logging.getLogger('agents_remember.x').debug('not this')\n"
        "logging.getLogger('elsewhere').info('nor this')\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    lines = child.stderr.splitlines()
    assert lines == ["INFO:     agents_remember.serving.leaf_worktree_watch: the plan"]
