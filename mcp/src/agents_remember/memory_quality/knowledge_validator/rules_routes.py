"""MIK-R04's rules in the validator's registry (MIK-R22 rule 9): family routes.

* **Rule 1, routes are directories.** A route a commit adds to a family must be a directory of the
  paired code tree. A route the family already listed in a comparison base (K_B, or a merge parent)
  is carried: when its directory is absent it is refused at every commit and landing route and
  reported only at a sync merge for the route-maintenance pass (MIK-R06 ``route_path_absent``,
  MIK-R53 ``merge_reports``) -- the same split MIK-R22 rule 6 makes for anchors. A standalone
  conversion has no code tree and checks no route.
* **Rule 2, Coverage and Non-empty**, refused, over the realization entries of the tree.
* **Rule 4, the states with a named reason to report** (MIK-R53 rules 10-12):
  ``unrealized_family`` and ``carried-route-absent`` refuse at every commit and landing route and
  report only at a sync merge (``merge_reports``); ``route_unassigned`` is conversion-written
  migration debt (``migration_debt``). A retired family is exempt from every route rule.

Every violation names the family, the route and, for Coverage, the uncovered path. The refusing
rules carry ``writer_reports``: inside the writer they are reported while every commit route
refuses them, so a leaf may break a route rule mid-way and repair it before closeout (rule 6).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from agents_remember.memory_quality.knowledge_validator.family_routes import (
    FamilyRouteState,
    family_route_state,
    realization_locations,
)
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    debt_report_note,
    debt_reports_at,
    merge_report_note,
    merge_reports_at,
    register_rule,
)
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, RECORD_DIRECTORIES
from agents_remember.models.knowledge_files.records import FamilyRecord
from agents_remember.models.knowledge_files.shapes import LEGACY_UNASSESSED

_FAMILY_DIRECTORY = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['family']}/"


@dataclass(frozen=True)
class _Family:
    path: str
    state: FamilyRouteState

    @property
    def id(self) -> str:
        return self.state.family.id


def _families(context: ValidationContext) -> Iterator[_Family]:
    locations = realization_locations(context.parsed.sidecars)
    for record in context.parsed.records:
        if isinstance(record.record, FamilyRecord):
            yield _Family(record.path, family_route_state(record.record, locations))


def _base_families(bases: Iterable[KnowledgeTree]) -> Iterator[FamilyRecord]:
    """Every family record a comparison base holds that parses."""

    for base in bases:
        for path, data in base.files.items():
            if not (path.startswith(_FAMILY_DIRECTORY) and path.endswith(".json")):
                continue
            try:
                yield FamilyRecord.model_validate(parse_json(data.decode("utf-8")))
            except ValueError:  # UnicodeDecodeError, a JSON error and ValidationError are all one
                continue


def _base_family_routes(bases: Iterable[KnowledgeTree]) -> frozenset[tuple[str, str]]:
    """``(family ID, route)`` for every route a family lists in any base that parses."""

    return frozenset(
        (family.id, route) for family in _base_families(bases) for route in family.routes
    )


def _base_routeless_family_ids(bases: Iterable[KnowledgeTree]) -> frozenset[str]:
    """The IDs of the families a comparison base holds as a live unassigned finding (rule 10).

    Carried debt is the actual base finding: a family the base holds with routes, assessed, or
    retired does not carry the candidate's unassigned state, so a newly live unassigned family
    refuses at once (review round F10).
    """

    return frozenset(
        family.id
        for family in _base_families(bases)
        if not family.routes
        and family.admission == LEGACY_UNASSESSED
        and family.status != "retired"
    )


@dataclass(frozen=True)
class _AbsentRoute:
    family: _Family
    index: int
    route: str
    carried: bool
    code: str


def _absent_routes(context: ValidationContext) -> Iterator[_AbsentRoute]:
    code = context.code
    if context.conversion or code is None:
        return
    carried = _base_family_routes(context.bases)
    for family in _families(context):
        if family.state.retired:
            continue
        for index, route in enumerate(family.state.family.routes):
            if not code.has_directory(route):
                yield _AbsentRoute(family, index, route, (family.id, route) in carried, code.label)


def check_route_directories(context: ValidationContext) -> Iterator[Finding]:
    for absent in _absent_routes(context):
        if not absent.carried:
            yield Finding(
                absent.family.path,
                f"routes.{absent.index}",
                f"family {absent.family.id}: route {absent.route} is not a directory of the "
                f"paired code tree {absent.code}",
            )


def check_carried_route_absent(context: ValidationContext) -> Iterator[Finding]:
    reporting = merge_reports_at(context)
    for absent in _absent_routes(context):
        if absent.carried:
            message = (
                f"family {absent.family.id}: carried route {absent.route} is absent from "
                f"{absent.code}: route_path_absent, for the "
                "route-maintenance pass (MIK-R06)"
            )
            if reporting:
                message += f"; {merge_report_note(context)}"
            yield Finding(
                absent.family.path,
                f"routes.{absent.index}",
                message,
            )


def check_coverage(context: ValidationContext) -> Iterator[Finding]:
    for family in _families(context):
        state = family.state
        if state.routeless:
            uncovered = ", ".join(location.path for location in state.locations) or "none yet"
            yield Finding(
                family.path,
                "routes",
                f"family {family.id} lists no route and is not legacy-unassessed: Coverage "
                f"needs at least one route (realization paths: {uncovered})",
            )
            continue
        for location in state.uncovered:
            yield Finding(
                family.path,
                "routes",
                f"family {family.id}: realization {location.entry} ({location.invariant}) at "
                f"{location.path} lies under no route of {list(state.family.routes)}",
            )


def check_non_empty(context: ValidationContext) -> Iterator[Finding]:
    for family in _families(context):
        routes = family.state.family.routes
        for route in family.state.emptied:
            yield Finding(
                family.path,
                f"routes.{routes.index(route)}",
                f"family {family.id}: route {route} contains none of its members' realization "
                "entries",
            )


def check_unrealized_family(context: ValidationContext) -> Iterator[Finding]:
    reporting = merge_reports_at(context)
    for family in _families(context):
        if family.state.unrealized and not family.state.retired:
            message = (
                f"family {family.id} is an unrealized_family: no member has a realization entry "
                "yet, so Non-empty is waived"
            )
            if reporting:
                message += f"; {merge_report_note(context)}"
            yield Finding(
                family.path,
                "members",
                message,
            )


def check_route_unassigned(context: ValidationContext) -> Iterator[Finding]:
    carried_ids = _base_routeless_family_ids(context.bases)
    migration = context.migration
    for family in _families(context):
        if family.state.unassigned and not family.state.retired:
            carried = family.id in carried_ids
            message = (
                f"family {family.id} is route_unassigned: an exported family with no routes; "
                "Coverage is waived until a leaf or the migration assigns them (MIK-R06, MIK-R19)"
            )
            if debt_reports_at(context, carried=carried):
                message += f"; {debt_report_note(migration)}"
            yield Finding(
                family.path,
                "routes",
                message,
                carried=carried,
            )


ROUTE_RULES = (
    ValidationRule(
        "R04.1-route-directory",
        "MIK-R04 rule 1",
        "an added family route is a directory of the code tree",
        check_route_directories,
        writer_reports=True,
    ),
    ValidationRule(
        "R04.1-carried-route-absent",
        "MIK-R04 rule 1 (MIK-R06)",
        "a carried family route whose directory is absent refuses, merge_reports",
        check_carried_route_absent,
        writer_reports=True,
        merge_reports=True,
    ),
    ValidationRule(
        "R04.2-coverage",
        "MIK-R04 rule 2",
        "every realization of a family lies under one of its routes",
        check_coverage,
        writer_reports=True,
    ),
    ValidationRule(
        "R04.2-non-empty",
        "MIK-R04 rule 2",
        "every family route contains a realization",
        check_non_empty,
        writer_reports=True,
    ),
    ValidationRule(
        "R04.4-unrealized-family",
        "MIK-R04 rule 4",
        "a family with no realization yet refuses, merge_reports",
        check_unrealized_family,
        writer_reports=True,
        merge_reports=True,
    ),
    ValidationRule(
        "R04.4-route-unassigned",
        "MIK-R04 rule 4",
        "an exported family with no routes is migration debt",
        check_route_unassigned,
        migration_debt=True,
    ),
)

for _rule in ROUTE_RULES:
    register_rule(_rule)
