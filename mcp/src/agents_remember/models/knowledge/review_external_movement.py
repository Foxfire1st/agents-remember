"""What a review can say about an identity a *raw* Git operation moved under it (ICR-R23@v1).

A comparison generation records the identities a review bound:
:mod:`agents_remember.application.review_comparison_generation` seals the work-branch head and the
code-base commit the capture was taken from, and the code tree and knowledge dataset the comparison
was between. Managed work then moves those identities through exactly one route -- ``worktree_sync``
-- which *measures* what it moved and writes that measurement beside the generation (ICR-R22@v1,
:mod:`agents_remember.models.knowledge.review_sync_rebinding`).

Nothing obliges a repository to use that route. ``git rebase``, ``git cherry-pick``, ``git revert``
and a branch or worktree switch are ordinary Git, they rewrite or replace the very refs the sealed
manifest names, and they leave no rebinding record behind because no managed transaction ran. A
reader holding only "a generation was frozen" cannot tell that state from an untouched one, which is
the packet's non-conforming example: attribution that is old is shown as current solely because its
record still exists on disk.

:class:`ExternalGitMovement` is the value such a boundary reports. It is deliberately **not** an
attempt to classify the Git command a person ran: Git leaves no such record, and a module that
guessed one would be inventing a measurement. It reports what *was* measured -- which declared
identities no longer appear in the repository, and which transition shape that is -- beside the
reconciliation routes this system does and does not have.

**Four states, and only one of them claims the identities still stand.** ``current`` says every
declared identity that could be compared is still there, ``stale`` says a declared identity was
replaced or the worktree left its declared branch, ``not-measured`` says the comparison could not be
taken at all, and ``unavailable`` says the generation itself could not be read. A movement never
carries a reason, because it is a measurement; an absence always carries one, because a reader
otherwise cannot tell an unperformed check from an unaffected repository.

**What is supported is a field, not a tone of voice.** ``reconciliation`` is ``supported`` on exactly
the transitions whose recovery this system actually performs -- the successor generation
:mod:`agents_remember.application.review_comparison_freeze` publishes from the reviewed generation --
and ``unsupported`` on every other one, with ``recovery_action`` naming what a person must do. The
matrix those labels come from is
:data:`agents_remember.application.review_external_git_movement.GIT_TRANSITION_SUPPORT`, and it is
rendered verbatim into ``docs/reference/worktrees-c09.md``; ``moved_identities`` is the field that
names the exact identity replaced, and ``unsupported`` repeats the matrix's unsupported verdicts so
that no reader can read this value and conclude that an unsupported transition was handled.

Nothing here writes, reconciles, re-freezes or deletes: the reviewed generation stays exactly where
it is, which is the packet's boundary example -- an exact historic generation stays inspectable while
live recovery is pending.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    GIT_OBJECT_PATTERN,
    PROSE_MAX_LENGTH,
    SHA256_PATTERN,
    UUID_PATTERN,
    KnowledgeModel,
)
from agents_remember.models.knowledge.review_staleness import ReviewSyncMovementState

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
    ``recovery_action`` is the step a person takes -- the successor generation the freeze owner
    publishes, whose lineage names this one as its predecessor -- and this value performs none of it.
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
    generation_id: str = Field(pattern=UUID_PATTERN)
    generation_index: int = Field(ge=1)
    reviewed_binding_digest: str = Field(pattern=SHA256_PATTERN)
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
