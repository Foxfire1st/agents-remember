"""Small contract tests for the executable evidence-lane registry."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from agents_remember.testing.evidence_lanes import (
    EVIDENCE_LANES,
    EvidenceTrigger,
    category_for_item,
    expression_for,
    pytest_collection_modifyitems,
    pytest_configure,
    validate_lane_registry,
)
from agents_remember.testing.evidence_lifecycle import EvidenceCategory

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Item:
    def __init__(
        self,
        *markers: str,
        path: Path = REPO_ROOT / "mcp/tests/test_evidence_lanes.py",
    ) -> None:
        self.path = path
        self.nodeid = f"{path.relative_to(REPO_ROOT).as_posix()}::test_example"
        self.markers = set(markers)
        self.user_properties: list[tuple[str, object]] = []

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name in self.markers else None

    def add_marker(self, marker: str) -> None:
        self.markers.add(marker)


class _Config:
    def __init__(self) -> None:
        self.rootpath = REPO_ROOT
        self.markers: list[str] = []

    def addinivalue_line(self, name: str, line: str) -> None:
        if name != "markers":
            raise AssertionError(f"unexpected ini setting {name}")
        self.markers.append(line)


def _as_item(item: _Item) -> pytest.Item:
    return cast(pytest.Item, item)


class EvidenceLaneRegistryTests(unittest.TestCase):
    def test_every_category_has_one_lane_and_diagnostic_evidence_is_non_accepting(self) -> None:
        validate_lane_registry()

        self.assertEqual(
            {lane.category for lane in EVIDENCE_LANES},
            set(EvidenceCategory),
        )
        diagnostic = next(
            lane for lane in EVIDENCE_LANES if lane.category is EvidenceCategory.DIAGNOSTIC
        )
        self.assertIn("non-accepting", diagnostic.authority)
        self.assertEqual(diagnostic.fidelity.value, "exact-node-diagnostic")
        self.assertIn("invocation-local", diagnostic.expected_lifetime)
        self.assertEqual(diagnostic.triggers, {EvidenceTrigger.DIAGNOSTIC})

    def test_incomplete_or_ambiguous_registry_is_refused(self) -> None:
        with self.assertRaisesRegex(pytest.UsageError, "missing categories"):
            validate_lane_registry(EVIDENCE_LANES[:-1])
        with self.assertRaisesRegex(pytest.UsageError, "categories must be unique"):
            validate_lane_registry((*EVIDENCE_LANES, EVIDENCE_LANES[0]))

        duplicate_marker = replace(EVIDENCE_LANES[-1], marker=EVIDENCE_LANES[1].marker)
        with self.assertRaisesRegex(pytest.UsageError, "markers must be unique"):
            validate_lane_registry((*EVIDENCE_LANES[:-1], duplicate_marker))

    def test_cadence_expressions_keep_stress_out_of_affected_runs(self) -> None:
        self.assertEqual(expression_for(EvidenceTrigger.AFFECTED), "not evidence_stress")
        self.assertEqual(expression_for(EvidenceTrigger.PROVIDER_BUMP), "evidence_provider")
        self.assertEqual(expression_for(EvidenceTrigger.SCHEDULED), "evidence_stress")
        self.assertEqual(expression_for(EvidenceTrigger.MIGRATION_WINDOW), "evidence_migration")
        self.assertIsNone(expression_for(EvidenceTrigger.RELEASE))
        with self.assertRaisesRegex(ValueError, "exact-node selection"):
            expression_for(EvidenceTrigger.DIAGNOSTIC)

    def test_item_category_is_exact_and_provider_gates_are_provider_evidence(self) -> None:
        self.assertIs(
            category_for_item(_as_item(_Item())),
            EvidenceCategory.UNIT_REGRESSION,
        )
        self.assertIs(
            category_for_item(_as_item(_Item("evidence_integration"))),
            EvidenceCategory.INTEGRATION,
        )
        self.assertIs(
            category_for_item(_as_item(_Item("ar_run_pi_rpc_smoke"))),
            EvidenceCategory.PROVIDER_CONFORMANCE,
        )

        with self.assertRaisesRegex(pytest.UsageError, "conflicting evidence categories"):
            category_for_item(_as_item(_Item("fitness", "evidence_contract")))
        with self.assertRaisesRegex(pytest.UsageError, "provider-gated evidence conflicts"):
            category_for_item(_as_item(_Item("ar_run_pi_rpc_smoke", "evidence_integration")))

    def test_plugin_registers_the_registry_and_reports_category_on_each_item(self) -> None:
        config = _Config()
        pytest_configure(cast(pytest.Config, config))
        expected = {lane.marker for lane in EVIDENCE_LANES if lane.marker is not None}
        self.assertEqual({line.partition(":")[0] for line in config.markers}, expected)

        item = _Item("evidence_contract")
        pytest_collection_modifyitems(cast(pytest.Config, config), [_as_item(item)])
        self.assertEqual(item.user_properties, [("arEvidenceCategory", "public-contract")])

        provider = _Item(path=REPO_ROOT / "mcp/tests/test_pi_rpc_adapter.py")
        pytest_collection_modifyitems(cast(pytest.Config, config), [_as_item(provider)])
        self.assertIn("evidence_provider", provider.markers)
        self.assertEqual(provider.user_properties, [("arEvidenceCategory", "provider-conformance")])
