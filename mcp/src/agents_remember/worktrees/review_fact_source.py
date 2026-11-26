"""The memory review's fact a leaf's closeout writes into its history file.

The fact is read from the leaf's task document: its last recorded memory review round, with the
memory tree the Reviewer named, the reference to its verdict and the stamps stored with that round.
A fact is written only for a round that binds to the memory being closed:

* the last recorded memory round passed -- by its stored verdict ``pass`` or ``pass-with-notes``,
  or, for a round recorded before rounds stored their verdict, by leaving no finding open. A later
  round that blocks takes the earlier pass back;
* it names its verdict reference;
* it names a memory tree, and that tree is the one the memory checkout holds when the closeout
  closes it, captured before the closing writes anything. A pass of an earlier attempt of a
  reopened leaf, or of a tree changed since, therefore never speaks for the attempt closed now.

The Reviewer's stamp and the stamp of the agent that handed the change over are the ones stored with
the round when it was recorded; nothing here reads a tool's input or the seats registered now. A
round recorded before rounds stored either stamp gives a stamp whose values read ``unknown`` with
the reason. A leaf with no qualifying round gets no fact, and its revisions stay proposed.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from agents_remember.models.knowledge_files.history import ReviewFact
from agents_remember.models.knowledge_files.writer_stamp import WriterStamp
from agents_remember.tasks import ReviewState
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.document_stamp_owner import document_stamp_owner
from agents_remember.tasks.route_review import ReviewRound, passed_memory_round, usable_verdict_ref
from agents_remember.tasks.store import read_task_doc
from agents_remember.tasks.writer_stamps import StampOwner
from agents_remember.worktrees.memory_verdict_ref import collected_verdict_ref
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.review_handover import unknown_stamp
from agents_remember.worktrees.worktree_contract import WorktreeContract

Clock = Callable[[], str]
UNSTAMPED_ROUND = "the round was recorded before reviewers were stamped"
UNRECORDED_HANDOVER = "the round was recorded before hand-overs were stored with it"
NO_PASS_SENTENCE = (
    "No passed memory review is recorded for this leaf, so the revisions it wrote stay proposed."
)


def _clock_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def leaf_review_fact(contract: WorktreeContract, *, now: Clock = _clock_now) -> ReviewFact | None:
    """The fact of the leaf's passed memory review of the memory it closes, or ``None``.

    The leaf's task document is found by the leaf's id in the contract's task folder, as for a
    direct landing: a leaf's enclosure contract names the master's ``task.md`` as its task
    artifact, so that path never leads to the leaf's rounds.
    """

    return owner_review_fact(contract, contract.leaf_id, now=now)


def owner_review_fact(
    contract: WorktreeContract,
    owner: str,
    memory_root: Path | None = None,
    *,
    now: Clock = _clock_now,
) -> ReviewFact | None:
    """The fact for the history ``owner`` closes in ``memory_root``.

    ``memory_root`` is the memory checkout being closed, the contract's memory worktree (else its
    memory repository) when not given; its tree is captured now, before the closing writes into
    it. The owner's task document is the one in the contract's task folder whose id is ``owner``;
    an owner with no such document records no review round.
    """

    try:
        ref = TaskDocumentTopology(contract.coordination_root).ref_for_id(
            contract.repo_name, contract.task_root, owner
        )
    except TaskDocumentRefError:
        return None
    leaf_json = TaskDocumentTopology(contract.coordination_root).path_for_ref(ref)
    root = memory_root or contract.memory_worktree or contract.memory_repo_path
    return _review_fact(
        leaf_json,
        _closed_tree(root),
        now,
        worktree_group=contract.worktree_group,
        task_root=contract.task_root,
    )


@dataclass(frozen=True)
class _Stamps:
    owner: StampOwner
    at: str


def qualifying_round(state: ReviewState | None, closed_tree: str | None) -> ReviewRound | None:
    """The passing round for the exact tree while the lane has no open findings."""

    if (
        state is None
        or state.remainingFindingIds
        or not state.recordedRounds
        or closed_tree is None
    ):
        return None
    last = state.recordedRounds[-1]
    if not passed_memory_round(last) or usable_verdict_ref(last.verdictRef) is None:
        return None
    return last if last.memoryTree == closed_tree else None


def _review_fact(
    leaf_json: Path,
    closed_tree: str | None,
    now: Clock,
    *,
    worktree_group: Path | None = None,
    task_root: Path | None = None,
) -> ReviewFact | None:
    try:
        document = read_task_doc(leaf_json)
    except FileNotFoundError:
        return None
    round_ = qualifying_round(document.memoryReviewState, closed_tree)
    if round_ is None:
        return None
    raw_ref = usable_verdict_ref(round_.verdictRef)
    assert raw_ref is not None
    verdict_ref = collected_verdict_ref(
        raw_ref,
        worktree_group=worktree_group,
        task_root=task_root,
    )
    stamps = _Stamps(document_stamp_owner(leaf_json.parent, document), now())
    return ReviewFact.model_validate(
        {
            "round": round_.round,
            "verdictRef": verdict_ref,
            "tree": round_.memoryTree,
            "reviewer": _reviewer(round_, stamps).to_document(),
            "handedOverBy": _handed_over_by(round_, stamps.owner).to_document(),
        }
    )


def _reviewer(round_: ReviewRound, stamps: _Stamps) -> WriterStamp:
    if round_.recordedBy is not None:
        return WriterStamp.model_validate(round_.recordedBy)
    return unknown_stamp(stamps.owner, UNSTAMPED_ROUND, stamps.at)


def _handed_over_by(round_: ReviewRound, owner: StampOwner) -> WriterStamp:
    if round_.handedOverBy is not None:
        return WriterStamp.model_validate(round_.handedOverBy)
    return unknown_stamp(owner, UNRECORDED_HANDOVER)


def _closed_tree(root: Path | None) -> str | None:
    """The Git tree ``root``'s working files make, as a commit of all of them would record it.

    The capture uses a throwaway index outside the checkout, so the checkout and its own index are
    not changed. ``None`` when the tree cannot be captured: then no round can be shown to name it.
    """

    if root is None or not root.is_dir():
        return None
    try:
        with TemporaryDirectory(prefix=".review-fact-memory-") as temporary:
            return worktree_candidate_tree(root, Path(temporary) / "index")
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None
