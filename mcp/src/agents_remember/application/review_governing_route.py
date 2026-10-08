"""The reviewed identity's recorded governing-route associations (ICR-R08@v1), read from text.

A governing route is a recorded association like a realization and a family membership, and it is the
one the comparison's union does not carry as an item. This module reads it for one identity on each
snapshot and displays the recorded sides.

**Where it is read from (MIK-R26).** The canonical store's scalar join row between an identity and a
route is retired, and the derived index never projected it, so the old read answered ``ungoverned``
for every identity of a converted tree. The association is now read from the text: a family declares
its route set in its record file (the index's ``ix_route``). Each declared route is one recorded
association, so a family with several routes is displayed as one relationship per route, each naming
that one route; no value holds a list of routes. An invariant file declares no route, so an invariant
stays ``ungoverned`` exactly as before.

Four states are kept apart, and each is a fact rather than a default:

* ``recorded`` -- the snapshot records the identity and declares this route for it;
* ``ungoverned`` -- the snapshot records the identity and no route governs it. An ungoverned identity
  is not placed in the repository root and no route is inferred from the paths its claims name;
* ``not_recorded`` -- the snapshot does not record the identity at all, which is a different fact from
  an identity recorded with no route, so the existence question is asked by identity before the route
  question is;
* ``unavailable`` -- the snapshot records the family and its file carries no route declarations this
  read can ask, so whether a route governs it was not read. It is never rendered as ``ungoverned``:
  that would state a fact nobody read.

A route that only one snapshot declares, while the other snapshot is governed by other routes, is a
one-sided association (``added`` or ``retracted``). A snapshot that has no route of its own to show
contributes its state side instead, so an identity that became governed, or one a snapshot does not
record, is still displayed with both sides.

A review with no reviewed identity -- a path seed -- asks about no identity's route and is answered
with no association rather than with a route read for whichever record the path matched first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from agents_remember.application.review_recorded_relationships import RecordedSnapshot
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review_relationships import (
    ReviewRelationshipGap,
    ReviewRelationshipMovement,
    ReviewRelationshipSide,
    ReviewRelationshipTransition,
)

__all__ = [
    "GOVERNING_ROUTE_KIND",
    "governing_route_movements",
]

GOVERNING_ROUTE_KIND: Literal["governing_route"] = "governing_route"

_IdentityKind = Literal["invariant", "family"]
_SideState = Literal["recorded", "ungoverned", "not_recorded", "unavailable"]

# The existence question a governing-route read needs first: a snapshot that does not hold the
# identity and a snapshot that holds it with no route are different facts, so the identity is asked
# for by itself, one statement per governed identity kind.
_IDENTITY_QUESTIONS: Mapping[str, str] = {
    "invariant": "SELECT 1 FROM invariant WHERE repository_id = ? AND invariant_id = ?",
    "family": "SELECT 1 FROM family WHERE repository_id = ? AND family_id = ?",
}

# The two index tables a family's route declarations are read through: the identifier mapping from
# the read's UUID to the record's text identifier, and the declared routes per family.
_ROUTE_TABLES = frozenset({"ix_uuid", "ix_route"})

# The gap each side state that is not an association is stated with.
_GAP_CODES: Mapping[str, Literal["route_not_recorded", "route_unavailable"]] = {
    "not_recorded": "route_not_recorded",
    "unavailable": "route_unavailable",
}

# --- the reviewed identity's governing routes -------------------------------------------------


def governing_route_movements(
    selector: KnowledgeReadSeed | None,
    repository_id: str,
    before: RecordedSnapshot,
    after: RecordedSnapshot,
) -> tuple[ReviewRelationshipMovement, ...]:
    """The reviewed identity's governing-route associations, one per route, or none when none is asked.

    A review with no reviewed identity -- a path seed, or the task-context composition -- asks about
    no identity's governing route, so none is displayed. That is a statement about the question, not
    about the repository: no root route and no guessed identity stands in for the one that was not
    named. An identity with no route on either snapshot is displayed as one movement whose two sides
    state that; otherwise each route either snapshot declares is one movement, in route order.
    """

    subject = _subject_identity(selector)
    if subject is None:
        return ()
    before_read = _read_side(before, repository_id, subject)
    after_read = _read_side(after, repository_id, subject)
    routes = sorted({*before_read.routes, *after_read.routes})
    if not routes:
        return (_movement(subject, before_read.state_side(), after_read.state_side()),)
    return tuple(
        _movement(subject, before_read.side_for(route), after_read.side_for(route))
        for route in routes
    )


def _subject_identity(selector: KnowledgeReadSeed | None) -> tuple[_IdentityKind, str] | None:
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
class _SideRead:
    """What one snapshot states about one identity's governing routes."""

    side: Literal["before", "after"]
    record_kind: _IdentityKind
    record_id: str
    state: _SideState
    routes: tuple[str, ...] = ()

    def side_for(self, route: str) -> ReviewRelationshipSide | None:
        """This snapshot's side of one route's association, or ``None`` when it holds none.

        A snapshot governed by other routes simply does not hold this association, which is the
        one-sided case. A snapshot with no route to show states what it is instead.
        """

        if route in self.routes:
            return self._rendered(
                "recorded",
                f"the {self.side} snapshot governs {self.record_kind} {self.record_id} by its "
                f"declared route {route}",
                route,
            )
        if self.state == "recorded":
            return None
        return self.state_side()

    def state_side(self) -> ReviewRelationshipSide:
        """The side of a snapshot that declares no route for the identity, stating which fact it is."""

        if self.state == "not_recorded":
            return ReviewRelationshipSide(
                side=self.side,
                state="not_recorded",
                detail=(
                    f"the {self.side} snapshot records no {self.record_kind} {self.record_id}, so "
                    "it records no governing route for it; this is a different fact from an "
                    "identity recorded with no route, and neither is filled in from the other "
                    "snapshot"
                ),
            )
        if self.state == "unavailable":
            return self._rendered(
                "unavailable",
                f"the {self.side} snapshot records {self.record_kind} {self.record_id}, and its "
                "file carries no route declarations to read, so whether a route governs it was "
                "not read; this is not the fact that no route governs it",
            )
        return self._rendered(
            "ungoverned",
            f"the {self.side} snapshot records {self.record_kind} {self.record_id} with no "
            "governing route; an ungoverned identity is not placed in the repository root and no "
            "route is inferred from the paths its claims name",
        )

    def _rendered(
        self, state: _SideState, detail: str, route: str | None = None
    ) -> ReviewRelationshipSide:
        """One side about a recorded identity; a route's own path is its identity in the text."""

        return ReviewRelationshipSide(
            side=self.side,
            state=state,
            relationship_id=route,
            record_kind=self.record_kind,
            record_id=self.record_id,
            route_id=route,
            route_path=route,
            detail=detail,
        )


def _read_side(
    snapshot: RecordedSnapshot, repository_id: str, subject: tuple[_IdentityKind, str]
) -> _SideRead:
    """Read what one snapshot states about one identity's governing routes."""

    record_kind, record_id = subject
    side: Literal["before", "after"] = "before" if snapshot.side == "before" else "after"
    if not _identity_recorded(snapshot, repository_id, record_kind, record_id):
        return _SideRead(side, record_kind, record_id, "not_recorded")
    if record_kind == "invariant":
        # An invariant file declares no route, whatever tables the snapshot's file carries.
        return _SideRead(side, record_kind, record_id, "ungoverned")
    if not _route_tables_present(snapshot):
        return _SideRead(side, record_kind, record_id, "unavailable")
    routes = _declared_routes(snapshot, record_id)
    return _SideRead(side, record_kind, record_id, "recorded" if routes else "ungoverned", routes)


def _declared_routes(snapshot: RecordedSnapshot, family_id: str) -> tuple[str, ...]:
    """The route set one family record declares in this snapshot's text, in route order."""

    texts = [
        str(row[0])
        for row in snapshot.connection.execute(
            "SELECT id FROM ix_uuid WHERE uuid = ?", (family_id,)
        )
    ]
    return tuple(
        sorted(
            {
                str(row[0])
                for text in texts
                for row in snapshot.connection.execute(
                    "SELECT route FROM ix_route WHERE family = ?", (text,)
                )
            }
        )
    )


def _route_tables_present(snapshot: RecordedSnapshot) -> bool:
    """Whether the snapshot's file carries the tables a family's route declarations are read from."""

    names = {
        str(row[0])
        for row in snapshot.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('ix_uuid', 'ix_route')"
        )
    }
    return names == _ROUTE_TABLES


def _movement(
    subject: tuple[_IdentityKind, str],
    before_side: ReviewRelationshipSide | None,
    after_side: ReviewRelationshipSide | None,
) -> ReviewRelationshipMovement:
    """Display one governing-route association with the side each snapshot states for it."""

    record_kind, record_id = subject
    transition = _route_transition(before_side, after_side)
    two_sided = before_side is not None and after_side is not None
    return ReviewRelationshipMovement(
        relationship_kind=GOVERNING_ROUTE_KIND,
        record_kind=record_kind,
        record_id=record_id,
        transition=transition,
        before=() if before_side is None else (before_side,),
        after=after_side,
        # Both sides are one association: a governing route of the identity this review selected,
        # read on each snapshot. The basis names that, so the movement states why its two sides are
        # one rather than asserting a movement. A route only one snapshot declares paired nothing.
        pairing_basis="same_governed_identity" if two_sided else None,
        gaps=_route_gaps(before_side, after_side),
        statement=_route_statement(subject, before_side, after_side, transition),
    )


def _route_transition(
    before_side: ReviewRelationshipSide | None, after_side: ReviewRelationshipSide | None
) -> ReviewRelationshipTransition:
    """Name what happened to one association between the two snapshots.

    A comparison with a side whose route declarations were not read measured nothing and says so;
    it is never ``unchanged``, even when both sides are unreadable.
    """

    if before_side is None:
        return "added"
    if after_side is None:
        return "retracted"
    if "unavailable" in (before_side.state, after_side.state):
        return "unresolved"
    return "unchanged" if before_side.state == after_side.state else "moved"


def _route_gaps(
    before_side: ReviewRelationshipSide | None, after_side: ReviewRelationshipSide | None
) -> tuple[ReviewRelationshipGap, ...]:
    """One gap per displayed side that could not state whether a route governs the identity."""

    return tuple(
        ReviewRelationshipGap(
            side=side.side,
            field="route",
            code=_GAP_CODES[side.state],
            detail=side.detail,
        )
        for side in (before_side, after_side)
        if side is not None and side.state in _GAP_CODES
    )


def _route_statement(
    subject: tuple[_IdentityKind, str],
    before_side: ReviewRelationshipSide | None,
    after_side: ReviewRelationshipSide | None,
    transition: ReviewRelationshipTransition,
) -> str:
    """State the route association on the displayed sides, and what the difference between them is."""

    record_kind, record_id = subject
    if before_side is None or after_side is None:
        return _one_sided_statement(subject, before_side or after_side)
    before_word = _route_word(record_kind, before_side)
    after_word = _route_word(record_kind, after_side)
    if transition == "unchanged":
        return (
            f"the {record_kind} {record_id} is associated with the same governing route on both "
            f"snapshots ({before_word})"
        )
    if transition == "unresolved":
        return (
            f"the {record_kind} {record_id} records {before_word} on the baseline and {after_word} "
            "on the candidate; a side whose route declarations were not read cannot be compared, "
            "so no movement of the governing-route association is stated"
        )
    return (
        f"the {record_kind} {record_id} records {before_word} on the baseline and {after_word} on "
        "the candidate, so the governing-route association moved; both recorded sides are displayed"
    )


def _one_sided_statement(
    subject: tuple[_IdentityKind, str], held: ReviewRelationshipSide | None
) -> str:
    """State a route that one snapshot declares while the other is governed by other routes."""

    record_kind, record_id = subject
    assert held is not None, "a governing-route movement displays at least one side"
    holder, other = (
        ("baseline", "candidate") if held.side == "before" else ("candidate", "baseline")
    )
    return (
        f"the {record_kind} {record_id} declares the governing route {held.route_path} on the "
        f"{holder} and not on the {other}, which governs it by other routes; this one recorded "
        "side is displayed"
    )


def _route_word(record_kind: str, side: ReviewRelationshipSide) -> str:
    """Name one side's route association in one phrase, for a sentence about both sides."""

    if side.state == "recorded":
        return f"route {side.route_path}"
    if side.state == "ungoverned":
        return "no governing route"
    if side.state == "unavailable":
        return "route declarations that were not read"
    return f"no {record_kind} identity recorded"


def _identity_recorded(
    snapshot: RecordedSnapshot, repository_id: str, record_kind: str, record_id: str
) -> bool:
    """Whether one snapshot records one identity row at all."""

    statement = _IDENTITY_QUESTIONS[record_kind]
    return (
        next(iter(snapshot.connection.execute(statement, (repository_id, record_id))), None)
        is not None
    )
