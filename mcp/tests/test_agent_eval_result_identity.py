"""Result keys bind every declared verdict input and reject null identities."""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.result_identity import (
    deterministic_identity,
    judged_identity,
    source_snapshot,
    validate_result_identity,
)
from agents_remember.errors import AuthorityError


class ResultIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for path in (
            "scripts/agent_evals/graders/cases.py",
            "mcp/src/agents_remember/models/history.py",
            "mcp/test_support/helper.py",
            "mcp/uv.lock",
            "mcp/tests/test_not_runtime.py",
        ):
            file = self.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("initial")

    def values(self) -> dict:
        return {
            "grader_id": "case-code/CE10",
            "source": source_snapshot(self.root),
            "capture": {"events": ["native"]},
            "effects": {"before": [{"path": "x", "sha256": "a" * 64}], "after": []},
            "admission": {"case": "CE10", "admittedPaths": ["x"]},
        }

    def test_deterministic_key_changes_with_each_verdict_input(self) -> None:
        original = self.values()
        identity = deterministic_identity(**original)
        self.assertEqual(validate_result_identity(identity), identity)
        for field, replacement in (
            ("grader_id", "other-grader"),
            ("capture", {"events": ["other"]}),
            ("effects", {"before": [], "after": []}),
            ("admission", {"case": "CE10", "admittedPaths": []}),
        ):
            with self.subTest(field=field):
                changed = {**original, field: replacement}
                self.assertNotEqual(deterministic_identity(**changed)["digest"], identity["digest"])
        history = self.root / "mcp/src/agents_remember/models/history.py"
        history.write_text("changed transitive product history validator")
        self.assertNotEqual(
            deterministic_identity(**{**original, "source": source_snapshot(self.root)})["digest"],
            identity["digest"],
        )

    def test_source_closure_covers_product_support_and_lock_but_excludes_tests(self) -> None:
        snapshot = source_snapshot(self.root)
        paths = {item["path"] for item in snapshot["files"]}
        self.assertIn("mcp/src/agents_remember/models/history.py", paths)
        self.assertIn("mcp/test_support/helper.py", paths)
        self.assertIn("mcp/uv.lock", paths)
        self.assertNotIn("mcp/tests/test_not_runtime.py", paths)
        self.assertIn("pythonVersion", snapshot["runtime"])
        self.assertIn("dependencies", snapshot["runtime"])
        self.assertIn("git", snapshot["runtime"])

    def test_judge_model_and_package_both_change_identity(self) -> None:
        opus = judged_identity("a" * 64, "claude-opus-5-5")
        self.assertNotEqual(opus, judged_identity("a" * 64, "gpt-6.1-sol"))
        self.assertNotEqual(opus, judged_identity("b" * 64, "claude-opus-5-5"))

    def test_null_and_tampered_components_are_errors(self) -> None:
        valid = judged_identity("a" * 64, "claude-opus-5-5")
        invalid = copy.deepcopy(valid)
        invalid["components"]["judgeModel"] = None
        for value in (None, {}, invalid, {**valid, "digest": "b" * 64}):
            with self.subTest(value=value), self.assertRaises(AuthorityError):
                validate_result_identity(value)
