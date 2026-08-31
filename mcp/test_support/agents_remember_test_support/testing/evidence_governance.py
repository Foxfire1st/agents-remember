"""Repository discovery policy for durable test-evidence artifacts."""

from __future__ import annotations

import re
from pathlib import Path

DATA_SUFFIXES = frozenset({".json", ".jsonl", ".yaml", ".yml", ".csv", ".bin"})
SOURCE_SUFFIXES = frozenset({".py", ".pyi"})
TASK_DATE_PROOF = re.compile(r"(?:^|[_-])(?:baseline|\d{6})(?:[_\-.]|$)")
POLICY_MANIFESTS = frozenset({"mcp/tests/test-evidence-lanes.toml"})
LIFECYCLE_CATALOG_PATH = "mcp/tests/evidence-lifecycle.toml"
PERMANENT_E2E_SUPPORT_ROOTS = (Path("scripts/e2e_harness"),)


def governed_artifact_paths(
    root: Path,
    *,
    large_fixture_bytes: int = 25_000,
) -> set[str]:
    """Discover durable fixtures, support modules, and task-shaped proof.

    The size threshold closes the catalog over durable files whose suffix is not
    known in advance. Python source remains governed by the repository source and
    file-size rails rather than being misclassified as fixture evidence.
    """

    if large_fixture_bytes <= 0:
        raise ValueError("large_fixture_bytes must be a positive integer")
    tests_root = root / "mcp/tests"
    files = tuple(
        path
        for path in tests_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and _relative(root, path) != LIFECYCLE_CATALOG_PATH
    )
    governed = {_relative(root, path) for path in files if path.suffix.lower() in DATA_SUFFIXES}
    governed.update(
        _relative(root, path)
        for path in files
        if path.suffix.lower() == ".py"
        and not path.name.startswith("test_")
        and not path.name.endswith("_test.py")
    )
    governed.update(_relative(root, path) for path in files if TASK_DATE_PROOF.search(path.name))
    governed.update(
        _relative(root, path)
        for path in files
        if path.suffix.lower() not in SOURCE_SUFFIXES and path.stat().st_size >= large_fixture_bytes
    )
    governed.update(path for path in POLICY_MANIFESTS if (root / path).is_file())
    for support_root in PERMANENT_E2E_SUPPORT_ROOTS:
        governed.update(
            _relative(root, path) for path in (root / support_root).glob("*.py") if path.is_file()
        )
    return governed


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
