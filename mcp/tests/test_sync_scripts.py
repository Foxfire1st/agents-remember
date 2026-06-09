"""Tests for the crash-safe copy-then-swap behavior of the sync scripts."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_script(name: str) -> ModuleType:
    path = REPO_ROOT / "scripts" / name
    module_name = name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves the defining module through sys.modules at class
    # creation time, so the script module must be registered before exec.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ReplaceTreeTests(unittest.TestCase):
    """Both scripts share the same replace_tree contract; sync-runtime is the
    representative module and sync-skills is spot-checked for parity."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.sync_runtime = load_script("sync-runtime.py")
        cls.sync_skills = load_script("sync-skills.py")

    def _make_source(self, root: Path) -> Path:
        source = root / "source"
        (source / "nested").mkdir(parents=True)
        (source / "top.md").write_text("top", encoding="utf-8")
        (source / "nested" / "leaf.md").write_text("leaf", encoding="utf-8")
        return source

    def test_replaces_target_with_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_source(root)
            target = root / "target"
            target.mkdir()
            (target / "stale.md").write_text("stale", encoding="utf-8")

            self.sync_runtime.replace_tree(source, target)

            self.assertEqual((target / "top.md").read_text(encoding="utf-8"), "top")
            self.assertEqual((target / "nested" / "leaf.md").read_text(encoding="utf-8"), "leaf")
            self.assertFalse((target / "stale.md").exists())
            self.assertFalse((root / "target.ar-sync-new").exists())
            self.assertFalse((root / "target.ar-sync-old").exists())

    def test_crash_during_copy_leaves_target_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_source(root)
            target = root / "target"
            target.mkdir()
            (target / "live.md").write_text("live", encoding="utf-8")

            with (
                mock.patch.object(
                    self.sync_runtime.shutil, "copytree", side_effect=OSError("boom")
                ),
                self.assertRaises(OSError),
            ):
                self.sync_runtime.replace_tree(source, target)

            # The crash happened before any rename: the live target is intact.
            self.assertEqual((target / "live.md").read_text(encoding="utf-8"), "live")

    def test_rerun_heals_stale_staging_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_source(root)
            target = root / "target"
            stale_staging = root / "target.ar-sync-new"
            stale_staging.mkdir()
            (stale_staging / "half-copied.md").write_text("partial", encoding="utf-8")
            stale_retired = root / "target.ar-sync-old"
            stale_retired.mkdir()

            self.sync_runtime.replace_tree(source, target)

            self.assertEqual((target / "top.md").read_text(encoding="utf-8"), "top")
            self.assertFalse(stale_staging.exists())
            self.assertFalse(stale_retired.exists())

    def test_sync_skills_replace_tree_matches_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_source(root)
            target = root / "target"

            self.sync_skills.replace_tree(source, target)

            self.assertEqual((target / "nested" / "leaf.md").read_text(encoding="utf-8"), "leaf")
            self.assertFalse((root / "target.ar-sync-new").exists())

    def test_extended_length_is_idempotent_and_platform_aware(self) -> None:
        sample = Path(tempfile.gettempdir()) / "ar-sync-sample"
        once = self.sync_runtime.extended_length(sample)
        twice = self.sync_runtime.extended_length(once)
        self.assertEqual(str(once), str(twice))
        if self.sync_runtime.os.name == "nt":
            self.assertTrue(str(once).startswith("\\\\?\\"))
        else:
            self.assertEqual(once, sample)

    def test_copy_ignores_cache_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._make_source(root)
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
            target = root / "target"

            self.sync_runtime.replace_tree(source, target)

            self.assertFalse((target / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
