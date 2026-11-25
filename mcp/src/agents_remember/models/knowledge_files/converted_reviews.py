"""The review facts of owners closed before reviews were recorded in the memory.

A leaf closed out since then carries its review fact in its own history file (:mod:`.history`,
``review``). An owner closed before that has a frozen history file, which no write may change. Its
fact, taken once from its task document by the one-time state conversion, stands in this one file
of the product's beside the history files instead::

    knowledge/history/converted-reviews.json   (ar-converted-reviews/v1)

``reviews`` holds one entry per owner and attempt: ``owner`` is the leaf, wave or crossing ID,
``attempt`` the leaf's attempt (absent for the first, as in the history file's own name) and
``fact`` the :class:`.history.ReviewFact`. Only the conversion writes the file. A revision reads
the fact here exactly as it reads its owner's own ``review`` (:mod:`.record_state`).

The file is no history file: it names no owner of its own, is never closed and is never frozen; a
reader that walks ``knowledge/history/`` for owners' files passes it by
(:func:`is_converted_reviews_path`).
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge_files.history import Attempt, OwnerId, ReviewFact
from agents_remember.models.knowledge_files.shapes import FileModel, require_unique

CONVERTED_REVIEWS_SCHEMA: Final = "ar-converted-reviews/v1"
CONVERTED_REVIEWS_PATH: Final = "knowledge/history/converted-reviews.json"


class ConvertedReview(FileModel):
    """``{ owner, attempt?, fact }``: the review fact of one owner's attempt."""

    owner: OwnerId
    attempt: Attempt | None = None
    fact: ReviewFact

    @property
    def attempt_number(self) -> int:
        """The attempt this fact belongs to: ``attempt``, or 1 for an owner's first file."""

        return self.attempt or 1


class ConvertedReviews(FileModel):
    """``ar-converted-reviews/v1``: the converted review facts, unique by owner and attempt."""

    schema_: Literal["ar-converted-reviews/v1"] = Field(
        default=CONVERTED_REVIEWS_SCHEMA, alias="schema"
    )
    reviews: tuple[ConvertedReview, ...]

    @model_validator(mode="after")
    def _require_one_fact_per_attempt(self) -> ConvertedReviews:
        require_unique(
            tuple(f"{one.owner}#{one.attempt_number}" for one in self.reviews),
            what="converted review owners and attempts",
        )
        return self

    def by_attempt(self) -> dict[tuple[str, int], ReviewFact]:
        """Each fact keyed by ``(owner, attempt)``."""

        return {(one.owner, one.attempt_number): one.fact for one in self.reviews}


def is_converted_reviews_path(path: str) -> bool:
    """Whether a memory-relative ``path`` is the converted review facts' file."""

    return path == CONVERTED_REVIEWS_PATH
