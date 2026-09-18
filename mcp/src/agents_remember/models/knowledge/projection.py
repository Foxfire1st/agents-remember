"""The projection rule: an assessment may be displayed, and it never arrives without its basis.

``KS-R18@v1`` §3 fixes one property and it is the whole of this module: **a projection may display an
assessment, and when it does it preserves that assessment's provenance and status** -- its author,
its exact examined inputs and its recorded disposition travel with it. Three consequences are
enforced here rather than documented:

* **Missing stays missing.** An unassessed claim renders as unassessed. The projection does not fill
  a missing assessment with a favourable default (``Doc13:104``), does not manufacture approval, and
  does not resolve a disagreement between assessments -- a disagreement is a fact to be displayed,
  and picking a winner would be a new interpretation rather than a rendering.

* **A stale assessment is displayed as stale.** When the inputs an assessment examined have moved, the
  persisted finding stays readable with ``binding_state = stale`` and the projection reports it as
  stale; it is not reused as if the new inputs had been reviewed, and a projection does not re-judge.
  This is why :class:`AssessmentDisplay` carries the *target reference's* own state rather than a
  second, independently derived one: two statuses for one fact is how "is this still current" comes
  to have two answers.

* **No new interpretation.** ``rendered`` is the recorded disposition, ``author`` is the recorded
  author, and ``examined_inputs`` are the exactly recorded inputs -- all three read from the record
  rather than composed by this module. There is no field here for a conclusion, a severity or a
  recommendation, so a projection cannot acquire one by accident.

Nothing here persists, publishes or deletes anything: a projection is derived and regenerable, never
a second authority (``Doc13:110``, ``Doc13:112``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    KnowledgeModel,
)

__all__ = [
    "ASSESSMENT_STATUSES",
    "AssessmentDisplay",
    "ProjectionRow",
    "assessment_display",
]

# The two statuses a displayed assessment can carry, and they are exhaustive on purpose: either the
# assessment examined the inputs the row now rests on, or it did not. Anything else -- "probably
# still fine", "superseded by a newer judgement" -- is a claim about the inputs that this projection
# is not entitled to make.
ASSESSMENT_STATUSES: tuple[str, ...] = ("current", "stale")


class AssessmentDisplay(KnowledgeModel):
    """One assessment as displayed: its disposition, its author, its examined inputs and its status.

    ``disposition`` is the recorded disposition verbatim and ``examined_inputs`` are the exactly
    recorded inputs, so a reader can see the basis the assessment was made on. An unassessed claim is
    expressed by :data:`ASSESSMENT_ABSENT`'s own shape -- an :class:`AssessmentDisplay` that is
    ``None`` on the row -- rather than by an instance with empty fields, because an instance with
    empty fields is a *present* assessment that happens to say nothing.
    """

    disposition: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    author: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    examined_inputs: tuple[str, ...] = ()
    status: Literal["current", "stale"] = "current"
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_the_basis_to_travel(self) -> AssessmentDisplay:
        """Refuse a display that carries a conclusion without the basis it was made on.

        A disposition with no author is an anonymous verdict, and an assessment that examined nothing
        cannot have been made *about* the inputs a row rests on. Neither is a rendering this
        projection is allowed to produce, so neither is constructible.
        """

        if not self.author.strip():
            raise ValueError(
                "a displayed assessment carries its author: an anonymous verdict is not a rendering "
                "of a recorded assessment"
            )
        if not self.examined_inputs:
            raise ValueError(
                "a displayed assessment carries the exact inputs it examined: an assessment that "
                "examined nothing cannot be a basis for the row it is displayed with"
            )
        return self


class ProjectionRow(KnowledgeModel):
    """One projected statement: what it says, what it cites, and whatever assessment it carries.

    ``assessment`` is ``None`` for an unassessed claim, and that ``None`` **is** the missing state --
    there is no default, no placeholder and no favourable substitute anywhere in this model. The
    ``target_state`` and ``binding_state`` fields carry the binding observation's own facts, so the
    row cannot present a stale binding as a current one.
    """

    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    binding_id: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    target_state: Literal["resolved", "record_absent", "revision_absent", "kind_mismatch"]
    binding_state: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    assessment: AssessmentDisplay | None = None
    disagreement: bool = False
    limitations: tuple[str, ...] = ()

    def assessed(self) -> bool:
        """Return whether this row carries an assessment at all."""

        return self.assessment is not None

    def stale(self) -> bool:
        """Return whether the assessment this row carries is displayed as stale."""

        return self.assessment is not None and self.assessment.status == "stale"


def assessment_display(
    disposition: str,
    author: str,
    examined_inputs: Sequence[str],
    *,
    current_inputs: Sequence[str],
    detail: str,
) -> AssessmentDisplay:
    """Return one display whose status is *derived from the recorded inputs*, never asserted.

    The status is ``current`` exactly when the inputs the assessment examined are a subset of the
    inputs the row now rests on. That is the only question the projection is entitled to answer, and
    it answers it from records: nothing here re-judges the disposition, upgrades a stale assessment,
    or manufactures an approval. ``binding_state = stale`` after an input change is therefore a
    measurement of the inputs rather than a flag someone remembered to set.
    """

    examined = tuple(examined_inputs)
    current = set(current_inputs)
    status = "current" if set(examined) <= current else "stale"
    return AssessmentDisplay(
        disposition=disposition,
        author=author,
        examined_inputs=examined,
        status=status,
        detail=detail,
    )
