from __future__ import annotations

import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from _user_settings_guard import user_settings_path_for_write
from agents_remember.cli.__main__ import main
from agents_remember.install import provider_watchers as install_provider_watchers
from agents_remember.install import runtime as install_runtime
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    agentic_settings_path,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.server import create_server
from agents_remember.worktrees.services import reset_worktree_services
from mcp.shared.memory import create_connected_server_and_client_session

SETTINGS_REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "reference" / "settings-json.md"
# The two commands the settings reference gives a user, as it prints them.
DOCUMENTED_EDIT_COMMAND = (
    'chmod u+w "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember" '
    '"${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember/settings.json"'
)
DOCUMENTED_CLOSE_COMMAND = (
    'chmod 444 "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember/settings.json" '
    '&& chmod 555 "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember"'
)


def write_file(path: Path, content: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def call_registered_tool(
    config: McpRuntimeConfig, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Call one tool as a harness does, through the server's registered handler."""
    server = create_server(config)

    async def call() -> dict[str, Any]:
        with anyio.fail_after(60):
            async with create_connected_server_and_client_session(server._mcp_server) as client:
                result = await client.call_tool(name, arguments)
        assert not result.isError and result.structuredContent is not None, result.content
        return result.structuredContent

    try:
        return anyio.run(call)
    finally:
        reset_worktree_services()


def create_runtime_source(root: Path) -> Path:
    source_root = root / "source"
    runtime_root = source_root / "runtime"
    write_file(source_root / "mcp" / "src" / "agents_remember" / "mcp" / "__init__.py")
    write_file(source_root / "mcp" / "requirements.txt", "mcp==1.12.4\n")
    for source_rel in install_runtime.AGENTS_MD_TARGETS:
        write_file(runtime_root / source_rel)
    write_file(runtime_root / "skills" / "U-01-core-skills" / "C-04" / "SKILL.md")
    write_file(runtime_root / "providers" / "requirements" / "codegraphcontext.txt")
    write_file(runtime_root / "providers" / "requirements" / "grepai.txt")
    write_file(runtime_root / "providers" / "patches" / "codegraphcontext" / "patch.diff")
    return source_root


def enabled_provider_settings(*provider_ids: str) -> dict:
    return {
        "contextProviders": {
            "enabled": True,
            "providers": {provider_id: {"enabled": True} for provider_id in provider_ids},
        }
    }


def watcher_result(action: str, *, ok: bool = True, partial: bool = False) -> dict:
    return {
        "provider": "watchers",
        "action": action,
        "ok": ok,
        "partial": partial,
        "recoveryActions": [],
    }


class InstallRuntimeTests(unittest.TestCase):
    def test_runtime_install_preserves_docker_provider_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            providers_root = coordination_root / "providers"
            cgc_exe = providers_root / "_venvs" / "codegraphcontext" / "Scripts" / "cgc.exe"
            cgc_state = (
                providers_root
                / "runners"
                / "codegraphcontext"
                / "repo"
                / ".codegraphcontext"
                / "state.json"
            )
            grepai_watch_log = (
                providers_root / "runners" / "grepai" / "memory-repos" / "logs" / "watch.log"
            )
            cgc_data = providers_root / "data" / "codegraphcontext" / "graph.db"
            cgc_log = providers_root / "logs" / "codegraphcontext" / "watch.log"
            central_cgc_log = (
                coordination_root / "logs" / "providers" / "codegraphcontext" / "watch.log"
            )
            old_script = coordination_root / "scripts" / "install-skills.sh"

            write_file(providers_root / "_bin" / "grepai.exe", "legacy grepai\n")
            write_file(cgc_exe, "live cgc\n")
            write_file(cgc_state)
            write_file(grepai_watch_log)
            write_file(cgc_data, "live graph\n")
            write_file(cgc_log, "live log\n")
            write_file(central_cgc_log, "live central log\n")
            write_file(providers_root / "old.txt")
            write_file(old_script)

            # The stop is always attempted (MIK-R112 F03); keep this fixture
            # hermetic with an ok stop and no watcher side effects.
            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=lambda args, action: watcher_result(action),
            ):
                summary = install_runtime.install_runtime(
                    source_root,
                    coordination_root,
                    dry_run=False,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings={}, timeout=1800, enabled=False
                    ),
                )

            self.assertEqual(summary.dependency_runs, 0)
            self.assertFalse((providers_root / "_bin" / "grepai.exe").exists())
            self.assertFalse(cgc_exe.exists())
            self.assertTrue(cgc_state.exists())
            self.assertTrue(grepai_watch_log.exists())
            self.assertTrue(cgc_data.exists())
            self.assertFalse(cgc_log.exists())
            self.assertTrue(central_cgc_log.exists())
            self.assertFalse((providers_root / "old.txt").exists())
            self.assertTrue((providers_root / "requirements" / "grepai.txt").exists())
            self.assertFalse((coordination_root / "scripts").exists())
            leftovers = {item["path"] for item in summary.leftover_provider_folders}
            self.assertIn((providers_root / "runners").as_posix(), leftovers)
            self.assertIn((providers_root / "data").as_posix(), leftovers)
            self.assertIn("leftover_provider_bytes=", summary.report())

    def test_runtime_install_attempts_only_the_old_watcher_stop(self) -> None:
        """MIK-R112 F02: knobs on, prior watcher settings — stop only, zero install/start/restart."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            events: list[str] = []

            def fake_watchers(args, action):
                events.append(f"watchers:{action}")
                return watcher_result(action)

            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=fake_watchers,
            ):
                summary = install_runtime.install_runtime(
                    source_root,
                    coordination_root,
                    dry_run=False,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings=enabled_provider_settings("grepai-memory"),
                        timeout=1800,
                        enabled=True,
                    ),
                )

            self.assertEqual(events, ["watchers:stop"])
            self.assertEqual(summary.dependency_runs, 0)
            self.assertEqual(summary.unused_provider_keys, ["grepai-memory"])
            self.assertIn("unused_provider_keys=[grepai-memory]", summary.report())
            # The install itself completed: scaffold files landed.
            self.assertTrue((coordination_root / "AGENTS.md").exists())

    def test_runtime_install_failed_stop_continues_and_reports_the_process(self) -> None:
        """MIK-R112 F03: a failed old-watcher stop is reported, not fatal; install continues."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            events: list[str] = []

            def fake_watchers(args, action):
                events.append(f"watchers:{action}")
                return {
                    **watcher_result(action, ok=False),
                    "process": "process12345",
                    "error": "stop refused",
                }

            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=fake_watchers,
            ):
                summary = install_runtime.install_runtime(
                    source_root,
                    coordination_root,
                    dry_run=False,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings=enabled_provider_settings("codegraphcontext-code"),
                        timeout=1800,
                        enabled=True,
                    ),
                )

            self.assertEqual(events, ["watchers:stop"])
            self.assertTrue((coordination_root / "AGENTS.md").exists())
            assert summary.provider_watcher_rebind is not None
            self.assertIs(summary.provider_watcher_rebind.ok, False)
            self.assertTrue(summary.provider_watcher_rebind.recovery_actions)
            self.assertTrue(
                any(
                    "process12345" in message
                    for message in summary.provider_watcher_rebind.messages
                ),
                "the failed stop's process must be named in the install result",
            )
            self.assertEqual(summary.unused_provider_keys, ["codegraphcontext-code"])

    def test_runtime_install_stop_is_independent_of_dependency_knobs(self) -> None:
        """MIK-R112 F03: knobs off with a prior watcher still attempts the stop; keys still reported."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            events: list[str] = []
            settings = enabled_provider_settings("grepai-memory", "codegraphcontext-code")
            before = json.loads(json.dumps(settings))

            def fake_watchers(args, action):
                events.append(f"watchers:{action}")
                return watcher_result(action)

            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=fake_watchers,
            ):
                summary = install_runtime.install_runtime(
                    source_root,
                    coordination_root,
                    dry_run=False,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings=settings, timeout=1800, enabled=False
                    ),
                )

            self.assertEqual(events, ["watchers:stop"])
            self.assertEqual(
                summary.unused_provider_keys,
                ["codegraphcontext-code", "grepai-memory"],
            )
            self.assertEqual(settings, before)


class AgenticSettingsSeedTests(unittest.TestCase):
    """Shared settings belong to setup; runtime_install never overwrites developer settings."""

    def test_existing_settings_file_is_never_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            settings_path = user_settings_path_for_write()
            developer_content = '{"orchestration": {"spawn": {"harness": "codex"}}}\n'
            write_file(settings_path, developer_content)

            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=lambda args, action: watcher_result(action),
            ):
                install_runtime.install_runtime(
                    source_root,
                    coordination_root,
                    dry_run=False,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings={}, timeout=1800, enabled=False
                    ),
                )

            self.assertEqual(settings_path.read_text(encoding="utf-8"), developer_content)

    def test_dry_run_installs_assets_without_writing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"

            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=lambda args, action: watcher_result(action),
            ):
                summary = install_runtime.install_runtime(
                    source_root,
                    coordination_root,
                    dry_run=True,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings={}, timeout=1800, enabled=False
                    ),
                )

            self.assertFalse(user_settings_path_for_write().exists())
            self.assertGreater(summary.copied_files, 0)

    def test_public_upgrade_dry_run_then_move_preserves_the_entire_file_and_repeats_without_copy(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordination = Path(folder) / "coord"
            old = agentic_settings_path(coordination)
            new = user_settings_path_for_write()
            old.parent.mkdir(parents=True)
            original = b'{"orchestration":{"roles":{}},"otherFamily":{"untouched":true}}\r\n'
            old.write_bytes(original)
            old.chmod(0o600)
            command = ["upgrade-user-settings", "--coordination-root", str(coordination)]
            for arguments, state in [
                ([*command, "--dry-run"], "would-move"),
                (command, "moved"),
                (command, "already-current"),
            ]:
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(main(arguments), 0)
                self.assertEqual(json.loads(output.getvalue())["state"], state)
                if state == "would-move":
                    self.assertEqual(old.read_bytes(), original)
                    self.assertFalse(new.exists())
                else:
                    self.assertFalse(old.exists())
                    self.assertEqual(new.read_bytes(), original)
                    # MIK-R137 rule 4: the moved file is read-only and its
                    # folder is closed to new and removed entries.
                    self.assertEqual(stat.S_IMODE(new.stat().st_mode), 0o444)
                    self.assertEqual(stat.S_IMODE(new.parent.stat().st_mode), 0o555)
                    result = json.loads(output.getvalue())
                    self.assertIsNone(result["permissionsNote"])
                    self.assertFalse(result["protection"]["fileOpen"])
                    self.assertFalse(result["protection"]["folderOpen"])
            # The closed folder must open again for the temporary tree's cleanup.
            new.parent.chmod(0o755)

    def test_runtime_install_old_or_both_file_refusal_precedes_all_asset_writes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = create_runtime_source(root)
            coordination = root / "coord"
            old = agentic_settings_path(coordination)
            new = user_settings_path_for_write()
            old.parent.mkdir(parents=True)
            old.write_text("{}")
            for both in (False, True):
                if both:
                    new.parent.mkdir(parents=True, exist_ok=True)
                    new.write_text('{"user":"keep"}')
                with (
                    self.subTest(both=both),
                    self.assertRaisesRegex(AgenticSettingsError, "upgrade-user-settings") as caught,
                ):
                    install_runtime.install_runtime(
                        source,
                        coordination,
                        dry_run=False,
                        provider_deps=install_runtime.ProviderDependencyInstall(
                            settings={}, timeout=1800, enabled=False
                        ),
                    )
                self.assertIn(str(old), str(caught.exception))
                self.assertIn(str(new), str(caught.exception))
                self.assertFalse((coordination / "AGENTS.md").exists())
                self.assertEqual(old.read_text(), "{}")

    def test_upgrade_collision_and_failed_transfer_preserve_the_old_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            coordination = Path(folder) / "coord"
            old = agentic_settings_path(coordination)
            new = user_settings_path_for_write()
            old.parent.mkdir(parents=True)
            old.write_bytes(b"old complete bytes")
            new.parent.mkdir(parents=True, exist_ok=True)
            new.write_bytes(b"existing user bytes")
            with self.assertRaisesRegex(AgenticSettingsError, "upgrade-collision"):
                install_runtime.upgrade_user_settings(coordination, dry_run=False)
            self.assertEqual(old.read_bytes(), b"old complete bytes")
            self.assertEqual(new.read_bytes(), b"existing user bytes")
            new.unlink()
            with (
                patch.object(
                    install_runtime.shutil, "copyfileobj", side_effect=OSError("transfer failed")
                ),
                self.assertRaisesRegex(OSError, "transfer failed"),
            ):
                install_runtime.upgrade_user_settings(coordination, dry_run=False)
            self.assertEqual(old.read_bytes(), b"old complete bytes")


class UserSettingsProtectionReportTests(unittest.TestCase):
    """MIK-R137 rule 5: an open file is named with the close and pre-edit commands."""

    def test_open_file_and_folder_are_named_with_both_commands(self) -> None:
        with tempfile.TemporaryDirectory():
            path = user_settings_path_for_write()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
            path.chmod(0o644)
            report = install_runtime.user_settings_protection_report()
            self.assertEqual(report["userPath"], str(path))
            self.assertTrue(report["fileExists"])
            self.assertTrue(report["fileOpen"])
            self.assertTrue(report["folderOpen"])
            self.assertIn("chmod 444", report["closeCommand"])
            self.assertIn("chmod 555", report["closeCommand"])
            self.assertIn(str(path), report["closeCommand"])
            self.assertIn(str(path.parent), report["closeCommand"])
            self.assertIn("chmod u+w", report["editCommand"])
            self.assertIn(str(path), report["editCommand"])

    def test_closed_file_and_folder_are_reported_closed(self) -> None:
        with tempfile.TemporaryDirectory():
            path = user_settings_path_for_write()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}")
            install_runtime._close_user_settings_permissions(path)
            report = install_runtime.user_settings_protection_report()
            self.assertTrue(report["fileExists"])
            self.assertFalse(report["fileOpen"])
            self.assertFalse(report["folderOpen"])
            path.parent.chmod(0o755)


def _server_config(root: Path) -> McpRuntimeConfig:
    """A server configuration whose every root lies in the test's own folder."""
    coordination = root / "ar-coordination"
    coordination.mkdir()
    return McpRuntimeConfig(
        config_path=root / "mcp-settings.json",
        coordination_root=coordination,
        workspace_root=root / "workspace",
        transcript_root=coordination / "logs" / "mcp",
    )


def _settings_state(path: Path) -> tuple[bytes, int, int]:
    """The settings file's bytes, its mode and the mode of its folder."""
    return (
        path.read_bytes(),
        stat.S_IMODE(path.stat().st_mode),
        stat.S_IMODE(path.parent.stat().st_mode),
    )


def _closed_settings_under_a_path_with_a_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """The settings of a made-up account whose configuration folder is a custom one, closed.

    The folder's name holds what a shell would split or read: a space, a quote, a ``;`` and
    a ``$``. A command that names it has to carry it as one operand.
    """
    custom = tmp_path / "made up account" / "config home; it's $HOME"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(custom))
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"edited": false}\n')
    install_runtime._close_user_settings_permissions(path)
    return path


def _run_as_the_user(command: str, account: dict[str, str] | None = None) -> None:
    """Run one of the user's two commands in a shell, in the made-up account's environment."""
    environment = {"PATH": os.environ["PATH"], **(account or {})}
    subprocess.run(["sh", "-c", command], check=True, env=environment)


def _save_by_replacing(path: Path, content: bytes) -> None:
    """Save as an editor does that writes a new file beside the old one and renames it over."""
    replacement = path.with_name(f"{path.name}.new")
    replacement.write_bytes(content)
    os.replace(replacement, path)


def test_server_info_names_an_open_settings_file_or_folder_and_changes_nothing(
    tmp_path: Path,
) -> None:
    """MIK-R137 rule 5 on the report every start reads: open is named, closed is not."""
    config = _server_config(tmp_path)
    path = user_settings_path_for_write()
    folder = path.parent
    folder.mkdir(parents=True)
    path.write_bytes(b'{"version": 1}\n')
    both = (
        ["chmod", "444", str(path), "&&", "chmod", "555", str(folder)],
        ["chmod", "u+w", str(folder), str(path)],
    )
    try:
        for file_mode, folder_mode in ((0o644, 0o755), (0o444, 0o755), (0o644, 0o555)):
            path.chmod(file_mode)
            folder.chmod(folder_mode)
            before = _settings_state(path)

            notice = call_registered_tool(config, "server_info", {})["openUserSettings"]

            assert _settings_state(path) == before, (oct(file_mode), oct(folder_mode))
            assert notice["userPath"] == str(path) and notice["fileExists"] is True
            assert notice["fileOpen"] is (file_mode == 0o644)
            assert notice["folderOpen"] is (folder_mode == 0o755)
            assert (
                shlex.split(notice["closeCommand"]),
                shlex.split(notice["editCommand"]),
            ) == both

        path.chmod(0o444)
        folder.chmod(0o555)
        closed = _settings_state(path)
        assert "openUserSettings" not in call_registered_tool(config, "server_info", {})
        assert _settings_state(path) == closed == (b'{"version": 1}\n', 0o444, 0o555)

        # An open folder without a file takes a new one from anybody: it is named by itself.
        folder.chmod(0o755)
        path.unlink()
        notice = call_registered_tool(config, "server_info", {})["openUserSettings"]
        assert (notice["fileExists"], notice["fileOpen"], notice["folderOpen"]) == (
            False,
            False,
            True,
        )
        assert shlex.split(notice["closeCommand"]) == ["chmod", "555", str(folder)]
        assert shlex.split(notice["editCommand"]) == ["chmod", "u+w", str(folder)]
        assert list(folder.iterdir()) == [] and stat.S_IMODE(folder.stat().st_mode) == 0o755
    finally:
        folder.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="nothing is closed by mode to the superuser")
def test_the_reported_commands_carry_a_replacing_edit_under_a_path_with_a_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MIK-R137 rules 5 and 6: open, save by replacement, close; the product lifts nothing."""
    path = _closed_settings_under_a_path_with_a_space(tmp_path, monkeypatch)
    config = _server_config(tmp_path)
    try:
        with pytest.raises(PermissionError):
            _save_by_replacing(path, b"refused\n")
        # Closed, nothing is named at a start; the upgrade command's answer holds the commands.
        upgrade = ["upgrade-user-settings", "--coordination-root", str(config.coordination_root)]
        assert main(upgrade) == 0
        protection = json.loads(capsys.readouterr().out)["protection"]
        assert (protection["fileOpen"], protection["folderOpen"]) == (False, False)
        assert shlex.split(protection["editCommand"]) == [
            "chmod",
            "u+w",
            str(path.parent),
            str(path),
        ]
        assert _settings_state(path) == (b'{"edited": false}\n', 0o444, 0o555)

        _run_as_the_user(protection["editCommand"])
        _save_by_replacing(path, b'{"edited": true}\n')
        # Open, the start report names it with the same two commands.
        notice = call_registered_tool(config, "server_info", {})["openUserSettings"]
        assert (notice["closeCommand"], notice["editCommand"]) == (
            protection["closeCommand"],
            protection["editCommand"],
        )
        _run_as_the_user(notice["closeCommand"])

        assert _settings_state(path) == (b'{"edited": true}\n', 0o444, 0o555)
        with pytest.raises(PermissionError):
            _save_by_replacing(path, b"refused\n")
    finally:
        path.parent.chmod(0o755)


def test_the_documented_commands_carry_the_same_edit_under_a_custom_configuration_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R137 rule 6: the settings reference gives commands that reach the file the product reads."""
    reference = SETTINGS_REFERENCE.read_text(encoding="utf-8")
    assert DOCUMENTED_EDIT_COMMAND in reference
    assert DOCUMENTED_CLOSE_COMMAND in reference
    path = _closed_settings_under_a_path_with_a_space(tmp_path, monkeypatch)
    account = {
        "HOME": str(tmp_path / "made up account"),
        "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"],
    }
    try:
        _run_as_the_user(DOCUMENTED_EDIT_COMMAND, account)
        assert _settings_state(path) == (b'{"edited": false}\n', 0o644, 0o755)
        _save_by_replacing(path, b'{"edited": true}\n')
        _run_as_the_user(DOCUMENTED_CLOSE_COMMAND, account)

        assert _settings_state(path) == (b'{"edited": true}\n', 0o444, 0o555)
    finally:
        path.parent.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
