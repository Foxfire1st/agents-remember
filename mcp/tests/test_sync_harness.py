"""Tests for the harness starter package generator.

The first test is the enforcing one: it fails when a generated harness file in this
checkout no longer matches ``scripts/harness/``. Without it the generator would be a
script someone has to remember to run, which is the failure mode it exists to remove.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import ModuleType

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


class GeneratedTreesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sync_harness = load_script("sync-harness.py")

    def test_every_generated_harness_file_matches_its_source(self) -> None:
        stale = [
            self.sync_harness.repo_relative(generated.path)
            for generated in self.sync_harness.generated_files()
            if self.sync_harness.describe_drift(generated)
        ]
        self.assertEqual(
            stale,
            [],
            "generated harness files drifted from scripts/harness/; "
            "run: python3 scripts/sync-harness.py",
        )

    def test_no_two_harnesses_claim_the_same_destination(self) -> None:
        counts = Counter(generated.path for generated in self.sync_harness.generated_files())
        self.assertEqual([path for path, count in counts.items() if count > 1], [])

    def test_every_declared_fragment_exists_in_its_library(self) -> None:
        starter = self.sync_harness.fragment_sources(self.sync_harness.STARTER_LIBRARY)
        hooks = self.sync_harness.fragment_sources(self.sync_harness.HOOK_LIBRARY)
        for harness in self.sync_harness.HARNESSES:
            for name in (*harness.starter.fragments, harness.starter.entry):
                self.assertIn(name, starter, f"{harness.key}: unknown starter fragment {name!r}")
            if harness.hook is not None:
                for name in harness.hook.fragments:
                    self.assertIn(name, hooks, f"{harness.key}: unknown hook fragment {name!r}")

    def test_every_starter_carries_the_shared_body(self) -> None:
        """A target that silently drops a shared fragment would regrow a private copy."""
        for harness in self.sync_harness.HARNESSES:
            missing = set(self.sync_harness.SHARED_STARTER_FRAGMENTS) - set(
                harness.starter.fragments
            )
            self.assertEqual(missing, set(), f"{harness.key} is missing shared fragments")

    def test_generated_programs_parse_and_have_an_entry_point(self) -> None:
        for generated in self.sync_harness.generated_files():
            if generated.path.suffix != ".py":
                continue
            with self.subTest(path=self.sync_harness.repo_relative(generated.path)):
                module = ast.parse(generated.text)
                guards = [
                    node
                    for node in module.body
                    if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "__name__ == '__main__'"
                ]
                self.assertEqual(len(guards), 1)

    def test_drift_is_reported_for_content_and_for_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generated.py"
            generated = self.sync_harness.GeneratedFile(label="probe", path=path, text="expected\n")
            self.assertEqual(self.sync_harness.describe_drift(generated), ["file is missing"])

            path.write_text("expected\n", encoding="utf-8")
            path.chmod(self.sync_harness.GENERATED_MODE)
            self.assertEqual(self.sync_harness.describe_drift(generated), [])

            path.write_text("hand edited\n", encoding="utf-8")
            self.assertIn("content differs:", self.sync_harness.describe_drift(generated))

            path.write_text("expected\n", encoding="utf-8")
            path.chmod(0o755)
            self.assertEqual(
                self.sync_harness.describe_drift(generated),
                ["mode is 0755, expected 0644"],
            )


if __name__ == "__main__":
    unittest.main()
