from __future__ import annotations

import errno
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo import paseo_start
from agents_remember.serving.paseo.paseo_daemon import runtime_status
from agents_remember.serving.paseo.paseo_settings import daemon_settings
from agents_remember.serving.paseo.paseo_start import ensure_host, observe_host
from paseo_runtime_test_support import (
    PINNED,
    FakePaseo,
    file_states,
    free,
    refused,
    runtime_settings,
    write_shared_runtime,
)


def configured(root):
    settings = runtime_settings(root)
    write_shared_runtime(root, settings)
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "logs",
    )
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, PINNED)
    fake.write_settings(daemon_settings(settings))
    return config, fake


def test_running_host_is_read_only_including_mismatches_and_session_names(tmp_path):
    config, fake = configured(tmp_path)
    fake.start()
    fake.processes[4242] = replace(fake.supervisor(), session_variables=("CODEX_THREAD_ID",))
    for version, listen in ((PINNED, fake.settings.listen), ("0.10.2", "127.0.0.1:6832")):
        fake.running()["version"] = version
        fake.running()["started_with"]["daemon.listen"] = listen
        before = file_states(fake.settings.home, fake.settings.install_prefix)
        index = len(fake.calls)
        result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
        assert result.state == "running"
        assert version in result.line and listen in result.line
        assert "CODEX_THREAD_ID" in result.line
        assert fake.mutations(index) == []
        assert file_states(fake.settings.home, fake.settings.install_prefix) == before

    # The current target exists, while the owned supervisor may still use the earlier Node.
    for executable, expected in ((product_node().node.as_posix(), []), ("/old/bin/node", ["node"])):
        fake.running()["version"] = PINNED
        fake.running()["started_with"]["daemon.listen"] = fake.settings.listen
        fake.processes[4242] = replace(fake.supervisor(), node_executable=executable)
        before = file_states(tmp_path)
        index = len(fake.calls)
        observed = observe_host(config, runner=fake, reader=fake.reader)
        status = runtime_status(fake.settings, runner=fake, reader=fake.reader)
        assert observed.state == "running" and status["running"]
        for facts in (observed.facts, status):
            assert facts["nodeExecutable"] == executable
            assert facts["node"]["path"] == product_node().node.as_posix()
            assert facts["restartRequired"] == expected
        assert ("Node restart required" in observed.line) is bool(expected)
        assert bool(status["restartRemedy"]) is bool(expected)
        if expected:
            assert executable in observed.line and product_node().node.as_posix() in observed.line
            assert (
                "paseo provision" in observed.line and "paseo provision" in status["restartRemedy"]
            )
        assert fake.mutations(index) == [] and fake.signalled == []
        assert file_states(tmp_path) == before


def test_a_live_unanswering_supervisor_never_gets_a_second_start(tmp_path):
    config, fake = configured(tmp_path)
    fake.record(4242, fake.supervisor())
    fake.answers[("daemon", "status")] = refused("DAEMON_REQUEST_TIMEOUT", "worker not ready")
    result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
    assert result.state == "not answering"
    assert fake.mutations() == []


def test_down_states_start_only_once_and_never_install_configure_or_reload(tmp_path):
    config, fake = configured(tmp_path)
    fake.record(999)  # stale record: a status names it; only a successful start removes it
    stale = fake.record_file.read_bytes()
    result = observe_host(config, runner=fake, reader=fake.reader)
    assert result.state == "not running" and "stale process record" in result.line
    assert fake.record_file.read_bytes() == stale
    before_config = fake.config_file.read_bytes()
    result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
    assert result.ready
    assert fake.mutations() == [("daemon", "start")]
    assert fake.config_file.read_bytes() == before_config


def test_missing_or_wrong_install_and_occupied_port_do_not_start(tmp_path):
    config, fake = configured(tmp_path)
    fake.install(fake.settings.install_prefix, "0.10.2")
    result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
    assert result.state == "not installed" and "0.10.2" in result.line and PINNED in result.line
    assert fake.mutations() == []
    fake.install(fake.settings.install_prefix, PINNED)
    result = ensure_host(
        config,
        runner=fake,
        reader=fake.reader,
        probe=lambda host, port: OSError(errno.EADDRINUSE, "occupied"),
    )
    assert not result.ready and "listen_port_in_use" in result.line
    assert fake.mutations() == []
    (fake.settings.install_prefix / "node_modules/.bin/paseo").unlink()
    assert ensure_host(config, runner=fake, reader=fake.reader, probe=free).state == "not installed"


def test_two_concurrent_starts_share_one_home_lock(tmp_path):
    config, fake = configured(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(ensure_host, config, runner=fake, reader=fake.reader, probe=free)
            for _ in range(2)
        ]
        assert all(future.result().ready for future in futures)
    assert fake.mutations() == [("daemon", "start")]


def test_no_shared_block_is_a_named_non_starting_state(tmp_path):
    config, fake = configured(tmp_path)
    write_shared_runtime(tmp_path, None)
    result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
    assert result.state == "not configured" and "system/settings.json" in result.line
    assert fake.calls == []


@pytest.mark.parametrize("failed", [False, True])
def test_contending_start_joins_the_same_owned_outcome_without_retry(tmp_path, failed):
    config, original = configured(tmp_path)
    entered, observed, release = threading.Event(), threading.Event(), threading.Event()

    class Starting(FakePaseo):
        def _daemon_start(self, words):
            self.record(4242, self.supervisor())
            entered.set()
            assert release.wait(2)
            self.record_file.unlink()
            self.processes.pop(4242)
            return super()._daemon_start(words)

        def _daemon_status(self, words):
            if self.recorded_pid() is not None and self.daemon is None:
                observed.set()
            return super()._daemon_status(words)

    fake = Starting(original.settings)
    fake.port_held = failed
    fake.lingering_supervisor = failed
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ensure_host, config, runner=fake, reader=fake.reader, probe=free)
        try:
            assert entered.wait(1)
            second = pool.submit(ensure_host, config, runner=fake, reader=fake.reader, probe=free)
            assert observed.wait(1)
            assert not second.done()
        finally:
            release.set()
        one, two = first.result(2), second.result(2)
    assert one.state == two.state and one.line == two.line
    assert one.ready is (not failed)
    assert fake.mutations().count(("daemon", "start")) == 1


def test_expired_start_reports_still_live_supervisor_and_does_not_duplicate_it(
    monkeypatch, tmp_path
):
    config, original = configured(tmp_path)
    clock = [10.0]
    monkeypatch.setattr(paseo_start.time, "monotonic", lambda: clock[0])

    class Expired(FakePaseo):
        def _daemon_start(self, _words):
            self.record(4242, self.supervisor())
            clock[0] += 121
            return refused("DAEMON_START_FAILED", "worker deadline")

    fake = Expired(original.settings)
    result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
    assert result.state == "not answering" and result.facts["supervisorAlive"]
    assert "cleanup is outstanding" in result.line
    assert fake.signalled == [] and fake.recorded_pid() == 4242
    assert ensure_host(config, runner=fake, reader=fake.reader, probe=free).state == "not answering"
    assert fake.mutations().count(("daemon", "start")) == 1


def test_incomplete_configuration_cannot_start_host_defaults(tmp_path):
    config, fake = configured(tmp_path)
    for incomplete in (None, {}):
        if incomplete is None:
            fake.config_file.unlink()
        else:
            fake.save(incomplete)
        result = ensure_host(config, runner=fake, reader=fake.reader, probe=free)
        assert result.state == "not installed" and "runtime_install" in result.line
        assert not fake.mutations() and not fake.speech_downloaded


def test_start_and_status_never_query_npm_and_name_actual_discovered_config(tmp_path):
    config, fake = configured(tmp_path)
    for result in (
        observe_host(config, runner=fake, reader=fake.reader),
        ensure_host(config, runner=fake, reader=fake.reader, probe=free),
    ):
        assert str(config.config_path) in result.line
    assert not any("npm-cli.js" in word for argv in fake.calls for word in argv)
