"""The mechanical census inventory at a pinned baseline (MIK-R20 rule 1).

:func:`take_inventory` reads two Git commits -- the code commit and the memory commit -- through
Git objects only (no checkout), and returns the census's ``baseline.json`` and ``inventory.json``:

* one **source row** per file of the code commit inside the census scope (every file when the scope
  is empty), with the governing onboarding route of its onboarding location
  ``onboarding/<path>.md``;
* one **artifact row** per file under ``onboarding/`` of the memory commit (the generated
  ``*.index.json`` cache and hidden directories excluded, as everywhere else), with its governing
  route.

An onboarding route is a directory under ``onboarding/`` holding an ``overview.md``; the governing
route of a location is the nearest such ancestor directory (MIK-R21 rule 1). A path no route governs
has no ``route``. Nothing here reads the content of a file: the inventory is mechanical, and claims
are agent work (D11).

A baseline that cannot be read -- a revision that names no commit, or a tree Git cannot list -- is
refused with :class:`CensusBaselineError`; no inventory is taken.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTreeReadError,
    code_tree_from_git,
    is_knowledge_path,
)
from agents_remember.models.knowledge_files.census import (
    CensusBaseline,
    CensusCommit,
    CensusInventory,
    InventoryRow,
)
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

ROUTE_CARD: Final = "overview.md"


class CensusBaselineError(ValueError):
    """The baseline cannot be read, so no inventory is taken at it."""


def resolve_commit(repository: Path, revision: str, *, side: str) -> str:
    """Return the exact commit ID ``revision`` names in ``repository``, or refuse."""

    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not commit:
        raise CensusBaselineError(
            f"unreadable baseline: {side} revision {revision!r} names no commit in {repository}"
        )
    return commit


def _paths_at(repository: Path, commit: str, *, side: str) -> frozenset[str]:
    try:
        return code_tree_from_git(repository, commit).paths
    except (KnowledgeTreeReadError, OSError, ValueError) as error:
        raise CensusBaselineError(
            f"unreadable baseline: cannot list the {side} commit {commit}: {error}"
        ) from error


def onboarding_routes(memory_paths: Iterable[str]) -> frozenset[str]:
    """The onboarding routes of a memory tree: ``.`` or the directory holding an ``overview.md``."""

    routes: set[str] = set()
    for path in memory_paths:
        directory, _, name = path.rpartition("/")
        if name != ROUTE_CARD:
            continue
        if directory == ONBOARDING_ROOT:
            routes.add(ROOT_ROUTE_PATH)
        elif directory.startswith(f"{ONBOARDING_ROOT}/"):
            routes.add(directory.removeprefix(f"{ONBOARDING_ROOT}/"))
    return frozenset(routes)


def governing_route(onboarding_location: str, routes: frozenset[str]) -> str | None:
    """The nearest onboarding route above ``onboarding_location``, or ``None``."""

    directory = posixpath.dirname(onboarding_location)
    while directory.startswith(f"{ONBOARDING_ROOT}/"):
        route = directory.removeprefix(f"{ONBOARDING_ROOT}/")
        if route in routes:
            return route
        directory = posixpath.dirname(directory)
    if directory == ONBOARDING_ROOT and ROOT_ROUTE_PATH in routes:
        return ROOT_ROUTE_PATH
    return None


def in_scope(path: str, scope: Sequence[str]) -> bool:
    """Whether a code path lies in one of the scope directories (every path, for no scope)."""

    return not scope or any(path == entry or path.startswith(f"{entry}/") for entry in scope)


def build_inventory(
    census_id: str,
    *,
    code_paths: Iterable[str],
    memory_paths: Iterable[str],
    scope: Sequence[str] = (),
) -> CensusInventory:
    """Build the inventory from the two commits' path sets (the mechanical part, without Git)."""

    artifacts = sorted(
        path
        for path in memory_paths
        if path.startswith(f"{ONBOARDING_ROOT}/") and is_knowledge_path(path)
    )
    routes = onboarding_routes(artifacts)

    def row(path: str, location: str) -> InventoryRow:
        route = governing_route(location, routes)
        return InventoryRow(path=path) if route is None else InventoryRow(path=path, route=route)

    return CensusInventory(
        census=census_id,
        sources=tuple(
            row(path, f"{ONBOARDING_ROOT}/{path}.md")
            for path in sorted(code_paths)
            if in_scope(path, scope)
        ),
        artifacts=tuple(row(path, path) for path in artifacts),
    )


@dataclass(frozen=True)
class BaselineSide:
    """One side of a baseline to pin: a repository and the revision naming the commit in it."""

    repository: Path
    revision: str = "HEAD"


def take_inventory(
    census_id: str,
    *,
    code: BaselineSide,
    memory: BaselineSide,
    scope: Sequence[str] = (),
) -> tuple[CensusBaseline, CensusInventory]:
    """Pin the baseline and inventory it; refuse a baseline that cannot be read."""

    code = BaselineSide(code.repository.absolute(), code.revision)
    memory = BaselineSide(memory.repository.absolute(), memory.revision)
    code_commit = resolve_commit(code.repository, code.revision, side="code")
    memory_commit = resolve_commit(memory.repository, memory.revision, side="memory")
    baseline = CensusBaseline(
        census=census_id,
        code=CensusCommit(commit=code_commit),
        memory=CensusCommit(commit=memory_commit),
        scope=tuple(sorted(scope)),
    )
    inventory = build_inventory(
        census_id,
        code_paths=_paths_at(code.repository, code_commit, side="code"),
        memory_paths=_paths_at(memory.repository, memory_commit, side="memory"),
        scope=baseline.scope,
    )
    return baseline, inventory
