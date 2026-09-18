"""Small contract tests for the executable evidence-lane registry."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from agents_remember_test_support.testing.evidence_lanes import (
    EVIDENCE_LANES,
    validate_lane_registry,
)
from agents_remember_test_support.testing.evidence_lifecycle import EvidenceCategory
from agents_remember_test_support.testing.lane_manifest import LaneManifest, load_lane_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = LaneManifest(
    files={
        Path("mcp/tests/test_evidence_lanes.py"): EvidenceCategory.UNIT_REGRESSION,
        Path("mcp/tests/test_active_projector_singleflight.py"): EvidenceCategory.UNIT_REGRESSION,
        Path("mcp/tests/test_conversation_control_api.py"): EvidenceCategory.INTEGRATION,
        Path("mcp/tests/test_pi_rpc_real_smoke.py"): EvidenceCategory.PROVIDER_CONFORMANCE,
        Path("mcp/tests/test_pi_rpc_adapter.py"): EvidenceCategory.PROVIDER_CONFORMANCE,
    },
    overrides=(),
    digest="a" * 64,
)


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
    def test_incomplete_or_ambiguous_registry_is_refused(self) -> None:
        with self.assertRaisesRegex(pytest.UsageError, "missing categories"):
            validate_lane_registry(EVIDENCE_LANES[:-1])
        with self.assertRaisesRegex(pytest.UsageError, "categories must be unique"):
            validate_lane_registry((*EVIDENCE_LANES, EVIDENCE_LANES[0]))

        duplicate_marker = replace(EVIDENCE_LANES[-1], marker=EVIDENCE_LANES[1].marker)
        with self.assertRaisesRegex(pytest.UsageError, "markers must be unique"):
            validate_lane_registry((*EVIDENCE_LANES[:-1], duplicate_marker))


def test_the_enforcing_hook_is_registered_and_armed(pytestconfig: pytest.Config) -> None:
    """The lane registry's enforcement hook must be *registered*, not merely defined.

    ``evidence_lanes.pytest_collection_modifyitems`` loads the manifest and raises when it
    refuses, but it was absent from ``conftest.pytest_plugins``, so no run had ever asked
    the loader its verdict while the manifest was in fact refusing -- the hook existed and
    the registry was decorative. This asserts the armed state rather than the source text:
    the module is on the plugin manager, and its collection hook is one of the implementations
    pytest will actually call.
    """

    plugin = "agents_remember_test_support.testing.evidence_lanes"
    assert pytestconfig.pluginmanager.hasplugin(plugin)
    armed = {
        impl.function.__module__
        for impl in pytestconfig.hook.pytest_collection_modifyitems.get_hookimpls()
    }
    assert plugin in armed


def test_the_checked_in_manifest_accepts_the_checked_in_population() -> None:
    """Every test module on disk has a lane row, asserted against the population itself.

    ``load_lane_manifest`` refuses ``expected - declared``, so this is the loader's own
    verdict on this worktree rather than a restatement of it. It pins no count: a leaf that
    adds a module without a row fails here, and a leaf that adds both passes.
    """

    manifest = load_lane_manifest(REPO_ROOT)
    assert manifest.digest
