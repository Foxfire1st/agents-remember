"""memory_quality must not depend on the closeout plane.

A curator runs memory-quality checks before closeout exists at all: there is no
contract, no door and no operation record yet. So memory_quality importing
`worktrees.integration.closeout/**`, `worktrees.integration.lifecycle/**` or
`models.lifecycles.operation` is a backwards dependency -- a pre-closeout
quality service depending on the plane it is supposed to be independent of.

This test is the executable specification of that direction, and it names every
offending edge so the fix is driven rather than planned.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agents_remember import memory_quality
from agents_remember.memory_quality.check import run_memory_quality_check
from agents_remember.memory_quality.style.citations.resolution import Trees

FORBIDDEN_PREFIXES = (
    "agents_remember.worktrees.integration.closeout",
    "agents_remember.worktrees.integration.lifecycle",
    "agents_remember.models.lifecycles.operation",
)


def _memory_quality_root() -> Path:
    root = Path(memory_quality.__file__).parent
    assert root.is_dir()
    return root


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def test_memory_quality_imports_no_closeout_plane_module() -> None:
    """Zero closeout, zero lifecycle, zero operation-model imports."""

    offenders: list[str] = []
    for path in sorted(_memory_quality_root().rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if module.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{path.name}: {module}")
    assert offenders == [], (
        "memory_quality depends on the closeout plane it must be independent of:\n  "
        + "\n  ".join(offenders)
    )


def test_memory_quality_public_api_imports_with_no_closeout_in_existence() -> None:
    """The pure quality surface loads with no contract, door or operation record."""

    assert run_memory_quality_check is not None
    assert Trees is not None
