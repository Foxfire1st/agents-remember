"""The migration census as files: baseline, inventory, claims and route status (MIK-R20).

A census lives in ``knowledge/census/<census-id>/`` and is written in the canonical formatting:

======================================  =========================================================
``baseline.json``                       ``ar-census-baseline/v1``: the pinned code and memory
                                        commits, and the code scope the inventory covers
``inventory.json``                      ``ar-census-inventory/v1``: one row per in-scope source
                                        file and per onboarding artifact, each with its governing
                                        onboarding route (MIK-R21 rule 1); written mechanically
``claims/<route-slug>.json``            ``ar-census-claims/v1``: the legacy claims agents
                                        extracted in that route, with their assessments and
                                        migration disposition
``routes/<route-slug>.json``            ``ar-census-route/v1``: that route's migration status
                                        history
======================================  =========================================================

**Observations are appended, never edited.** A claim's ``assessments`` and a route's ``statuses``
are append-only lists (rules 3 and 6); the latest entry governs. A correction appends a new
assessment. The validator (MIK-R22 rule 9) enforces this against the comparison bases; the models
here check shape only.

**Measures.** A claim is in the cohort ``N`` exactly when its ``applicability`` is ``assessable``
(Doc12: "eligible, independently assessable legacy claims"). Its latest assessment's verdict places
it in ``T`` (``no_concern_found``), ``F`` (``concern_found``) or ``U`` (``unresolved``); a claim with
no assessment is ``P``. So ``N = T + F + U + P`` by construction.

**Route slugs.** ``claims/`` and ``routes/`` files are named by :func:`route_slug`, an injective,
readable spelling of the route path: ``/`` becomes ``+``, every other character outside
``[A-Za-z0-9._-]`` (and a leading ``.``) is percent-encoded, and the repository root route ``.`` is
``@root``. The file also names its ``route``, and the validator checks the two agree.

**Governing status.** A route's migration status is the latest status entry for it across every
census (MIK-R10 rule 1): the entry with the latest ``provenance.at``; a tie is broken by census ID,
then by list position. A route with no entry is ``pending``. :func:`governing_status` is that rule,
at the ``models`` rank so every layer can apply it.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, Field, StrictInt, model_validator

from agents_remember.models.knowledge.base import GIT_OBJECT_PATTERN, PATH_MAX_LENGTH
from agents_remember.models.knowledge_files.ids import (
    CROCKFORD_ALPHABET,
    MINTED_BODY_LENGTH,
    RECORD_PREFIXES,
)
from agents_remember.models.knowledge_files.shapes import (
    FileModel,
    Label,
    RecordId,
    Reference,
    RepositoryPath,
    Text,
    require_repository_path,
    require_unique,
)
from agents_remember.models.knowledge_files.sidecars import ROOT_ROUTE_PATH

CENSUS_BASELINE_SCHEMA: Final = "ar-census-baseline/v1"
CENSUS_INVENTORY_SCHEMA: Final = "ar-census-inventory/v1"
CENSUS_CLAIMS_SCHEMA: Final = "ar-census-claims/v1"
CENSUS_ROUTE_SCHEMA: Final = "ar-census-route/v1"

BASELINE_FILENAME: Final = "baseline.json"
INVENTORY_FILENAME: Final = "inventory.json"
CLAIMS_DIRECTORY: Final = "claims"
ROUTES_DIRECTORY: Final = "routes"
ONBOARDING_PREFIX: Final = "onboarding/"
ROOT_ROUTE_SLUG: Final = "@root"

CLAIM_PREFIX: Final = "CLM"
CLAIM_ID_PATTERN: Final = rf"^{CLAIM_PREFIX}-[{CROCKFORD_ALPHABET}]{{{MINTED_BODY_LENGTH}}}$"
CENSUS_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

ClaimKind = Literal[
    "unclassified",
    "current_behavior",
    "accepted_invariant",
    "historical_rationale",
    "realization_attribution",
]
Applicability = Literal["assessable", "non_claim", "historical_non_applicable"]
COHORT_APPLICABILITY: Final = "assessable"
Verdict = Literal["no_concern_found", "concern_found", "unresolved"]
Cell = Literal["T", "F", "U", "P"]
CELL_OF_VERDICT: Final[dict[str, Cell]] = {
    "no_concern_found": "T",
    "concern_found": "F",
    "unresolved": "U",
}
Disposition = Literal[
    "admitted_as_invariant",
    "admitted_as_family",
    "admitted_as_decision",
    "admitted_as_other_record",
    "kept_as_prose",
    "demoted",
    "discarded_false",
    "pending",
]
DISPOSITIONS: Final[tuple[Disposition, ...]] = (
    "admitted_as_invariant",
    "admitted_as_family",
    "admitted_as_decision",
    "admitted_as_other_record",
    "kept_as_prose",
    "demoted",
    "discarded_false",
    "pending",
)
RouteStatusValue = Literal["pending", "in_progress", "migrated", "excluded", "blocked"]
ROUTE_STATUSES: Final[tuple[RouteStatusValue, ...]] = (
    "pending",
    "in_progress",
    "migrated",
    "excluded",
    "blocked",
)
DEFAULT_ROUTE_STATUS: Final[RouteStatusValue] = "pending"

# The record prefixes each admitting disposition links to. ``admitted_as_other_record`` takes every
# record kind that is not an invariant, family or decision.
_ADMITTED_PREFIXES: Final[dict[str, frozenset[str]]] = {
    "admitted_as_invariant": frozenset({RECORD_PREFIXES["invariant"]}),
    "admitted_as_family": frozenset({RECORD_PREFIXES["family"]}),
    "admitted_as_decision": frozenset({RECORD_PREFIXES["decision"]}),
    "admitted_as_other_record": frozenset(
        prefix
        for kind, prefix in RECORD_PREFIXES.items()
        if kind not in {"invariant", "family", "decision"}
    ),
}


def mint_claim_id() -> str:
    """Mint a random claim ID, ``CLM-`` and six Crockford base32 characters."""

    body = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(MINTED_BODY_LENGTH))
    return f"{CLAIM_PREFIX}-{body}"


def _require_route_path(value: str) -> str:
    return value if value == ROOT_ROUTE_PATH else require_repository_path(value)


def _require_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"not an ISO 8601 time: {value!r}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"a census time names its UTC offset: {value!r}")
    return value


def _require_onboarding_path(value: str) -> str:
    if not value.startswith(ONBOARDING_PREFIX):
        raise ValueError(f"an onboarding artifact lives under {ONBOARDING_PREFIX}: {value!r}")
    return value


CensusId = Annotated[str, Field(min_length=1, max_length=128, pattern=CENSUS_ID_PATTERN)]
RoutePath = Annotated[
    str, Field(min_length=1, max_length=PATH_MAX_LENGTH), AfterValidator(_require_route_path)
]
GitObjectId = Annotated[str, Field(pattern=GIT_OBJECT_PATTERN)]
Timestamp = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_require_timestamp)]
ArtifactPath = Annotated[RepositoryPath, AfterValidator(_require_onboarding_path)]
ClaimId = Annotated[str, Field(pattern=CLAIM_ID_PATTERN)]


def route_slug(route: str) -> str:
    """Return the file stem of ``route``'s claims and status files (injective, readable)."""

    _require_route_path(route)
    if route == ROOT_ROUTE_PATH:
        return ROOT_ROUTE_SLUG
    parts: list[str] = []
    for index, character in enumerate(route):
        if character == "/":
            parts.append("+")
        elif re.match(r"[A-Za-z0-9_-]", character) or (character == "." and index > 0):
            parts.append(character)
        else:
            parts.append("".join(f"%{byte:02X}" for byte in character.encode("utf-8")))
    return "".join(parts)


def time_of(value: str) -> datetime:
    """Return a validated census time as an aware datetime."""

    return datetime.fromisoformat(_require_timestamp(value))


# --------------------------------------------------------------------------------------------------
# Baseline and inventory
# --------------------------------------------------------------------------------------------------


class CensusCommit(FileModel):
    """``{ commit }``: one side of the pinned baseline, an exact Git commit ID."""

    commit: GitObjectId


class CensusBaseline(FileModel):
    """``ar-census-baseline/v1``: the code and memory commits a census is pinned to.

    ``scope`` lists the code directories whose files are in scope; empty means the whole code tree.
    """

    schema_: Literal["ar-census-baseline/v1"] = Field(
        default=CENSUS_BASELINE_SCHEMA, alias="schema"
    )
    census: CensusId
    code: CensusCommit
    memory: CensusCommit
    scope: tuple[RepositoryPath, ...]

    @model_validator(mode="after")
    def _require_distinct_scope(self) -> CensusBaseline:
        require_unique(self.scope, what="scope")
        return self


class InventoryRow(FileModel):
    """One inventoried path with its governing onboarding route; ``route`` is absent when none."""

    path: RepositoryPath
    route: RoutePath | None = None


def _require_sorted_unique(rows: tuple[InventoryRow, ...], *, what: str) -> None:
    paths = [row.path for row in rows]
    if paths != sorted(set(paths)):
        raise ValueError(f"inventory {what} are listed once each, sorted by path")


class CensusInventory(FileModel):
    """``ar-census-inventory/v1``: every in-scope source file and every onboarding artifact.

    ``sources`` are code-repository paths; ``artifacts`` are memory-repository paths under
    ``onboarding/``. Both are written mechanically from the baseline and sorted by path.
    """

    schema_: Literal["ar-census-inventory/v1"] = Field(
        default=CENSUS_INVENTORY_SCHEMA, alias="schema"
    )
    census: CensusId
    sources: tuple[InventoryRow, ...]
    artifacts: tuple[InventoryRow, ...]

    @model_validator(mode="after")
    def _require_mechanical_rows(self) -> CensusInventory:
        _require_sorted_unique(self.sources, what="sources")
        _require_sorted_unique(self.artifacts, what="artifacts")
        for row in self.artifacts:
            _require_onboarding_path(row.path)
        return self

    def artifact(self, path: str) -> InventoryRow | None:
        return next((row for row in self.artifacts if row.path == path), None)

    @property
    def routes(self) -> tuple[str, ...]:
        """Every onboarding route that governs at least one inventoried row, sorted."""

        return tuple(
            sorted({row.route for row in (*self.sources, *self.artifacts) if row.route is not None})
        )


# --------------------------------------------------------------------------------------------------
# Claims (rules 2 and 6)
# --------------------------------------------------------------------------------------------------


class Provenance(FileModel):
    """``{ leaf | wave, session, at }``: who recorded an observation, in which session, when."""

    leaf: Label | None = None
    wave: Label | None = None
    session: Label
    at: Timestamp

    @model_validator(mode="after")
    def _leaf_or_wave(self) -> Provenance:
        if (self.leaf is None) == (self.wave is None):
            raise ValueError("a census provenance names exactly one of leaf or wave")
        return self


class Assessment(FileModel):
    """One recorded assessment of a claim: its verdict, the evidence, and who made it."""

    verdict: Verdict
    evidence: tuple[Reference, ...] = Field(min_length=1)
    provenance: Provenance
    note: Text | None = None


class LineSpan(FileModel):
    start: Annotated[StrictInt, Field(ge=1)]
    end: Annotated[StrictInt, Field(ge=1)]

    @model_validator(mode="after")
    def _ordered(self) -> LineSpan:
        if self.end < self.start:
            raise ValueError("a line span ends at or after its start")
        return self


class ClaimLocation(FileModel):
    """Where the original text sits: an inventoried onboarding artifact, optionally its lines."""

    artifact: ArtifactPath
    lines: LineSpan | None = None


def _require_disposition_records(disposition: Disposition, records: tuple[str, ...] | None) -> None:
    """An admitting disposition links to records of its kind; only ``demoted`` may also link."""

    if records is not None:
        require_unique(records, what="records")
    allowed = _ADMITTED_PREFIXES.get(disposition)
    if allowed is None:
        if records is not None and disposition != "demoted":
            raise ValueError(f"a {disposition} claim links to no record")
        return
    if not records:
        raise ValueError(f"a claim {disposition} links to the records it became")
    wrong = [record for record in records if record.split("-")[0] not in allowed]
    if wrong:
        raise ValueError(f"{disposition} links to {sorted(allowed)} records, not {wrong}")


class CensusClaim(FileModel):
    """One legacy claim: text and location, kind and applicability, assessments, disposition."""

    id: ClaimId
    text: Text
    location: ClaimLocation
    kind: ClaimKind
    applicability: Applicability
    assessments: tuple[Assessment, ...]
    disposition: Disposition
    records: tuple[RecordId, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_consistent_disposition(self) -> CensusClaim:
        _require_disposition_records(self.disposition, self.records)
        if self.disposition == "discarded_false" and self.verdict != "concern_found":
            raise ValueError(
                "discarded_false is for a claim whose latest assessment is concern_found"
            )
        return self

    @property
    def verdict(self) -> Verdict | None:
        """The latest assessment's verdict, which governs; ``None`` when unassessed."""

        return self.assessments[-1].verdict if self.assessments else None

    @property
    def in_cohort(self) -> bool:
        return self.applicability == COHORT_APPLICABILITY

    @property
    def cell(self) -> Cell:
        verdict = self.verdict
        return "P" if verdict is None else CELL_OF_VERDICT[verdict]


class CensusClaims(FileModel):
    """``ar-census-claims/v1``: the claims agents extracted in one onboarding route."""

    schema_: Literal["ar-census-claims/v1"] = Field(default=CENSUS_CLAIMS_SCHEMA, alias="schema")
    census: CensusId
    route: RoutePath
    claims: tuple[CensusClaim, ...]

    @model_validator(mode="after")
    def _require_distinct_claims(self) -> CensusClaims:
        require_unique(tuple(claim.id for claim in self.claims), what="claim ids")
        return self


# --------------------------------------------------------------------------------------------------
# Route status (rule 3)
# --------------------------------------------------------------------------------------------------


class StatusEntry(FileModel):
    """One route status: the status, why, the code tree it was set against, and provenance."""

    status: RouteStatusValue
    reason: Text
    tree: GitObjectId
    provenance: Provenance


class CensusRoute(FileModel):
    """``ar-census-route/v1``: one route's append-only status history; the latest entry governs."""

    schema_: Literal["ar-census-route/v1"] = Field(default=CENSUS_ROUTE_SCHEMA, alias="schema")
    census: CensusId
    route: RoutePath
    statuses: tuple[StatusEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_time_order(self) -> CensusRoute:
        times = [time_of(entry.provenance.at) for entry in self.statuses]
        if any(later < earlier for earlier, later in pairwise(times)):
            raise ValueError("status entries are appended in time order")
        return self


CENSUS_MODELS: Final[dict[str, type[FileModel]]] = {
    CENSUS_BASELINE_SCHEMA: CensusBaseline,
    CENSUS_INVENTORY_SCHEMA: CensusInventory,
    CENSUS_CLAIMS_SCHEMA: CensusClaims,
    CENSUS_ROUTE_SCHEMA: CensusRoute,
}
CensusDocument = CensusBaseline | CensusInventory | CensusClaims | CensusRoute


@dataclass(frozen=True)
class GoverningStatus:
    """A route's governing migration status and where it was recorded (``None``: the default)."""

    route: str
    status: RouteStatusValue
    census: str | None = None
    entry: StatusEntry | None = None


def governing_status(route: str, histories: Iterable[CensusRoute]) -> GoverningStatus:
    """Return ``route``'s latest status entry across every census, or ``pending``.

    The latest entry is the one with the latest ``provenance.at``; a tie is broken by census ID and
    then by list position (a later append wins).
    """

    best: tuple[datetime, str, int] | None = None
    found = GoverningStatus(route=route, status=DEFAULT_ROUTE_STATUS)
    for history in histories:
        if history.route != route:
            continue
        for position, entry in enumerate(history.statuses):
            key = (time_of(entry.provenance.at), history.census, position)
            if best is None or key > best:
                best = key
                found = GoverningStatus(route, entry.status, history.census, entry)
    return found
