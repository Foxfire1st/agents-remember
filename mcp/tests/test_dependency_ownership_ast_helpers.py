"""Focused proof for pytest-plugin and support dependency discovery.

This module owns the repository's real-boundary census over the evidence-lifecycle catalog, so it
also carries the ``LOCR-R26@v1`` guard: the retained headless production-chain proof of
``LOCR-R16@v1`` is ordinary ``test_`` source, adds no governed evidence artifact, and leaves the
catalog closed over the governed inventory.

Amendment on the record (developer decision, 2026-09-16): ``LOCR-R26@v1``'s catalog-freeze clause was
amended to permit registering artifacts that ANOTHER leaf introduced. Under it the two handoff
support modules added by ``LOCR-L04`` were registered in ``mcp/tests/evidence-lifecycle.toml``, which
is what closed the inventory. The proof's own artifact delta remains exactly empty, and the freeze
still forbids the proof adding or widening anything.
"""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from pathlib import Path

import pytest
from agents_remember_test_support.code_quality.dependency_ownership import (
    AMBIENT_ROLE_RUNNER_PATH,
    CODEX_CONFIG_PATH,
    LAYERS_CONTRACT_PATH,
    DependencyOwnershipGraph,
)
from agents_remember_test_support.testing.evidence_governance import governed_artifact_paths
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
)

REPOSITORY_ROOT = Path(__file__).parents[2]

PRODUCTION_PROOF_MODULE = Path("mcp/tests/test_lifecycle_owned_completion_relay.py")
"""The retained headless production-chain proof required by ``LOCR-R16@v1``."""

LIFECYCLE_CATALOG = Path("mcp/tests/evidence-lifecycle.toml")
LANE_MANIFEST = Path("mcp/tests/test-evidence-lanes.toml")

LIFECYCLE_SCHEMA = "ar-test-evidence-lifecycle/v3"
LIFECYCLE_CONTRACT_COUNT = 4
LIFECYCLE_ARTIFACT_COUNT = 51
LIFECYCLE_CATALOG_SHA256 = "812211e9f93fc3a75c2759126e7605a5998f79d745cd491aeee63348c11c9d6e"
"""``mcp/tests/evidence-lifecycle.toml`` byte-for-byte, re-pinned deliberately twice.

The digest was first pinned at the R16 proof's landing (5b7a84f2) as
``a9d83c375d1bfdcae7d0c46020eba41fbaf305a306fe89bb1b9b479b861c2002``. ``LOCR-R26@v1``'s
catalog-freeze clause was then amended by explicit developer decision to permit registering
artifacts that ANOTHER leaf introduced, and the two ``LOCR-L04`` handoff support modules were
registered, closing the inventory. This value is the post-registration catalog. The proof's own
artifact delta remains exactly empty, so the freeze still forbids the proof adding or widening
anything, and any further catalog change must re-pin this digest deliberately.

**Second deliberate re-pin (260915-CAPS-L16, 2026-09-16).** The first re-pin left the pin behind
the file: at the source-line convergence merge ``23cc7a72`` the catalog was already
``bb567a25f30b9e3bdbd48a9dd1d2641a6a240763f545ddeca1a4b7932e98cb15`` over **4 contracts / 50
artifacts** while this pin still read ``293a187f…`` / 45, because the merge carried the landed
260831-LOCR line's governed artifacts in without a re-pin, and ``260915-CAPS-L13`` then moved the
file again (``9ea1d207…``, 4/50). That stale pin was the single pre-existing integration failure
(D10) inherited by every candidate cut from the master tip, and it is nobody's finding.

The value below is re-derived at ``8997e184`` -- the tip this leaf lands -- where the catalog is
``812211e9…`` over **4 contracts / 51 artifacts**: ``260915-CAPS-L7``'s landing added the
fifty-first governed artifact row. The population moved 45 -> 50 (the merged LOCR line) -> 51
(L7), and each of those states was measured rather than assumed. It is pinned here because a leaf
that changes the catalog last must pin the value at its own tip; a value correct for someone
else's base re-reds the moment this one lands. The proof's own artifact delta is still exactly
empty: this leaf registered nothing and added no consumer.
"""

REJECTED_STANDALONE_IDENTITY = "lifecycle-owned-completion-relay-production-chain"
"""The standalone contract identity ``LOCR-R26@v1`` explicitly rejects: no artifact references it."""


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
    """The R16 production-chain proof is ordinary test source and adds no governed artifact.

    ``LOCR-R26@v1``: the proof's controlled adapter frames, ports, and helper objects stay Python
    source inside the one module the lane manifest already selects, so the proof's own
    governed-artifact delta is exactly empty, the catalog fully closes over the governed inventory,
    and ``mcp/tests/evidence-lifecycle.toml`` keeps its pinned bytes. Five distinct escapes redden
    here: a proof fixture or support module landing unregistered in the governed tree, a catalog row
    deleted so a governed path loses its metadata, a standalone ``[[contract]]`` row or any other
    catalog edit, the proof module renamed out of ``test_`` source, and a dropped lane row.
    """

    catalog = _load(LIFECYCLE_CATALOG)
    _assert_the_proof_is_ordinary_test_source()
    _assert_the_proof_is_selected_by_the_lane_manifest()
    _assert_the_governed_inventory_is_closed(catalog)
    _assert_the_catalog_kept_its_bytes_and_identities(catalog)


def _assert_the_proof_is_ordinary_test_source() -> None:
    module = REPOSITORY_ROOT / PRODUCTION_PROOF_MODULE
    assert module.is_file(), f"{PRODUCTION_PROOF_MODULE.as_posix()} is the retained R16 proof"
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
        f"existing test and quality selection keeps running the exact R16 node; found {owning}"
    )


def _assert_the_governed_inventory_is_closed(catalog: Mapping[str, object]) -> None:
    """Every governed path carries lifecycle metadata, and the catalog itself validates whole."""

    governed = _governed_artifact_paths(catalog)
    cataloged = _cataloged_paths(catalog)
    assert governed == cataloged, (
        "the governed-artifact inventory must stay closed over the lifecycle catalog, so the R16 "
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


def _assert_the_catalog_kept_its_bytes_and_identities(catalog: Mapping[str, object]) -> None:
    assert catalog["schema_version"] == LIFECYCLE_SCHEMA, catalog["schema_version"]
    declared, referenced = _artifact_identities(catalog)
    assert REJECTED_STANDALONE_IDENTITY not in declared, REJECTED_STANDALONE_IDENTITY
    assert declared == referenced, sorted(declared - referenced)
    digest = hashlib.sha256((REPOSITORY_ROOT / LIFECYCLE_CATALOG).read_bytes()).hexdigest()
    contracts = _tables(catalog, "contract")
    artifacts = _tables(catalog, "artifact")
    assert (
        digest,
        len(contracts),
        len(artifacts),
    ) == (
        LIFECYCLE_CATALOG_SHA256,
        LIFECYCLE_CONTRACT_COUNT,
        LIFECYCLE_ARTIFACT_COUNT,
    ), "mcp/tests/evidence-lifecycle.toml must stay at its pinned bytes and populations"


def _load(relative: Path) -> Mapping[str, object]:
    with (REPOSITORY_ROOT / relative).open("rb") as stream:
        return tomllib.load(stream)


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


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
