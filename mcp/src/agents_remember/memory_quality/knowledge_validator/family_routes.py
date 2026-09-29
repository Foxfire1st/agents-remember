"""Family routes (MIK-R04): where a family's code lives, checked against its realization entries.

A family record owns ``routes``: repository directories where part of the family's code lives (D12,
D20). The directory tree is the route hierarchy; there is no route registry and no parent edge.
Two rules bind the route set, evaluated over the realization entries of the same memory tree (proof
entries do not count -- tests live in their own directories):

* **Coverage:** every realization entry of every member lies under at least one route;
* **Non-empty:** every route contains at least one such entry.

Three states are reported, never refused (rule 4):

* ``unrealized_family`` -- no member has a realization entry yet; Non-empty is waived;
* ``route_unassigned`` -- ``routes: []`` with ``admission: legacy-unassessed``, which only the export
  (MIK-R24) produces; Coverage is waived. Any other family with ``routes: []`` violates Coverage;
* a retired family -- neither rule applies.

:func:`family_route_state` computes one family's state; the validator's rules
(:mod:`.rules_routes`) and, later, the route-maintenance worklist (MIK-R06) read it.
:func:`suggest_routes` is the mechanical suggestion of rule 3. It is labelled ``mechanical`` and never
written by itself: the curator places routes as deep as makes sense.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from posixpath import dirname
from typing import Final, Literal

from agents_remember.memory_quality.knowledge_validator.parsed import SidecarFile
from agents_remember.models.knowledge_files.records import FamilyRecord
from agents_remember.models.knowledge_files.shapes import LEGACY_UNASSESSED
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH, FileSidecar

MECHANICAL: Final = "mechanical"


@dataclass(frozen=True)
class RealizationLocation:
    """One realization entry, where it lies: the source file its sidecar belongs to."""

    entry: str
    invariant: str
    path: str
    sidecar: str


def realization_locations(
    sidecars: Iterable[SidecarFile],
) -> Mapping[str, tuple[RealizationLocation, ...]]:
    """Every realization entry of the tree, by the invariant it realizes."""

    found: dict[str, list[RealizationLocation]] = defaultdict(list)
    for sidecar in sidecars:
        if not isinstance(sidecar.sidecar, FileSidecar):
            continue
        for entry in sidecar.sidecar.realizes:
            found[entry.invariant].append(
                RealizationLocation(entry.id, entry.invariant, sidecar.sidecar.path, sidecar.path)
            )
    return {invariant: tuple(locations) for invariant, locations in found.items()}


def route_covers(route: str, path: str) -> bool:
    """Answer whether ``path`` lies under the directory ``route``; the root route ``.`` covers all."""

    return route == ROOT_ROUTE_PATH or path.startswith(f"{route}/")


@dataclass(frozen=True)
class FamilyRouteState:
    """One family's routes against its members' realization entries (MIK-R04 rules 2 and 4)."""

    family: FamilyRecord
    locations: tuple[RealizationLocation, ...]

    @property
    def retired(self) -> bool:
        return self.family.status == "retired"

    @property
    def unrealized(self) -> bool:
        """``unrealized_family``: no member has a realization entry yet."""

        return not self.locations

    @property
    def unassigned(self) -> bool:
        """``route_unassigned``: no routes, and the family is an unassessed export."""

        return not self.family.routes and self.family.admission == LEGACY_UNASSESSED

    @property
    def coverage_applies(self) -> bool:
        return not self.retired and not self.unassigned

    @property
    def non_empty_applies(self) -> bool:
        return not self.retired and not self.unrealized

    @property
    def uncovered(self) -> tuple[RealizationLocation, ...]:
        """The entries under no route, when Coverage applies."""

        if not self.coverage_applies:
            return ()
        routes = self.family.routes
        return tuple(
            location
            for location in self.locations
            if not any(route_covers(route, location.path) for route in routes)
        )

    @property
    def emptied(self) -> tuple[str, ...]:
        """The routes that contain no entry, when Non-empty applies."""

        if not self.non_empty_applies:
            return ()
        return tuple(
            route
            for route in self.family.routes
            if not any(route_covers(route, location.path) for location in self.locations)
        )

    @property
    def routeless(self) -> bool:
        """Coverage is violated by the route set itself: no route, and not an unassessed export."""

        return self.coverage_applies and not self.family.routes


def family_route_state(
    family: FamilyRecord, locations: Mapping[str, tuple[RealizationLocation, ...]]
) -> FamilyRouteState:
    """Return ``family``'s route state over the realization entries of its members."""

    members = tuple(location for member in family.members for location in locations.get(member, ()))
    return FamilyRouteState(family=family, locations=members)


# --------------------------------------------------------------------------------------------------
# The mechanical suggestion (rule 3)
# --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteSuggestion:
    """A mechanical route set for one family. It is offered, never written by itself.

    ``at_repository_root`` lists realization paths that sit directly at the repository root. Only
    the root route ``.`` covers them, so it is suggested exactly then (MIK-R04's rare case), beside
    the local routes of the family's other files; otherwise the root is never suggested.
    """

    family: str
    routes: tuple[str, ...]
    at_repository_root: tuple[str, ...] = ()
    label: Literal["mechanical"] = MECHANICAL

    def to_document(self) -> dict[str, object]:
        return {
            "family": self.family,
            "label": self.label,
            "routes": list(self.routes),
            "atRepositoryRoot": list(self.at_repository_root),
        }


def _without_descendants(directories: set[str]) -> set[str]:
    return {
        directory
        for directory in directories
        if not any(route_covers(other, directory) for other in directories if other != directory)
    }


def _only_family_code(
    parent: str, family_files: frozenset[str], code_files: frozenset[str]
) -> bool:
    under = [path for path in code_files if route_covers(parent, path)]
    return bool(under) and all(path in family_files for path in under)


def _collapse_once(
    directories: set[str], family_files: frozenset[str], code_files: frozenset[str]
) -> set[str] | None:
    siblings: dict[str, list[str]] = defaultdict(list)
    for directory in directories:
        parent = dirname(directory)
        if parent:  # the repository root is never a suggested route
            siblings[parent].append(directory)
    for parent in sorted(siblings, key=lambda item: (-item.count("/"), item)):
        if len(siblings[parent]) > 1 and _only_family_code(parent, family_files, code_files):
            return _without_descendants({*directories, parent})
    return None


def suggest_routes(
    family: str, realization_paths: Iterable[str], code_files: Iterable[str]
) -> RouteSuggestion:
    """Suggest routes for ``family`` from the files that hold its members' realization entries.

    Start from the directories containing those files (a directory under another one is covered by
    it). Then collapse each group of sibling directories to their parent, only when every file of the
    code tree under that parent holds family code, and repeat until nothing collapses. The deepest
    parents collapse first; collapsing never reaches the repository root. The root route ``.`` is
    added only when a realization file lies directly at the root, where nothing narrower covers it.
    """

    paths = frozenset(realization_paths)
    code = frozenset(code_files)
    directories = _without_descendants({dirname(path) for path in paths if dirname(path)})
    while (collapsed := _collapse_once(directories, paths, code)) is not None:
        directories = collapsed
    at_root = tuple(sorted(path for path in paths if not dirname(path)))
    if at_root:
        directories.add(ROOT_ROUTE_PATH)
    return RouteSuggestion(
        family=family, routes=tuple(sorted(directories)), at_repository_root=at_root
    )


def suggest_family_routes(
    family: FamilyRecord,
    locations: Mapping[str, tuple[RealizationLocation, ...]],
    code_files: Iterable[str],
) -> RouteSuggestion:
    """:func:`suggest_routes` for a family record over a tree's realization entries."""

    state = family_route_state(family, locations)
    return suggest_routes(family.id, (location.path for location in state.locations), code_files)
