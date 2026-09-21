"""Which exact Git objects a *committed* change-set range binds, and what to say when one is absent.

The committed views answer one question -- "what did this leaf land?" -- and that question has one
honest answer: the range between the two commits the enclosure contract **recorded**, the base it
forked from and the commit its closeout or integration wrote. Those two cell values are immutable
task facts; a branch tip and a worktree ``HEAD`` are not. ``HEAD`` advances with every ordinary
commit the task makes, so binding it would publish a range that is not the leaf's landed delta under
a label that says it is, and would answer differently on the next poll of the same URL.

A side whose recorded endpoint is absent is therefore reported as **absent, by name**: nothing has
recorded that side's landed commit yet, so the committed range does not exist yet. No ``HEAD``, no
branch and no working tree is substituted for it. The *uncommitted* view keeps its own name
(``working``) and its own live-worktree requirement, so "what is not committed yet" stays available
and stays labelled as exactly that.

The recorded objects are also required to be resolvable in the repository the range is read from: a
recorded commit this checkout does not hold is a named absence too, rather than an unhandled Git
failure escaping the route.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "NOT_RECORDED",
    "RecordedEndpointAbsenceKind",
    "RecordedEndpointAbsent",
    "RecordedRange",
    "recorded_committed_range",
]

# The side names a refusal uses. They are the two halves the change-set shape already publishes, so
# a reader who is told which side is missing reads the same word the payload uses for it.
CODE_SIDE = "code"
MEMORY_SIDE = "memory"

# Why one side's committed range could not be read. The three are separated because a caller may act
# on them differently: an endpoint nothing has written yet leaves a *half* with nothing to show,
# while the other two are broken state that no view may report as an empty range.
RecordedEndpointAbsenceKind = Literal["not-recorded", "no-repository", "unresolvable"]

# The one absence a half may degrade to "nothing to show" for: nothing has recorded that side's
# landed commit yet, so the range does not exist yet rather than existing and being unreadable.
NOT_RECORDED: RecordedEndpointAbsenceKind = "not-recorded"


@dataclass(frozen=True)
class RecordedRange:
    """One side's committed range, exactly as the enclosure contract recorded it.

    ``repository`` is where the two objects are read from -- the code repository for the code side,
    the memory repository for the memory side -- and it is carried with the commit ids because a
    commit id without the repository that holds it is not resolvable.
    """

    side: str
    repository: Path
    base_commit: str
    head_commit: str


class RecordedEndpointAbsent(FileNotFoundError):
    """One recorded committed endpoint is missing, so the committed range has no second object.

    It is a ``FileNotFoundError`` because that is the type the change-set routes already map to a
    404 naming what could not be found; it is its own type because the *reason* -- nothing has
    recorded this side's landed commit yet -- is a fact about the task's progress rather than about
    a path, and the message names the side and the action that produces the endpoint.

    ``kind`` keeps the three reasons apart for a caller that carries a second, independently resolved
    half: only :data:`NOT_RECORDED` is an endpoint that may still be written, so only it may leave one
    half with nothing to show. ``no-repository`` and ``unresolvable`` are broken state, and a view
    that reported either as an empty range would be publishing a measurement it never made.
    """

    def __init__(self, detail: str, *, kind: RecordedEndpointAbsenceKind = NOT_RECORDED) -> None:
        self.kind = kind
        super().__init__(detail)


def recorded_committed_range(contract: WorktreeContract, *, memory: bool) -> RecordedRange:
    """One side's recorded committed range, or the named refusal an unrecorded endpoint earns.

    ``code_commit`` and ``memory_content_commit`` are written by closeout; ``integrated_code_commit``
    and ``integrated_memory_content_commit`` are written when the task's line lands into its source
    branch. Either is *recorded*, and either therefore binds the range exactly; the closeout cell is
    preferred only because it is the one a comparison against the working view is made against.
    """

    side = MEMORY_SIDE if memory else CODE_SIDE
    repository = contract.memory_repo_path if memory else contract.code_repo_path
    base, head = _recorded_commits(contract, memory=memory)
    if repository is None:
        raise RecordedEndpointAbsent(
            f"the contract names no {side} repository for leaf {_leaf(contract)}, so this side has "
            "no committed range to read; the change-set publishes nothing for a side the task does "
            "not run",
            kind="no-repository",
        )
    if not head or not base:
        missing = "landed commit" if not head else "base commit"
        raise RecordedEndpointAbsent(
            f"the contract records no {side} {missing} for leaf {_leaf(contract)}, so this leaf has "
            f"no committed {side} range yet: the committed view reads the two recorded commits and "
            "substitutes no HEAD, branch or working tree for either. Read the uncommitted view "
            "(mode=working) while the task is live, or reopen this view after closeout records the "
            "range",
            kind=NOT_RECORDED,
        )
    unresolvable = _unresolvable(repository, base, head)
    if unresolvable is not None:
        raise RecordedEndpointAbsent(
            f"the recorded {side} committed range for leaf {_leaf(contract)} names "
            f"{unresolvable}, which the {side} repository does not hold, so the committed "
            "change-set cannot be read from the recorded endpoints and no branch or working tree is "
            "substituted for them",
            kind="unresolvable",
        )
    return RecordedRange(side=side, repository=repository, base_commit=base, head_commit=head)


def _recorded_commits(contract: WorktreeContract, *, memory: bool) -> tuple[str, str]:
    """The recorded ``(base, head)`` commit cells for one side, unvalidated."""

    if memory:
        return (
            contract.memory_base_commit,
            contract.memory_content_commit or contract.integrated_memory_content_commit,
        )
    return (
        contract.code_base_commit,
        contract.code_commit or contract.integrated_code_commit,
    )


def _leaf(contract: WorktreeContract) -> str:
    """The leaf a refusal names, falling back to the task id for a contract with no leaf id."""

    return contract.leaf_id or contract.task_id


def _unresolvable(repository: Path, *commits: str) -> str | None:
    """The first recorded commit this repository cannot resolve, or ``None`` when all resolve."""

    for commit in commits:
        if run_git(repository, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
            return commit
    return None
