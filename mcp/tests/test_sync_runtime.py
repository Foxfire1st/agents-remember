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
        self.assertEqual(
            labels,
            {"agents-md-files", "benchmarks", "providers", "system", "eve-runtime"},
        )

        for target in sync_runtime.TARGETS:
            target_path = target.path.as_posix()
            self.assertIn("/mcp/src/agents_remember/package_data/", target_path)
            self.assertNotIn("/.claude/", target_path)
            self.assertNotIn("/.codex/", target_path)
            self.assertNotIn("/.cursor/", target_path)
            self.assertNotIn("/.github-vscode/", target_path)

    def test_a_missing_canonical_source_is_never_reported_in_sync(self) -> None:
        """An empty comparison is not evidence of a synced tree.

        With both sides absent the digest maps compare equal, so before this guard a caller who
        pointed the generator at the wrong root read five green rows for nothing — measured by an
        independent reviewer whose drift harness did exactly that.
        """

        sync_runtime = load_sync_runtime()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            diff = sync_runtime.diff_target(
                sync_runtime.RuntimeTarget("fixture", root / "absent", root / "also-absent")
            )
            self.assertTrue(diff.source_missing)
            self.assertFalse(diff.in_sync)
            self.assertEqual(diff.missing, ())
            self.assertEqual(diff.extra, ())
            self.assertEqual(diff.changed, ())

            # The same tree, present on both sides and equal, is still in sync.
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "asset.md").write_text("# Asset\n", encoding="utf-8")
            (target / "asset.md").write_text("# Asset\n", encoding="utf-8")
            self.assertTrue(
                sync_runtime.diff_target(
                    sync_runtime.RuntimeTarget("fixture", source, target)
                ).in_sync
            )

    def test_only_the_eve_application_target_ignores_machine_local_trees(self) -> None:
        """The per-target ignore rule stays scoped to the tree that needs it.

        ``node_modules`` and eve's generated ``.eve``/``.output``/``.vercel`` are
        machine-local, so the eve application target must skip them while every other
        canonical tree keeps syncing a same-named directory if it ever grows one.
        """

        sync_runtime = load_sync_runtime()

        ignoring = {target.label for target in sync_runtime.TARGETS if target.ignored_names}
        self.assertEqual(ignoring, {"eve-runtime"})
        eve = next(target for target in sync_runtime.TARGETS if target.label == "eve-runtime")
        self.assertEqual(
            eve.ignored_names,
            frozenset({"node_modules", ".eve", ".output", ".vercel"}),
        )
        self.assertTrue(sync_runtime.ignored(Path("node_modules/eve/index.js"), eve.ignored_names))
        self.assertFalse(sync_runtime.ignored(Path("node_modules/eve/index.js")))
        self.assertFalse(sync_runtime.ignored(Path("agent/agent.ts"), eve.ignored_names))


if __name__ == "__main__":
    unittest.main()
