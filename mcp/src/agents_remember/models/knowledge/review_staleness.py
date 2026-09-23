"""Whether a review's comparison is still the one it was, and whether it may be submitted against.

This module owns the **review surface's own state about its inputs**, which is a different question
from what the comparison *contains*: whether the comparison the reader is looking at is still the
candidate's comparison (``ReviewStaleness``), whether a managed sync moved the reviewed inputs of the
generation this leaf published (``ReviewSyncMovement``), and whether an assessment may be submitted
against what is displayed (``ReviewSubmission``).

**Why it is its own module.** ``models/knowledge/review.py`` -- the payload vocabulary that re-exports
these three names -- was at 1198 lines against the repository's 1200-line hard rail, so adding the
movement vocabulary there would have made it a new offender. The extraction the file-size rule asks for
is this one: one cohesive responsibility, moved whole, with the payload module re-exporting every name
so its importers and tests keep working. There is one implementation of each rule and it lives here.

``ReviewStaleness`` is ``ICR-R17@v1``'s: the identity a reader carried beside the comparison rendered
now, and the labelled previous input a mismatch earns. ``ReviewSyncMovement`` is ``ICR-R22@v1``'s: what
a leaf's own managed sync *measured* when it resolved the pair, read from the durable rebinding record
and rendered on the live review read, so a review whose inputs a sync moved never reads as untouched.
``ReviewSubmission`` carries the display-only boundary: nothing here publishes an assessment.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)

__all__ = [
    "ReviewStaleness",
    "ReviewSubmission",
    "ReviewSyncMovement",
    "ReviewSyncMovementState",
]


# The four states a measured movement can hold, named once because the vocabulary, the projection and
# the sentence builder all switch on them. ``current`` is the only state that claims agreement;
# ``not-measured`` and ``unavailable`` report an absence and carry the reason behind it.
ReviewSyncMovementState = Literal["current", "stale", "not-measured", "unavailable"]


class ReviewStaleness(KnowledgeModel):
    """Whether the displayed comparison is still the candidate's comparison, and if not, what was.

    A stale payload keeps the last displayed comparison as a *labelled previous input*: the identity
    is retained and named as previous, so a reviewer can see what was reviewed while being unable to
    mistake it for a review of what is there now.

    ``not_compared`` is the task-context state and not a third flavour of current: a review opened
    from the task alone compared no knowledge operand, so there is no comparison binding that could
    be current or stale, and the response says that instead of borrowing the word for either.

    A ``stale`` state has three possible causes and the statement names which one it is: the reader
    carried a comparison identity that no longer matches (``ICR-R17@v1``), a recorded managed sync
    moved the reviewed inputs of the generation this leaf published (``ICR-R22@v1``, which
    ``ReviewSyncMovement`` reports beside it), or both. The previous input is whichever identity was
    actually displayed or reviewed, so the field never labels an identity nobody held.
    """

    state: Literal["current", "stale", "not_compared"]
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    previous_comparison_ref: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    moved: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_the_previous_input_to_be_labelled(self) -> ReviewStaleness:
        if self.state == "stale" and self.previous_comparison_ref is None:
            raise ValueError(
                "a stale comparison retains the comparison it is labelling as previous input"
            )
        if self.state != "stale" and self.previous_comparison_ref is not None:
            raise ValueError("only a stale comparison has a previous input to label")
        return self


class ReviewSyncMovement(KnowledgeModel):
    """What one leaf's managed sync measured against the comparison generation it published.

    ``ICR-R22@v1`` requires that a completed sync which carried the official line leave the review
    unable to keep reading as untouched, and this is the value the live review read renders for that:
    the generation that was measured, the inputs of it that moved, the identities the sync resolved in
    their place, and one sentence derived from those fields.

    **The vocabulary is the measured-currentness one, deliberately, and it is four-valued because the
    record it reads is three-valued.** ``binding_state`` follows from what was measured, never from a
    two-way reading of the channel matches: a retained input the sync never compared is
    ``not-measured`` (never ``current``, which would claim a measurement nobody made, and never
    ``stale``, which would fabricate one), an unusable record is ``unavailable``, a moved input is
    ``stale``, and only a record whose every retained channel was compared and matched is ``current``.
    ``record_readable``, ``reuse_permitted`` and ``reinterpreted_for_new_inputs`` carry the same three
    facts ``ICR-R15@v1`` carries, and ``reason`` names the underlying fact whenever the state is one
    that reports an absence -- the record's own verdict, the location's own state and the channel it
    belongs to. The recovery is a successor generation, which ``successor_action`` names and this
    record never performs.

    ``resolved_*`` name the pair the sync resolved, and each is present exactly when that channel was
    the one that moved -- a channel that still matches, was never compared, or could not be read has
    nothing to report here, and inventing a value for it would be the fabricated identity this
    vocabulary exists to refuse.
    """

    binding_state: ReviewSyncMovementState
    moved_identities: tuple[str, ...] = ()
    # The fact behind an absence, in the record's own words: which channel was not compared and why,
    # or why the record could not be used. Empty exactly for the two states that report a measurement.
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    generation_id: str = Field(pattern=UUID_PATTERN)
    generation_index: int = Field(ge=1)
    # The comparison identity the generation bound: the previous input a stale movement labels.
    reviewed_binding_digest: str = Field(pattern=SHA256_PATTERN)
    reviewed_candidate_code_tree_id: str = Field(pattern=GIT_OBJECT_PATTERN)
    reviewed_knowledge_logical_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    resolved_code_head: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    resolved_candidate_code_tree_id: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    resolved_knowledge_logical_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    successor_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    record_readable: bool = True
    reuse_permitted: bool = False
    reinterpreted_for_new_inputs: Literal[False] = False

    @model_validator(mode="after")
    def _the_state_follows_from_what_was_measured(self) -> ReviewSyncMovement:
        """Refuse a movement whose state disagrees with what it reports measuring.

        Every direction is checked, because each wrong combination is a different false claim: a
        named moved input is ``stale``; a ``stale`` state with nothing moved asserts movement nobody
        observed; ``current`` is the *only* state that claims agreement, so it may carry neither a
        moved input nor a reason; ``not-measured`` and ``unavailable`` are states that report an
        absence, so they must carry the reason behind it and may not claim agreement; and
        ``record_readable`` follows from whether the record could be used at all. Resolved identities
        travel only with a movement, and a stale movement must name at least one of them.
        """

        moved = bool(self.moved_identities)
        state = self.binding_state
        if moved and state != "stale":
            raise ValueError(
                f"a movement naming {len(self.moved_identities)} moved identit(y/ies) is stale, and "
                f"this one records {state}"
            )
        if state == "stale" and not moved:
            raise ValueError(
                "a stale movement names the input that moved: without one it asserts movement "
                "nobody observed"
            )
        reports_absence = state in {"not-measured", "unavailable"}
        if reports_absence and not (self.reason or "").strip():
            raise ValueError(
                f"a movement reported as {state} names the reason behind the absence: without one "
                "the reader cannot tell an uncompared input from an unreadable record"
            )
        if not reports_absence and self.reason is not None:
            raise ValueError(
                f"a movement reported as {state} carries no reason: {state} is a measurement, and a "
                "reason beside it describes an absence that was not reported"
            )
        if (state == "unavailable") == self.record_readable:
            raise ValueError(
                f"a movement reported as {state} records record_readable={self.record_readable}: "
                "only an unusable record is unreadable, and every other state was read"
            )
        named = (
            self.resolved_code_head is not None,
            self.resolved_candidate_code_tree_id is not None,
            self.resolved_knowledge_logical_digest is not None,
        )
        if state != "stale" and any(named):
            raise ValueError(
                f"a movement reported as {state} records no resolved identity: nothing moved, so "
                "there is no resolved value to name beside the reviewed one"
            )
        if state == "stale" and not any(named):
            raise ValueError(
                "a stale movement names the identities the sync resolved: without one it asserts "
                "movement without the value that moved it"
            )
        return self


class ReviewSubmission(KnowledgeModel):
    """Whether an assessment may be submitted against this comparison, and through what.

    This increment ships the review surface **display-only**, and the state says so rather than
    leaving it implicit: there is no serving route that publishes an assessment, so the surface
    reports the absence and names the existing authority that does. ``proposed_dispositions``
    publishes which judgements the authority accepts, and ``none_is_approval`` states the boundary
    the vocabulary itself enforces -- none of the three is publication approval.
    """

    state: Literal["unavailable", "disabled_stale"]
    reason: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    proposed_dispositions: tuple[str, ...] = ()
    none_is_approval: bool = True
