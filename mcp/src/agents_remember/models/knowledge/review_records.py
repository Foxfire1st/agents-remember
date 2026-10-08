"""The record-collection availability vocabulary one review's composition reports (``ICR-R14@v1``).

Availability is a fact *per collection*, and the five states below are five different facts rather
than one empty tuple with a label: an owner that answered with records, an owner that answered with
none, expected content that could not be read, a quantity nobody measured, and a collection this
composition never read. The model lives beside the review payload it travels on and in its own module
because the vocabulary is a contract of its own -- the record owner resolves it, the pane carries it,
and neither derives it from a collection's length.

Nothing here selects, measures or judges anything: a channel reports what one owner's answer was, and
there is no state in this vocabulary that could be read as a favourable default.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "EVIDENCE_RECORD_CLASSES",
    "ReviewRecordChannel",
    "ReviewRecordChannelState",
    "ReviewRecordClassName",
    "evidence_classes_unread",
]

ReviewRecordClassName = Literal[
    "assessments",
    "detection_signals",
    "verification_observations",
    "authored_effects",
    "evidence_claims",
    "assessment_currentness",
]
"""The record collections one review composition supplies, named by record class, not by pane.

A collection's availability is a fact about the owner that produced the records, and a reader needs
the same fact whichever pane happens to render them: signals are displayed in the knowledge pane,
observations and evidence claims in the evidence pane, and both are read from the same bundle.
"""

ReviewRecordChannelState = Literal[
    "recorded",
    "none_recorded",
    "unavailable",
    "not_measured",
    "not_selected",
]
"""The five states a collection's availability can be in, each a different fact.

``recorded`` and ``none_recorded`` are the owner's own answers -- it holds records of this class and
supplied them, or it holds none. ``unavailable`` is expected content that could not be read, with
its provenance: absent, corrupt, refused or not yet established are all reported here and never as
an empty collection. ``not_measured`` is a quantity nobody measured (dependency currentness).
``not_selected`` is a collection this composition never read because the review selected no operand
that would reach it. The five are separate so that an empty tuple never has to stand for all of
them.
"""

_COUNTED_CHANNEL_STATES: frozenset[str] = frozenset({"recorded", "none_recorded"})

EVIDENCE_RECORD_CLASSES: frozenset[ReviewRecordClassName] = frozenset(
    {"verification_observations", "evidence_claims"}
)
"""The two record classes the evidence pane's summary state counts.

The pane's ``evidence_state`` is a roll-up of these classes and of nothing else: assessments have
their own summary, and detection signals are displayed in the knowledge pane.
"""


class ReviewRecordChannel(KnowledgeModel):
    """One record collection's availability, as the owner that produces it answered.

    The state is carried with the owner that answered and, for anything short of an answer, what
    would produce one. ``record_count`` is the number of records supplied, and it exists exactly when
    the owner answered: ``None`` is not a zero, it is the absence of a count, which is why a state
    that could not count cannot carry one. A collection that was read and holds none reports
    ``none_recorded`` with a real zero.

    ``unreadable`` names the exact identities inside an otherwise-answered collection that this
    composition could not serve, so one damaged record does not withdraw its siblings and is not
    silently dropped either. There is no field here for a favourable default: no state in this
    vocabulary means "nothing to worry about".
    """

    records: ReviewRecordClassName
    state: ReviewRecordChannelState
    owner: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    record_count: int | None = Field(default=None, ge=0)
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    unreadable: tuple[str, ...] = ()
    next_action: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_an_answer_to_carry_its_count(self) -> ReviewRecordChannel:
        """Refuse a count on a channel that did not answer, and a count that is not an answer.

        A channel bent to report a number it did not measure is the defect this model exists to make
        unrepresentable: an unreadable authority has no count, and a state that did not read the
        collection may not be spelled as a zero.
        """

        counted = self.state in _COUNTED_CHANNEL_STATES
        if counted and self.record_count is None:
            raise ValueError(
                f"a {self.state!r} channel reports the number of records it supplied; a count of "
                "None is the absence of a count, which only an unanswered channel may carry"
            )
        if not counted and self.record_count is not None:
            raise ValueError(
                f"a {self.state!r} channel measured no count, so it cannot carry one: "
                f"{self.record_count} would be read as a measured zero"
            )
        if self.state == "recorded" and not self.record_count:
            raise ValueError(
                "a recorded channel supplied at least one record; a recorded channel with no records "
                "is the 'none_recorded' state"
            )
        if self.state == "none_recorded" and self.record_count != 0:
            raise ValueError("a none_recorded channel answered with a measured zero")
        if not counted and not (self.next_action or "").strip():
            raise ValueError(
                f"the {self.state!r} state is not an answer about this collection, so it must name "
                "what would produce one; a state a reader cannot act on is a silent gap"
            )
        return self


def evidence_classes_unread(channels: Iterable[ReviewRecordChannel]) -> bool:
    """Whether a class the evidence summary counts could not be read.

    A summary of no evidence is a measured zero only when every class it counts answered; one
    ``unavailable`` class makes the summary ``unavailable`` too, so the roll-up never says less than
    the channels beside it.
    """

    return any(
        channel.records in EVIDENCE_RECORD_CLASSES and channel.state == "unavailable"
        for channel in channels
    )
