"""``sync-dashboard.py --check`` reports drift and writes nothing.

The write path is a release build step and cannot be a pre-commit gate: it copies, renames,
and rewrites the fingerprint sidecar. A gate needs the one question the write path cannot
answer -- whether what is placed STILL matches the source -- and it must be answerable without
changing the tree it is measuring, or running the check is what makes the tree dirty.

Measured defect (S6a of `260915-CAPS-L21`): the script had no read-only mode at all. Its only
two write sites (`shutil.rmtree` of the staged and retired copies) are unconditional on the
write path, so there was no way to ask the question.

The three cases below are the three answers that have to be distinguishable: a current tree
passes, a drifted tree fails, and a tree that cannot be verified at all fails as drift rather
than passing green -- the conflation the DELETED check mode was removed for.
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sync-dashboard.py"
BUILD_INPUT = "package.json"


def load_sync_dashboard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_dashboard", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load sync-dashboard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DashboardCheckTests(unittest.TestCase):
    """One fixture root, re-pointed at the loaded module's own path constants."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.module: Any = load_sync_dashboard()
        self.dashboard = self.root / "dashboard"
        self.source_tree = self.dashboard / "src"
        self.source_tree.mkdir(parents=True)
        (self.source_tree / "main.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (self.dashboard / BUILD_INPUT).write_text("{}\n", encoding="utf-8")
        self.dist = self.dashboard / "dist"
        self.dist.mkdir()
        self.target = self.root / "package_data" / "dashboard"
        self.fingerprint_file = self.target.parent / "dashboard.fingerprint"
        self.module.SOURCE = self.dist
        self.module.TARGET = self.target
        self.module.SOURCE_TREE = self.source_tree
        self.module.FINGERPRINT_FILE = self.fingerprint_file

    def write_bundle(self, fingerprint: str) -> None:
        """The placed bundle and ``dist``, byte-identical and carrying ``fingerprint``."""
        for base in (self.dist, self.target):
            base.mkdir(parents=True, exist_ok=True)
            (base / "index.js").write_text(f'const b = "{fingerprint}";\n', encoding="utf-8")
        self.fingerprint_file.write_text(f"{fingerprint}\n", encoding="utf-8")

    def run_check(self) -> tuple[int, str]:
        argv = sys.argv
        sys.argv = ["sync-dashboard.py", "--check"]
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = self.module.main()
        finally:
            sys.argv = argv
        return code, out.getvalue() + err.getvalue()

    def snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }

    def test_the_check_reports_all_three_answers_and_writes_nothing_any_of_them(self) -> None:
        """A current tree passes; a drifted one fails; an unverifiable one fails as DRIFT.

        The third answer is the one the deleted check mode got wrong: "nothing has been built
        or placed" is not a pass, and this test refuses to let it become one again. Every arm
        snapshots the whole fixture so "writes nothing" is measured, not asserted.
        """
        payload = self.module.source_fingerprint()
        self.write_bundle(payload)

        before = self.snapshot()
        code, output = self.run_check()
        self.assertEqual(code, 0, output)
        self.assertIn("matches dashboard/dist", output)
        self.assertEqual(before, self.snapshot())

        (self.target / "index.js").write_text('const b = "tampered";\n', encoding="utf-8")
        before = self.snapshot()
        code, output = self.run_check()
        self.assertEqual(code, 1, output)
        self.assertIn("has drifted", output)
        self.assertIn("index.js differs between dashboard/dist and the placed bundle", output)
        self.assertEqual(before, self.snapshot())

        self.write_bundle(payload)
        shutil.rmtree(self.dist)
        before = self.snapshot()
        code, output = self.run_check()
        self.assertEqual(code, 1, output)
        self.assertIn("dashboard/dist is absent", output)
        self.assertEqual(before, self.snapshot())

    def test_the_write_path_still_places_the_bundle(self) -> None:
        """``--check`` is additive: the release write path is unchanged."""
        payload = self.module.source_fingerprint()
        self.dist.mkdir(exist_ok=True)
        (self.dist / "index.js").write_text(f'const b = "{payload}";\n', encoding="utf-8")

        code = self.module.sync()

        self.assertEqual(code, 0)
        self.assertEqual(
            (self.target / "index.js").read_text(encoding="utf-8"),
            f'const b = "{payload}";\n',
        )
        self.assertEqual(self.fingerprint_file.read_text(encoding="utf-8"), f"{payload}\n")


if __name__ == "__main__":
    unittest.main()
