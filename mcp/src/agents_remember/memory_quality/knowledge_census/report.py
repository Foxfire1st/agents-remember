"""The census report: measures with counts, slices, dispositions and route status (MIK-R20 rule 5).

:func:`census_reports` builds one :class:`CensusReport` per census of a memory tree. Two censuses
have two baselines and so are two cohorts: their claims are never added together. Route status is
the exception: a route's *governing* status is its latest status entry across every census
(rule 3), so each report shows it next to that census's own status history for the route.

The report is the data behind both the ``agents-remember knowledge-census report`` command and the
reader's census view (MIK-R29 rule 3): :meth:`CensusReport.to_document` is its JSON form and
:meth:`CensusReport.render` its text form.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agents_remember.memory_quality.knowledge_census.files import CensusTree, ParsedCensus
from agents_remember.memory_quality.knowledge_census.measures import (
    Counts,
    Measure,
    compute_measures,
)
from agents_remember.models.knowledge_files.census import (
    DISPOSITIONS,
    ROUTE_STATUSES,
    CensusBaseline,
    CensusClaim,
    CensusInventory,
    CensusRoute,
    GoverningStatus,
    StatusEntry,
    governing_status,
)


@dataclass(frozen=True)
class Slice:
    """The claims of one route or one claim kind, with their counts and measures."""

    key: str
    counts: Counts

    @property
    def measures(self) -> tuple[Measure, ...]:
        return compute_measures(self.counts)

    def render(self) -> str:
        shares = "; ".join(measure.render() for measure in self.measures[:4])
        return f"{self.key}: {self.counts.render()}; {shares}"

    def to_document(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "counts": self.counts.to_document(),
            "measures": [measure.to_document() for measure in self.measures[:4]],
        }


@dataclass(frozen=True)
class RouteLine:
    """One route of the census: its governing status and this census's status history for it."""

    route: str
    governing: GoverningStatus
    history: tuple[StatusEntry, ...]

    def render(self) -> str:
        governing = self.governing
        if governing.entry is None:
            return f"{self.route}: pending (no status recorded)"
        where = "" if governing.census is None else f", census {governing.census}"
        return (
            f"{self.route}: {governing.status} against {governing.entry.tree} "
            f"({governing.entry.provenance.at}{where}; {len(self.history)} entr"
            f"{'y' if len(self.history) == 1 else 'ies'} here)"
        )

    def to_document(self) -> dict[str, Any]:
        entry = self.governing.entry
        return {
            "route": self.route,
            "status": self.governing.status,
            "governingCensus": self.governing.census,
            "governingEntry": None if entry is None else entry.to_document(),
            "history": [item.to_document() for item in self.history],
        }


@dataclass(frozen=True)
class CensusReport:
    census_id: str
    baseline: CensusBaseline | None
    sources: int
    artifacts: int
    unrouted_sources: int
    counts: Counts
    dispositions: dict[str, int]
    by_route: tuple[Slice, ...]
    by_kind: tuple[Slice, ...]
    routes: tuple[RouteLine, ...]

    @property
    def measures(self) -> tuple[Measure, ...]:
        return compute_measures(self.counts)

    @property
    def route_statuses(self) -> dict[str, int]:
        counted = Counter(line.governing.status for line in self.routes)
        return {status: counted[status] for status in ROUTE_STATUSES}

    def render(self) -> str:
        baseline = (
            "baseline missing"
            if self.baseline is None
            else f"code {self.baseline.code.commit}, memory {self.baseline.memory.commit}"
        )
        statuses = self.route_statuses
        lines = [
            f"census {self.census_id}: {baseline}",
            f"inventory: {self.sources} source files ({self.unrouted_sources} with no onboarding "
            f"route), {self.artifacts} onboarding artifacts, {len(self.routes)} routes",
            f"claims: {self.counts.render()}; out of cohort: non_claim {self.counts.non_claim}, "
            f"historical_non_applicable {self.counts.historical_non_applicable}",
            *(f"  {measure.render()}" for measure in self.measures),
            "dispositions: "
            + ", ".join(f"{name} {count}" for name, count in self.dispositions.items()),
            f"routes: {statuses['migrated']} of {len(self.routes)} migrated ("
            + ", ".join(f"{name} {count}" for name, count in statuses.items())
            + ")",
            *(f"  {line.render()}" for line in self.routes),
            "by route:",
            *(f"  {item.render()}" for item in self.by_route),
            "by claim kind:",
            *(f"  {item.render()}" for item in self.by_kind),
        ]
        return "\n".join(lines)

    def to_document(self) -> dict[str, Any]:
        return {
            "census": self.census_id,
            "baseline": None if self.baseline is None else self.baseline.to_document(),
            "inventory": {
                "sources": self.sources,
                "artifacts": self.artifacts,
                "unroutedSources": self.unrouted_sources,
                "routes": len(self.routes),
            },
            "counts": self.counts.to_document(),
            "measures": [measure.to_document() for measure in self.measures],
            "dispositions": dict(self.dispositions),
            "routeStatuses": self.route_statuses,
            "routes": [line.to_document() for line in self.routes],
            "byRoute": [item.to_document() for item in self.by_route],
            "byKind": [item.to_document() for item in self.by_kind],
        }


def _slices(pairs: Iterable[tuple[str, CensusClaim]]) -> tuple[Slice, ...]:
    grouped: dict[str, list[CensusClaim]] = {}
    for key, claim in pairs:
        grouped.setdefault(key, []).append(claim)
    return tuple(Slice(key, Counts.of(grouped[key])) for key in sorted(grouped))


def _census_routes(census: ParsedCensus) -> list[str]:
    routes = set(census.inventory.routes if census.inventory is not None else ())
    routes |= {claims_file.route for claims_file in census.claims.values()}
    routes |= {history.route for history in census.routes.values()}
    return sorted(routes)


def _route_line(route: str, census: ParsedCensus, histories: tuple[CensusRoute, ...]) -> RouteLine:
    own = tuple(
        entry
        for history in census.routes.values()
        if history.route == route
        for entry in history.statuses
    )
    return RouteLine(route=route, governing=governing_status(route, histories), history=own)


def _inventory_counts(inventory: CensusInventory | None) -> tuple[int, int, int]:
    if inventory is None:
        return 0, 0, 0
    unrouted = sum(row.route is None for row in inventory.sources)
    return len(inventory.sources), len(inventory.artifacts), unrouted


def census_report(tree: CensusTree, census: ParsedCensus) -> CensusReport:
    """Build the report of one census; route status is governed across every census of ``tree``."""

    routed_claims = [
        (claims.route, claim) for claims in census.claims.values() for claim in claims.claims
    ]
    claims = [claim for _, claim in routed_claims]
    histories = tuple(tree.route_histories())
    dispositions = Counter(claim.disposition for claim in claims)
    sources, artifacts, unrouted = _inventory_counts(census.inventory)
    return CensusReport(
        census_id=census.census_id,
        baseline=census.baseline,
        sources=sources,
        artifacts=artifacts,
        unrouted_sources=unrouted,
        counts=Counts.of(claims),
        dispositions={name: dispositions[name] for name in DISPOSITIONS},
        by_route=_slices(routed_claims),
        by_kind=_slices((claim.kind, claim) for claim in claims),
        routes=tuple(_route_line(route, census, histories) for route in _census_routes(census)),
    )


def census_reports(tree: CensusTree, census_id: str | None = None) -> tuple[CensusReport, ...]:
    """Return the report of every census in ``tree``, or of ``census_id`` only."""

    if census_id is not None and census_id not in tree.censuses:
        raise KeyError(f"no census {census_id!r}; known: {sorted(tree.censuses)}")
    return tuple(
        census_report(tree, tree.censuses[name])
        for name in sorted(tree.censuses)
        if census_id is None or name == census_id
    )
