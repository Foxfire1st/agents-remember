"""Focused proof for pytest-plugin and support dependency discovery.

This module owns the repository's real-boundary census over the evidence-lifecycle catalog. It also
guards the retained headless production-chain proof: the proof is ordinary ``test_`` source, owns
no governed evidence artifact or contract, and leaves the catalog closed over the governed
inventory. Every property is computed from the catalog; no byte, row count or file count of a
catalog is written down here, so adding a test file never edits this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest
from agents_remember_test_support.code_quality.dependency_ownership import (
    AMBIENT_ROLE_RUNNER_PATH,
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

PRODUCTION_PROOF_MODULE = Path("mcp/tests/test_lifecycle_owned_completion_relay.py")
"""The retained headless production-chain proof."""

LIFECYCLE_CATALOG = Path("mcp/tests/evidence-lifecycle.toml")
LANE_MANIFEST = Path("mcp/tests/test-evidence-lanes.toml")

LIFECYCLE_SCHEMA = "ar-test-evidence-lifecycle/v3"

REJECTED_STANDALONE_IDENTITY = "lifecycle-owned-completion-relay-production-chain"
"""The standalone contract identity the production proof must not register: nothing references it."""


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
    for source, consumer in (
        (AMBIENT_ROLE_RUNNER_PATH, Path("mcp/tests/test_agents_remember_quality.py")),
        (LAYERS_CONTRACT_PATH, Path("mcp/tests/test_layering.py")),
    ):
        impact = graph.resolve([source])
        assert impact.complete
        assert not impact.global_invalidation
        assert consumer in impact.tests
        assert any(
            reason.kind.value == "declared-consumer" for reason in impact.reasons_for(consumer)
        )


@pytest.mark.integration
def test_production_proof_adds_no_governed_evidence_artifact() -> None:
    """The production-chain proof is ordinary test source and owns no governed artifact.

    The proof's controlled adapter frames, ports, and helper objects stay Python source inside the
    one module the lane manifest already selects, so the catalog fully closes over the governed
    inventory and nothing in it belongs to the proof. Each escape reddens here: a proof fixture or
    support module landing unregistered in the governed tree, a catalog row deleted so a governed
    path loses its metadata, a standalone ``[[contract]]`` row, a row or contract the proof owns,
    the proof module renamed out of ``test_`` source, and a dropped lane row.
    """

    catalog = _load(LIFECYCLE_CATALOG)
    _assert_the_proof_is_ordinary_test_source()
    _assert_the_proof_is_selected_by_the_lane_manifest()
    _assert_the_governed_inventory_is_closed(catalog)
    _assert_the_catalog_keeps_its_identities(catalog)
    assert proof_ownership(catalog, PRODUCTION_PROOF_MODULE.as_posix()) == []


def proof_ownership(catalog: Mapping[str, object], proof: str) -> list[str]:
    """Name every catalog row or contract that the module ``proof`` owns; none is allowed."""

    owned: list[str] = []
    for row in _tables(catalog, "artifact"):
        consumers = row.get("consumers")
        if row.get("path") == proof:
            owned.append(f"artifact row with path {proof}")
        if row.get("owner") == proof:
            owned.append(f"artifact row {row.get('path')} is owned by {proof}")
        if isinstance(consumers, list) and consumers == [proof]:
            owned.append(f"artifact row {row.get('path')} has {proof} as its only consumer")
    for contract in _tables(catalog, "contract"):
        if contract.get("owner") == proof:
            owned.append(f"contract {contract.get('id')} is owned by {proof}")
        if str(contract.get("evidence_node")).partition("::")[0] == proof:
            owned.append(f"contract {contract.get('id')} names {proof} as its evidence node")
    return owned


def _synthetic_catalog(
    artifact: Mapping[str, object], contract: Mapping[str, object]
) -> dict[str, object]:
    other = "mcp/tests/test_other.py"
    base_artifact = {"path": "mcp/tests/_support.py", "owner": "c", "consumers": [other]}
    base_contract = {"id": "c", "owner": "mcp/tests/_support.py", "evidence_node": f"{other}::t"}
    return {
        "artifact": [{**base_artifact, **artifact}],
        "contract": [{**base_contract, **contract}],
    }


@pytest.mark.parametrize(
    ("artifact", "contract", "expected"),
    [
        ({}, {}, 0),
        ({"path": "mcp/tests/test_proof.py"}, {}, 1),
        ({"owner": "mcp/tests/test_proof.py"}, {}, 1),
        ({"consumers": ["mcp/tests/test_proof.py"]}, {}, 1),
        ({}, {"owner": "mcp/tests/test_proof.py"}, 1),
        ({}, {"evidence_node": "mcp/tests/test_proof.py::test_x"}, 1),
        ({"consumers": ["mcp/tests/test_proof.py", "mcp/tests/test_other.py"]}, {}, 0),
    ],
)
def test_proof_ownership_is_computed_from_the_catalog(
    artifact: Mapping[str, object], contract: Mapping[str, object], expected: int
) -> None:
    """The proof owns nothing: path, owner, sole consumer, contract owner and evidence node."""

    catalog = _synthetic_catalog(artifact, contract)
    assert len(proof_ownership(catalog, "mcp/tests/test_proof.py")) == expected


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


def _assert_the_proof_is_ordinary_test_source() -> None:
    module = REPOSITORY_ROOT / PRODUCTION_PROOF_MODULE
    assert module.is_file(), (
        f"{PRODUCTION_PROOF_MODULE.as_posix()} is the retained production-chain proof"
    )
    assert module.name.startswith("test_"), (
        f"{PRODUCTION_PROOF_MODULE.as_posix()} must stay ordinary test source: a non-``test_`` "
        "name moves it into the governed inventory, where no lifecycle row is permitted for it"
    )
    governed = governed_artifact_paths(REPOSITORY_ROOT)
    assert PRODUCTION_PROOF_MODULE.as_posix() not in governed, (
        "the proof's controlled adapter frames, ports, and fakes are module-local test source and "
        f"must stay outside the governed inventory, but {PRODUCTION_PROOF_MODULE.as_posix()} was "
        "discovered by governed_artifact_paths"
    )


def _assert_the_proof_is_selected_by_the_lane_manifest() -> None:
    membership = _lane_membership()
    owning = sorted(
        category
        for category, paths in membership.items()
        if PRODUCTION_PROOF_MODULE.as_posix() in paths
    )
    assert len(owning) == 1, (
        f"{PRODUCTION_PROOF_MODULE.as_posix()} must stay in exactly one evidence lane so the "
        f"existing test and quality selection keeps running the exact production-chain node; found {owning}"
    )


def _assert_the_governed_inventory_is_closed(catalog: Mapping[str, object]) -> None:
    """Every governed path carries lifecycle metadata, and the catalog itself validates whole."""

    governed = _governed_artifact_paths(catalog)
    cataloged = _cataloged_paths(catalog)
    assert governed == cataloged, (
        "the governed-artifact inventory must stay closed over the lifecycle catalog, so the "
        "production proof adds no unregistered path and no row points outside it; "
        f"unregistered={sorted(governed - cataloged)}, stale={sorted(cataloged - governed)}. A "
        "proof fixture or support module that escaped the test module reddens here: return the "
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
    assert REJECTED_STANDALONE_IDENTITY not in declared, REJECTED_STANDALONE_IDENTITY
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


def _lane_membership() -> dict[str, list[str]]:
    files = _load(LANE_MANIFEST)["files"]
    assert isinstance(files, Mapping), files
    return {str(category): [str(path) for path in paths] for category, paths in files.items()}
