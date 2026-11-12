"""Talking to an old host never needs the next build's Node to have been installed."""

from dataclasses import replace
from subprocess import CompletedProcess

import pytest
from agents_remember.cli import paseo_bridge
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_command import PaseoCli
from agents_remember.serving.paseo.paseo_daemon import runtime_status
from agents_remember.serving.paseo.paseo_process_record import ProcessUnreadable
from agents_remember.serving.paseo.paseo_running_node import communication_node
from agents_remember.serving.paseo.paseo_start import observe_host
from paseo_runtime_test_support import PINNED, FakePaseo, runtime_settings, write_runtime_block


def old_host(root):
    settings = runtime_settings(root)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, PINNED)
    fake.start()
    old = root / "old-node"
    old.touch()
    fake.processes[4242] = replace(fake.supervisor(), node_executable=str(old))
    product_node().node.unlink()
    return settings, fake, old


def test_communication_selects_existing_product_then_proven_running_node(tmp_path):
    settings, fake, old = old_host(tmp_path)
    assert communication_node(settings, fake.reader) == old
    product_node().node.touch()
    assert communication_node(settings, fake.reader) == product_node().node


@pytest.mark.parametrize("fault", ["missing", "foreign", "absent"])
def test_no_host_node_does_not_select_an_unrelated_executable(tmp_path, fault):
    settings, fake, old = old_host(tmp_path)
    if fault == "missing":
        old.unlink()
    elif fault == "foreign":
        fake.processes[4242] = replace(fake.supervisor(), paseo_home=str(tmp_path / "other"))
    else:
        fake.processes.clear()
    with pytest.raises(PaseoRuntimeFailure) as raised:
        communication_node(settings, fake.reader)
    assert raised.value.code == "node_not_installed"


def test_status_dashboard_and_bridge_use_old_node_but_start_never_does(tmp_path, monkeypatch):
    settings, fake, old = old_host(tmp_path)
    used = []

    def runner(argv, timeout):
        used.append(argv[0])
        return fake([str(product_node().node), *argv[1:]], timeout)

    status = runtime_status(settings, runner=runner, reader=fake.reader)
    assert status["running"] and status["restartRequired"] == ["node"]
    assert used and set(used) == {str(old)}
    path = tmp_path / "settings.json"
    write_runtime_block(path, settings)
    config = McpRuntimeConfig(
        config_path=path,
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    observed = observe_host(config, runner=runner, reader=fake.reader)
    assert observed.state == "running" and observed.facts["restartRequired"] == ["node"]
    monkeypatch.setattr(paseo_bridge, "read_process", fake.reader)
    (settings.home / "server-id").write_text("fixture")

    def bridge(argv, **kwargs):
        assert argv[0] == str(old)
        return CompletedProcess(argv, 0, '{"ok":true,"delivered":true}', "")

    monkeypatch.setattr(paseo_bridge.subprocess, "run", bridge)
    reply = paseo_bridge.bridge_call(config, "agent-send", {})
    assert reply["delivered"] and reply["restartRequired"] == ["node"]
    assert "paseo provision" in reply["restartRemedy"]
    for action in ("start", "restart"):
        with pytest.raises(PaseoRuntimeFailure) as raised:
            PaseoCli(settings, runner, reader=fake.reader).call("daemon", action, "--json")
        assert raised.value.code == "node_not_installed"
    assert fake.signalled == []


def test_acknowledgement_survives_an_unreadable_followup_host_observation(tmp_path, monkeypatch):
    settings, _fake, old = old_host(tmp_path)
    path = tmp_path / "settings.json"
    write_runtime_block(path, settings)
    config = McpRuntimeConfig(
        config_path=path,
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    (settings.home / "server-id").write_text("fixture")
    monkeypatch.setattr(paseo_bridge, "communication_node", lambda *args: old)

    def unreadable(_pid):
        raise ProcessUnreadable("the host environment became unreadable after delivery")

    monkeypatch.setattr(paseo_bridge, "read_process", unreadable)
    monkeypatch.setattr(
        paseo_bridge.subprocess,
        "run",
        lambda argv, **kwargs: CompletedProcess(argv, 0, '{"ok":true,"delivered":true}', ""),
    )
    assert paseo_bridge.bridge_call(config, "agent-send", {}) == {"ok": True, "delivered": True}
