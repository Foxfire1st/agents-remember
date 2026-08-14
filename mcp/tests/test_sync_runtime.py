from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sync-runtime.py"


def load_sync_runtime() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_runtime", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load sync-runtime.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SyncRuntimeTests(unittest.TestCase):
    def test_diff_reports_missing_extra_and_changed_files(self) -> None:
        sync_runtime = load_sync_runtime()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "source"
            target = root / "target"
            (source / "nested").mkdir(parents=True)
            target.mkdir()
            (source / "same.txt").write_text("same\n", encoding="utf-8")
            (target / "same.txt").write_text("same\n", encoding="utf-8")
            (source / "changed.txt").write_text("source\n", encoding="utf-8")
            (target / "changed.txt").write_text("target\n", encoding="utf-8")
            (source / "nested" / "missing.txt").write_text("missing\n", encoding="utf-8")
            (target / "extra.txt").write_text("extra\n", encoding="utf-8")

            diff = sync_runtime.diff_target(sync_runtime.RuntimeTarget("fixture", source, target))

            self.assertEqual(diff.missing, (Path("nested/missing.txt"),))
            self.assertEqual(diff.extra, (Path("extra.txt"),))
            self.assertEqual(diff.changed, (Path("changed.txt"),))
            self.assertFalse(diff.in_sync)

    def test_sync_target_replaces_target_with_source_tree(self) -> None:
        sync_runtime = load_sync_runtime()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "source"
            target = root / "target"
            (source / "package").mkdir(parents=True)
            (source / "__pycache__").mkdir()
            target.mkdir()
            (source / "package" / "asset.md").write_text("# Asset\n", encoding="utf-8")
            (source / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
            (target / "stale.md").write_text("stale\n", encoding="utf-8")

            sync_runtime.sync_target(sync_runtime.RuntimeTarget("fixture", source, target))

            self.assertTrue((target / "package" / "asset.md").is_file())
            self.assertFalse((target / "stale.md").exists())
            self.assertFalse((target / "__pycache__").exists())
            self.assertTrue(
                sync_runtime.diff_target(
                    sync_runtime.RuntimeTarget("fixture", source, target)
                ).in_sync
            )

    def test_default_targets_only_write_to_mcp_package_data(self) -> None:
        sync_runtime = load_sync_runtime()

        labels = {target.label for target in sync_runtime.TARGETS}
        self.assertEqual(labels, {"agents-md-files", "benchmarks", "providers", "system"})

        for target in sync_runtime.TARGETS:
            target_path = target.path.as_posix()
            self.assertIn("/mcp/src/agents_remember/package_data/", target_path)
            self.assertNotIn("/.claude/", target_path)
            self.assertNotIn("/.codex/", target_path)
            self.assertNotIn("/.cursor/", target_path)
            self.assertNotIn("/.github-vscode/", target_path)


if __name__ == "__main__":
    unittest.main()
