"""Change-kind facts of the reviewer's family tree on a tree comparison (MIK-R33, adopting ICR-R32).

Every family occurrence and every member occurrence in the tree says what kind of recorded change
brings it into review. The facts are comparison facts of the four-tree comparison (MIK-R25), read
from each side's derived index, and computed on the server for the members a roster page returned;
the browser orders and traverses by them and never recomputes one. Three facts, each established,
not established, or unknown:

* ``intent`` -- the member invariant's ``revision`` differs between the memory base and the memory
  candidate tree, or the invariant is added or removed (a retired record is not live, so it counts
  as removed). One revision whose authored text differs between the sides is never unchanged either:
  it is ``intent`` marked ``text_differs`` ("same revision; text differs", the master's byte-comparison
  ruling);
* ``implementation`` -- an entry of the member was added, retired or re-anchored between the sides
  (MIK-R08 definition 7), or an entry's range intersects a changed hunk as the unexplained-changes
  lane classifies it (MIK-R32), or -- for a changed non-text file, which has no hunk -- a ``file``
  entry of the member covers it (MIK-R08 definition 8). A test that proves the member counts, and
  ``proof`` says so. A linked file in the change inventory establishes nothing by itself;
* ``membership`` -- this family's record lists the invariant on one side only.

The **primary** kind is the highest established fact in the precedence ``intent`` >
``implementation`` > ``membership``; with none established it is ``unknown`` when a fact is unknown
and ``unchanged`` only when all three are established as not changed. The other established facts,
``text_differs``, ``test`` for a proof, and ``unknown`` are the **marks** beside it. ``unknown`` is
added for an unknown fact, and -- unconditionally, as ICR-R32 rule 1 says -- for a realization of the
member in a changed file whose range cannot be resolved there (``range_unresolved``), even when
``implementation`` is established through another entry. Every unknown names why -- the change
kind's (``intent``, ``implementation``, an unresolved range) in ``unknown_reasons``, the
membership's in ``membership_reasons`` -- in the lane's per-entry terms or naming the side that could
not be read, so an unknown badge is never unexplained and "change kind unknown" never reads as
"membership unknown". The family's own row carries the fact of its guarantee: ``intent`` when the
``guarantee`` text differs (or the family is added or removed).
Nothing here is a verdict, a risk score or an assessment.
"""

from __future__ import annotations

from typing import Final, Literal, NamedTuple

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "CHANGE_PRECEDENCE",
    "EVIDENCE_LIMIT",
    "EVIDENCE_TEXT_LIMIT",
    "MEMBERSHIP_REASON_LIMIT",
    "UNKNOWN_FACTS",
    "UNKNOWN_REASON_LIMIT",
    "ChangeEvidence",
    "ChangeFact",
    "ChangeFacts",
    "ChangeKind",
    "ChangeMark",
    "GuaranteeChange",
    "ReviewFamilyChanges",
    "ReviewMemberChange",
    "change_marks",
    "primary_change",
]

ChangeKind = Literal["intent", "implementation", "membership", "unknown", "unchanged"]
ChangeFact = Literal["established", "not_established", "unknown"]
ChangeMark = Literal["intent", "implementation", "membership", "text_differs", "test", "unknown"]
GuaranteeChange = Literal["intent", "unchanged", "unknown"]

# Precedence and sort weight, highest first (ICR-R32 rule 2).
CHANGE_PRECEDENCE: Final[tuple[ChangeKind, ...]] = (
    "intent",
    "implementation",
    "membership",
    "unknown",
    "unchanged",
)
# An occurrence names at most this many evidence lines, each clipped; the facts are the whole truth.
EVIDENCE_LIMIT: Final = 8
EVIDENCE_TEXT_LIMIT: Final = 600
UNKNOWN_REASON_LIMIT: Final = 6
MEMBERSHIP_REASON_LIMIT: Final = 2


class ChangeFacts(NamedTuple):
    """The three facts of one member occurrence, whether a proof entry established one, whether
    ``intent`` rests only on one revision's differing text, and whether an entry of the member in a
    changed file supplies no range there."""

    intent: ChangeFact
    implementation: ChangeFact
    membership: ChangeFact
    proof: bool = False
    text_differs: bool = False
    range_unresolved: bool = False


class ChangeEvidence(NamedTuple):
    """What established the established facts, and why each unknown fact is unknown."""

    established: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    membership: tuple[str, ...] = ()


UNKNOWN_FACTS: Final = ChangeFacts("unknown", "unknown", "unknown")


def primary_change(
    intent: ChangeFact, implementation: ChangeFact, membership: ChangeFact
) -> ChangeKind:
    """The highest established fact, else ``unknown`` when one is unknown, else ``unchanged``."""

    facts: tuple[tuple[ChangeKind, ChangeFact], ...] = (
        ("intent", intent),
        ("implementation", implementation),
        ("membership", membership),
    )
    for kind, fact in facts:
        if fact == "established":
            return kind
    return "unknown" if any(fact == "unknown" for _, fact in facts) else "unchanged"


def change_marks(facts: ChangeFacts) -> tuple[ChangeMark, ...]:
    """The marks beside the primary kind: the other established facts, ``text_differs``, ``test``,
    then ``unknown``."""

    intent, implementation, membership, proof, text_differs, unresolved = facts
    primary = primary_change(intent, implementation, membership)
    named: tuple[tuple[ChangeMark, ChangeFact], ...] = (
        ("intent", intent),
        ("implementation", implementation),
        ("membership", membership),
    )
    marks: list[ChangeMark] = [
        kind for kind, fact in named if fact == "established" and kind != primary
    ]
    if text_differs:
        marks.append("text_differs")
    if proof:
        marks.append("test")
    if primary != "unknown" and (unresolved or any(fact == "unknown" for _, fact in named)):
        marks.append("unknown")
    return tuple(marks)


class ReviewMemberChange(KnowledgeModel):
    """The change facts of one member occurrence: one invariant under one family.

    ``member_id`` is the roster's own membership identity, the same on both sides for one invariant
    under one family, so every node of the occurrence (both revisions of a revised member) reads the
    same facts. ``authored_position`` is the invariant's index in the family record's authored
    ``members`` list (the after side's, else the before side's): the tree's authored order.
    ``evidence`` names what established each established fact. ``unknown_reasons`` names why the
    change kind is not fully known (an unknown ``intent`` or ``implementation``, or an unresolved
    range), present exactly when it is not; ``membership_reasons`` names why the membership is
    unknown, present exactly when it is. All are bounded.
    """

    member_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    invariant: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    authored_position: int | None = Field(default=None, ge=0)
    intent: ChangeFact
    implementation: ChangeFact
    membership: ChangeFact
    proof: bool = False
    text_differs: bool = False
    range_unresolved: bool = False
    primary: ChangeKind
    marks: tuple[ChangeMark, ...] = ()
    evidence: tuple[str, ...] = Field(default=(), max_length=EVIDENCE_LIMIT)
    unknown_reasons: tuple[str, ...] = Field(default=(), max_length=UNKNOWN_REASON_LIMIT)
    membership_reasons: tuple[str, ...] = Field(default=(), max_length=MEMBERSHIP_REASON_LIMIT)

    @classmethod
    def of(
        cls,
        member_id: str,
        invariant: str | None,
        facts: ChangeFacts,
        evidence: ChangeEvidence | None = None,
        *,
        authored_position: int | None = None,
    ) -> ReviewMemberChange:
        """Build the occurrence with its primary kind and marks derived from ``facts``."""

        evidence = evidence or ChangeEvidence()

        facts = facts._replace(
            proof=facts.proof and facts.implementation == "established",
            text_differs=facts.text_differs and facts.intent == "established",
        )
        intent, implementation, membership, proof, text_differs, unresolved = facts
        return cls(
            member_id=member_id,
            invariant=invariant,
            authored_position=authored_position,
            intent=intent,
            implementation=implementation,
            membership=membership,
            proof=proof,
            text_differs=text_differs,
            range_unresolved=unresolved,
            primary=primary_change(intent, implementation, membership),
            marks=change_marks(facts),
            evidence=tuple(_clipped(line) for line in evidence.established[:EVIDENCE_LIMIT]),
            unknown_reasons=tuple(
                _clipped(line) for line in evidence.unknown[:UNKNOWN_REASON_LIMIT]
            ),
            membership_reasons=tuple(
                _clipped(line) for line in evidence.membership[:MEMBERSHIP_REASON_LIMIT]
            ),
        )

    @model_validator(mode="after")
    def _derived_from_the_facts(self) -> ReviewMemberChange:
        if self.primary != primary_change(self.intent, self.implementation, self.membership):
            raise ValueError(
                "the primary kind is the highest established fact, else unknown, else unchanged"
            )
        if self.proof and self.implementation != "established":
            raise ValueError("a proof marks an established implementation fact only")
        if self.text_differs and self.intent != "established":
            raise ValueError("a differing text of one revision marks an established intent only")
        if self.range_unresolved and self.implementation == "not_established":
            raise ValueError("an unresolved range leaves implementation established or unknown")
        facts = ChangeFacts(
            self.intent,
            self.implementation,
            self.membership,
            self.proof,
            self.text_differs,
            self.range_unresolved,
        )
        if self.marks != change_marks(facts):
            raise ValueError(
                "the marks are the other established facts, text_differs, test for a proof, and "
                "unknown for an unknown fact"
            )
        return self

    @model_validator(mode="after")
    def _unknowns_say_why(self) -> ReviewMemberChange:
        kind_unknown = self.range_unresolved or "unknown" in (self.intent, self.implementation)
        if kind_unknown != bool(self.unknown_reasons):
            raise ValueError(
                "an unknown fact names why it is unknown, and a known one names no such reason"
            )
        if (self.membership == "unknown") != bool(self.membership_reasons):
            raise ValueError(
                "an unknown membership names why it is unknown, and a known one names no such reason"
            )
        lines = (*self.evidence, *self.unknown_reasons, *self.membership_reasons)
        if any(len(line) > EVIDENCE_TEXT_LIMIT for line in lines):
            raise ValueError("an evidence line is clipped to its bound")
        return self


class ReviewFamilyChanges(KnowledgeModel):
    """The change facts of one family occurrence and of each returned member occurrence under it.

    ``members_total`` is the deduplicated union of the family's live members on either side (the
    members its rosters can return), or ``None`` when a side's family or invariant records could not
    all be read. ``members`` holds one occurrence per returned member and no other: the facts of a
    member no page returned are never computed or implied.
    """

    family: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    guarantee: GuaranteeChange
    guarantee_detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    members_total: int | None = Field(default=None, ge=0)
    members: tuple[ReviewMemberChange, ...] = ()
    detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _one_occurrence_per_member(self) -> ReviewFamilyChanges:
        identities = [member.member_id for member in self.members]
        if len(identities) != len(set(identities)):
            raise ValueError("a member occurrence is listed once under its family")
        return self


def _clipped(text: str) -> str:
    return text if len(text) <= EVIDENCE_TEXT_LIMIT else f"{text[: EVIDENCE_TEXT_LIMIT - 1]}…"
