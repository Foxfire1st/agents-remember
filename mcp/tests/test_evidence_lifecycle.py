"""Forcing tests for durable evidence admission, expiry, and replacement metadata."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agents_remember.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    governed_artifact_paths,
    load_evidence_inventory,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class _Artifact:
    kind: str = "fixture"
    authority: str = "internal-canonical"
    category: str = "unit-regression"
    fidelity: str = "in-process"
    cadence: str = "affected"
    lifetime: str = "permanent"
    replacement: str = "contract:owned-behavior"
    expires_after: str | None = None
    permanence_rationale: str | None = "Retained as the smallest owned proof."


class _Project:
    def __init__(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self._temporary = temporary
        self.root = Path(temporary.name)
        (self.root / "mcp/tests/fixtures").mkdir(parents=True)
        (self.root / "mcp/tests/test_owner.py").write_text("def test_owner(): pass\n")

    def close(self) -> None:
        self._temporary.cleanup()

    def write(self, path: str, content: str = "{}\n") -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def catalog(
        self,
        path: str,
        *,
        spec: _Artifact | None = None,
    ) -> None:
        artifact = spec or _Artifact()
        optional = ""
        if artifact.expires_after is not None:
            optional += f"expires_after = {artifact.expires_after}\n"
        if artifact.permanence_rationale is not None:
            optional += f'permanence_rationale = "{artifact.permanence_rationale}"\n'
        content = f'''schema_version = "ar-test-evidence-lifecycle/v1"
large_fixture_bytes = 25000

[[artifact]]
path = "{path}"
kind = "{artifact.kind}"
authority = "{artifact.authority}"
owner = "test-owner"
category = "{artifact.category}"
fidelity = "{artifact.fidelity}"
cadence = "{artifact.cadence}"
source_version_or_generator = "test generator"
introduced_by = "test"
lifetime = "{artifact.lifetime}"
replacement_contract = "{artifact.replacement}"
consumers = ["mcp/tests/test_owner.py"]
{optional}'''
        self.write("mcp/tests/evidence-lifecycle.toml", content)


class EvidenceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = _Project()
        self.addCleanup(self.project.close)

    def test_repository_inventory_is_closed_over_governed_artifacts(self) -> None:
        inventory = load_evidence_inventory(REPO_ROOT, today=date(2026, 8, 24))

        self.assertEqual(
            {item.path for item in inventory.artifacts},
            governed_artifact_paths(REPO_ROOT),
        )
        self.assertGreaterEqual(len(inventory.artifacts), 24)

    def test_new_fixture_or_shared_support_without_metadata_is_refused(self) -> None:
        known = "mcp/tests/fixtures/known.json"
        self.project.write(known)
        self.project.write("mcp/tests/fixtures/new.json")
        self.project.write("mcp/tests/_new_support.py", "VALUE = 1\n")
        self.project.catalog(known)

        with self.assertRaises(EvidenceLifecycleError) as refusal:
            load_evidence_inventory(self.project.root)

        self.assertIn("mcp/tests/fixtures/new.json", str(refusal.exception))
        self.assertIn("mcp/tests/_new_support.py", str(refusal.exception))

    def test_task_or_date_shaped_baseline_is_governed(self) -> None:
        known = "mcp/tests/fixtures/known.json"
        self.project.write(known)
        self.project.write("mcp/tests/test_260824_model_baseline.py", "VALUE = 1\n")
        self.project.catalog(known)

        with self.assertRaisesRegex(EvidenceLifecycleError, "test_260824_model_baseline.py"):
            load_evidence_inventory(self.project.root)

    def test_stale_or_contradictory_metadata_is_refused(self) -> None:
        ghost = "mcp/tests/fixtures/ghost.json"
        self.project.catalog(
            ghost,
            spec=_Artifact(authority="internal-canonical", lifetime="versioned"),
        )

        with self.assertRaises(EvidenceLifecycleError) as refusal:
            load_evidence_inventory(self.project.root)

        message = str(refusal.exception)
        self.assertIn("cataloged artifact does not exist", message)
        self.assertIn("versioned evidence must have external authority", message)
        self.assertIn("outside governed evidence", message)

    def test_temporary_evidence_requires_a_real_executable_replacement(self) -> None:
        artifact = "mcp/tests/fixtures/migration.json"
        self.project.write(artifact)
        self.project.catalog(
            artifact,
            spec=_Artifact(
                category="migration",
                cadence="migration-window",
                lifetime="temporary",
                replacement="contract:future-proof",
                expires_after="2026-09-30",
                permanence_rationale=None,
            ),
        )

        with self.assertRaisesRegex(EvidenceLifecycleError, "requires an executable node"):
            load_evidence_inventory(self.project.root, today=date(2026, 8, 24))

        self.project.catalog(
            artifact,
            spec=_Artifact(
                category="migration",
                cadence="migration-window",
                lifetime="temporary",
                replacement="node:mcp/tests/missing.py::test_replacement",
                expires_after="2026-09-30",
                permanence_rationale=None,
            ),
        )
        with self.assertRaisesRegex(EvidenceLifecycleError, "replacement node path does not exist"):
            load_evidence_inventory(self.project.root, today=date(2026, 8, 24))

        self.project.catalog(
            artifact,
            spec=_Artifact(
                category="migration",
                fidelity="transition-comparison",
                cadence="migration-window",
                lifetime="temporary",
                replacement="node:mcp/tests/test_owner.py::test_missing",
                expires_after="2026-09-30",
                permanence_rationale=None,
            ),
        )
        with self.assertRaisesRegex(EvidenceLifecycleError, "selector does not exist"):
            load_evidence_inventory(self.project.root, today=date(2026, 8, 24))

    def test_expired_migration_fails_even_when_its_replacement_exists(self) -> None:
        artifact = "mcp/tests/fixtures/migration.json"
        self.project.write(artifact)
        self.project.catalog(
            artifact,
            spec=_Artifact(
                category="migration",
                fidelity="transition-comparison",
                cadence="migration-window",
                lifetime="temporary",
                replacement="node:mcp/tests/test_owner.py::test_owner",
                expires_after="2026-08-23",
                permanence_rationale=None,
            ),
        )

        with self.assertRaisesRegex(EvidenceLifecycleError, "evidence expired on 2026-08-23"):
            load_evidence_inventory(self.project.root, today=date(2026, 8, 24))

    def test_future_migration_with_an_existing_replacement_is_valid(self) -> None:
        artifact = "mcp/tests/fixtures/migration.json"
        self.project.write(artifact)
        self.project.catalog(
            artifact,
            spec=_Artifact(
                category="migration",
                fidelity="transition-comparison",
                cadence="migration-window",
                lifetime="temporary",
                replacement="node:mcp/tests/test_owner.py::test_owner",
                expires_after="2026-09-30",
                permanence_rationale=None,
            ),
        )

        inventory = load_evidence_inventory(self.project.root, today=date(2026, 8, 24))
        self.assertEqual([item.path for item in inventory.artifacts], [artifact])
