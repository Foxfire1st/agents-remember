"""Exact Git mutations and proofs for resumable code and memory-content sync.

The memory side excludes its root memory.md cache from candidate, conflict, and merge-tree
identity. Code-side files keep ordinary Git semantics, including a file named memory.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: How many paths a refusal names inline; the full set always reaches the log.
MAX_NAMED_SYNC_PATHS = 10


@dataclass(frozen=True)
class ExpectedReappliedContent:
    """What the reapply of one parked path onto the carried head gives.

    ``kind`` is ``"bytes"`` (the worktree must hold ``content``), ``"absent"``
    (the path must be gone), ``"uncomputable"`` (no construction proves it: the
    proof fails the path and the cancel leads back), or ``"unreadable"`` (the
    live worktree entry, never an expectation: a directory or device where a
    file belongs, which matches nothing).
    """

    kind: str
    content: bytes = b""


@dataclass(frozen=True)
class CancelTreeAssessment:
    """Whether a cancel may discard one side's tree before rolling its head back.

    ``later_work`` names dirty paths no stash and no commit holds: the cancel saves
    them first (MIK-R61 rule 12) and names the saved files, instead of refusing
    over bytes it cannot attribute. ``unproven_paths`` names dirty paths no
    reference shows to be the transaction's own after an interrupted step: where no
    later work stands beside them the cancel saves them first (MIK-R61 rule 12) and
    names the saved files, instead of refusing over bytes it cannot attribute.
    ``owned_untracked`` names untracked paths the recorded stash holds, removed
    explicitly because ``reset --hard`` keeps them. ``needs_discard`` says owned
    dirt is present at all. ``merge_fault`` holds git's words where it could not
    make the reapply's merge again: a path both sides changed then cannot be shown
    to be the reapply's own and stands in ``later_work``, and the refusal says why.
    """

    later_work: tuple[str, ...] = ()
    owned_untracked: tuple[str, ...] = ()
    unproven_paths: tuple[str, ...] = ()
    needs_discard: bool = False
    merge_fault: str = ""


#: Blob modes whose reapply the proof computes; a symlink or submodule change the
#: proof cannot compute fails its path instead of passing it.
_REGULAR_BLOB_MODES = frozenset({"100644", "100755"})

#: A full object name, as ``git merge-tree`` writes the merged tree's.
_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class SyncGitProofError(RuntimeError):
    """Live Git state cannot be attributed exactly to the journaled sync."""


class SyncKnowledgeValidationError(SyncGitProofError):
    """The knowledge validator (MIK-R22) refuses the staged memory merge; it stays staged."""


class SyncFastForwardValidationError(SyncKnowledgeValidationError):
    """The knowledge validator (MIK-R22) refuses a fast-forward's incoming tree.

    Raised before anything moves (MIK-R61 rule 4): the violation is on the source line,
    so the leaf's refs never move and the way on is repairing that line and syncing again.
    """


@dataclass(frozen=True)
class SideMergeOutcome:
    """What one side's merge attempt reached.

    ``state`` is ``completed`` or ``resolution-required``; ``conflicts`` names the paths still
    unmerged; ``message`` is Git's own text for a conflict. ``crossing_report`` is the path of the
    structural knowledge merge's report, when it left items for the curator or converted a side.
    ``commit_check`` carries the repository's commit-check words when the content merged but
    the commit -- and the pre-commit check it runs -- did not (MIK-R61 rule 12).
    """

    state: str
    conflicts: tuple[str, ...] = ()
    message: str = ""
    crossing_report: str = ""
    commit_check: str = ""
