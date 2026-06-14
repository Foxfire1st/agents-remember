from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sync-dashboard.py"


def load_sync_dashboard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_dashboard", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load sync-dashboard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SyncDashboardTests(unittest.TestCase):
    def test_file_digests_skips_ignored_and_missing(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(sync.file_digests(root / "absent"), {})
            (root / "assets").mkdir()
            (root / "assets" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "x.pyc").write_bytes(b"x")
            (root / ".DS_Store").write_bytes(b"junk")
            self.assertEqual(set(sync.file_digests(root)), {Path("assets/app.js")})

    def test_replace_tree_swaps_target_to_source(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "dist"
            target = root / "pkg"
            (source / "assets").mkdir(parents=True)
            (source / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
            (source / "assets" / "app.js").write_text("app\n", encoding="utf-8")
            (source / ".DS_Store").write_bytes(b"junk")
            target.mkdir()
            (target / "stale.txt").write_text("stale\n", encoding="utf-8")

            sync.replace_tree(source, target)

            self.assertTrue((target / "index.html").is_file())
            self.assertTrue((target / "assets" / "app.js").is_file())
            self.assertFalse((target / "stale.txt").exists())
            self.assertFalse((target / ".DS_Store").exists())

    def test_check_and_sync_roundtrip(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "dist"
            target = root / "pkg"
            source.mkdir()
            (source / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
            with (
                patch.object(sync, "SOURCE", source),
                patch.object(sync, "TARGET", target),
            ):
                self.assertEqual(sync.check(), 1)  # target absent -> out of sync
                self.assertEqual(sync.sync(), 0)  # sync makes them match
                self.assertEqual(sync.check(), 0)

    def test_check_noops_without_a_build(self) -> None:
        sync = load_sync_dashboard()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(sync, "SOURCE", Path(tmp) / "dist-absent"),
            patch.object(sync, "TARGET", Path(tmp) / "pkg"),
        ):
            self.assertEqual(sync.check(), 0)  # no dist yet -> shipped placeholder retained

    def test_paths_target_package_data(self) -> None:
        sync = load_sync_dashboard()
        self.assertEqual(sync.SOURCE.as_posix().split("/")[-2:], ["dashboard", "dist"])
        self.assertIn("/mcp/src/agents_remember/package_data/dashboard", sync.TARGET.as_posix())


if __name__ == "__main__":
    unittest.main()
