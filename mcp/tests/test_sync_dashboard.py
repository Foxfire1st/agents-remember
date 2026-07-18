from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sync-dashboard.py"
GIT_ATTRIBUTES_PATH = SCRIPT_PATH.parents[1] / ".gitattributes"


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
            (root / "src").mkdir()
            (root / "src" / "main.tsx").write_text("export {}\n", encoding="utf-8")
            with (
                patch.object(sync, "SOURCE", source),
                patch.object(sync, "TARGET", target),
                patch.object(sync, "SOURCE_TREE", root / "src"),
                patch.object(sync, "FINGERPRINT_FILE", root / "dashboard.fingerprint"),
            ):
                self.assertEqual(sync.check(), 1)  # target absent -> out of sync
                self.assertEqual(sync.sync(), 0)  # sync makes them match + records fingerprint
                self.assertEqual(sync.check(), 0)

    def test_check_noops_without_a_build(self) -> None:
        sync = load_sync_dashboard()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(sync, "SOURCE", Path(tmp) / "dist-absent"),
            patch.object(sync, "TARGET", Path(tmp) / "pkg"),
            patch.object(sync, "FINGERPRINT_FILE", Path(tmp) / "dashboard.fingerprint"),
        ):
            self.assertEqual(sync.check(), 0)  # no dist yet -> shipped placeholder retained

    def test_paths_target_package_data(self) -> None:
        sync = load_sync_dashboard()
        self.assertEqual(sync.SOURCE.as_posix().split("/")[-2:], ["dashboard", "dist"])
        self.assertIn("/mcp/src/agents_remember/package_data/dashboard", sync.TARGET.as_posix())
        # The fingerprint sidecar lives *beside* the served bundle, never inside it.
        self.assertEqual(sync.FINGERPRINT_FILE.name, "dashboard.fingerprint")
        self.assertEqual(sync.FINGERPRINT_FILE.parent, sync.TARGET.parent)


class SourceFingerprintTests(unittest.TestCase):
    def _seed(self, root: Path) -> Path:
        """A minimal dashboard checkout: src tree + one production config file."""
        src = root / "src"
        (src / "panels").mkdir(parents=True)
        (src / "main.tsx").write_text("export const app = 1\n", encoding="utf-8")
        (src / "panels" / "Foo.tsx").write_text("export const Foo = () => null\n", encoding="utf-8")
        (root / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
        return src

    def test_flags_source_change_without_rebuild(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self._seed(root)
            fingerprint = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE", root / "dist-absent"),
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint),
            ):
                sync.write_fingerprint()  # records the freshly-built state
                self.assertTrue(fingerprint.is_file())
                self.assertEqual(sync.check(), 0)  # in sync -> passes

                (src / "panels" / "Foo.tsx").write_text(
                    "export const Foo = () => 1\n", encoding="utf-8"
                )
                self.assertEqual(sync.check(), 1)  # source moved past the shipped bundle

    def test_config_change_is_a_build_input(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self._seed(root)
            fingerprint = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE", root / "dist-absent"),
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint),
            ):
                sync.write_fingerprint()
                self.assertEqual(sync.check(), 0)
                (root / "vite.config.ts").write_text(
                    "export default { base: '/x/' }\n", encoding="utf-8"
                )
                self.assertEqual(sync.check(), 1)

    def test_test_files_do_not_demand_a_rebuild(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self._seed(root)
            (src / "panels" / "Foo.test.tsx").write_text("test('x', () => {})\n", encoding="utf-8")
            fingerprint = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE", root / "dist-absent"),
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint),
            ):
                sync.write_fingerprint()
                # Editing a test/spec/story module never bundles, so the gate stays green.
                (src / "panels" / "Foo.test.tsx").write_text(
                    "test('y', () => {})\n", encoding="utf-8"
                )
                self.assertEqual(sync.check(), 0)
                # ...but editing the real module it covers does trip it.
                (src / "panels" / "Foo.tsx").write_text(
                    "export const Foo = () => 2\n", encoding="utf-8"
                )
                self.assertEqual(sync.check(), 1)

    def test_gate_skipped_until_a_fingerprint_is_recorded(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self._seed(root)
            with (
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "FINGERPRINT_FILE", root / "dashboard.fingerprint"),
            ):
                # Legacy placeholder bundle: no fingerprint yet -> gate is a no-op, not a failure.
                self.assertEqual(sync.fingerprint_check(), 0)

    def test_gate_skipped_without_a_source_tree(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fingerprint = root / "dashboard.fingerprint"
            fingerprint.write_text("deadbeef\n", encoding="utf-8")
            with (
                patch.object(sync, "SOURCE_TREE", root / "src-absent"),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint),
            ):
                # A packaged install ships no dashboard/src; nothing to verify against.
                self.assertEqual(sync.fingerprint_check(), 0)

    def test_sync_records_and_then_verifies_fingerprint(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = self._seed(root)
            source = root / "dist"
            source.mkdir()
            (source / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
            fingerprint = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE", source),
                patch.object(sync, "TARGET", root / "pkg"),
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint),
            ):
                self.assertEqual(sync.sync(), 0)
                self.assertTrue(fingerprint.is_file())
                self.assertEqual(len(fingerprint.read_text(encoding="utf-8").strip()), 64)


class GeneratedDashboardWhitespacePolicyTests(unittest.TestCase):
    def test_generated_js_allows_literal_whitespace_without_relaxing_authored_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            shutil.copy2(GIT_ATTRIBUTES_PATH, root / ".gitattributes")

            generated = (
                root
                / "mcp/src/agents_remember/package_data/dashboard/assets/index-generated.js"
            )
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"const snippet = `if ${}:\n\t\n`;\n")

            authored = root / "dashboard/src/main.tsx"
            authored.parent.mkdir(parents=True)
            authored.write_bytes(b"export const app = true;  \n")

            subprocess.run(["git", "add", "."], cwd=root, check=True)
            result = subprocess.run(
                ["git", "diff", "--cached", "--check"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(generated.relative_to(root).as_posix(), result.stdout)
            self.assertIn(
                "dashboard/src/main.tsx:1: trailing whitespace.", result.stdout
            )


if __name__ == "__main__":
    unittest.main()
