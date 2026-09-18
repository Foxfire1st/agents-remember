"""The two evidence-catalog gates, and how to tell them apart when one of them is red.

``mcp/tests/test_dependency_ownership_ast_helpers.py`` runs both of these inside one test, so a
reader sees one gate. They are two:

* **The catalog byte pin** -- ``LIFECYCLE_CATALOG_SHA256``, ``LIFECYCLE_CONTRACT_COUNT`` and
  ``LIFECYCLE_ARTIFACT_COUNT``, asserted against ``mcp/tests/evidence-lifecycle.toml``'s own bytes
  and populations. It answers "is this the exact catalog file that was measured?". Nothing in the
  source tree can redden it: only an edit to the catalog's bytes changes it, and its documented
  repair is a re-pin after the edit was reviewed.
* **The consumer-completeness oracle** -- ``load_evidence_inventory`` and its finding
  ``<artifact>: consumer proof differs from source-derived ownership``. It answers "does the catalog
  agree with the source tree it describes?", by deriving the repository's dependency graph and
  requiring every ``consumer_scope = "exact"`` artifact's declared ``consumers`` list to equal the
  test modules that actually reach it. It reddens whenever a module starts or stops consuming a
  governed artifact, and its documented repair is a registry row -- which is *why* the pin moves
  afterwards.

The case below evaluates both in one run over one catalog edit, so the distinction is a measurement
rather than a claim: the oracle is red and the pin is green at the same instant.
"""

from __future__ import annotations

import hashlib
import subprocess
import tomllib
from pathlib import Path

import pytest
from _evidence_catalog_fixture import write_synthetic_evidence_catalog
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
)

# The pinned catalog, read by this case only to show that the oracle's refusal left it alone. Its
# digest and populations are compared against each other rather than against constants, so this case
# cannot become a second place that has to be re-pinned: the pin's own predicate lives in
# ``test_dependency_ownership_ast_helpers.py`` and is evaluated over these same bytes.
REPOSITORY_ROOT = Path(__file__).parents[2]
PINNED_CATALOG = Path("mcp/tests/evidence-lifecycle.toml")

ARTIFACT = "mcp/tests/_catalog_anchor.py"
FIRST_CONSUMER = "mcp/tests/test_alpha.py"
SECOND_CONSUMER = "mcp/tests/test_beta.py"


def synthetic_repo(root: Path) -> Path:
    """Build one valid catalog over two real consumers, and return the catalog path."""

    tests = root / "mcp" / "tests"
    tests.mkdir(parents=True)
    for name in (FIRST_CONSUMER, SECOND_CONSUMER):
        (root / name).write_text("def test_plain():\n    assert True\n", encoding="utf-8")
    (root / ARTIFACT).write_text("VALUE = 1\n", encoding="utf-8")
    catalog = write_synthetic_evidence_catalog(root, {ARTIFACT: (FIRST_CONSUMER, SECOND_CONSUMER)})
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["mcp/tests"]\n', encoding="utf-8"
    )
    # The oracle derives its graph from the repository's tracked files, so the synthetic repository
    # has to be one.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return catalog


def populations(catalog: Path) -> tuple[str, int, int]:
    """Return one catalog's byte digest and its declared contract and artifact populations."""

    document = tomllib.loads(catalog.read_text(encoding="utf-8"))
    return (
        hashlib.sha256(catalog.read_bytes()).hexdigest(),
        len(document.get("contract", [])),
        len(document.get("artifact", [])),
    )


def test_the_oracle_reddens_while_the_byte_pin_stays_green(tmp_path: Path) -> None:
    """One catalog edit: the consumer oracle refuses the tree, the byte pin keeps passing.

    The synthetic catalog starts valid, so the oracle's refusal below is produced by the edit and
    not by the fixture. The edit removes one declared consumer from the artifact's ``consumers``
    list while the artifact is still consumed by that module, which is exactly the shape a landing
    produces when it adds a test module that imports a governed support module and does not add the
    registry row -- the catalog's bytes are then still the ones the pin measured, and the tree is no
    longer the one the catalog describes.

    Reddening the *pin* instead would take an edit to the catalog file itself, which is why the
    pinned catalog is read here as well: its digest and populations are identical before and after
    the oracle has refused, so the two gates are shown to be independent rather than to be one gate
    reported twice. That comparison is between the file and itself, deliberately -- the pin's own
    constants are asserted in the pin's own test, and duplicating them here would create a second
    place to re-pin.
    """

    root = tmp_path / "repo"
    catalog = synthetic_repo(root)
    assert load_evidence_inventory(root), "the fixture's own catalog must be valid"

    pinned = REPOSITORY_ROOT / PINNED_CATALOG
    pinned_before = populations(pinned)

    doctored = catalog.read_text(encoding="utf-8").replace(
        f'"{SECOND_CONSUMER}"', f'"{FIRST_CONSUMER}"'
    )
    assert doctored != catalog.read_text(encoding="utf-8")
    catalog.write_text(doctored, encoding="utf-8")

    with pytest.raises(EvidenceLifecycleError) as caught:
        load_evidence_inventory(root)

    findings = str(caught.value)
    assert "consumer proof differs from source-derived ownership" in findings
    assert f"missing=['{SECOND_CONSUMER}']" in findings
    assert populations(pinned) == pinned_before, (
        "the oracle's refusal is about the source tree, and it may not move the pinned bytes"
    )
