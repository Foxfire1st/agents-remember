"""Collect the canonical population but execute only dependency-owned retry files."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pytest

RETRY_EXECUTE_PATH_OPTION = "--ar-retry-execute-path"


class _CollectionState:
    def __init__(self) -> None:
        self.root: Path | None = None
        self.modules: set[Path] = set()


_COLLECTION_STATE = _CollectionState()


class _CollectReport(Protocol):
    passed: bool
    fspath: str


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        RETRY_EXECUTE_PATH_OPTION,
        action="append",
        default=[],
        help=(
            "candidate-relative affected test module for dependency-owned retry proof; "
            "repeat for every affected module, including successfully collected zero-body "
            "modules"
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Start one invocation-local record of successfully collected Python modules."""

    _COLLECTION_STATE.root = Path(str(config.rootpath)).resolve()
    _COLLECTION_STATE.modules.clear()


def pytest_collectreport(report: _CollectReport) -> None:
    """Remember modules whose collector completed, even when they own zero test bodies."""

    root = _COLLECTION_STATE.root
    if root is None or not report.passed:
        return
    path = Path(str(report.fspath)).resolve()
    if path.suffix != ".py":
        return
    try:
        _COLLECTION_STATE.modules.add(path.relative_to(root))
    except ValueError:
        return


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect every body outside the explicit affected-module population.

    Collection is intentionally complete: module-import coverage has no per-test
    context and therefore must be rebuilt for the current candidate. Execution is
    intentionally narrow. A configured path must either own an executable item or have a
    successful Python-module collection report. The latter distinction preserves import/collection
    coverage for zero-body shared-definition modules without treating a missing path as present.
    """

    root = Path(str(config.rootpath)).resolve()
    allowed = _configured_allowed_paths(root, config.getoption(RETRY_EXECUTE_PATH_OPTION))
    selected, deselected, observed = _partition_items(root, items, allowed)
    _refuse_uncollected_paths(allowed, observed)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


def _configured_allowed_paths(root: Path, raw_paths: object) -> set[Path]:
    if (
        not isinstance(raw_paths, list)
        or not raw_paths
        or not all(isinstance(value, str) and value for value in raw_paths)
    ):
        raise pytest.UsageError(
            "dependency-owned retry execution requires one or more explicit test paths"
        )
    return {_candidate_relative_path(root, value) for value in raw_paths}


def _partition_items(
    root: Path,
    items: list[pytest.Item],
    allowed: set[Path],
) -> tuple[list[pytest.Item], list[pytest.Item], set[Path]]:
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    observed: set[Path] = set()
    for item in items:
        relative = _item_relative_path(root, item)
        if relative in allowed:
            selected.append(item)
            observed.add(relative)
        else:
            deselected.append(item)
    return selected, deselected, observed


def _refuse_uncollected_paths(allowed: set[Path], observed: set[Path]) -> None:
    missing = sorted(allowed - observed - _COLLECTION_STATE.modules, key=Path.as_posix)
    if missing:
        rendered = ", ".join(path.as_posix() for path in missing)
        raise pytest.UsageError(
            f"dependency-owned retry paths were not collected as test modules: {rendered}"
        )


def _candidate_relative_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise pytest.UsageError(
            f"dependency-owned retry path must be candidate-relative: {value!r}"
        )
    resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:  # pragma: no cover - resolve plus '..' guard is defensive
        raise pytest.UsageError(
            f"dependency-owned retry path escapes the candidate root: {value!r}"
        ) from error
    if relative.suffix != ".py":
        raise pytest.UsageError(
            f"dependency-owned retry path must name a Python test module: {value!r}"
        )
    return relative


def _item_relative_path(root: Path, item: pytest.Item) -> Path:
    try:
        return Path(item.path).resolve().relative_to(root)
    except ValueError as error:
        raise pytest.UsageError(
            f"dependency-owned retry item is outside the candidate root: {item.nodeid}"
        ) from error
