"""Source-bound raw Git ancestry on retained review trees (ICR-R23).

Version 2 carries the complete tree comparison and its canonical digest. Candidate/base commits
can be checked for ancestry; an uncommitted tree pin supplies no observed work-branch head and
that channel stays not-measured. A missing comparison carries no invented UUID or zero digest.
Historical v1 shapes retain their required generation identity for read-only evidence decoding.

The report distinguishes current, stale, not-measured and unavailable observations. It does not
identify which Git command ran or reconcile it. The application owner's support matrix names the
unsupported transitions and the exact recovery instructions; nothing in this vocabulary writes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PROSE_MAX_LENGTH,
    SHA256_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.review_final_output_receipt import tree_comparison_digest
from agents_remember.models.knowledge.review_staleness import ReviewSyncMovementState
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "ExternalGitMovement",
    "ExternalGitTransition",
    "GitMovementChannel",
    "GitMovementEvidence",
    "GitTransitionReconciliation",
]


# The shapes a repository's own Git history can show, plus the value for observing none of them. Each
# names the *shape* measured, not the command a person ran: Git keeps no record of the latter, so a
# value claiming it would be a guess. ``unchanged`` is not a transition -- it is the measured absence
# of one, published exactly when every declared identity still stands at its record, so that the
# commonest state of all cannot be labelled with an event that did not happen.
ExternalGitTransition = Literal[
    "unchanged",
    "ordinary-append",
    "rebase",
    "cherry-pick",
    "revert",
    "branch-switch",
]

# Whether this system reconciles a transition at all. ``supported`` is earned by the one recovery it
# performs -- the successor generation the freeze owner publishes from the reviewed one -- and it is
# never a claim that the raw operation itself was handled.
GitTransitionReconciliation = Literal["supported", "unsupported"]

# The declared identities a boundary compares, each named by the thing that moved rather than by the
# command that moved it: the code work branch's recorded head, the code-base commit the generation
# was captured from, and the memory work branch's recorded base. The recorded knowledge *dataset*
# identity is measured by the managed-sync rebinding (ICR-R22@v1) and is not restated here.
GitMovementChannel = Literal["code-work-branch", "declared-source-branch", "memory-work-branch"]

# What one channel's ancestry check found. ``advanced`` is a measurement of its own: the recorded
# identity is still an ancestor of the branch, so the branch moved forward without replacing it --
# which is the state a rebase and a cherry-pick of the same content are told apart by, and the reason
# this vocabulary needs three measured values rather than a boolean.
GitMovementEvidence = Literal["current", "advanced", "replaced", "unavailable"]


class ExternalGitMovement(KnowledgeModel):
    """One boundary's report: which declared identities were replaced, and what recovery exists.

    ``binding_state`` reuses the measured-currentness vocabulary
    :class:`~agents_remember.models.knowledge.review_staleness.ReviewSyncMovement` publishes, because
    a reader of either value must read the same four words the same way -- and because R15's
    currentness rules are the ones this packet's failure clause names: an unrecognized transition may
    not reuse stale assessment as current, so ``stale`` is the only state a replaced identity earns
    and it is rendered wherever a reader could otherwise act on the comparison.

    ``transitions`` names every shape observed, so a rebase and a switch that happened together are
    both reported rather than one being chosen. It carries ``unchanged`` -- and nothing else -- when
    every declared identity was compared and every one of them still stands at its record, which is
    the state every ordinary review of an untouched leaf is in: naming that state
    ``ordinary-append`` would assert an advance that did not happen. It is **empty** when no shape was
    observed and something could not be compared, which is the honest answer for a boundary that
    measured nothing. ``transition_evidence`` carries the per-channel measurement beside them, so
    ``advanced`` -- a branch that moved forward without replacing the recorded commit -- is
    distinguishable from ``current`` without reading a sentence.

    ``observed_*`` carry the branch heads the check actually read, and they exist to locate the
    replacement, never to substitute for the recorded identity: a channel that could not be read
    reports ``unavailable`` in ``transition_evidence`` and carries no observed value at all.
    ``recovery_action`` is the step a person takes -- a successor live review that records the exact code and memory tree comparison -- and this value performs none of it.
    """

    binding_state: ReviewSyncMovementState
    transitions: tuple[ExternalGitTransition, ...] = ()
    transition_evidence: tuple[tuple[GitMovementChannel, GitMovementEvidence], ...] = ()
    # The exact replacement, spelled ``channel:identity`` for the identity that was replaced. Present
    # exactly when something was, which is what makes ``stale`` a measurement rather than a verdict.
    moved_identities: tuple[str, ...] = ()
    # The fact behind an absence, in the boundary's own words. Empty exactly for the states that
    # report a measurement, so an unreadable generation can never read as an unaffected repository.
    reason: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)
    statement: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    movement_version: Literal["ar-review-external-movement/v2"] = "ar-review-external-movement/v2"
    reviewed_binding_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    comparison: ReviewTreeComparisonRecord | None = None
    declared_work_branch: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    observed_code_work_branch_head: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    observed_declared_source_branch_head: str | None = Field(
        default=None, pattern=GIT_OBJECT_PATTERN
    )
    observed_memory_work_branch_head: str | None = Field(default=None, pattern=GIT_OBJECT_PATTERN)
    successor_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    # The matrix's unsupported verdicts, repeated where the reader is: the transitions this system
    # does not reconcile. Empty is not "none exist" -- it is "this report has none to name", and the
    # documented matrix is the authority on which transitions those are.
    unsupported: tuple[str, ...] = ()
    generation_readable: bool = True

    @model_validator(mode="after")
    def _validate_source_comparison(self) -> ExternalGitMovement:
        if self.binding_state != "unavailable" and self.comparison is None:
            raise ValueError("a measured tree movement carries its source comparison")
        if self.comparison is not None and self.reviewed_binding_digest != tree_comparison_digest(
            self.comparison
        ):
            raise ValueError("the digest must describe the complete source comparison")
        return self

    @model_validator(mode="after")
    def _the_state_follows_from_what_was_measured(self) -> ExternalGitMovement:
        """Refuse a report whose state disagrees with what it says it measured.

        Each direction is a different false claim. A named replacement is ``stale``; a ``stale``
        report with nothing replaced asserts movement nobody observed; ``current`` is the only state
        that claims the declared identities still stand, so it may carry neither a replacement nor a
        reason; ``not-measured`` and ``unavailable`` are the two states that report an absence, so
        both must carry the reason behind it; ``generation_readable`` follows from whether the
        generation could be read at all, so nothing can report an unreadable generation beside a
        measurement taken from it; and ``unchanged`` is the measured absence of a transition, so it
        cannot travel beside a movement, beside another shape, or in a state that is not ``current``.
        """

        moved = bool(self.moved_identities)
        state = self.binding_state
        if moved and state != "stale":
            raise ValueError(
                f"a movement naming {len(self.moved_identities)} replaced identit(y/ies) is stale, "
                f"and this one records {state}"
            )
        if state == "stale" and not moved:
            raise ValueError(
                "a stale movement names the declared identity that was replaced: without one it "
                "asserts movement nobody observed"
            )
        reports_absence = state in {"not-measured", "unavailable"}
        if reports_absence and not (self.reason or "").strip():
            raise ValueError(
                f"a movement reported as {state} names the reason behind the absence: without one a "
                "reader cannot tell an unperformed check from an unaffected repository"
            )
        if not reports_absence and self.reason is not None:
            raise ValueError(
                f"a movement reported as {state} carries no reason: {state} is a measurement, and a "
                "reason beside it describes an absence that was not reported"
            )
        if state == "unavailable" and self.generation_readable:
            raise ValueError(
                "a movement reported as unavailable records generation_readable=True: only an "
                "unreadable generation is unavailable, and every other state was read"
            )
        if "unchanged" in self.transitions and (
            state != "current" or moved or len(self.transitions) != 1
        ):
            raise ValueError(
                "a movement recording 'unchanged' reports that every declared identity still "
                "stands at its record and that no other shape was observed, so it cannot travel "
                f"beside state {state}, {len(self.moved_identities)} replaced identit(y/ies) or "
                f"the other shapes {tuple(t for t in self.transitions if t != 'unchanged')}"
            )
        return self
