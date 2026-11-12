"""The census integrity checks the validator registers (MIK-R20 rule 1, MIK-R22 rule 9).

Each check reads the candidate tree's census files and, where it compares, the comparison bases
(K_B at a commit route, every parent at a merge). The rules:

* ``R20.1-census-shape`` / ``R20.1-census-canonical``: every census file sits at a census location,
  parses under its ``ar-census-*/v1`` schema, names its census and route consistently, and is
  canonically formatted; every census has its baseline and inventory.
* ``R20.1-census-pinned``: ``baseline.json`` and ``inventory.json`` are never changed or deleted
  once committed: the census is pinned to its baseline.
* ``R20.2-claim-rows``: a claim's location names an inventoried onboarding artifact, governed by the
  route whose file holds the claim, and a claim ID is defined once per census.
* ``R20.2-claim-stable``: a recorded claim's ``id``, ``text``, ``location``, ``kind`` and
  ``applicability`` never change (architect ruling, L20 review R1): only its assessments are
  appended, and its disposition and records set.
* ``R20.2-claim-records``: every record an admitted (or demoted) claim links to exists.
* ``R20.3-route-known``: a claims or status file's route governs at least one inventory row.
* ``R20.3-status-append-only`` (rule 3) and ``R20.6-assessment-append-only`` (rule 6): a status
  history and a claim's assessments only grow. With one base, the base's list is a prefix of the
  candidate's; at a merge, each parent's list is a subsequence of the candidate's. A recorded claim
  or status file is never removed.

The checks judge no meaning: whether an assessment is right is the curator's.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from agents_remember.memory_quality.knowledge_census.files import (
    CENSUS_ROOT,
    CensusTree,
    ParsedCensus,
    census_id_of,
    read_censuses,
)
from agents_remember.models.knowledge_files.census import (
    BASELINE_FILENAME,
    INVENTORY_FILENAME,
    CensusClaim,
    CensusRoute,
    InventoryRow,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    split_record_filename,
)

CENSUS_RULES: Final[Mapping[str, tuple[str, str]]] = {
    "R20.1-census-shape": ("MIK-R20 rule 1", "census files match their schema and location"),
    "R20.1-census-canonical": ("MIK-R20 rule 1", "census JSON is canonically formatted"),
    "R20.1-census-pinned": ("MIK-R20 rule 1", "a census baseline and inventory never change"),
    "R20.2-claim-rows": ("MIK-R20 rule 2", "claims name inventoried artifacts of their route"),
    "R20.2-claim-stable": (
        "MIK-R20 rules 2 and 6",
        "a recorded claim's text, location, kind and applicability never change",
    ),
    "R20.2-claim-records": ("MIK-R20 rule 2", "a claim's disposition links to existing records"),
    "R20.3-route-known": ("MIK-R20 rule 3", "census routes govern inventoried rows"),
    "R20.3-status-append-only": ("MIK-R20 rule 3", "route status histories are append-only"),
    "R20.6-assessment-append-only": ("MIK-R20 rule 6", "claim assessments are append-only"),
}


# The fields of a recorded claim that never change: changing ``applicability`` would silently move a
# claim out of the cohort N, and ``text`` and ``location`` are the original observation. Only
# ``assessments`` (append-only), ``disposition`` and ``records`` change after recording.
STABLE_CLAIM_FIELDS: Final = ("id", "text", "location", "kind", "applicability")


@dataclass(frozen=True, order=True)
class CensusFinding:
    rule: str
    path: str
    field: str
    message: str


def record_ids_in(files: Collection[str]) -> frozenset[str]:
    """The record IDs named by the record filenames among ``files``."""

    directories = {f"{KNOWLEDGE_ROOT}/{directory}/" for directory in RECORD_DIRECTORIES.values()}
    found: set[str] = set()
    for path in files:
        directory, _, filename = path.rpartition("/")
        if f"{directory}/" not in directories:
            continue
        try:
            found.add(split_record_filename(filename)[0])
        except ValueError:
            continue
    return frozenset(found)


def _is_subsequence[T](base: Sequence[T], candidate: Sequence[T]) -> bool:
    remaining = iter(candidate)
    return all(any(item == other for other in remaining) for item in base)


def _preserved[T](base: Sequence[T], candidate: Sequence[T], *, merge: bool) -> bool:
    if merge:
        return _is_subsequence(base, candidate)
    return tuple(candidate[: len(base)]) == tuple(base)


def _problem_findings(tree: CensusTree) -> Iterator[CensusFinding]:
    for problem in tree.problems:
        rule = "R20.1-census-canonical" if problem.category == "canonical" else "R20.1-census-shape"
        yield CensusFinding(rule, problem.path, problem.field, problem.message)


def _is_pinned_path(path: str) -> bool:
    """Exactly ``knowledge/census/<id>/baseline.json`` or ``…/inventory.json``, nothing deeper."""

    census_id = census_id_of(path)
    if census_id is None:
        return False
    return path.removeprefix(f"{CENSUS_ROOT}{census_id}/") in {
        BASELINE_FILENAME,
        INVENTORY_FILENAME,
    }


def _pinned_findings(
    candidate: Mapping[str, bytes], bases: Sequence[Mapping[str, bytes]]
) -> Iterator[CensusFinding]:
    pinned = {path for base in bases for path in base if _is_pinned_path(path)}
    for path in sorted(pinned):
        if any(path in base and base[path] != candidate.get(path) for base in bases):
            change = "deleted" if path not in candidate else "changed"
            yield CensusFinding(
                "R20.1-census-pinned",
                path,
                "",
                f"this file is pinned to its census baseline and was {change}; "
                "a new baseline is a new census",
            )


def _claim_findings(census: ParsedCensus, record_ids: Collection[str]) -> Iterator[CensusFinding]:
    inventory = census.inventory
    artifacts = {} if inventory is None else {row.path: row for row in inventory.artifacts}
    routes = frozenset(() if inventory is None else inventory.routes)
    holders: dict[str, str] = {}
    for path, claims in sorted(census.claims.items()):
        if inventory is not None and claims.route not in routes:
            yield CensusFinding(
                "R20.3-route-known",
                path,
                "route",
                f"route {claims.route!r} governs no row of census {census.census_id}'s inventory",
            )
        for index, claim in enumerate(claims.claims):
            field = f"claims.{index}"
            if claim.id in holders:
                yield CensusFinding(
                    "R20.2-claim-rows",
                    path,
                    f"{field}.id",
                    f"claim {claim.id} is already defined in {holders[claim.id]}",
                )
            holders.setdefault(claim.id, path)
            if inventory is not None:
                yield from _claim_row_findings(path, field, claim, claims.route, artifacts)
            for record in claim.records or ():
                if record not in record_ids:
                    yield CensusFinding(
                        "R20.2-claim-records",
                        path,
                        f"{field}.records",
                        f"claim {claim.id} links to {record}, which does not exist",
                    )


def _claim_row_findings(
    path: str, field: str, claim: CensusClaim, route: str, artifacts: Mapping[str, InventoryRow]
) -> Iterator[CensusFinding]:
    row = artifacts.get(claim.location.artifact)
    if row is None:
        yield CensusFinding(
            "R20.2-claim-rows",
            path,
            f"{field}.location.artifact",
            f"claim {claim.id} names {claim.location.artifact}, which is no inventory row",
        )
    elif row.route != route:
        governing = "no route" if row.route is None else f"route {row.route!r}"
        yield CensusFinding(
            "R20.2-claim-rows",
            path,
            f"{field}.location.artifact",
            f"claim {claim.id}'s artifact is governed by {governing}, not {route!r}",
        )


def _route_findings(census: ParsedCensus) -> Iterator[CensusFinding]:
    if census.inventory is None:
        return
    for path, history in sorted(census.routes.items()):
        if history.route not in census.inventory.routes:
            yield CensusFinding(
                "R20.3-route-known",
                path,
                "route",
                f"route {history.route!r} governs no row of census {census.census_id}'s inventory",
            )


def _claims_by_id(tree: CensusTree) -> dict[tuple[str, str], tuple[str, CensusClaim]]:
    return {
        (census.census_id, claim.id): (path, claim)
        for census in tree.censuses.values()
        for path, claims in census.claims.items()
        for claim in claims.claims
    }


def _claim_append_findings(
    base: CensusTree,
    candidate_claims: dict[tuple[str, str], tuple[str, CensusClaim]],
    *,
    merge: bool,
) -> Iterator[CensusFinding]:
    for (census_id, claim_id), (path, claim) in sorted(_claims_by_id(base).items()):
        found = candidate_claims.get((census_id, claim_id))
        if found is None:
            yield CensusFinding(
                "R20.6-assessment-append-only",
                path,
                "claims",
                f"recorded claim {claim_id} was removed; a claim and its assessments stay",
            )
            continue
        changed = [
            name for name in STABLE_CLAIM_FIELDS if getattr(claim, name) != getattr(found[1], name)
        ]
        if changed:
            yield CensusFinding(
                "R20.2-claim-stable",
                found[0],
                ",".join(changed),
                f"recorded claim {claim_id}'s {', '.join(changed)} changed; a recorded claim's "
                "identity, text, location, kind and applicability are stable (record a new claim)",
            )
        if not _preserved(claim.assessments, found[1].assessments, merge=merge):
            yield CensusFinding(
                "R20.6-assessment-append-only",
                found[0],
                "assessments",
                f"claim {claim_id}'s recorded assessments were edited or removed; "
                "a correction appends a new assessment",
            )


def _status_append_findings(
    base: CensusTree, candidate_routes: dict[str, CensusRoute], *, merge: bool
) -> Iterator[CensusFinding]:
    for census in base.censuses.values():
        for path, history in sorted(census.routes.items()):
            current = candidate_routes.get(path)
            if current is None:
                message = "a recorded status history was removed"
            elif _preserved(history.statuses, current.statuses, merge=merge):
                continue
            else:
                message = "recorded status entries were edited or removed; append a new entry"
            yield CensusFinding("R20.3-status-append-only", path, "statuses", message)


def _append_only_findings(tree: CensusTree, bases: Sequence[CensusTree]) -> Iterator[CensusFinding]:
    merge = len(bases) > 1
    candidate_claims = _claims_by_id(tree)
    candidate_routes = {
        path: history
        for census in tree.censuses.values()
        for path, history in census.routes.items()
    }
    for base in bases:
        yield from _claim_append_findings(base, candidate_claims, merge=merge)
        yield from _status_append_findings(base, candidate_routes, merge=merge)


def check_censuses(
    candidate: Mapping[str, bytes],
    *,
    bases: Sequence[Mapping[str, bytes]] = (),
    record_ids: Collection[str] | None = None,
) -> list[CensusFinding]:
    """Return every census finding of ``candidate`` against its comparison ``bases``.

    ``record_ids`` are the record IDs that exist in the candidate; by default they are read from
    its record filenames.
    """

    tree = read_censuses(candidate)
    known = record_ids_in(candidate) if record_ids is None else record_ids
    base_trees = [read_censuses(base) for base in bases]
    findings = [
        *_problem_findings(tree),
        *_pinned_findings(candidate, bases),
        *(
            finding
            for census in tree.censuses.values()
            for finding in _claim_findings(census, known)
        ),
        *(finding for census in tree.censuses.values() for finding in _route_findings(census)),
        *_append_only_findings(tree, base_trees),
    ]
    return sorted(set(findings))
