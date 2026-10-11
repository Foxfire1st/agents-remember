"""The explicit provider-removal command (MIK-R112 rule 3).

Only this command removes provider data on disk: the install stops watchers and
prunes legacy binaries but never deletes runtime folders. The command stops the
watchers first, refuses when a stop fails or a live process holds a folder open
(naming it), and otherwise removes the provider runtime folders and reports the
bytes freed. A settings file that still holds provider keys loads unchanged.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_remember.cli import providers_remove
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember_test_support.testing.waits import wait_until
from test_config import settings_payload


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _settings(root: Path) -> Path:
    path = root / "mcp-settings.json"
    path.write_text(json.dumps(settings_payload(root)), encoding="utf-8")
    return path


def _providers_tree(coordination_root: Path) -> list[Path]:
    folders = [
        coordination_root / "providers" / "runners" / "grepai",
        coordination_root / "providers" / "data" / "codegraphcontext",
    ]
    for folder in folders:
        _write(folder / "state.bin", "0123456789")
    return folders


def _args(config: Path, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(config=str(config), dry_run=dry_run)


class ProvidersRemoveTests(unittest.TestCase):
    def test_dry_run_reports_without_stopping_or_removing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            # The settings carry provider keys from an older build; they load unchanged.
            config = load_config(_settings(root))
            folders = _providers_tree(config.coordination_root)

            code = providers_remove.run(_args(root / "mcp-settings.json", dry_run=True))

            self.assertEqual(code, 0)
            self.assertEqual(config.coordination_root, root / "ar-coordination")
            for folder in folders:
                self.assertTrue(folder.is_dir())

    def test_apply_stops_watchers_then_removes_and_reports_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_config(_settings(root))
            folders = _providers_tree(config.coordination_root)
            sizes = {
                folder: sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
                for folder in folders
            }
            stopped: list[bool] = []
            providers_remove.stop_configured_watchers = (  # type: ignore[method-assign]
                lambda config, *, dry_run: stopped.append(dry_run) or {"ok": True}
            )
            try:
                code = providers_remove.run(_args(root / "mcp-settings.json"))
            finally:
                del providers_remove.stop_configured_watchers

            self.assertEqual(code, 0)
            self.assertEqual(stopped, [False])
            for folder in folders:
                self.assertFalse(folder.exists())
                self.assertGreater(sizes[folder], 0)

    def test_stop_failure_refuses_and_leaves_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_config(_settings(root))
            folders = _providers_tree(config.coordination_root)
            providers_remove.stop_configured_watchers = (  # type: ignore[method-assign]
                lambda config, *, dry_run: {"ok": False, "detail": "docker unreachable"}
            )
            try:
                code = providers_remove.run(_args(root / "mcp-settings.json"))
            finally:
                del providers_remove.stop_configured_watchers

            self.assertEqual(code, 1)
            for folder in folders:
                self.assertTrue(folder.is_dir())

    def test_busy_folder_refuses_naming_the_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_config(_settings(root))
            folders = _providers_tree(config.coordination_root)
            providers_remove.stop_configured_watchers = (  # type: ignore[method-assign]
                lambda config, *, dry_run: {"ok": True}
            )
            holder = subprocess.Popen(["sleep", "30"], cwd=folders[0])
            try:
                wait_until(
                    lambda: providers_remove._process_holders(folders[0]),
                    "the holder process to appear in /proc",
                )
                code = providers_remove.run(_args(root / "mcp-settings.json"))
            finally:
                holder.terminate()
                holder.wait()
                del providers_remove.stop_configured_watchers

            self.assertEqual(code, 1)
            for folder in folders:
                self.assertTrue(folder.is_dir())

    def _foreign_tree(self, root: Path) -> tuple[Path, Path]:
        """A foreign repository with a 13-byte user-data sentinel."""
        foreign = root / "foreign-memory-repository" / "data"
        sentinel = foreign / "user.dat"
        _write(sentinel, "user-data-13!")
        assert len("user-data-13!") == 13
        return foreign, sentinel

    def test_aliased_providers_root_refuses_and_keeps_foreign_bytes(self) -> None:
        """MIK-R112 F04: the providers dir aliased at a foreign repo refuses; sentinel intact."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_config(_settings(root))
            foreign, sentinel = self._foreign_tree(root)
            providers = config.coordination_root / "providers"
            (providers / "runners").mkdir(parents=True, exist_ok=True)
            _write(providers / "runners" / "owned.bin", "0123456789")
            # Replace the owned providers dir with an alias at the foreign tree.
            shutil.rmtree(providers)
            providers.symlink_to(foreign, target_is_directory=True)
            stopped: list[bool] = []
            providers_remove.stop_configured_watchers = (  # type: ignore[method-assign]
                lambda config, *, dry_run: stopped.append(dry_run) or {"ok": True}
            )
            try:
                code = providers_remove.run(_args(root / "mcp-settings.json"))
            finally:
                del providers_remove.stop_configured_watchers

            self.assertEqual(code, 1)
            self.assertEqual(stopped, [])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-data-13!")
            self.assertTrue(sentinel.is_file())

    def test_consistent_ancestor_alias_still_cleans_owned_folders(self) -> None:
        """MIK-R112 F04: a coordination root reached through an alias is contained, not refused."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_config(_settings(root))
            folders = _providers_tree(config.coordination_root)
            link_coord = root / "linked-coord"
            link_coord.symlink_to(config.coordination_root, target_is_directory=True)
            stopped: list[bool] = []
            providers_remove.stop_configured_watchers = (  # type: ignore[method-assign]
                lambda config, *, dry_run: stopped.append(dry_run) or {"ok": True}
            )
            try:
                found = providers_remove.provider_folders(link_coord)
                code = providers_remove.run(_args(root / "mcp-settings.json"))
            finally:
                del providers_remove.stop_configured_watchers

            self.assertEqual(len(found), 2)
            self.assertEqual(code, 0)
            self.assertEqual(stopped, [False])
            for folder in folders:
                self.assertFalse(folder.exists())

    def test_dry_run_refuses_an_escaped_root(self) -> None:
        """MIK-R112 F04: dry run performs no removal and still refuses the alias."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = load_config(_settings(root))
            foreign, sentinel = self._foreign_tree(root)
            providers = config.coordination_root / "providers"
            providers.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(providers)
            providers.symlink_to(foreign, target_is_directory=True)
            code = providers_remove.run(_args(root / "mcp-settings.json", dry_run=True))

            self.assertEqual(code, 1)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-data-13!")


if __name__ == "__main__":
    unittest.main()
