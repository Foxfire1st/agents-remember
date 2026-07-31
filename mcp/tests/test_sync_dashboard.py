"""Contract of the dashboard build-placement step.

The bundle under ``package_data/dashboard`` is no longer committed (master decision OQ6,
2026-07-31), so ``sync-dashboard.py`` is a release build step rather than a drift check.
Its one non-negotiable property, exercised here from every angle: it cannot place an
artifact that was not built from the dashboard source as it stands right now. The old
suite asserted the opposite in three places -- absent ``dist`` passes, absent sidecar
passes, absent ``dashboard/src`` passes -- and those tests are replaced by their
inversions below.
"""

from __future__ import annotations

import importlib.util
import subprocess
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


def seed_source(root: Path) -> Path:
    """A minimal dashboard checkout: a src tree plus one production config file."""
    src = root / "src"
    (src / "panels").mkdir(parents=True)
    (src / "main.tsx").write_text("export const app = 1\n", encoding="utf-8")
    (src / "panels" / "Foo.tsx").write_text("export const Foo = () => null\n", encoding="utf-8")
    (root / "vite.config.ts").write_text("export default {}\n", encoding="utf-8")
    return src


def emit_bundle(root: Path, build_identity: str) -> Path:
    """Stand in for ``vite build``: emit a dist whose JS carries ``__AR_DASHBOARD_BUILD__``.

    Vite's ``define`` substitutes the identifier with a string literal, so a real bundle
    contains its build-input fingerprint verbatim; that literal is the whole handshake
    this script relies on, and reproducing it is what makes these fixtures faithful.
    """
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text(
        f'const CLIENT_DASHBOARD_BUILD="{build_identity}";export{{CLIENT_DASHBOARD_BUILD}};\n',
        encoding="utf-8",
    )
    return dist


class BuildPlacementTests(unittest.TestCase):
    """``sync()`` places a current bundle and refuses every non-current one."""

    def test_places_bundle_and_records_the_identity_the_bundle_itself_carries(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = seed_source(root)
            target = root / "pkg"
            fingerprint_file = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "TARGET", target),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint_file),
            ):
                identity = sync.source_fingerprint()
                dist = emit_bundle(root, identity)
                with patch.object(sync, "SOURCE", dist):
                    self.assertEqual(sync.sync(), 0)

                self.assertTrue((target / "index.html").is_file())
                self.assertTrue((target / "assets" / "index-abc123.js").is_file())
                # The sidecar serving/build_info.py reads is the value compiled into the
                # bundle, so a live tab's CLIENT_DASHBOARD_BUILD comparison is meaningful.
                recorded = fingerprint_file.read_text(encoding="utf-8").strip()
                self.assertEqual(recorded, identity)
                self.assertEqual(len(recorded), 64)
                self.assertIn(recorded, (dist / "assets" / "index-abc123.js").read_text("utf-8"))

    def test_refuses_when_dist_is_absent(self) -> None:
        """Replaces ``test_check_noops_without_a_build``, which asserted the fail-open.

        That test encoded the defect: with no ``dashboard/dist`` the old ``check()``
        printed "shipped placeholder retained" and exited 0, so on every fresh clone --
        which is most clones -- the gate certified an artifact nobody had built.
        """
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = seed_source(root)
            target = root / "pkg"
            fingerprint_file = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE", root / "dist-absent"),
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "TARGET", target),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint_file),
            ):
                self.assertEqual(sync.sync(), 1)
            self.assertFalse(target.exists())
            self.assertFalse(fingerprint_file.exists())

    def test_refuses_a_dist_built_from_different_source(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = seed_source(root)
            target = root / "pkg"
            fingerprint_file = root / "dashboard.fingerprint"
            dist = emit_bundle(root, "0" * 64)  # some other build's identity
            with (
                patch.object(sync, "SOURCE", dist),
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "TARGET", target),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint_file),
            ):
                self.assertEqual(sync.sync(), 1)
            # A refusal writes nothing: no sidecar may advertise a bundle that was
            # never placed, and no stale tree may be shipped under a fresh stamp.
            self.assertFalse(target.exists())
            self.assertFalse(fingerprint_file.exists())

    def test_refuses_without_a_dashboard_source_tree(self) -> None:
        """Replaces ``test_gate_skipped_without_a_source_tree``, another fail-open.

        ``source_inputs()`` used to return ``{}`` when ``dashboard/src`` was missing,
        which the old docstring described as disabling the freshness gate. Now the empty
        input set simply yields a fingerprint no real bundle carries, so the placement
        refuses instead of certifying.
        """
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = emit_bundle(root, "f" * 64)
            fingerprint_file = root / "dashboard.fingerprint"
            with (
                patch.object(sync, "SOURCE", dist),
                patch.object(sync, "SOURCE_TREE", root / "src-absent"),
                patch.object(sync, "TARGET", root / "pkg"),
                patch.object(sync, "FINGERPRINT_FILE", fingerprint_file),
            ):
                self.assertEqual(sync.sync(), 1)
            self.assertFalse(fingerprint_file.exists())

    def test_refuses_after_a_source_edit_that_was_never_rebuilt(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = seed_source(root)
            target = root / "pkg"
            with (
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "TARGET", target),
                patch.object(sync, "FINGERPRINT_FILE", root / "dashboard.fingerprint"),
            ):
                dist = emit_bundle(root, sync.source_fingerprint())
                with patch.object(sync, "SOURCE", dist):
                    self.assertEqual(sync.sync(), 0)

                    (src / "panels" / "Foo.tsx").write_text(
                        "export const Foo = () => 1\n", encoding="utf-8"
                    )
                    # The bundle on disk is unchanged and now predates its inputs.
                    self.assertEqual(sync.sync(), 1)

    def test_refuses_after_a_production_config_edit(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = seed_source(root)
            with (
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "TARGET", root / "pkg"),
                patch.object(sync, "FINGERPRINT_FILE", root / "dashboard.fingerprint"),
            ):
                dist = emit_bundle(root, sync.source_fingerprint())
                with patch.object(sync, "SOURCE", dist):
                    self.assertEqual(sync.sync(), 0)
                    (root / "vite.config.ts").write_text(
                        "export default { base: '/x/' }\n", encoding="utf-8"
                    )
                    self.assertEqual(sync.sync(), 1)

    def test_test_modules_are_not_build_inputs(self) -> None:
        sync = load_sync_dashboard()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = seed_source(root)
            (src / "panels" / "Foo.test.tsx").write_text("test('x', () => {})\n", encoding="utf-8")
            with (
                patch.object(sync, "SOURCE_TREE", src),
                patch.object(sync, "TARGET", root / "pkg"),
                patch.object(sync, "FINGERPRINT_FILE", root / "dashboard.fingerprint"),
            ):
                dist = emit_bundle(root, sync.source_fingerprint())
                with patch.object(sync, "SOURCE", dist):
                    # Vite never bundles a test/spec/story module, so editing one leaves
                    # the built bundle current and placement still succeeds.
                    (src / "panels" / "Foo.test.tsx").write_text(
                        "test('y', () => {})\n", encoding="utf-8"
                    )
                    self.assertEqual(sync.sync(), 0)
                    # ...but editing the module it covers does invalidate the bundle.
                    (src / "panels" / "Foo.tsx").write_text(
                        "export const Foo = () => 2\n", encoding="utf-8"
                    )
                    self.assertEqual(sync.sync(), 1)


class ReplaceTreeTests(unittest.TestCase):
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


class PlacementSurfaceTests(unittest.TestCase):
    def test_paths_target_package_data(self) -> None:
        sync = load_sync_dashboard()
        self.assertEqual(sync.SOURCE.as_posix().split("/")[-2:], ["dashboard", "dist"])
        self.assertIn("/mcp/src/agents_remember/package_data/dashboard", sync.TARGET.as_posix())
        # The fingerprint sidecar lives *beside* the served bundle, never inside it.
        self.assertEqual(sync.FINGERPRINT_FILE.name, "dashboard.fingerprint")
        self.assertEqual(sync.FINGERPRINT_FILE.parent, sync.TARGET.parent)

    def test_check_mode_no_longer_exists(self) -> None:
        """``--check`` is gone with the committed bundle it used to compare against.

        Asserted through the process boundary because that is where the old fail-open
        lived: the hooks and CI invoked ``--check`` and read its exit status. A silently
        accepted flag would let a caller keep believing a check runs, so the flag must
        fail loudly rather than be tolerated.
        """
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments: --check", result.stderr)


if __name__ == "__main__":
    unittest.main()
