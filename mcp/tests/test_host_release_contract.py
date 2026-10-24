from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from _user_settings_guard import user_settings_path_for_write
from agents_remember.cli import paseo_runtime
from agents_remember.install.runtime import upgrade_user_settings
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    parse_paseo_runtime_settings,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.kernel.primitives.user_settings_location import (
    UserSettingsLocationError,
    require_user_settings_path,
)
from agents_remember.serving.paseo.paseo_daemon import runtime_status, stop_runtime
from agents_remember.serving.paseo.paseo_provision import provision_runtime
from agents_remember.serving.paseo.paseo_start import ensure_host, observe_host
from paseo_runtime_test_support import (
    FakePaseo,
    free,
    runtime_settings,
    write_runtime_block,
    write_user_settings_block,
)


def test_plugin_pin_drift_fails_release_concordance(tmp_path):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "host_check", root / "scripts/check-host-contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check(root) == []
    source = root / "mcp/src/agents_remember/package_data"
    target = tmp_path / "mcp/src/agents_remember/package_data"
    shutil.copytree(source / "paseo_host", target / "paseo_host")
    (target / "paseo_plugin").mkdir()
    plugin = json.loads((source / "paseo_plugin/package.json").read_text())
    plugin["devDependencies"]["@getpaseo/plugin"] = "other-release"
    (target / "paseo_plugin/package.json").write_text(json.dumps(plugin))
    assert module.check(tmp_path) == [
        "plugin development SDK version does not match the build host"
    ]


@pytest.mark.parametrize("mutation", ["host-lock", "node-url"])
def test_release_concordance_rejects_mismatched_lock_entry_or_archive(tmp_path, mutation):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "host_check", root / "scripts/check-host-contract.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = root / "mcp/src/agents_remember/package_data"
    target = tmp_path / "mcp/src/agents_remember/package_data"
    shutil.copytree(source / "paseo_host", target / "paseo_host")
    (target / "paseo_plugin").mkdir()
    shutil.copyfile(source / "paseo_plugin/package.json", target / "paseo_plugin/package.json")
    if mutation == "host-lock":
        path = target / "paseo_host/package-lock.json"
        data = json.loads(path.read_text())
        data["packages"]["node_modules/@getpaseo/cli"]["version"] = "0.10.2"
    else:
        path = target / "paseo_host/contract.json"
        data = json.loads(path.read_text())
        archive = data["node"]["archives"]["linux-x64"]
        archive["url"] = archive["url"].replace("linux-x64", "linux-arm64")
    path.write_text(json.dumps(data))
    assert module.check(tmp_path)


@pytest.mark.parametrize("command", ["build", "check", "start", "stop"])
def test_public_sandbox_ports_reach_the_command_with_defaults_preserved(
    command, tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    cli = importlib.import_module("pnt_sandbox.cli")

    seen = []
    monkeypatch.setattr(cli, "require_checkout", lambda path: path)
    monkeypatch.setattr(cli, "read_marker", lambda layout: {"state": "built"})
    monkeypatch.setattr(cli.builder, "build", lambda layout, *args: seen.append(layout))
    for method in ("check", "start", "stop"):
        monkeypatch.setattr(cli.commands, method, lambda layout, *args: seen.append(layout) or True)
    base = [command, "--sandbox", str(tmp_path / "one-root")]
    if command == "start":
        base.append(str(tmp_path / "product"))
    for arguments, expected in (
        (base, (6820, 9797)),
        ([*base, "--host-port", "6871", "--dashboard-port", "9871"], (6871, 9871)),
    ):
        cli._run(cli.build_parser().parse_args(arguments))
        assert (seen[-1].paseo_port, seen[-1].dashboard_port) == expected
        assert seen[-1].root == tmp_path / "one-root"
    assert len(seen) == 2


def test_public_sandbox_ports_refuse_invalid_live_and_equal_values_before_actions(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    cli = importlib.import_module("pnt_sandbox.cli")

    calls = []
    monkeypatch.setattr(cli.builder, "build", lambda *args: calls.append(args))
    for value in ("0", "65536", "9785", "9786", "not-a-port"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["build", "--host-port", value])
    with pytest.raises(cli.SandboxRefusal, match="must differ"):
        cli._run(
            cli.build_parser().parse_args(
                ["build", "--host-port", "6871", "--dashboard-port", "6871"]
            )
        )
    assert calls == []


def test_retired_selector_notice_once_on_each_public_command_and_dashboard_line(
    tmp_path, monkeypatch, capsys
):

    settings = runtime_settings(tmp_path)
    path = tmp_path / "settings.json"
    write_runtime_block(path, settings)
    document = json.loads(path.read_text())
    document["paseoRuntime"]["version"] = "*"
    document["coordinationRoot"] = str(tmp_path / "coordination")
    document["workspaceRoot"] = str(tmp_path / "projects")
    path.write_text(json.dumps(document))
    cfg = load_config(path)
    fake = FakePaseo(settings)
    operations = {
        "provision": lambda actual: provision_runtime(
            actual, runner=fake, reader=fake.reader, probe=free
        ),
        "status": lambda actual: runtime_status(actual, runner=fake, reader=fake.reader),
        "stop": lambda actual: stop_runtime(actual, runner=fake, reader=fake.reader),
    }
    monkeypatch.setattr(
        paseo_runtime,
        "_COMMANDS",
        {name: ("command", operation) for name, operation in operations.items()},
    )
    for command in operations:
        assert paseo_runtime.run(Namespace(config=str(path), paseo_command=command)) == 0
        output = capsys.readouterr().out
        assert output.count("paseoRuntime.version") == 1
        notice = json.loads(output)["notice"]
        assert "'*'" in notice and settings.version in notice and str(path) in notice
    for result in (
        ensure_host(cfg, runner=fake, reader=fake.reader, probe=free),
        observe_host(cfg, runner=fake, reader=fake.reader),
    ):
        assert result.line.count("paseoRuntime.version") == 1
        # One file is both the command's config and the host settings the notice names.
        assert result.line.count(f"host settings: {path}") == 1
        assert settings.version in result.line and "'*'" in result.line


@pytest.mark.parametrize("both", [False, True])
def test_host_and_all_standalone_starters_refuse_old_location_before_any_write(
    tmp_path, monkeypatch, both
):
    coordination = tmp_path / "old coordination"
    old = coordination / "system/settings.json"
    old.parent.mkdir(parents=True)
    # A location refusal must precede even parsing the removed file.
    old.write_bytes(b"\xff old settings must not be read")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    new = user_settings_path_for_write()
    if both:
        new.parent.mkdir(parents=True)
        new.write_bytes(b"\xff existing user settings must not be read")
    before = old.read_bytes(), new.read_bytes() if both else None
    with pytest.raises(UserSettingsLocationError, match="location-upgrade-required") as caught:
        require_user_settings_path(coordination)
    for path in (old, new):
        assert str(path) in str(caught.value)
    # The eight starter packages carried this renderer until MIK-R196 rule 9 retired it: a
    # starter package now carries configuration, hooks and instruction files only, so there is
    # no starter left to ask. The claim that survives is the location refusal itself, above,
    # and that neither the removed file nor an existing user file was read.
    assert old.read_bytes() == before[0]
    if both:
        assert new.read_bytes() == before[1]


def test_whole_file_upgrade_retains_configured_host_and_all_other_families(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "sandbox/config"))
    coordination = tmp_path / "sandbox/coordination"
    settings = runtime_settings(tmp_path / "sandbox")
    write_user_settings_block(settings)
    new = user_settings_path_for_write()
    document = json.loads(new.read_text())
    document["futureFamily"] = {"unknownKey": ["keep", 42]}
    document["orchestration"] = {"roles": {"worker": {"harness": "codex", "mode": "auto"}}}
    old = coordination / "system/settings.json"
    old.parent.mkdir(parents=True)
    old.write_bytes((json.dumps(document, ensure_ascii=False, indent=3) + "\n").encode())
    old.chmod(0o640)
    new.unlink()
    before = old.read_bytes(), stat.S_IMODE(old.stat().st_mode)
    preview = upgrade_user_settings(coordination, dry_run=True)
    assert preview["state"] == "would-move" and old.exists() and not new.exists()
    assert upgrade_user_settings(coordination, dry_run=False)["state"] == "moved"
    # MIK-R137 rule 4: the moved file keeps its bytes but not its open mode.
    assert not old.exists() and new.read_bytes() == before[0]
    assert stat.S_IMODE(new.stat().st_mode) == 0o444
    assert stat.S_IMODE(new.parent.stat().st_mode) == 0o555
    new.parent.chmod(0o755)
    actual = parse_paseo_runtime_settings(
        json.loads(new.read_text(encoding="utf-8")).get("paseoRuntime")
    )
    assert actual is not None and new == require_user_settings_path(coordination)
    assert (actual.home, actual.install_prefix, actual.listen, actual.providers, actual.embed) == (
        settings.home,
        settings.install_prefix,
        settings.listen,
        settings.providers,
        settings.embed,
    )
    assert upgrade_user_settings(coordination, dry_run=False)["state"] == "already-current"


def test_sandbox_environment_reads_only_its_own_user_settings(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    sandbox_environment = importlib.import_module("pnt_sandbox.environment").sandbox_environment
    SandboxLayout = importlib.import_module("pnt_sandbox.layout").SandboxLayout

    real_home = tmp_path / "simulated-user"
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(real_home / "config"))
    outside = user_settings_path_for_write()
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"\xff must not be read by the sandbox")
    layout = SandboxLayout(tmp_path / "sandbox")
    layout.user_settings_file.parent.mkdir(parents=True)
    layout.user_settings_file.write_text(json.dumps({"paseoRuntime": None, "other": "owned"}))
    environment = sandbox_environment(os.environ, layout)
    assert environment["HOME"] == str(real_home)
    with patch.dict(os.environ, environment, clear=True):
        assert user_settings_path_for_write() == layout.user_settings_file
        assert (
            parse_paseo_runtime_settings(
                json.loads(layout.user_settings_file.read_text(encoding="utf-8")).get(
                    "paseoRuntime"
                )
            )
            is None
        )
    assert outside.read_bytes() == b"\xff must not be read by the sandbox"
