from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.cli import paseo_runtime
from agents_remember.install.runtime import upgrade_user_settings
from agents_remember.kernel.agentic_settings import user_agentic_settings_path
from agents_remember.kernel.primitives.paseo_authority import load_shared_paseo_runtime
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettingsError
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.serving.paseo.paseo_daemon import runtime_status, stop_runtime
from agents_remember.serving.paseo.paseo_provision import provision_runtime
from agents_remember.serving.paseo.paseo_start import ensure_host, observe_host
from paseo_runtime_test_support import FakePaseo, free, runtime_settings, write_shared_runtime


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


def test_starter_renders_one_shared_block_and_preserves_existing_settings(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "starter_renderer", root / "scripts/harness/render_starter.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    settings = tmp_path / "harness/mcp/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text((root / "scripts/harness/shared/agents-remember-settings.json").read_text())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    module.render_settings(settings, tmp_path, ["repo"])
    shared = user_agentic_settings_path()
    host = json.loads(shared.read_text())["paseoRuntime"]
    assert "paseoRuntime" not in json.loads(settings.read_text())
    assert "version" not in host
    assert host["listen"] == "127.0.0.1:8766"
    assert host["installPrefix"] == str(tmp_path / "data/agents-remember/paseo/prefix")
    assert host["home"] == str(tmp_path / "state/agents-remember/paseo")
    document = json.loads(shared.read_text())
    document["paseoRuntime"]["listen"] = "127.0.0.1:9786"
    document["developerSetting"] = "keep"
    shared.write_text(json.dumps(document))
    before = shared.read_bytes()
    module.render_settings(settings, tmp_path, ["repo"])
    assert shared.read_bytes() == before


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


def test_same_starter_input_differs_only_in_recorded_root_and_ports(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "renderer_options", root / "scripts/harness/render_starter.py"
    )
    assert spec is not None and spec.loader is not None
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    for name, folder in (("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(name, str(tmp_path / folder))
    template = (root / "scripts/harness/shared/agents-remember-settings.json").read_text()
    default, actual = tmp_path / "default.json", tmp_path / "explicit.json"
    default.write_text(template)
    actual.write_text(template)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "default-user"))
    renderer.render_settings(default, tmp_path, ["repo"])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "explicit-user"))
    renderer.render_settings(
        actual,
        tmp_path,
        ["repo"],
        renderer.RenderOptions(tmp_path / "custom-coordination", 6820, 9797),
    )
    standard = json.loads(default.read_text())
    explicit = json.loads(actual.read_text())
    assert {
        key: value
        for key, value in standard.items()
        if key not in {"coordinationRoot", "transcriptRoot", "dashboard"}
    } == {
        key: value
        for key, value in explicit.items()
        if key not in {"coordinationRoot", "transcriptRoot", "dashboard"}
    }
    assert standard["dashboard"]["port"] == 8765 and explicit["dashboard"]["port"] == 9797
    for path, expected_port in ((default, 8766), (actual, 6820)):
        monkeypatch.setenv(
            "XDG_CONFIG_HOME",
            str(tmp_path / ("default-user" if path == default else "explicit-user")),
        )
        config = load_config(path)
        host = config.paseo_runtime
        assert host is not None
        assert host.listen_port == expected_port
        assert host.install_prefix == tmp_path / "data/agents-remember/paseo/prefix"
        assert host.home == tmp_path / "state/agents-remember/paseo"
        assert host.providers == {} and len(host.embed) == 2


def test_native_public_renderer_consumes_shared_setup_without_output_edits(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "native_renderer", root / "scripts/pnt_sandbox/render_starter_settings.py"
    )
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    coordination = tmp_path / "coordination"
    shared = user_agentic_settings_path()
    shared.parent.mkdir(parents=True)
    block = {
        "installPrefix": str(tmp_path / "paseo/prefix"),
        "home": str(tmp_path / "paseo/home"),
        "listen": "127.0.0.1:6820",
        "providers": {"pi": {"command": ["pi", "--no-extensions", "-e", "builtin:mcp"]}},
        "embed": [
            {"dashboardOrigin": f"http://{host}:9797", "frameBaseUrl": "http://127.0.0.1:6820"}
            for host in ("127.0.0.1", "localhost")
        ],
    }
    shared.write_text(json.dumps({"paseoRuntime": block}))
    for name, folder in (("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(name, str(tmp_path / folder))
    settings = tmp_path / "settings.json"
    settings.write_text((root / "scripts/harness/shared/agents-remember-settings.json").read_text())
    before = shared.read_bytes()
    result = adapter.render(
        Namespace(
            settings=settings,
            workspace=tmp_path,
            coordination=coordination,
            host_port=6820,
            dashboard_port=9797,
            repo="repo",
        )
    )
    assert result["invocations"] == 1 and shared.read_bytes() == before
    assert set(result["hostDifferenceKeys"]) == {
        "installPrefix",
        "home",
        "listen",
        "providers",
        "embed",
    }
    host = load_config(settings).paseo_runtime
    assert host is not None and host.providers == block["providers"]
    assert result["sharedBeforeSha256"] == result["sharedAfterSha256"]
    assert (
        result["renderedSettingsSha256"]
        == __import__("hashlib").sha256(settings.read_bytes()).hexdigest()
    )
    assert result["rendererSource"] == str(root / "scripts/harness/render_starter.py")


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
    write_shared_runtime(tmp_path, settings)
    shared = user_agentic_settings_path()
    document = json.loads(shared.read_text())
    document["paseoRuntime"]["version"] = "*"
    shared.write_text(json.dumps(document))
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "coordinationRoot": str(tmp_path / "coordination"),
                "workspaceRoot": str(tmp_path / "projects"),
            }
        )
    )
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
        assert "'*'" in notice and settings.version in notice and str(shared) in notice
    for result in (
        ensure_host(cfg, runner=fake, reader=fake.reader, probe=free),
        observe_host(cfg, runner=fake, reader=fake.reader),
    ):
        assert result.line.count("paseoRuntime.version") == 1
        assert result.line.count(str(shared)) == 1
        assert settings.version in result.line and "'*'" in result.line


@pytest.mark.parametrize("both", [False, True])
def test_host_and_all_standalone_starters_refuse_old_location_before_any_write(
    tmp_path, monkeypatch, both
):
    root = Path(__file__).resolve().parents[2]
    coordination = tmp_path / "old coordination"
    old = coordination / "system/settings.json"
    old.parent.mkdir(parents=True)
    # A location refusal must precede even parsing the removed file.
    old.write_bytes(b"\xff old settings must not be read")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    new = user_agentic_settings_path()
    if both:
        new.parent.mkdir(parents=True)
        new.write_bytes(b"\xff existing user settings must not be read")
    before = old.read_bytes(), new.read_bytes() if both else None
    with pytest.raises(PaseoRuntimeSettingsError, match="location-upgrade-required") as caught:
        load_shared_paseo_runtime(coordination)
    for path in (old, new):
        assert str(path) in str(caught.value)
    for harness in (
        ".claude",
        ".codex",
        ".cursor",
        ".github-vscode",
        ".hermes",
        ".openclaw",
        ".pi",
        ".agents",
    ):
        workspace = tmp_path / harness.removeprefix(".")
        (workspace / "repo").mkdir(parents=True)
        starter = workspace / harness / "render-starter.py"
        starter.parent.mkdir()
        shutil.copyfile(root / harness / "render-starter.py", starter)
        original = starter.read_bytes()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(starter),
                "--repo",
                "repo",
                "--coordination-root",
                str(coordination),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "location-upgrade-required" in result.stderr
        assert str(old) in result.stderr and str(new) in result.stderr
        assert "agents-remember upgrade-user-settings --coordination-root" in result.stderr
        assert starter.read_bytes() == original
        assert sorted(
            p.relative_to(workspace).as_posix() for p in workspace.rglob("*") if p.is_file()
        ) == [f"{harness}/render-starter.py"]
    assert (old.read_bytes(), new.read_bytes() if both else None) == before


def test_whole_file_upgrade_retains_configured_host_and_all_other_families(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "sandbox/config"))
    coordination = tmp_path / "sandbox/coordination"
    settings = runtime_settings(tmp_path / "sandbox")
    write_shared_runtime(tmp_path, settings)
    new = user_agentic_settings_path()
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
    assert not old.exists() and (new.read_bytes(), stat.S_IMODE(new.stat().st_mode)) == before
    actual = load_shared_paseo_runtime(coordination)
    assert actual is not None and actual.source_path == new
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
    outside = user_agentic_settings_path()
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"\xff must not be read by the sandbox")
    layout = SandboxLayout(tmp_path / "sandbox")
    layout.user_settings_file.parent.mkdir(parents=True)
    layout.user_settings_file.write_text(json.dumps({"paseoRuntime": None, "other": "owned"}))
    environment = sandbox_environment(os.environ, layout)
    assert environment["HOME"] == str(real_home)
    with patch.dict(os.environ, environment, clear=True):
        assert user_agentic_settings_path() == layout.user_settings_file
        assert load_shared_paseo_runtime(layout.coordination) is None
    assert outside.read_bytes() == b"\xff must not be read by the sandbox"
