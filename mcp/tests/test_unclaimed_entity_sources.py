"""Ranked, report-only coverage of sources absent from the entity register."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents_remember.memory_quality.integrity.onboarding_drift_check.models import DriftRow
from agents_remember.memory_quality.integrity.onboarding_drift_check.report import (
    _append_unclaimed_entity_report,
    counts,
    write_markdown_report,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.unclaimed_entities import (
    UnclaimedEntityReport,
    UnclaimedEntitySource,
    rank_unclaimed_entity_sources,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DURABLE_STORE = "mcp/src/agents_remember/controlplane/durable_store.py"


def _catalog(path: Path, *evidence_paths: str) -> Path:
    rows = [
        (f"| Entity {index} | `git-blob-set-v1` | `sha256:{index}` | `{evidence_path}` |")
        for index, evidence_path in enumerate(evidence_paths, start=1)
    ]
    path.write_text(
        "\n".join(
            [
                "# Entities",
                "",
                "## Entity Fingerprints",
                "",
                "| Entity | Algorithm | Fingerprint | Evidence Paths |",
                "| --- | --- | --- | --- |",
                *rows,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class UnclaimedEntitySourceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def _source(self, relative_path: str, source: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_ranks_meaningful_set_difference_without_listing_ordinary_modules(self) -> None:
        self._source(
            "pkg/durable.py",
            "\n".join(
                [
                    'DURABLE_STORE_CONTRACT = "ar-durable-store/1.0"',
                    'SCHEMA_VERSION = "1.0"',
                    'GATE_OWNERSHIP = StoreOwnership(store="gate")',
                    'INBOX_OWNERSHIP = StoreOwnership(store="inbox")',
                ]
            ),
        )
        self._source("pkg/contract.py", 'WIRE_CONTRACT = "ar-wire/2.0"\n')
        self._source("pkg/claimed.py", 'CLAIMED_SCHEMA = "ar-claimed/v1"\n')
        self._source("pkg/ordinary.py", "def run() -> None:\n    pass\n")
        catalog = _catalog(self.root / "entities.md", "pkg/claimed.py")

        report = rank_unclaimed_entity_sources(
            self.root,
            catalog,
            source_inventory=[
                "pkg/durable.py",
                "pkg/contract.py",
                "pkg/claimed.py",
                "pkg/ordinary.py",
            ],
        )

        self.assertEqual(report.source_count, 4)
        self.assertEqual(report.claimed_source_count, 1)
        self.assertEqual(report.unclaimed_source_count, 3)
        self.assertEqual(
            [source.path for source in report.ranked],
            ["pkg/durable.py", "pkg/contract.py"],
        )
        self.assertEqual(report.ranked[0].priority, "contract + authority")
        self.assertEqual(
            report.ranked[0].authority_declarations,
            ("GATE_OWNERSHIP", "INBOX_OWNERSHIP"),
        )

    def test_empty_meaningful_subset_still_reports_complete_coverage_counts(self) -> None:
        self._source("pkg/ordinary.py", "VALUE = 1\n")

        report = rank_unclaimed_entity_sources(
            self.root,
            _catalog(self.root / "entities.md"),
            source_inventory=["pkg/ordinary.py"],
        )

        self.assertEqual(report.unclaimed_source_count, 1)
        self.assertEqual(report.ranked, ())

    def test_path_is_the_deterministic_tie_breaker(self) -> None:
        self._source("pkg/z.py", 'EVENT_SCHEMA = "ar-z/v1"\n')
        self._source("pkg/a.py", 'EVENT_SCHEMA = "ar-a/v1"\n')

        report = rank_unclaimed_entity_sources(
            self.root,
            _catalog(self.root / "entities.md"),
            source_inventory=["pkg/z.py", "pkg/a.py"],
        )

        self.assertEqual([source.path for source in report.ranked], ["pkg/a.py", "pkg/z.py"])

    def test_the_real_durable_store_contract_carries_the_top_rank_signals(self) -> None:
        report = rank_unclaimed_entity_sources(
            REPO_ROOT,
            _catalog(self.root / "entities.md"),
            source_inventory=[DURABLE_STORE],
        )

        self.assertEqual([source.path for source in report.ranked], [DURABLE_STORE])
        durable = report.ranked[0]
        self.assertEqual(durable.priority, "contract + authority")
        self.assertIn(
            "DURABLE_STORE_CONTRACT=ar-durable-store/1.0",
            durable.versioned_contracts,
        )
        self.assertEqual(len(durable.authority_declarations), 6)

    def test_renderer_names_the_complete_denominator_ranked_count_and_language_limit(self) -> None:
        lines: list[str] = []
        _append_unclaimed_entity_report(
            lines,
            UnclaimedEntityReport(
                source_count=7,
                claimed_source_count=2,
                unclaimed_source_count=5,
                ranked=(
                    UnclaimedEntitySource(
                        path="pkg/contract.py",
                        versioned_contracts=("WIRE_CONTRACT=ar-wire/1",),
                        authority_declarations=(),
                        schema_declarations=(),
                    ),
                ),
            ),
        )

        rendered = "\n".join(lines)
        self.assertIn(
            "2 of 7 tracked repository files; 5 are unclaimed; ranked count: 1",
            rendered,
        )
        self.assertIn("All tracked repository files participate in the unclaimed count", rendered)
        self.assertIn("Only Python declaration signals are currently ranked", rendered)
        self.assertIn("non-Python artifacts remain counted but unranked", rendered)

    def test_report_emits_ranked_findings_without_changing_drift_classifications(self) -> None:
        rows = [
            DriftRow(
                onboarding_file="onboarding/pkg/a.py.md",
                source_file="pkg/a.py",
                repository="fixture",
                storage_mode="sidecar",
                last_verified_hash="abc123",
                last_verified_date="2026-08-02",
                classification="up to date",
                trust="high",
                affected_sections="none",
                note="current",
            )
        ]
        before = counts(rows)
        ranked = UnclaimedEntityReport(
            source_count=2,
            claimed_source_count=1,
            unclaimed_source_count=1,
            ranked=(
                UnclaimedEntitySource(
                    path="pkg/contract.py",
                    versioned_contracts=("WIRE_CONTRACT=ar-wire/1",),
                    authority_declarations=(),
                    schema_declarations=(),
                ),
            ),
        )
        report_path = self.root / "report.md"

        with (
            mock.patch(
                "agents_remember.memory_quality.integrity.onboarding_drift_check.report."
                "_entity_catalog_path",
                return_value=self.root / "entities.md",
            ),
            mock.patch(
                "agents_remember.memory_quality.integrity.onboarding_drift_check.report."
                "rank_unclaimed_entity_sources",
                return_value=ranked,
            ),
        ):
            write_markdown_report(rows, report_path, self.root, self.root)

        rendered = report_path.read_text(encoding="utf-8")
        self.assertIn("Ranked Unclaimed Entity Sources (report only)", rendered)
        self.assertIn("`pkg/contract.py`", rendered)
        self.assertEqual(counts(rows), before)
        self.assertEqual(rows[0].classification, "up to date")


if __name__ == "__main__":
    unittest.main()
