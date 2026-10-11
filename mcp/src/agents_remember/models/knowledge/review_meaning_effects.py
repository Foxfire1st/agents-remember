"""The recorded effect of a meaning change, as the reviewer shows it (MIK-R43).

Where the reviewer shows that the reviewed leaf changed the meaning of an invariant or the
guarantee of a family, it also shows the effect that the leaf's curator recorded for that
change. A record judged in more than one attempt of the leaf shows one step per attempt, in
attempt order. The reviewer derives no label and states no net effect: every label shown was
recorded by the curator in the leaf's own history rows, and the projection below refuses to
invent one.

Three states, never mixed, for each record:

* ``steps``: one or more recorded steps;
* ``none``: the history is readable and complete and holds no step of this leaf about the
  record -- ``No meaning effect recorded by this leaf``;
* ``unavailable``, with the reason: the after memory side cannot be read or is
  ``legacy-unavailable``; the index is incomplete; or a row about the record is invalid.
  This state is never shown as ``none``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)
from agents_remember.models.knowledge.effect import ADMITTED_EFFECT_LABELS, EffectLabel

MeaningEffectState = Literal["steps", "none", "unavailable"]
MeaningRecordKind = Literal["invariant", "family"]

# A changed family row written before the effect label became required carries neither the
# effect nor its own revision. It is still a step, and it reads exactly this.
EFFECT_NOT_RECORDED = "effect not recorded"
FAMILY_REVISION_NOT_RECORDED = "family revision not recorded"
OLD_ROW_SENTENCE = "This row was written before the effect label became required."

# Rule 8's ``none`` text, stated exactly.
NONE_TEXT = "No meaning effect recorded by this leaf"

# Rule 7's line under the title when there is more than one step.
MULTI_STEP_LINE = "These are the leaf's successive recorded judgments."

# Rule 6's fixed sentences: what each admitted label means. They explain the label; they do
# not say whether the curator's judgment is right.
EFFECT_EXPLANATIONS: dict[str, str] = {
    "restore": "brings back an obligation that had been dropped or broken.",
    "clarify": "rewords the obligation; what it requires stays the same.",
    "introduce": "states an obligation that was not recorded before.",
    "strengthen": "adds to the obligation or tightens it.",
    "weaken": "relaxes the obligation or removes part of it.",
    "replace": "puts a different obligation in its place.",
    "split": "divides one obligation into several records.",
    "merge": "joins several records into one obligation.",
    "retire": "ends the obligation.",
}


class RecordedMeaningStep(KnowledgeModel):
    """One attempt's recorded judgment about one record: a history row that is a step.

    ``effect`` is ``None`` only for a changed family row written before the effect label
    became required, which reads ``effect not recorded``; ``revision`` is ``None`` only for
    the same rows, which read ``family revision not recorded``. Nothing is filled in from a
    member, from the family's present revision or from the reason.
    """

    attempt: int = Field(ge=1)
    row_id: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    history_path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    closed: bool
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    effect: EffectLabel | None = None
    revision: int | None = Field(default=None, ge=1)

    @property
    def label(self) -> str:
        """The badge token of this step: its effect, or ``effect not recorded``."""

        return self.effect if self.effect is not None else EFFECT_NOT_RECORDED


class RecordMeaningEffects(KnowledgeModel):
    """One record's answer: its steps in attempt order, or why there are none.

    ``detail`` is the ``none`` text for ``none``, the unavailable reason for
    ``unavailable``, and a one-line count for ``steps``; the fixed display sentences live
    beside the model (``NONE_TEXT``, ``MULTI_STEP_LINE``, ``OLD_ROW_SENTENCE``) so the
    wire carries facts and the reader renders them.
    """

    record: str = Field(min_length=1, max_length=REFERENCE_MAX_LENGTH)
    kind: MeaningRecordKind
    state: MeaningEffectState
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    steps: tuple[RecordedMeaningStep, ...] = ()

    @model_validator(mode="after")
    def _require_steps_exactly_for_steps(self) -> RecordMeaningEffects:
        if self.state == "steps" and not self.steps:
            raise ValueError("a steps answer carries its steps, never an empty list")
        if self.state != "steps" and self.steps:
            raise ValueError("a none or unavailable answer carries no steps")
        return self

    @model_validator(mode="after")
    def _require_the_none_text(self) -> RecordMeaningEffects:
        if self.state == "none" and self.detail != NONE_TEXT:
            raise ValueError("a none answer states exactly that no meaning effect was recorded")
        return self


class ReviewMeaningEffects(KnowledgeModel):
    """The steps field of one subject review: every covered record's answer.

    It covers the selected record and the families and members that the answer's family
    context returns. A roster continuation carries the steps of the members it returns
    because those members are in the returned context.
    """

    records: tuple[RecordMeaningEffects, ...] = ()

    def for_record(self, record: str) -> RecordMeaningEffects | None:
        """The answer for ``record``, or ``None`` when the review covers no such record."""

        for entry in self.records:
            if entry.record == record:
                return entry
        return None


class ReviewOutlineMeaning(KnowledgeModel):
    """One outline row's recorded effects: the labels in attempt order and the state.

    ``labels`` holds display tokens -- effect labels, or ``effect not recorded`` for an old
    family row -- so the tree badge joins exactly what the steps say. The state ``none``
    shows no badge; only ``steps`` carries labels.
    """

    state: MeaningEffectState
    labels: tuple[str, ...] = ()
    detail: str = Field(default="", max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_labels_exactly_for_steps(self) -> ReviewOutlineMeaning:
        if self.state == "steps" and not self.labels:
            raise ValueError("a steps meaning carries its labels, never an empty list")
        if self.state != "steps" and self.labels:
            raise ValueError("a none or unavailable meaning carries no labels")
        return self


def _check_explanations_cover_the_vocabulary() -> None:
    if set(EFFECT_EXPLANATIONS) != set(ADMITTED_EFFECT_LABELS):
        raise ValueError("every admitted effect label is explained exactly once")
    if tuple(EFFECT_EXPLANATIONS) != tuple(ADMITTED_EFFECT_LABELS):
        raise ValueError("the explanations follow the vocabulary's own order")


_check_explanations_cover_the_vocabulary()
