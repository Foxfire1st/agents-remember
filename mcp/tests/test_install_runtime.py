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
from agents_remember.install import runtime as install_runtime
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    agentic_settings_path,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.server import create_server
from agents_remember.worktrees.services import reset_worktree_services

from mcp import Client

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
            async with Client(server, mode="legacy") as client:
                result = await client.call_tool(name, arguments)
        assert not result.is_error, result.content
        if result.structured_content is not None:
            return result.structured_content
        return json.loads(result.content[0].text)  # type: ignore[union-attr]

    try:
        return anyio.run(call)
    finally:
        reset_worktree_services()


def create_runtime_source(root: Path) -> Path:
    source_root = root / "source"
    runtime_root = source_root / "runtime"
    write_file(source_root / "mcp" / "src" / "agents_remember" / "mcp" / "__init__.py")
    write_file(runtime_root / "skills" / "U-01-core-skills" / "C-04" / "SKILL.md")
    write_file(runtime_root / "docs" / "tool-details.md", "# Tool details\n")
    return source_root


class InstallRuntimeTests(unittest.TestCase):
    def test_runtime_install_creates_logs_without_provider_folders_and_removes_scripts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            old_script = coordination_root / "scripts" / "install-skills.sh"
            write_file(old_script)

            summary = install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=False,
            )

            self.assertGreater(summary.created_dirs, 0)
            self.assertTrue((coordination_root / "logs").is_dir())
            self.assertTrue((coordination_root / "logs" / "mcp").is_dir())
            self.assertTrue((coordination_root / "docs" / "tool-details.md").is_file())
            self.assertFalse((coordination_root / "AGENTS.md").exists())
            self.assertFalse((coordination_root / "scripts").exists())
            self.assertFalse((coordination_root / "providers").exists())
            self.assertFalse((coordination_root / "logs" / "providers").exists())
            self.assertFalse((coordination_root / "logs" / "observer" / "providers").exists())

    def test_runtime_install_leaves_legacy_provider_trees_intact_and_reports_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"
            providers_data = coordination_root / "providers" / "data" / "graph.db"
            logs_providers = coordination_root / "logs" / "providers"
            observer_providers = (
                coordination_root / "logs" / "observer" / "providers" / "metrics.jsonl"
            )

            write_file(providers_data, "legacy graph\n")
            logs_providers.mkdir(parents=True, exist_ok=True)
            write_file(observer_providers, "{}\n")

            summary = install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=False,
            )

            self.assertTrue(providers_data.is_file())
            self.assertTrue(logs_providers.is_dir())
            self.assertTrue(observer_providers.is_file())
            reported = {Path(item["path"]) for item in summary.removed_part_stores}
            self.assertEqual(
                reported,
                {
                    coordination_root / "providers",
                    coordination_root / "logs" / "providers",
                    coordination_root / "logs" / "observer" / "providers",
                },
            )


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

            install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=False,
            )

            self.assertEqual(settings_path.read_text(encoding="utf-8"), developer_content)

    def test_dry_run_installs_assets_without_writing_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source_root = create_runtime_source(root)
            coordination_root = root / "ar-coordination"

            summary = install_runtime.install_runtime(
                source_root,
                coordination_root,
                dry_run=True,
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
