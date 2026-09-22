"""The reviewed identity's recorded governing-route association (ICR-R08@v1).

A governing route is a recorded association like a realization and a family membership, and it is the
one the comparison's union does not carry as an item: it is a join row between an identity and a
route. This module reads it for one identity on each snapshot and displays both recorded sides.

Three states are kept apart, and each is a fact rather than a default:

* ``recorded`` -- the snapshot records the identity and a route governs it, with the route's own id and
  its recorded path;
* ``ungoverned`` -- the snapshot records the identity and no route governs it. An ungoverned identity
  is not placed in the repository root and no route is inferred from the paths its claims name;
* ``not_recorded`` -- the snapshot does not record the identity at all. The route owner says in full
  that its own read answers ``None`` for both this case and the ungoverned one, so the existence
  question is asked here, by identity, before the route question is.

A review with no reviewed identity -- a path seed -- asks about no identity's route and is answered
with no association rather than with a route read for whichever record the path matched first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agents_remember.application.review_recorded_relationships import RecordedSnapshot
from agents_remember.memory.knowledge.routes import find_governing_route, route_path_for_id
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipGap,
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
)

__all__ = [
    "GOVERNING_ROUTE_KIND",
    "governing_route_movement",
]

GOVERNING_ROUTE_KIND: Literal["governing_route"] = "governing_route"

# The tables a governing route may be read for, keyed by the identity kind the selector names. They
# are the route owner's own governed-table names, so the read asks the question the write answers.
_GOVERNED_TABLES: Mapping[str, str] = {"invariant": "invariant", "family": "family"}

# The existence question a governing-route read needs first: the route owner says in full that
# ``find_governing_route`` answers ``None`` both for a row stored with no route and for a row this
# repository does not hold, and that a caller which must tell those apart asks the existence question
# itself. This is that question, one statement per governed identity kind.
_IDENTITY_QUESTIONS: Mapping[str, str] = {
    "invariant": "SELECT 1 FROM invariant WHERE repository_id = ? AND invariant_id = ?",
    "family": "SELECT 1 FROM family WHERE repository_id = ? AND family_id = ?",
}

# --- the reviewed identity's governing route --------------------------------------------------


def governing_route_movement(
    selector: KnowledgeReadSeed | None,
    repository_id: str,
    before: RecordedSnapshot,
    after: RecordedSnapshot,
) -> ReviewRelationshipMovement | None:
    """The reviewed identity's recorded governing-route association, or ``None`` when none is asked.

    A review with no reviewed identity -- a path seed, or the task-context composition -- asks about
    no identity's governing route, so none is displayed. That is a statement about the question, not
    about the repository: no root route and no guessed identity stands in for the one that was not
    named.
    """

    subject = _subject_identity(selector)
    if subject is None:
        return None
    record_kind, record_id = subject
    before_side = _route_side(before, repository_id, record_kind, record_id)
    after_side = _route_side(after, repository_id, record_kind, record_id)
    transition = _route_transition(before_side, after_side)
    return ReviewRelationshipMovement(
        relationship_kind=GOVERNING_ROUTE_KIND,
        record_kind=record_kind,  # type: ignore[arg-type]
        record_id=record_id,
        transition=transition,  # type: ignore[arg-type]
        before=(before_side,),
        after=after_side,
        # Both sides are one association: the governing route of the identity this review selected,
        # read on each snapshot. The basis names that, so the movement states why its two sides are
        # one rather than asserting a movement.
        pairing_basis="same_governed_identity",
        gaps=_route_gaps(before_side, after_side),
        statement=_route_statement(record_kind, record_id, before_side, after_side, transition),
    )


def _subject_identity(selector: KnowledgeReadSeed | None) -> tuple[str, str] | None:
    """Return ``(identity kind, identity)`` for a selector that names one, or ``None``.

    A selector that names an identity -- or an exact revision of one -- asks about that identity's
    governing route. A path selector names no identity, so it is answered with no route association
    rather than with a route read for whichever record the path happened to match first.
    """

    kind = getattr(selector, "kind", None)
    if kind in ("invariant", "invariant_revision"):
        return ("invariant", str(selector.invariant_id))  # type: ignore[attr-defined]
    if kind in ("family", "family_revision"):
        return ("family", str(selector.family_id))  # type: ignore[attr-defined]
    return None


@dataclass(frozen=True)
class _RouteFacts:
    """The recorded governing-route association one side states: the identity, and its route."""

    record_kind: Literal["invariant", "family"]
    record_id: str
    route_id: str | None = None
    route_path: str | None = None


def _route_side(
    snapshot: RecordedSnapshot, repository_id: str, record_kind: str, record_id: str
) -> ReviewRelationshipSide:
    """Read one snapshot's recorded governing-route association for one identity."""

    governed = _GOVERNED_TABLES[record_kind]
    if not _identity_recorded(snapshot, repository_id, record_kind, record_id):
        return _route_side_of(
            snapshot.side,
            "not_recorded",
            f"the {snapshot.side} snapshot records no {record_kind} {record_id}, so it records no "
            "governing route for it; this is a different fact from an identity recorded with no "
            "route, and neither is filled in from the other snapshot",
        )
    route_id = find_governing_route(snapshot.connection, repository_id, governed, record_id)
    if route_id is None:
        return _route_side_of(
            snapshot.side,
            "ungoverned",
            f"the {snapshot.side} snapshot records {record_kind} {record_id} with no governing "
            "route; an ungoverned identity is not placed in the repository root and no route is "
            "inferred from the paths its claims name",
            _RouteFacts(record_kind=record_kind, record_id=record_id),  # type: ignore[arg-type]
        )
    path = route_path_for_id(snapshot.connection, repository_id, route_id)
    return _route_side_of(
        snapshot.side,
        "recorded",
        f"the {snapshot.side} snapshot governs {record_kind} {record_id} by route {route_id} at "
        f"{path}",
        _RouteFacts(
            record_kind=record_kind,  # type: ignore[arg-type]
            record_id=record_id,
            route_id=str(route_id),
            route_path=path,
        ),
    )


def _route_side_of(
    side: str,
    state: str,
    detail: str,
    facts: _RouteFacts | None = None,
) -> ReviewRelationshipSide:
    """Render one governing-route side from the facts it can be.

    ``facts`` is absent exactly for the state that has none -- an identity this snapshot does not
    record -- and present for the two states that are about a recorded identity. The state itself is
    what says which of them a reader is looking at, so no blank stands in for an association.
    """

    return ReviewRelationshipSide(
        side=side,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        relationship_id=None if facts is None else facts.route_id,
        record_kind=None if facts is None else facts.record_kind,
        record_id=None if facts is None else facts.record_id,
        route_id=None if facts is None else facts.route_id,
        route_path=None if facts is None else facts.route_path,
        detail=detail,
    )


def _route_transition(
    before_side: ReviewRelationshipSide, after_side: ReviewRelationshipSide
) -> str:
    """Name what happened to the association: unchanged, reassigned, or not the same association."""

    if before_side.route_id is not None and after_side.route_id is not None:
        return "unchanged" if before_side.route_id == after_side.route_id else "reassigned"
    if before_side.state == after_side.state:
        return "unchanged"
    return "moved"


def _route_gaps(
    before_side: ReviewRelationshipSide, after_side: ReviewRelationshipSide
) -> tuple[ReviewRelationshipGap, ...]:
    """One gap per side whose snapshot does not record the reviewed identity at all."""

    return tuple(
        ReviewRelationshipGap(
            side=side.side,
            field="route",
            code="route_not_recorded",
            detail=side.detail,
        )
        for side in (before_side, after_side)
        if side.state == "not_recorded"
    )


def _route_statement(
    record_kind: str,
    record_id: str,
    before_side: ReviewRelationshipSide,
    after_side: ReviewRelationshipSide,
    transition: str,
) -> str:
    """State the recorded route association on both sides, and what the difference between them is."""

    if transition == "unchanged":
        return (
            f"the {record_kind} {record_id} is associated with the same governing route on both "
            f"snapshots ({_route_word(before_side)})"
        )
    return (
        f"the {record_kind} {record_id} records {_route_word(before_side)} on the baseline and "
        f"{_route_word(after_side)} on the candidate, so the governing-route association moved; both "
        "recorded sides are displayed"
    )


def _route_word(side: ReviewRelationshipSide) -> str:
    """Name one side's route association in one phrase, for a sentence about both sides."""

    if side.state == "recorded":
        return f"route {side.route_id} at {side.route_path}"
    if side.state == "ungoverned":
        return "no governing route"
    return f"no {side.record_kind} identity recorded"


def _identity_recorded(
    snapshot: RecordedSnapshot, repository_id: str, record_kind: str, record_id: str
) -> bool:
    """Whether one snapshot records one identity row at all."""

    statement = _IDENTITY_QUESTIONS[record_kind]
    return (
        next(iter(snapshot.connection.execute(statement, (repository_id, record_id))), None)
        is not None
    )


# --- the labelled rename inference --------------------------------------------------------------
