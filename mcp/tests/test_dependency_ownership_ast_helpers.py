"""Focused proof for pytest-plugin and support dependency discovery.

This module owns the repository's real-boundary census over the evidence-lifecycle catalog: the
catalog stays closed over the governed inventory and declares no contract that nothing references.
Every property is computed from the catalog; no byte, row count or file count of a catalog is
written down here, so adding a test file never edits this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from agents_remember_test_support.code_quality.dependency_ownership import (
    CODEX_CONFIG_PATH,
    LAYERS_CONTRACT_PATH,
    DependencyOwnershipGraph,
)
from agents_remember_test_support.testing.catalog_canonical import read_catalog
from agents_remember_test_support.testing.evidence_governance import governed_artifact_paths
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
)

REPOSITORY_ROOT = Path(__file__).parents[2]

LIFECYCLE_CATALOG = Path("mcp/tests/evidence-lifecycle.toml")

LIFECYCLE_SCHEMA = "ar-test-evidence-lifecycle/v3"


@pytest.mark.integration
def test_repository_inputs_reach_their_supported_consumers() -> None:
    # The graph validates the complete artifact catalog as part of this one real census.
    graph = DependencyOwnershipGraph(Path(__file__).parents[2])
    empty = graph.resolve([CODEX_CONFIG_PATH])
    assert empty.complete and not empty.tests
    assert [reason.detail for reason in empty.input_decisions] == [
        "verified-repository-input-no-consumers"
    ]
    assert not graph.resolve([Path("unknown") / "settings" / "unowned.toml"]).complete
    consumer = Path("mcp/tests/test_layering.py")
    impact = graph.resolve([LAYERS_CONTRACT_PATH])
    assert impact.complete
    assert not impact.global_invalidation
    assert consumer in impact.tests
    assert any(reason.kind.value == "declared-consumer" for reason in impact.reasons_for(consumer))


@pytest.mark.integration
def test_the_catalog_is_closed_over_the_governed_inventory_and_keeps_its_identities() -> None:
    """The catalog closes over the governed inventory and every contract in it is referenced.

    Each escape reddens here: a fixture or support module landing unregistered in the governed
    tree, a catalog row deleted so a governed path loses its metadata, a row left behind for a
    removed file, and a standalone ``[[contract]]`` row that no artifact row names.
    """

    catalog = _load(LIFECYCLE_CATALOG)
    _assert_the_governed_inventory_is_closed(catalog)
    _assert_the_catalog_keeps_its_identities(catalog)


def test_a_catalog_that_does_not_parse_fails_these_checks_with_the_recovery_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / LIFECYCLE_CATALOG
    catalog.parent.mkdir(parents=True)
    catalog.write_text('consumers = [ path = "x"\n', encoding="utf-8")
    monkeypatch.setitem(globals(), "REPOSITORY_ROOT", tmp_path)
    with pytest.raises(AssertionError, match="interleaved by the merge") as caught:
        _load(LIFECYCLE_CATALOG)
    assert str(catalog) in str(caught.value)


def _assert_the_governed_inventory_is_closed(catalog: Mapping[str, object]) -> None:
    """Every governed path carries lifecycle metadata, and the catalog itself validates whole."""

    governed = _governed_artifact_paths(catalog)
    cataloged = _cataloged_paths(catalog)
    assert governed == cataloged, (
        "the governed-artifact inventory must stay closed over the lifecycle catalog: no "
        "unregistered path and no row that points outside it; "
        f"unregistered={sorted(governed - cataloged)}, stale={sorted(cataloged - governed)}. A "
        "fixture or support module that escaped its test module reddens here: return the "
        "input to module-local test source instead of registering a file to force green."
    )
    try:
        load_evidence_inventory(REPOSITORY_ROOT)
    except EvidenceLifecycleError as error:
        raise AssertionError(
            f"the evidence-lifecycle catalog does not validate: {error}"
        ) from error


def _assert_the_catalog_keeps_its_identities(catalog: Mapping[str, object]) -> None:
    assert catalog["schema_version"] == LIFECYCLE_SCHEMA, catalog["schema_version"]
    declared, referenced = _artifact_identities(catalog)
    assert declared == referenced, sorted(declared - referenced)


def _load(relative: Path) -> Mapping[str, object]:
    return read_catalog("test catalog", REPOSITORY_ROOT / relative, AssertionError)[1]


def _governed_artifact_paths(catalog: Mapping[str, object]) -> set[str]:
    threshold = catalog["large_fixture_bytes"]
    assert isinstance(threshold, int) and not isinstance(threshold, bool), threshold
    return governed_artifact_paths(REPOSITORY_ROOT, large_fixture_bytes=threshold)


def _cataloged_paths(catalog: Mapping[str, object]) -> set[str]:
    return {str(item["path"]) for item in _tables(catalog, "artifact")}


def _artifact_identities(catalog: Mapping[str, object]) -> tuple[set[str], set[str]]:
    declared = {str(item["id"]) for item in _tables(catalog, "contract")}
    referenced = {
        str(item["replacement_contract"]).removeprefix("contract:")
        for item in _tables(catalog, "artifact")
        if str(item["replacement_contract"]).startswith("contract:")
    }
    return declared, referenced


def _tables(catalog: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    rows = catalog[key]
    assert isinstance(rows, list), f"{key} must be a list"
    return [row for row in rows if isinstance(row, Mapping)]
