"""The consumer-completeness oracle reddens on the source tree, not on the catalog's form.

``load_evidence_inventory`` answers "does the catalog agree with the source tree it describes?" by
deriving the repository's dependency graph and requiring every ``consumer_scope = "exact"``
artifact's declared ``consumers`` list to equal the test modules that actually reach it. It is red
whenever a module starts or stops consuming a governed artifact, whatever form the catalog is in;
the catalog's own canonical form is a separate refusal with its own command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _evidence_catalog_fixture import write_synthetic_evidence_catalog
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
)

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


def test_the_oracle_refuses_a_tree_the_catalog_no_longer_describes(tmp_path: Path) -> None:
    """One catalog edit: the consumer oracle refuses the tree, and it is the only finding.

    The synthetic catalog starts valid and canonical, so the oracle's refusal below is produced by
    the edit and not by the fixture. The edit removes one declared consumer from the artifact's
    ``consumers`` list while the artifact is still consumed by that module, which is exactly the
    shape a landing produces when it adds a test module that imports a governed support module and
    does not add the registry row. The catalog stays canonical, so the refusal is about the source
    tree and not about the catalog's form.
    """

    root = tmp_path / "repo"
    catalog = synthetic_repo(root)
    assert load_evidence_inventory(root), "the fixture's own catalog must be valid"

    text = catalog.read_text(encoding="utf-8")
    doctored = text.replace(f'  "{SECOND_CONSUMER}",\n', "")
    assert doctored != text
    catalog.write_text(doctored, encoding="utf-8")

    with pytest.raises(EvidenceLifecycleError) as caught:
        load_evidence_inventory(root)

    findings = str(caught.value)
    assert "consumer proof differs from source-derived ownership" in findings
    assert f"missing=['{SECOND_CONSUMER}']" in findings
    assert "1 finding(s)" in findings
    assert "--write" not in findings
