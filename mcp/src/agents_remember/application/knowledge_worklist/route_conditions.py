"""MIK-R06's ``family_route_condition`` item kind: family routes maintained with the code.

**Conditions (rule 1).** Recomputed with every worklist run, over K_C's realization entries and the
code tree C. Every family the worklist reaches (a ``reached_family`` item) is evaluated on all four
conditions. Any other family is evaluated only for ``route_path_absent``, on a route this leaf's
range killed -- its directory existed at B and is absent at C -- so a carried dead route the
validator only reports (``R04.1-carried-route-absent``, L04 ruling 1) never survives the closeout of
the leaf that killed it; a route already dead at B stays the validator's report (ruling Q1):

* ``route_path_absent`` -- a route's directory is absent at C;
* ``route_emptied`` -- a route contains none of the family's realization entries (waived for an
  ``unrealized_family``, MIK-R04 rule 4);
* ``realization_uncovered`` -- a realization entry lies outside every route;
* ``route_unassigned`` -- the family has no routes (including an exported ``route_unassigned``).

A family whose record is ``retired`` in K_C raises nothing.

**Two views of the route set.** A condition is judged on the family's routes as K_B recorded them
(with K_C's members) and as K_C records them, and holds when it holds in either view. The base view
keeps an item raised after the curator has edited the routes, so the leaf still needs its row
(rule 3: "the leaf cannot close until its history file holds a family row"); the candidate view
catches a route set the leaf itself broke. The item's identity is its base-view affected set when
the base view shows the condition, and the candidate view's otherwise.

**Registration (rule 2).** One item per family and condition, subject ``<FAM-ID>#<condition>``.
Its facts: the affected routes or entry locations per view, the routes of both records, the family's
realization entries in K_C, the rename candidates, the MIK-R04 mechanical suggestion, and whether the
K_C record satisfies MIK-R04 (``recordSatisfiesRoutes``). When the renamed family files under an
affected route went to more than one directory, the rename target is ambiguous: the item lists every
candidate location and gives no suggestion (Failure And Recovery).

**Satisfying row (rule 3).** The leaf's family row about the family (the one row with the family ID
as its subject, MIK-R07 rule 5) satisfies the item when its disposition is ``rerouted``,
``assigned``, ``changed`` or ``retired`` -- never ``no_impact`` -- and the K_C record satisfies
MIK-R04, including its rule 4 waivers, or is retired. ``route_unassigned`` needs a non-empty route
set as well: the ``legacy-unassessed`` waiver alone does not answer it (ruling Q3).

**Locations at C (review N1).** Every condition, and the suggestion, is judged over the entries'
locations at C: an entry whose file is absent at C counts where L08's rename inventory moved it
(ruling Q5), so the conditions show before the curator moves the entries and their IDs do not change
when the move is recorded. ``recordSatisfiesRoutes`` holds when the K_C record satisfies MIK-R04
both as recorded (the validator's reading) and over those locations at C.

**Suggestion (ruling Q5).** Without a rename
for such a file, or with an ambiguous rename target, there is no suggestion and the item lists every
candidate directory (``renameCandidates``) and every unrenamed absent location
(``unmappedLocations``). :func:`family_route_item_open` applies both
halves to a stored item; the live run applies the same function, so ``satisfiedBy`` and the stored
predicate agree. Routes are never rewritten automatically (Exclusions).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from posixpath import dirname
from typing import Any, Final

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    register_item_kind,
    subject_row,
)
from agents_remember.memory_quality.knowledge_validator.family_routes import (
    FamilyRouteState,
    RealizationLocation,
    family_route_state,
    route_covers,
    suggest_routes,
)
from agents_remember.memory_quality.knowledge_validator.trees import CodePathSet
from agents_remember.models.knowledge_files.history import FamilyRow, HistoryFile, HistoryRow
from agents_remember.models.knowledge_files.records import FamilyRecord
from agents_remember.models.knowledge_files.sidecars import RealizationEntry

__all__ = [
    "CONDITIONS",
    "FAMILY_ROUTE_CONDITION_KIND",
    "ITEM_KIND",
    "SATISFYING_DISPOSITIONS",
    "RouteCondition",
    "RouteInputs",
    "family_of",
    "family_route_conditions",
    "family_route_item_open",
    "record_satisfies_routes",
]

ITEM_KIND: Final = "family_route_condition"
ROUTE_PATH_ABSENT: Final = "route_path_absent"
ROUTE_EMPTIED: Final = "route_emptied"
REALIZATION_UNCOVERED: Final = "realization_uncovered"
ROUTE_UNASSIGNED: Final = "route_unassigned"
CONDITIONS: Final = (ROUTE_PATH_ABSENT, ROUTE_EMPTIED, REALIZATION_UNCOVERED, ROUTE_UNASSIGNED)
SATISFYING_DISPOSITIONS: Final = ("rerouted", "assigned", "changed", "retired")
_VIEWS: Final = ("base", "candidate")


def family_of(subject: str) -> str:
    """The family ID of an item subject ``<FAM-ID>#<condition>``."""

    return subject.split("#", 1)[0]


_family_lookup = subject_row("family")


def _family_row(subject: str, history: HistoryFile | None) -> HistoryRow | None:
    return _family_lookup(family_of(subject), history)


FAMILY_ROUTE_CONDITION_KIND: Final = register_item_kind(
    ItemKind(
        name=ITEM_KIND,
        subject="family ID and route condition (<FAM-ID>#<condition>)",
        subject_pattern=rf"^FAM-[^#\s]+#(?:{'|'.join(CONDITIONS)})$",
        facts=(
            "family",
            "condition",
            "routes",
            "affected",
            "locations",
            "renameCandidates",
            "unmappedLocations",
            "suggestion",
            "recordSatisfiesRoutes",
        ),
        satisfying_row=(
            "the leaf's family row about the family whose disposition is rerouted, assigned, "
            "changed or retired (never no_impact), while the family record in K_C satisfies "
            "MIK-R04 including its rule 4 waivers or is retired (MIK-R06 rule 3); for "
            "route_unassigned the record's route set is non-empty (ruling Q3)"
        ),
        row_lookup=_family_row,
        owner="MIK-R06",
    )
)


def family_route_item_open(item: Mapping[str, Any], history: HistoryFile | None) -> bool:
    """The kind's satisfying rule over an item document, for the generic gate (MIK-R09).

    Open unless the leaf's family row about the item's family has a satisfying disposition (never
    ``no_impact``) and the item's facts record that the K_C family record satisfies MIK-R04.
    """

    facts = item.get("facts")
    subject = item.get("subject")
    if not isinstance(facts, Mapping) or not isinstance(subject, str):
        return True
    row = _family_row(subject, history)
    answered = isinstance(row, FamilyRow) and row.disposition in SATISFYING_DISPOSITIONS
    return not (answered and facts.get("recordSatisfiesRoutes") is True)


def record_satisfies_routes(
    record: FamilyRecord | None,
    locations: Mapping[str, tuple[RealizationLocation, ...]],
    code: CodePathSet,
    *,
    require_routes: bool = False,
) -> bool:
    """Whether the K_C record satisfies MIK-R04 (rule 4 waivers included), or is retired.

    ``require_routes`` is ``route_unassigned``'s stricter half (ruling Q3): the leaf assigns routes,
    so the ``legacy-unassessed`` waiver alone does not satisfy it -- the route set must be
    non-empty.
    """

    if record is None:
        return False
    state = family_route_state(record, locations)
    if state.retired:
        return True
    if require_routes and not record.routes:
        return False
    if any(not code.has_directory(route) for route in record.routes):
        return False
    return not (state.routeless or state.uncovered or state.emptied)


@dataclass(frozen=True)
class RouteCondition:
    """One raised condition of one family: its subject, facts, identity and satisfying row."""

    family: str
    condition: str
    facts: Mapping[str, Any]
    identities: Any
    satisfied_by: str | None

    @property
    def subject(self) -> str:
        return f"{self.family}#{self.condition}"


def _location_document(location: RealizationLocation) -> dict[str, str]:
    return {"entry": location.entry, "invariant": location.invariant, "path": location.path}


def _affected(state: FamilyRouteState, code: CodePathSet) -> dict[str, list[Any] | None]:
    """What each condition names in one view, or ``None`` where it does not hold."""

    if state.retired:
        return dict.fromkeys(CONDITIONS)
    routes = state.family.routes
    absent = [route for route in routes if not code.has_directory(route)]
    uncovered = [_location_document(location) for location in state.uncovered]
    return {
        ROUTE_PATH_ABSENT: absent or None,
        ROUTE_EMPTIED: list(state.emptied) or None,
        REALIZATION_UNCOVERED: uncovered or None,
        ROUTE_UNASSIGNED: [] if not routes else None,
    }


def _candidate_locations(candidate: KnowledgeSide) -> dict[str, tuple[RealizationLocation, ...]]:
    found: dict[str, list[RealizationLocation]] = {}
    for entry_id, indexed in sorted(candidate.entries.items()):
        if isinstance(indexed.entry, RealizationEntry):
            found.setdefault(indexed.entry.invariant, []).append(
                RealizationLocation(
                    entry_id, indexed.entry.invariant, indexed.path, indexed.sidecar
                )
            )
    return {invariant: tuple(locations) for invariant, locations in found.items()}


def _outermost(directories: Iterable[str]) -> list[str]:
    found = set(directories)
    return sorted(
        one for one in found if not any(route_covers(other, one) for other in found if other != one)
    )


@dataclass(frozen=True)
class _Family:
    """One family under evaluation: its records on both sides and its views."""

    id: str
    base: FamilyRecord | None
    candidate: FamilyRecord | None

    @property
    def current(self) -> FamilyRecord:
        record = self.candidate or self.base
        assert record is not None
        return record

    def view(self, side: str) -> FamilyRecord | None:
        if side == "candidate":
            return self.candidate
        if self.base is None:
            return None
        # K_B's route set, judged over the current family's members (K_C's record when present).
        return self.current.model_copy(
            update={"routes": self.base.routes, "admission": self.base.admission}
        )


Locations = Mapping[str, tuple[RealizationLocation, ...]]


@dataclass(frozen=True)
class _Judged:
    """One family's facts shared by all its conditions."""

    family: _Family
    views: Mapping[str, Mapping[str, list[Any] | None]]
    state: FamilyRouteState
    recorded: Locations
    """K_C's entries where K_C records them (MIK-R04's own reading)."""
    effective: Locations
    """The same entries where their files lie at C (:meth:`_Evaluation._effective`)."""

    @property
    def routes(self) -> dict[str, list[str] | None]:
        return {
            side: None if record is None else list(record.routes)
            for side, record in (("base", self.family.base), ("candidate", self.family.candidate))
        }

    def affected(self, condition: str) -> dict[str, list[Any] | None]:
        return {
            side: self.views[side][condition] if side in self.views else None for side in _VIEWS
        }


@dataclass(frozen=True)
class _Evaluation:
    base: KnowledgeSide
    candidate: KnowledgeSide
    code: CodePathSet
    base_code: CodePathSet
    renamed: Mapping[str, str]
    history: HistoryFile | None

    def conditions(self, reached: Iterable[str]) -> list[RouteCondition]:
        """Reached families on all four conditions; any other family only on a killed route."""

        recorded = _candidate_locations(self.candidate)
        effective = self._effective(recorded)
        reached_set = set(reached)
        raised: list[RouteCondition] = []
        for family_id in sorted(reached_set | self._with_killed_routes()):
            family = _Family(
                family_id, self.base.families.get(family_id), self.candidate.families.get(family_id)
            )
            if family.current.status == "retired":
                continue  # a retired family raises nothing (rule 1)
            judged = _Judged(
                family=family,
                views=self._views(family, effective, reached=family_id in reached_set),
                state=family_route_state(family.current, effective),
                recorded=recorded,
                effective=effective,
            )
            raised.extend(self._family_conditions(judged))
        return raised

    def _family_conditions(self, judged: _Judged) -> list[RouteCondition]:
        found: list[RouteCondition] = []
        for condition in CONDITIONS:
            affected = judged.affected(condition)
            if affected["base"] is not None or affected["candidate"] is not None:
                found.append(self._condition(judged, condition, affected))
        return found

    def _effective(self, recorded: Locations) -> dict[str, tuple[RealizationLocation, ...]]:
        """Each entry where it lies at C (review N1): a file absent at C counts at its rename.

        An entry K_C still records at a path the leaf renamed is judged where the file went (the
        Q5 mapping), so the route conditions show on the first worklist, before the curator moves
        the entries, and do not change when the move is recorded. An absent file without a rename
        keeps its recorded path.
        """

        def at_c(location: RealizationLocation) -> RealizationLocation:
            moved = self.renamed.get(location.path)
            if moved is None or self.code.has_file(location.path):
                return location
            return RealizationLocation(location.entry, location.invariant, moved, location.sidecar)

        return {
            invariant: tuple(at_c(one) for one in locations)
            for invariant, locations in recorded.items()
        }

    def _killed(self, route: str) -> bool:
        """A route this leaf's range killed: its directory existed at B and is absent at C."""

        return self.base_code.has_directory(route) and not self.code.has_directory(route)

    def _with_killed_routes(self) -> set[str]:
        """Families, on either side, with a route this leaf killed (L04 ruling 1, ruling Q1).

        A route already absent at B is not this leaf's: it stays the validator's report
        (``R04.1-carried-route-absent``) for the migration.
        """

        return {
            family_id
            for side in (self.base, self.candidate)
            for family_id, record in side.families.items()
            if any(self._killed(route) for route in record.routes)
        }

    def _views(
        self, family: _Family, locations: Locations, *, reached: bool
    ) -> dict[str, dict[str, list[Any] | None]]:
        views: dict[str, dict[str, list[Any] | None]] = {}
        for side in _VIEWS:
            record = family.view(side)
            if record is None:
                continue
            affected = _affected(family_route_state(record, locations), self.code)
            if not reached:  # an unreached family answers only for the routes this leaf killed
                killed = [one for one in affected[ROUTE_PATH_ABSENT] or () if self._killed(one)]
                affected = {**dict.fromkeys(CONDITIONS), ROUTE_PATH_ABSENT: killed or None}
            views[side] = affected
        return views

    def _condition(
        self, judged: _Judged, condition: str, affected: dict[str, list[Any] | None]
    ) -> RouteCondition:
        family = judged.family
        candidates = self._rename_candidates(family, condition, affected)
        suggestion, unmapped = self._suggestion(family, judged.state.locations, candidates)
        facts = {
            "family": family.id,
            "condition": condition,
            "routes": judged.routes,
            "affected": affected,
            "locations": [_location_document(one) for one in judged.state.locations],
            "renameCandidates": candidates,
            "unmappedLocations": unmapped,
            "suggestion": suggestion,
            "recordSatisfiesRoutes": self._record_satisfies(judged, condition),
        }
        view = "base" if affected["base"] is not None else "candidate"
        subject = f"{family.id}#{condition}"
        row = _family_row(subject, self.history)
        open_ = family_route_item_open({"subject": subject, "facts": facts}, self.history)
        return RouteCondition(
            family=family.id,
            condition=condition,
            facts=facts,
            identities={"view": view, "affected": affected[view]},
            satisfied_by=None if open_ or row is None else row.id,
        )

    def _record_satisfies(self, judged: _Judged, condition: str) -> bool:
        """The K_C record satisfies MIK-R04 as recorded and where its entries lie at C.

        As recorded is the validator's reading; at C (review N1) keeps a record whose entries still
        name renamed files from counting as repaired before the curator moves them.
        """

        return all(
            record_satisfies_routes(
                judged.family.candidate,
                locations,
                self.code,
                require_routes=condition == ROUTE_UNASSIGNED,
            )
            for locations in (judged.recorded, judged.effective)
        )

    def _suggestion(
        self, family: _Family, locations: tuple[RealizationLocation, ...], candidates: list[str]
    ) -> tuple[dict[str, object] | None, list[str]]:
        """The MIK-R04 mechanical suggestion, over the entries' locations at C (ruling Q5).

        ``locations`` are already where each file lies at C (:meth:`_effective`). An entry whose
        file is absent at C without a rename, or an ambiguous rename target (Q4), gives no
        suggestion; the absent unrenamed locations are listed instead.
        """

        paths = [location.path for location in locations]
        unmapped = sorted({path for path in paths if not self.code.has_file(path)})
        if unmapped or len(candidates) > 1:
            return None, unmapped
        return suggest_routes(family.id, paths, self.code.paths).to_document(), []

    def _base_realization_paths(self, family: _Family) -> set[str]:
        members = set(family.current.members) | set(family.base.members if family.base else ())
        entries = self.base.entries
        return {
            entries[entry_id].path
            for member in members
            for entry_id in self.base.entries_by_invariant.get(member, ())
            if isinstance(entries[entry_id].entry, RealizationEntry)
        }

    def _rename_candidates(
        self, family: _Family, condition: str, affected: Mapping[str, list[Any] | None]
    ) -> list[str]:
        """Where the family's renamed files under an affected route went (outermost directories)."""

        if condition not in (ROUTE_PATH_ABSENT, ROUTE_EMPTIED):
            return []
        routes = {route for side in _VIEWS for route in affected[side] or ()}
        return _outermost(
            dirname(self.renamed[path]) or "."
            for path in self._renamed_under(self._base_realization_paths(family), routes)
        )

    def _renamed_under(self, paths: Iterable[str], routes: set[str]) -> list[str]:
        """The renamed ``paths`` that lie under one of ``routes``."""

        return [
            path
            for path in paths
            if path in self.renamed and any(route_covers(route, path) for route in routes)
        ]


@dataclass(frozen=True)
class RouteInputs:
    """What the route conditions read from one worklist run."""

    base: KnowledgeSide
    candidate: KnowledgeSide
    code_paths: Iterable[str]
    """The files of C."""
    base_code_paths: Iterable[str]
    """The files of B."""
    renamed: Mapping[str, str]
    """B path -> C path, from the run's rename inference."""
    owner: str | None


def family_route_conditions(inputs: RouteInputs, reached: Iterable[str]) -> list[RouteCondition]:
    """Every ``family_route_condition`` of one run, sorted by subject."""

    evaluation = _Evaluation(
        base=inputs.base,
        candidate=inputs.candidate,
        code=CodePathSet(label="C", paths=frozenset(inputs.code_paths)),
        base_code=CodePathSet(label="B", paths=frozenset(inputs.base_code_paths)),
        renamed=inputs.renamed,
        history=None if inputs.owner is None else inputs.candidate.history(inputs.owner),
    )
    return sorted(evaluation.conditions(reached), key=lambda one: one.subject)
