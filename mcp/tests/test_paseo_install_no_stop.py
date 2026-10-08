"""Installing from an agent must never stop the host that owns its session."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

import pytest
from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_host_contract import HOST_DATA
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo import paseo_provision
from agents_remember.serving.paseo.paseo_command import CommandResult, CommandRunner, PaseoCli
from agents_remember.serving.paseo.paseo_daemon_config import (
    previous_config_path,
    write_provider_entries,
)
from agents_remember.serving.paseo.paseo_process_record import ProcessReader
from agents_remember.serving.paseo.paseo_provision import (
    provision_for_install,
    provision_runtime,
)
from agents_remember.serving.paseo.paseo_run import BindProbe, ProvisionIntent, _Run
from agents_remember.serving.paseo.paseo_settings import daemon_settings
from paseo_runtime_test_support import (
    FakePaseo,
    file_states,
    free,
    runtime_settings,
    write_plugin,
)


class ProvisionArguments(TypedDict):
    runner: CommandRunner
    reader: ProcessReader
    probe: BindProbe
    plugin_source: Path


class ObservationFailureFake(FakePaseo):
    """Both admission reads succeed; the next observation fails or contradicts them."""

    def __init__(self, settings, fault):
        super().__init__(settings)
        self.fault = fault
        self.reads = {}

    def __call__(self, argv, timeout):
        result = super().__call__(argv, timeout)
        words = list(argv)[2:]
        kind = (
            "version"
            if words[:1] == ["--version"]
            and argv[1] == (self.settings.install_prefix / "node_modules/.bin/paseo").as_posix()
            else "status"
            if words[:2] == ["daemon", "status"]
            else "config"
            if words[:3] == ["daemon", "config", "get"]
            else None
        )
        if kind is not None:
            self.reads[kind] = self.reads.get(kind, 0) + 1
        if kind != self.fault.split("-", 1)[0] or self.reads[kind] != 3:
            return result
        if self.fault == "status-stopped":
            return CommandResult(0, json.dumps({"localDaemon": "stopped"}), "")
        if self.fault == "config-mismatch":
            payload = json.loads(result.stdout)
            payload["value"]["daemon"]["listen"] = "127.0.0.1:9999"
            return CommandResult(0, json.dumps(payload), "")
        return CommandResult(1, "", "transient observation failure")


@pytest.mark.parametrize(
    "fault", ["version", "status-error", "status-stopped", "config-error", "config-mismatch"]
)
def test_install_preserves_live_host_after_successful_admission(tmp_path, fault):
    settings = runtime_settings(tmp_path)
    fake = ObservationFailureFake(settings, fault)
    source = write_plugin(tmp_path, "plugin")
    args = ProvisionArguments(runner=fake, reader=fake.reader, probe=free, plugin_source=source)
    fake.fault = ""
    assert provision_runtime(settings, **args)["ok"]
    fake.fault = fault
    fake.reads.clear()
    before_calls = len(fake.calls)
    before_files = file_states(settings.home, settings.install_prefix)
    before_daemon = fake.daemon
    report = provision_for_install(settings, **args)
    assert not report["ok"] or report["restartRequired"]
    assert fake.reads[fault.split("-", 1)[0]] >= 3
    assert fake.mutations(before_calls) == []
    assert fake.signalled == [] and fake.daemon is before_daemon
    assert file_states(settings.home, settings.install_prefix) == before_files


@pytest.mark.parametrize("site", ["replacement", "activation", "stop", "start"])
def test_install_protects_live_host_at_irreversible_sites(tmp_path, site):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "plugin")
    assert provision_runtime(
        settings, runner=fake, reader=fake.reader, probe=free, plugin_source=source
    )["ok"]
    run = _Run(
        settings, PaseoCli(settings, fake), source, free, fake.reader, ProvisionIntent.INSTALL
    )
    staging = settings.install_prefix / ".ar-staging"
    if site == "activation":
        fake.install(staging, settings.version)
    before_calls = len(fake.calls)
    before_files = file_states(settings.home, settings.install_prefix)
    with pytest.raises(PaseoRuntimeFailure, match="live host"):
        if site == "replacement":
            paseo_provision._stop_for_replacement(run, run.cli)
        elif site == "activation":
            paseo_provision._activate(run, settings.install_prefix, staging)
        elif site == "stop":
            paseo_provision._stop_daemon(run, run.cli, "install")
        else:
            paseo_provision._start_daemon(run)
    assert fake.mutations(before_calls) == [] and fake.signalled == []
    assert file_states(settings.home, settings.install_prefix) == before_files


def test_install_preserves_host_appearing_after_failed_start_reply(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    source = write_plugin(tmp_path, "late live host")
    observed = {}

    def host_appears():
        fake.start()
        observed["daemon"] = fake.daemon
        observed["files"] = file_states(settings.home, settings.install_prefix)

    fake.during[("daemon", "start")] = host_appears
    fake.answers[("daemon", "start")] = CommandResult(1, "", "transient failed start reply")
    report = provision_for_install(
        settings, runner=fake, reader=fake.reader, probe=free, plugin_source=source
    )
    assert not report["ok"]
    assert fake.signalled == [] and fake.daemon is observed["daemon"]
    assert file_states(settings.home, settings.install_prefix) == observed["files"]


def test_unfinished_live_provider_write_names_owned_terminal_recovery(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    source = write_plugin(tmp_path, "plugin")
    args = ProvisionArguments(runner=fake, reader=fake.reader, probe=free, plugin_source=source)
    assert provision_runtime(settings, **args)["ok"]
    write_provider_entries(settings.home, {"broken": {"extends": "nope"}})
    before = file_states(settings.home, settings.install_prefix)
    at = len(fake.calls)
    for _ in range(2):
        report = provision_for_install(settings, **args)
        assert report["error"]["code"] == "provider_recovery_required"
        assert "terminal outside the host" in report["error"]["message"]
        assert not report["changed"] and fake.mutations(at) == []
        assert file_states(settings.home, settings.install_prefix) == before
    assert provision_runtime(settings, **args)["ok"]
    assert not previous_config_path(settings.home).exists()
    assert fake.invalid_config() is None and fake.signalled == []


def test_non_utf8_harness_failure_preserves_earlier_install_result(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff")
    config = McpRuntimeConfig(
        config_path=path,
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path / "projects",
        transcript_root=tmp_path / "logs",
    )
    with patch(
        "agents_remember.application.runtime.install.install_runtime_from_config",
        return_value={"ok": True, "summary": {"copiedFiles": 5}},
    ):
        result = run_runtime_install(config, RuntimeInstallRequest())
    assert result["ok"] is False and result["summary"]["copiedFiles"] == 5
    assert result["host"]["error"]["code"] == "settings_invalid"
    assert {"code", "step", "message", "detail"} <= result["host"]["error"].keys()
    assert path.read_bytes() == b"\xff"


@pytest.mark.parametrize("missing", ["package.json", "package-lock.json"])
def test_fake_npm_requires_provision_to_supply_each_locked_input(tmp_path, missing):
    fake = FakePaseo(runtime_settings(tmp_path))
    stage = tmp_path / "staging"
    stage.mkdir()
    for name in ("package.json", "package-lock.json"):
        if name != missing:
            shutil.copyfile(HOST_DATA / name, stage / name)
    assert fake._npm_install(stage, "@getpaseo/cli@" + fake.settings.version).returncode != 0
    assert not (stage / "node_modules").exists() and not (stage / missing).exists()
    (stage / missing).write_text("wrong build input")
    assert fake._npm_install(stage, "@getpaseo/cli@" + fake.settings.version).returncode != 0


class LateRestartFake(FakePaseo):
    def _daemon_config(self, args):
        result = super()._daemon_config(args)
        if args[0] == "set" and self.daemon is not None:
            value = json.loads(result.stdout)
            value["restartRequiredPaths"] = [args[1]]
            return type(result)(0, json.dumps(value), "")
        return result


def test_install_defers_replacement_without_a_write(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, "0.10.2")
    fake.write_settings(daemon_settings(settings))
    fake.start()
    before = file_states(settings.home, settings.install_prefix)
    report = provision_for_install(settings, runner=fake, reader=fake.reader, probe=free)
    assert report["ok"] and not report["changed"]
    assert report["restartRequired"] == ["version"]
    assert fake.mutations() == []
    assert file_states(settings.home, settings.install_prefix) == before
    assert fake.signalled == []


def test_install_applies_live_settings_and_defers_a_late_restart(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = LateRestartFake(settings)
    source = write_plugin(tmp_path, "plugin")
    args = ProvisionArguments(runner=fake, reader=fake.reader, probe=free, plugin_source=source)
    assert provision_runtime(settings, **args)["ok"]
    fake.write("daemon.mcp.injectIntoAgents", True)
    before = len(fake.calls)
    report = provision_for_install(settings, **args)
    assert report["ok"]
    assert report["restartRequired"] == ["setting:daemon.mcp.injectIntoAgents"]
    assert ("daemon", "stop") not in fake.mutations(before)
    assert ("daemon", "start") not in fake.mutations(before)
    assert fake.signalled == []
    assert fake.configured("daemon.mcp.injectIntoAgents") is False


def test_install_leaves_a_supervisor_alone_while_its_worker_is_not_answering(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    fake.record(4242, fake.supervisor())
    before = file_states(settings.home, settings.install_prefix)
    report = provision_for_install(settings, runner=fake, reader=fake.reader, probe=free)
    assert not report["ok"]
    assert report["error"]["code"] == "daemon_not_answering"
    assert not report["changed"]
    assert fake.mutations() == []
    assert file_states(settings.home, settings.install_prefix) == before


def test_running_host_on_another_node_is_deferred_before_node_or_home_changes(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, settings.version)
    fake.write_settings(daemon_settings(settings))
    fake.start()
    earlier = product_node().root.with_name("node-v0.0.0-linux-x64")
    earlier.mkdir(parents=True)
    marker = earlier / "keep"
    marker.write_text("earlier Node")
    fake.processes[4242] = replace(
        fake.supervisor(), node_executable=(earlier / "bin/node").as_posix()
    )
    before = file_states(tmp_path)
    report = provision_for_install(settings, runner=fake, reader=fake.reader, probe=free)
    assert report["ok"] and report["restartRequired"] == ["node"]
    assert not report["changed"] and fake.calls == []
    assert report["node"]["path"] == product_node().node.as_posix()
    assert file_states(tmp_path) == before and fake.signalled == []

    report = provision_runtime(settings, runner=fake, reader=fake.reader, probe=free)
    assert report["ok"] and report["daemon"] == {"action": "restarted", "reasons": ["node"]}
    assert {"step": "node", "action": "no-longer-used", "path": earlier.as_posix()} in report[
        "changes"
    ]
    assert marker.read_text() == "earlier Node"
    supervisor = fake.reader(4242)
    assert supervisor is not None and supervisor.node_executable == product_node().node.as_posix()
    repeated = provision_runtime(settings, runner=fake, reader=fake.reader, probe=free)
    assert repeated["ok"] and not repeated["changed"] and repeated["changes"] == []
    assert marker.read_text() == "earlier Node"
