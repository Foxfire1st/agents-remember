"""Grade records name real producer/dependency bytes and identify stale records."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.graders.models import Grade, current_grade_sources, inspect_grade_sources
from agents_remember.errors import AuthorityError


class GradeSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.producer = "scripts/agent_evals/graders/sample.py"
        self.shared = "scripts/agent_evals/shared.py"
        self.write(self.producer, "from ..shared import check\n")
        self.write(self.shared, "def check(): return True\n")

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def record(self) -> dict:
        return {
            "grader_id": "GT2",
            "status": "pass",
            "producer": current_grade_sources(self.producer, code_root=self.root),
        }

    def test_produced_grade_names_actual_producer_and_shared_sources(self) -> None:
        grade = Grade.produced(
            __file__.replace(
                "mcp/tests/test_agent_eval_grade_sources.py",
                "scripts/agent_evals/graders/trajectory.py",
            ),
            "GT1",
            "unknown",
        )
        record = grade.to_dict()
        producer = record["producer"]
        self.assertIsInstance(producer, dict)
        assert isinstance(producer, dict)
        self.assertEqual(producer["path"], "scripts/agent_evals/graders/trajectory.py")
        actual = Path(__file__).resolve().parents[2] / producer["path"]
        self.assertEqual(producer["sha256"], hashlib.sha256(actual.read_bytes()).hexdigest())
        shared = {item["path"] for item in producer["shared_sources"]}
        self.assertIn("scripts/agent_evals/graders/models.py", shared)
        self.assertIn("scripts/agent_evals/adapters/models.py", shared)
        self.assertIn("scripts/agent_evals/grade_sources.py", shared)

    def test_unbound_new_grade_cannot_be_serialized(self) -> None:
        for producer in (None, {}):
            with self.subTest(producer=producer), self.assertRaises(AuthorityError):
                Grade("GT2", "pass", producer=producer).to_dict()

    def test_missing_legacy_metadata_is_stale(self) -> None:
        for record in ({"grader_id": "GT2"}, {"grader_id": "GT2", "producer": None}):
            self.assertEqual(inspect_grade_sources(record, code_root=self.root)["status"], "stale")

    def test_shared_source_change_is_stale_and_names_exact_path(self) -> None:
        record = self.record()
        self.assertEqual(inspect_grade_sources(record, code_root=self.root)["status"], "current")
        self.write(self.shared, "def check(): return False\n")
        result = inspect_grade_sources(record, code_root=self.root)
        self.assertEqual(result["status"], "stale")
        self.assertIn("grade source changed: " + self.shared, result["reasons"])

    def test_new_transitive_dependency_changes_the_recorded_set(self) -> None:
        record = self.record()
        self.write(self.shared, "from .extra import check\n")
        self.write("scripts/agent_evals/extra.py", "def check(): return False\n")
        result = inspect_grade_sources(record, code_root=self.root)
        self.assertIn("grade source added: scripts/agent_evals/extra.py", result["reasons"])

    def test_expected_producer_and_malformed_fingerprint_are_stale(self) -> None:
        record = self.record()
        other = "scripts/agent_evals/graders/other.py"
        self.write(other, "x = 1\n")
        result = inspect_grade_sources(record, code_root=self.root, expected_producer=other)
        self.assertEqual(result["status"], "stale")
        record["producer"]["sha256"] = None
        self.assertEqual(inspect_grade_sources(record, code_root=self.root)["status"], "stale")

    def test_census_can_reuse_its_explicit_current_snapshot(self) -> None:
        record = self.record()
        current = current_grade_sources(self.producer, code_root=self.root)
        with patch(
            "agent_evals.grade_sources.current_grade_sources",
            side_effect=AssertionError("must not scan again"),
        ):
            result = inspect_grade_sources(
                record,
                code_root=self.root,
                expected_producer=self.producer,
                current_sources=current,
            )
        self.assertEqual(result["status"], "current")
        with self.assertRaises(AuthorityError):
            inspect_grade_sources(record, code_root=self.root, current_sources={})

    def test_product_implementation_and_schema_transitive_dependencies_are_bound(self) -> None:
        self.write(
            self.producer,
            "from agents_remember.application.meaning import meaning\nfrom agents_remember.models.knowledge_files.record import Record\n",
        )
        self.write(
            "mcp/src/agents_remember/application/meaning.py",
            "from agents_remember.application.unrelated_service import service\ndef meaning(): return 1\n",
        )
        self.write("mcp/src/agents_remember/application/unrelated_service.py", "service = 0\n")
        self.write(
            "mcp/src/agents_remember/models/knowledge_files/record.py",
            "from .shape import Shape\nRecord = Shape\n",
        )
        self.write("mcp/src/agents_remember/models/knowledge_files/shape.py", "Shape = str\n")
        self.write("mcp/src/agents_remember/unimported.py", "not_a_dependency = True\n")
        paths = {item["path"] for item in self.record()["producer"]["shared_sources"]}
        self.assertIn("mcp/src/agents_remember/application/meaning.py", paths)
        self.assertIn("mcp/src/agents_remember/models/knowledge_files/shape.py", paths)
        self.assertIn("mcp/src/agents_remember/application/unrelated_service.py", paths)
        self.assertNotIn("mcp/src/agents_remember/unimported.py", paths)

    def test_source_outside_code_root_is_refused(self) -> None:
        with self.assertRaises(AuthorityError):
            current_grade_sources(Path(__file__), code_root=self.root)
