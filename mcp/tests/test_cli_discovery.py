"""Tests for the CLI trusted-settings discovery (260703 L1)."""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from _user_settings_guard import user_settings_path_for_write

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.runtime.install import run_runtime_install
from agents_remember.cli import __main__ as cli
from agents_remember.cli import dashboard, settings_provenance
from agents_remember.cli.discovery import (
    ConfigDiscoveryError,
    discover_config,
)
from agents_remember.install import runtime
from agents_remember.install.mcp_settings import copy_mcp_settings
from agents_remember.kernel.primitives.mcp_settings_location import mcp_settings_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.user_settings_location import UserSettingsLocationError
from agents_remember.models.core import ServingBuildPayload
from agents_remember.serving.daemon import DaemonState, daemon_state_directory, write_state
from agents_remember.serving.daemon_identity import DashboardIdentity


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _settings(path: Path, coordination_root: Path) -> Path:
    """A minimal USABLE settings file: coordinationRoot must exist and be absolute."""
    coordination_root.mkdir(parents=True, exist_ok=True)
    return _write(
        path, json.dumps({"version": 1, "coordinationRoot": coordination_root.as_posix()})
    )


def _mcp_json(directory: Path, config_path: Path | None, server: str = "agents-remember") -> Path:
    args = ["--config", config_path.as_posix()] if config_path else []
    return _write(
        directory / ".mcp.json",
        json.dumps({"mcpServers": {server: {"command": "uvx", "args": args}}}),
    )


class DiscoverConfigTests(unittest.TestCase):
    def test_user_config_wins_over_workspace_registrations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}):
                convention = _settings(
                    root / "config/agents-remember/agents-remember-settings.json",
                    root / "coordination",
                )
                other = _settings(
                    root / ".claude/mcp/agents-remember-settings.json", root / "coordination"
                )
                _mcp_json(root, other)
                self.assertEqual(discover_config(), convention)

    def test_current_directory_never_selects_another_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}):
                settings = _settings(
                    root / "config/agents-remember/agents-remember-settings.json",
                    root / "coordination",
                )
                near = _settings(root / "workspace/other.json", root / "coordination")
                _mcp_json(root / "workspace", near)
                with mock.patch.object(
                    Path, "cwd", side_effect=AssertionError("discovery must not inspect cwd")
                ):
                    self.assertEqual(discover_config(), settings)

    def test_malformed_and_foreign_registrations_do_not_affect_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}):
                settings = _settings(
                    root / "config/agents-remember/agents-remember-settings.json",
                    root / "coordination",
                )
                _write(root / "workspace/.mcp.json", "{not json")
                _mcp_json(root / "workspace/child", None)
                _mcp_json(root / "workspace/child/foreign", None, server="someone-else")
                self.assertEqual(discover_config(), settings)

    def test_miss_names_user_config_and_explicit_remedy_without_workspace_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}):
                _settings(root / ".claude/mcp/agents-remember-settings.json", root / "coordination")
                with self.assertRaises(ConfigDiscoveryError) as ctx:
                    discover_config()
                self.assertIn(
                    str(root / "config/agents-remember/agents-remember-settings.json"),
                    str(ctx.exception),
                )
                self.assertIn("--config", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()


def test_cli_reports_actual_discovery_and_preserves_explicit_settings(
    tmp_path, monkeypatch, capsys
):

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    coordination = tmp_path / "coordination"
    coordination.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    convention = _settings(
        tmp_path / "config/agents-remember/agents-remember-settings.json", coordination
    )
    explicit = _settings(workspace / "explicit.json", coordination)
    for path in (convention, explicit):
        data = json.loads(path.read_text())
        data["workspaceRoot"] = str(workspace)
        path.write_text(json.dumps(data))
    monkeypatch.chdir(workspace)
    selected = []

    def run(arguments):
        resolved = dashboard._resolve_settings(arguments)
        assert resolved is not None
        selected.append(resolved[0])
        print(json.dumps({"ok": True}))
        return 0

    monkeypatch.setattr(dashboard, "run", run)
    assert cli.main(["dashboard", "--status"]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out) == {"ok": True}
    assert f"settings discovered: {convention}" in result.err
    assert selected == [str(convention)]
    monkeypatch.setattr(
        dashboard,
        "discover_config",
        lambda: (_ for _ in ()).throw(AssertionError("explicit config must not discover")),
    )
    assert cli.main(["dashboard", "--status", "--config", str(explicit)]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out) == {"ok": True}
    assert "settings discovered:" not in result.err
    assert selected[-1] == str(explicit)


def test_discovery_names_actual_daemons_other_settings_only_for_same_root(
    tmp_path, monkeypatch, capsys
):

    workspace = tmp_path / "workspace"
    coordination = tmp_path / "coordination"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    convention = _settings(
        tmp_path / "config/agents-remember/agents-remember-settings.json", coordination
    )
    other = _settings(workspace / ".codex/mcp/agents-remember-settings.json", coordination)
    stale = _settings(workspace / "stale-settings.json", coordination)
    directory = daemon_state_directory(coordination)
    directory.mkdir(parents=True)
    write_state(
        directory, DaemonState(12, "127.0.0.1", 8765, "old", str(stale), "log", "then", "old")
    )
    actual = DashboardIdentity(
        99, ServingBuildPayload(version="new", bootedAt="now", sourceDigest="new"), str(other), None
    )
    monkeypatch.setattr(settings_provenance, "observe_dashboard", lambda host, port: actual)
    before = {
        path: path.read_bytes() for path in (convention, other, stale, directory / "daemon.json")
    }
    assert discover_config() == convention
    result = capsys.readouterr()
    assert result.out == ""
    assert str(convention) in result.err and str(other) in result.err
    assert "dashboard daemon pid 99" in result.err and "same coordination root" in result.err
    assert str(stale) not in result.err
    assert all(path.read_bytes() == body for path, body in before.items())
    foreign = tmp_path / "another-coordination"
    foreign.mkdir()
    other.write_text(json.dumps({"coordinationRoot": str(foreign)}))
    assert discover_config() == convention
    assert str(other) not in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "[]",
        "{}",
        '{"coordinationRoot":"relative"}',
        '{"coordinationRoot":"/does-not-exist-ar-fixture"}',
    ],
)
def test_user_config_requires_a_real_absolute_coordination_root(tmp_path, monkeypatch, body):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _write(tmp_path / "config/agents-remember/agents-remember-settings.json", body)
    with pytest.raises(ConfigDiscoveryError):
        discover_config()


def test_default_home_and_explicit_xdg_share_the_user_settings_folder(tmp_path, monkeypatch):

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert (
        mcp_settings_path()
        == tmp_path / "home/.config/agents-remember/agents-remember-settings.json"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "other-config"))
    assert mcp_settings_path() == user_settings_path_for_write().with_name(
        "agents-remember-settings.json"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    with pytest.raises(UserSettingsLocationError, match="absolute"):
        mcp_settings_path()


def _installation_settings(tmp_path, monkeypatch):

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("AR_EXPERIMENT", raising=False)
    monkeypatch.setattr(runtime, "lifecycle_settings_from_config", lambda: {})
    monkeypatch.setattr(
        runtime, "stop_provider_watchers_before_refresh", lambda _rebind: {"ok": True}
    )
    source = tmp_path / "source"
    for name in ("codegraphcontext.txt", "grepai.txt"):
        _write(source / "runtime/providers/requirements" / name, "fixture\n")
    (source / "runtime/skills").mkdir()
    (source / "runtime/docs").mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = McpRuntimeConfig(
        config_path=tmp_path / "explicit.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=workspace,
        transcript_root=tmp_path / "coordination/logs/mcp",
    )
    request = runtime.RuntimeInstallRequest(
        source_root=source, install_provider_deps=False, experiment=""
    )
    return runtime, config, request


def _workspace_files(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mode)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_actual_install_copies_once_leaves_old_and_harness_folders_untouched(tmp_path, monkeypatch):
    runtime, config, request = _installation_settings(tmp_path, monkeypatch)
    old = _settings(
        config.workspace_root / ".claude/mcp/agents-remember-settings.json",
        config.coordination_root,
    )
    old.chmod(0o600)
    for name in (
        ".agents",
        ".claude",
        ".codex",
        ".cursor",
        ".github-vscode",
        ".hermes",
        ".openclaw",
        ".pi",
        ".vscode",
        ".opencode",
        ".deepseek",
        ".deepcode",
        ".kimi-code",
        ".eve",
    ):
        _write(config.workspace_root / name / "user-owned.txt", name)
    _write(config.workspace_root / ".mcp.json", "user-owned registration")
    before = _workspace_files(config.workspace_root)
    harness_before = {
        str(p.relative_to(config.workspace_root))
        for p in config.workspace_root.rglob("*")
        if p.parts[len(config.workspace_root.parts)] != ".agents-remember"
    }
    first = runtime.install_runtime_from_config(config, request)
    target = tmp_path / "config/agents-remember/agents-remember-settings.json"
    assert first["mcpSettings"]["state"] == "copied"
    harness_after = {
        str(p.relative_to(config.workspace_root))
        for p in config.workspace_root.rglob("*")
        if p.parts[len(config.workspace_root.parts)] not in {".agents-remember", ".gitignore"}
    }
    assert harness_after == harness_before
    assert target.read_bytes() == old.read_bytes() and target.stat().st_mode == old.stat().st_mode
    # AR's own execution folder/ignore rule stays as before; harness-owned files stay exact.
    assert (config.workspace_root / ".agents-remember/role-executions").is_dir()
    assert all(
        (config.workspace_root / path).read_bytes() == body
        and (config.workspace_root / path).stat().st_mode == mode
        for path, (body, mode) in before.items()
    )
    target.write_bytes(b"user's current settings\n")
    second = runtime.install_runtime_from_config(config, request)
    assert second["mcpSettings"]["state"] == "already-present"
    assert target.read_bytes() == b"user's current settings\n"
    assert all(
        (config.workspace_root / path).read_bytes() == body
        and (config.workspace_root / path).stat().st_mode == mode
        for path, (body, mode) in before.items()
    )


def test_actual_install_dry_run_never_creates_settings_or_harness_folders(tmp_path, monkeypatch):

    runtime, config, request = _installation_settings(tmp_path, monkeypatch)
    _settings(
        config.workspace_root / ".claude/mcp/agents-remember-settings.json",
        config.coordination_root,
    )
    before = _workspace_files(config.workspace_root)
    result = runtime.install_runtime_from_config(config, replace(request, dry_run=True))
    assert result["mcpSettings"]["state"] == "would-copy"
    assert not (tmp_path / "config").exists()
    assert _workspace_files(config.workspace_root) == before


def test_actual_install_creates_no_harness_folder_without_a_source(tmp_path, monkeypatch):
    runtime, config, request = _installation_settings(tmp_path, monkeypatch)
    result = runtime.install_runtime_from_config(config, request)
    assert result["mcpSettings"]["state"] == "no-source"
    assert not (tmp_path / "config").exists()
    assert [p.name for p in config.workspace_root.iterdir() if p.is_dir()] == [".agents-remember"]


def test_settings_copy_never_opens_config_permissions(tmp_path, monkeypatch):
    _runtime, config, request = _installation_settings(tmp_path, monkeypatch)
    old = _write(config.workspace_root / ".claude/mcp/agents-remember-settings.json", "{}")
    target = tmp_path / "config/agents-remember/agents-remember-settings.json"
    target.parent.mkdir(parents=True)
    target.parent.chmod(0o555)
    old_mode = old.stat().st_mode
    original = os.open
    denied = []

    def closed(path, *args, **kwargs):
        if path == target and not target.parent.stat().st_mode & 0o222:
            # Root can bypass mode bits; model the kernel's denial at this exact destination.
            denied.append(Path(path))
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", closed)
    with (
        mock.patch(
            "agents_remember.application.runtime.install.install_host",
            return_value={"ok": True, "configured": False},
        ) as host,
        mock.patch(
            "agents_remember.application.runtime.install.ensure_running_dashboard",
            return_value=mock.Mock(action="unchanged", detail="fixture dashboard", state=None),
        ) as dashboard,
        mock.patch(
            "agents_remember.application.runtime.install.server_inventory", return_value=[]
        ) as inventory,
    ):
        result = run_runtime_install(config, request)
    assert denied == [target]
    assert result["ok"] is True
    assert result["mcpSettings"]["state"] == "permission"
    assert result["mcpSettings"]["userPath"] == str(target)
    assert result["mcpSettings"]["sourcePath"] == str(old)
    assert str(target) in result["mcpSettings"]["remedy"]
    assert "developer" in result["mcpSettings"]["remedy"]
    assert result["host"] == {"ok": True, "configured": False}
    assert result["dashboard"] == {"action": "unchanged", "detail": "fixture dashboard"}
    assert result["runningServers"] == []
    host.assert_called_once_with(config, False)
    dashboard.assert_called_once_with(config)
    inventory.assert_called_once_with(config)
    assert (config.workspace_root / ".agents-remember/role-executions").is_dir()
    assert target.parent.stat().st_mode & 0o777 == 0o555
    assert old.stat().st_mode == old_mode
    assert old.read_bytes() == b"{}" and not target.exists()


def test_copy_refuses_a_destination_created_after_the_absence_probe(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    old = _write(workspace / ".claude/mcp/agents-remember-settings.json", "source")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = mcp_settings_path()
    original = os.open

    def appeared(path, flags, mode):
        assert path == target and flags & os.O_EXCL
        target.write_bytes(b"concurrent owner")
        return original(path, flags, mode)

    monkeypatch.setattr(os, "open", appeared)
    result = copy_mcp_settings(workspace, dry_run=False)
    assert result["state"] == "refused"
    assert result["userPath"] == str(target) and str(target) in result["remedy"]
    assert target.read_bytes() == b"concurrent owner" and old.read_bytes() == b"source"


def test_missing_user_config_never_uses_a_registration_with_valid_explicit_path(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    explicit = _settings(tmp_path / "elsewhere.json", tmp_path / "coordination")
    _mcp_json(tmp_path, explicit)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigDiscoveryError):
        discover_config()


def test_relative_config_home_is_a_discovery_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    with pytest.raises(ConfigDiscoveryError, match="absolute"):
        discover_config()


def test_copy_failure_removes_only_its_own_incomplete_destination(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    old = _write(workspace / ".claude/mcp/agents-remember-settings.json", "source")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def interrupted(source, destination):
        destination.write(b"partial")
        raise OSError("interrupted fixture copy")

    monkeypatch.setattr("agents_remember.install.mcp_settings.shutil.copyfileobj", interrupted)
    result = copy_mcp_settings(workspace, dry_run=False)
    assert result["state"] == "refused" and "interrupted" in result["detail"]
    assert result["userPath"] == str(mcp_settings_path())
    assert str(mcp_settings_path()) in result["remedy"]
    assert old.read_bytes() == b"source" and not mcp_settings_path().exists()


def _settings_copy_error(path, error_number, observed):
    observed.append(str(path))
    raise OSError(error_number, os.strerror(error_number), str(path))


def _patch_settings_copy_io(patcher, paths, failure, error_number, observed):
    old, target = paths
    original_open = Path.open
    original_lstat = Path.lstat
    original_mkdir = Path.mkdir
    original_create = os.open

    def source_open(path, *args, **kwargs):
        if failure == "source-open" and path == old and args == ("rb",):
            _settings_copy_error(path, error_number, observed)
        return original_open(path, *args, **kwargs)

    def probe(path, *args, **kwargs):
        if (failure == "source-probe" and path == old) or (
            failure == "destination-probe" and path == target
        ):
            _settings_copy_error(path, error_number, observed)
        return original_lstat(path, *args, **kwargs)

    def mkdir(path, *args, **kwargs):
        if failure == "destination-parent" and path == target.parent:
            _settings_copy_error(path, error_number, observed)
        return original_mkdir(path, *args, **kwargs)

    def create(path, *args, **kwargs):
        if failure == "destination-create" and path == target:
            _settings_copy_error(path, error_number, observed)
        return original_create(path, *args, **kwargs)

    patcher.setattr(Path, "open", source_open)
    patcher.setattr(Path, "lstat", probe)
    patcher.setattr(Path, "mkdir", mkdir)
    patcher.setattr(os, "open", create)


def _patch_settings_copy_transfer(patcher, paths, failure, error_number, observed):
    old, target = paths
    original_metadata = shutil.copystat

    def partial_copy(source, destination):
        destination.write(b"partial")
        _settings_copy_error(target, error_number, observed)

    def metadata(source, destination, **kwargs):
        if source == old and destination == target:
            _settings_copy_error(destination, error_number, observed)
        return original_metadata(source, destination, **kwargs)

    if failure == "partial-copy":
        patcher.setattr("agents_remember.install.mcp_settings.shutil.copyfileobj", partial_copy)
    if failure == "copy-metadata":
        patcher.setattr("agents_remember.install.mcp_settings.shutil.copystat", metadata)


@pytest.mark.parametrize(
    ("failure", "error_number", "state"),
    [
        ("source-open", errno.EACCES, "permission"),
        ("source-open", errno.EIO, "refused"),
        ("source-probe", errno.EIO, "refused"),
        ("destination-probe", errno.EACCES, "permission"),
        ("destination-create", errno.EROFS, "refused"),
        ("destination-parent", errno.EIO, "refused"),
        ("partial-copy", errno.EIO, "refused"),
        ("copy-metadata", errno.EIO, "refused"),
    ],
)
def test_runtime_install_reports_settings_oserror_and_completes(
    tmp_path, monkeypatch, failure, error_number, state
):
    _runtime, config, request = _installation_settings(tmp_path, monkeypatch)
    old = _write(config.workspace_root / ".claude/mcp/agents-remember-settings.json", "{}")
    target = tmp_path / "config/agents-remember/agents-remember-settings.json"
    target.parent.mkdir(parents=True)
    old_mode = old.stat().st_mode
    parent_mode = target.parent.stat().st_mode
    observed = []
    with monkeypatch.context() as patcher:
        _patch_settings_copy_io(patcher, (old, target), failure, error_number, observed)
        _patch_settings_copy_transfer(patcher, (old, target), failure, error_number, observed)
        with (
            mock.patch(
                "agents_remember.application.runtime.install.install_host",
                return_value={"ok": True, "configured": False},
            ) as host,
            mock.patch(
                "agents_remember.application.runtime.install.ensure_running_dashboard",
                return_value=mock.Mock(action="unchanged", detail="fixture dashboard", state=None),
            ) as dashboard,
            mock.patch(
                "agents_remember.application.runtime.install.server_inventory", return_value=[]
            ) as inventory,
        ):
            result = run_runtime_install(config, request)
    assert observed
    assert result["ok"] is True
    settings = result["mcpSettings"]
    assert settings["state"] == state
    assert settings["sourcePath"] == str(old) and settings["userPath"] == str(target)
    assert os.strerror(error_number) in settings["detail"]
    assert str(old) in settings["remedy"] and str(target) in settings["remedy"]
    assert "developer" in settings["remedy"] and "runtime_install" in settings["remedy"]
    assert result["host"] == {"ok": True, "configured": False}
    assert result["dashboard"] == {"action": "unchanged", "detail": "fixture dashboard"}
    assert result["runningServers"] == []
    host.assert_called_once_with(config, False)
    dashboard.assert_called_once_with(config)
    inventory.assert_called_once_with(config)
    assert old.read_bytes() == b"{}" and old.stat().st_mode == old_mode
    assert target.parent.stat().st_mode == parent_mode
    assert not target.exists()


def _config_help_by_command() -> dict[str, str]:
    """Every ``--config`` help of the umbrella CLI, keyed by its subcommand path."""

    found: dict[str, str] = {}

    def walk(parser: argparse.ArgumentParser, prefix: str) -> None:
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    walk(child, f"{prefix} {name}".strip())
            elif "--config" in action.option_strings:
                found[prefix] = action.help or ""

    walk(cli.build_parser(), "")
    return found


@pytest.mark.parametrize(
    "command", ["memory-citations", "providers-remove", "removed-stores", "knowledge-bootstrap"]
)
def test_omitted_config_help_names_the_user_settings_file_not_the_directory(command: str) -> None:
    """L84-CUR-01: an omitted --config reads the user settings file, whatever the directory."""

    text = " ".join(_config_help_by_command()[command].split())
    assert (
        "Omit to use the user settings file ~/.config/agents-remember/agents-remember-settings.json "
        "(under XDG_CONFIG_HOME when set), whatever the current directory." in text
    )


def test_no_config_help_claims_directory_discovery() -> None:
    """L84-CUR-01: discover_config() never searches the caller's directory, so no help says so."""

    helps = _config_help_by_command()
    assert {"dashboard", "memory-citations", "providers-remove", "removed-stores"} <= set(helps)
    claims = {
        command: text
        for command, text in helps.items()
        if "working directory" in text
        or "discover it from" in text
        or "discovery from the current directory" in text
    }
    assert claims == {}


def test_daemon_help_names_the_source_digest_restart() -> None:
    """L84-CUR-02: ensure() restarts a daemon whose Python source digest differs."""

    parser = argparse.ArgumentParser()
    dashboard.add_arguments(parser)
    (daemon,) = [action for action in parser._actions if "--daemon" in action.option_strings]
    assert "restarts one whose version, Python source digest, host, or port differs." in (
        daemon.help or ""
    )


# uvx (`uv tool run`) reads its own options first, then one package-spec command, then passes every later
# argument to that command. A value-taking option consumes the next argument whatever it looks like.
_UVX_VALUE_OPTIONS = frozenset(
    {
        "--refresh-package",
        "--from",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--python",
        "-p",
        "--index",
        "--default-index",
        "--index-url",
        "--extra-index-url",
        "--find-links",
        "-f",
        "--config-file",
        "--cache-dir",
        "--directory",
        "--project",
    }
)
_UVX_FLAGS = frozenset(
    {"--refresh", "--isolated", "--offline", "--no-cache", "-n", "--quiet", "-q", "--verbose", "-v"}
)


def _uvx_split(args: list[str]) -> tuple[list[tuple[str, str | None]], str, list[str]]:
    """Split a uvx argv as uvx does: (its options, the package command, the command's arguments)."""

    options: list[tuple[str, str | None]] = []
    index = 0
    while index < len(args):
        token = args[index]
        if not token.startswith("-"):
            return options, token, args[index + 1 :]
        name, equals, inline = token.partition("=")
        if name in _UVX_VALUE_OPTIONS:
            if equals:
                options.append((name, inline))
                index += 1
            elif index + 1 < len(args):
                options.append((name, args[index + 1]))
                index += 2
            else:
                raise ValueError(f"uvx option {token} has no value")
        elif name in _UVX_FLAGS and not equals:
            options.append((name, None))
            index += 1
        else:
            raise ValueError(f"uvx has no option {token!r} before its command")
    raise ValueError("the uvx arguments name no package command")


def test_the_example_registration_parses_as_uvx_options_then_the_package_command() -> None:
    """L84-LC-03: the refresh option takes its package, the server spec follows, --config reaches it."""

    example = json.loads(
        (REPOSITORY_ROOT / "examples/mcp/server.example.json").read_text(encoding="utf-8")
    )
    assert example["command"] == "uvx"
    options, command, command_args = _uvx_split(example["args"])
    assert options == [("--refresh-package", "agents-remember-mcp")]
    assert command == "agents-remember-mcp@latest"
    assert command_args == [
        "--config",
        "<ABSOLUTE_USER_CONFIG_FOLDER>/agents-remember/agents-remember-settings.json",
    ]
    # The split is shown biting on the round-9 form: the refresh option swallowed the server spec, so
    # --config became an option of uvx itself.
    with pytest.raises(ValueError, match="no option '--config'"):
        _uvx_split(["--refresh-package", "agents-remember-mcp@latest", "--config", "settings.json"])
