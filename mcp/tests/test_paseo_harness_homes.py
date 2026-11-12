"""Inherited storage survives a host restart, or the user sees the change before a stop."""

import json
import logging
from argparse import Namespace
from dataclasses import replace

import pytest
from agents_remember.cli import paseo_runtime
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.serving.paseo.paseo_command import PaseoCli
from agents_remember.serving.paseo.paseo_daemon import runtime_status
from agents_remember.serving.paseo.paseo_harness_homes import (
    harness_home_status,
    home_variable_names,
    require_preserved_harness_homes,
)
from agents_remember.serving.paseo.paseo_provision import (
    _start_daemon,
    provision_runtime,
    provision_with_home_change,
)
from agents_remember.serving.paseo.paseo_run import _Run
from paseo_runtime_test_support import (
    PINNED,
    FakePaseo,
    free,
    refused,
    runtime_settings,
    write_runtime_block,
)


def test_inherited_names_and_settings_override_without_value_leaks(tmp_path):
    settings = runtime_settings(tmp_path, providers={})
    fake = FakePaseo(settings)
    homes = (("CODEX_HOME", "/private/rollouts"), ("CODEX_SQLITE_HOME", "/private/sqlite"))
    facts = replace(fake.supervisor(), harness_homes=homes)
    report = harness_home_status(settings, facts)
    assert report["inheritedHarnessHomes"] == ["CODEX_HOME", "CODEX_SQLITE_HOME"]
    assert "/private" not in json.dumps(report)
    configured = replace(settings, providers={"codex": {"env": dict(homes)}})
    assert harness_home_status(configured, facts) == {
        "inheritedHarnessHomes": [],
        "harnessHomeRemedy": None,
    }
    require_preserved_harness_homes(configured, facts, environment={})
    require_preserved_harness_homes(settings, facts, environment=dict(homes))
    require_preserved_harness_homes(settings, facts, environment={}, accept_change=True)
    with pytest.raises(PaseoRuntimeFailure) as raised:
        require_preserved_harness_homes(settings, facts, environment={})
    error = raised.value
    assert error.code == "harness-home-would-change"
    detail = error.detail
    assert detail is not None
    assert json.loads(detail) == [
        {"name": name, "runningValue": value, "newValue": None} for name, value in homes
    ]
    assert "--accept-harness-home-change" in str(error)
    assert "env" in str(error) and "same variable values" in str(error)


def test_unknown_environment_is_reported_and_adds_no_new_refusal(tmp_path):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    facts = replace(fake.supervisor(), harness_homes=None)
    assert harness_home_status(settings, facts)["inheritedHarnessHomes"] == "unknown"
    require_preserved_harness_homes(settings, facts, environment={})
    assert {"CODEX_HOME", "CODEX_SQLITE_HOME", "CLAUDE_CONFIG_DIR", "PI_CODING_AGENT_DIR"} <= set(
        home_variable_names()
    )


def test_home_change_refuses_before_stop_and_all_three_remedies_allow_restart(
    tmp_path, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    settings = runtime_settings(tmp_path, providers={})
    fake = FakePaseo(settings)
    assert provision_runtime(settings, runner=fake, reader=fake.reader, probe=free)["ok"]
    fake.processes[4242] = replace(fake.supervisor(), harness_homes=(("CODEX_HOME", "/old/codex"),))
    changed = replace(settings, listen="127.0.0.1:6832")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    before = len(fake.calls)
    refused = provision_runtime(changed, runner=fake, reader=fake.reader, probe=free)
    assert refused["error"]["code"] == "harness-home-would-change"
    assert ("daemon", "stop") not in fake.mutations(before)
    assert fake.signalled == []
    assert refused["inheritedHarnessHomes"] == ["CODEX_HOME"]
    assert "/old/codex" not in caplog.text
    assert all("/old/codex" not in word for call in fake.calls for word in call)
    assert "/old/codex" not in repr(fake.processes[4242])
    status = runtime_status(settings, runner=fake, reader=fake.reader)
    assert status["inheritedHarnessHomes"] == ["CODEX_HOME"]
    assert "/old/codex" not in json.dumps(status)
    for remedy in ("environment", "settings", "accept"):
        fake.processes[4242] = replace(
            fake.supervisor(), harness_homes=(("CODEX_HOME", "/old/codex"),)
        )
        fake.running()["started_with"]["daemon.listen"] = settings.listen
        selected = changed
        if remedy == "environment":
            monkeypatch.setenv("CODEX_HOME", "/old/codex")
        else:
            monkeypatch.delenv("CODEX_HOME", raising=False)
        if remedy == "settings":
            selected = replace(changed, providers={"codex": {"env": {"CODEX_HOME": "/old/codex"}}})
        operation = provision_with_home_change if remedy == "accept" else provision_runtime
        result = operation(selected, runner=fake, reader=fake.reader, probe=free)
        assert result["ok"], result
        assert result["daemon"]["action"] == "restarted"
        assert "/old/codex" not in json.dumps(result)
        assert "/old/codex" not in caplog.text
        assert all("/old/codex" not in word for call in fake.calls for word in call)


def test_provider_environment_is_not_rewritten_by_settings_parser(tmp_path):
    settings = runtime_settings(tmp_path)
    providers = {"codex": {"env": {"CODEX_HOME": "/private/codex", "TOKEN": "secret"}}}
    parsed = parse_paseo_runtime_settings(
        {
            "installPrefix": str(settings.install_prefix),
            "home": str(settings.home),
            "listen": settings.listen,
            "providers": providers,
            "embed": [],
        }
    )
    assert parsed is not None and parsed.providers == providers


def test_unreadable_environment_reports_unknown_without_weakening_process_identity(
    tmp_path, monkeypatch, capsys
):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    fake.start()
    fake.processes[4242] = None
    report = provision_runtime(settings, runner=fake, reader=fake.reader, probe=free)
    assert report["error"]["code"] == "process_record_unverifiable"
    assert report["inheritedHarnessHomes"] == "unknown"
    assert fake.signalled == [] and fake.mutations() == []
    path = tmp_path / "settings.json"
    write_runtime_block(path, settings)
    monkeypatch.setitem(
        paseo_runtime._COMMANDS,
        "status",
        ("status", lambda loaded: runtime_status(loaded, runner=fake, reader=fake.reader)),
    )
    assert paseo_runtime.run(Namespace(paseo_command="status", config=str(path))) == 1
    status = json.loads(capsys.readouterr().out)
    assert status["error"]["code"] == "process_record_unverifiable"
    assert status["inheritedHarnessHomes"] == "unknown"


def test_failed_start_cleanup_also_checks_homes_before_its_stop(tmp_path, monkeypatch):
    settings = runtime_settings(tmp_path)
    fake = FakePaseo(settings)
    fake.install(settings.install_prefix, PINNED)
    fake.start()
    fake.processes[4242] = replace(fake.supervisor(), harness_homes=(("CODEX_HOME", "/old/codex"),))
    fake.answers[("daemon", "start")] = refused("failed", "the supervisor is still alive")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    run = _Run(
        settings, PaseoCli(settings, fake, reader=fake.reader), settings.home, free, fake.reader
    )
    with pytest.raises(PaseoRuntimeFailure) as raised:
        _start_daemon(run)
    assert raised.value.code == "harness-home-would-change"
    assert ("daemon", "stop") not in fake.mutations() and fake.signalled == []
