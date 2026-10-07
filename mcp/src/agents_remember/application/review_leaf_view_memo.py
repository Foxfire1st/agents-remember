"""A bounded in-process memo of the reviewer's leaf-wide view over exact trees (MIK-R40 rule 4).

The leaf-wide tree view is asked for once when the reviewer opens. Its two expensive parts are pure
functions of exact identities: the Git diff of the two memory trees, and the leaf's worklist over
the four trees of the comparison. Computing them takes seconds and, in the same Python process,
slows every read a click issues meanwhile. The memo keeps both parts, so the view is computed once
per comparison and a repeat for unchanged trees costs the captures and the lookups only.

**The key names everything the computation reads.** It is the gate's own key
(:func:`agents_remember.application.knowledge_gate.memo.memo_key`) plus the comparison's base trees:

* the exact captured code tree C and memory tree K_C (content-addressed Git tree IDs);
* the contract's identity: its path and the SHA-256 of its bytes, which carry B and every branch;
* the parent line's memory tip (the line K_B pairs on) and the leaf's own memory ``HEAD``;
* the SHA-256 of the leaf's task document (its maintenance scope and declared effects);
* the bytes of the task documents the lookup actually consumed for this leaf (``task_reads``);
* the build that computed it (``measuring_build_stamp``);
* the code base tree, K_B's tree, and the tree K_B is compared as (its conversion when K_B is
  unconverted), exactly as the comparison record names them;
* the exact JSON bytes the strict task lookup parsed while establishing that request identity.

**Inputs outside the trees** are handled as the gate's memo handles them: every plain file the
computation read is recorded with the SHA-256 of the bytes read
(:func:`agents_remember.kernel.recorded_reads.recorded_reads`), and before a kept view is served
each recorded file is hashed again. Any difference is a miss, and the view is computed again.
Actual locator and root selections are recorded too and checked before byte rows, so a retargeted
locator cannot reuse its old judgment or cause a byte read outside its original confinement.

**What is never kept.** A view whose worklist is not ``complete``, a view with an unreadable
knowledge side, a diff behind which a Git read failed, and a computation that read one file twice
with different contents. The next request reads again.

The task reader records the bytes it actually parses, including both worklist lookups. Before the
view is returned or kept, those observations must equal the request's task-input snapshot, and
every observed dependency must still have its observed bytes. A mismatching computation is tried
once more; two mismatching attempts refuse instead of returning or keeping a raced view.

**Only what the view read** (MIK-R42). The strict task lookup records the leaf's own document and
nothing it merely ruled out when a claimant is established
(:func:`agents_remember.tasks.leaf_decisions.strict_leaf_doc`), so a
write to another leaf's document, a new document or an edit of the master's own ``task.json``
neither changes the key nor fails a kept view's recheck. A document the computation read that the
key did not identify, or read with other bytes, is a change; a document the key identified that the
computation did not read at all is not (:func:`moved_inputs`).
When the lookup runs and finds no claimant it records all opened documents and its JSON listing;
this is evidence of absence, not a computation that never performed the lookup.

**Bounds.** At most :data:`CAPACITY` views are kept, least recently used first out, and none is
served after :data:`MAX_AGE_SECONDS`. The memo lives in the process that serves the reviewer: a
restart empties it, nothing is written to disk for it (MIK-R25 forbids review copies), and it is
not the gate's memo, whose key and rules are unchanged. It is a working surface, never a record.

**What it does not replace.** Every read of an open leaf still captures both worktrees before it
looks here (MIK-R23 rule 5), so an edit is a new tree and therefore a new key. No composed review
is kept anywhere on the server.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agents_remember.application.knowledge_gate import memo as gate_memo
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.kernel.recorded_reads import (
    ABSENT,
    changed_observations,
    has_failed_observation,
    recorded_reads,
    replay_reads,
)
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeTreeDiff,
    ReviewWorklistView,
)
from agents_remember.worktrees.knowledge_gate import parent_memory_tip
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.application.review_tree_comparison import ReviewTrees

__all__ = [
    "CAPACITY",
    "LEAF_VIEW_MEMO",
    "MAX_AGE_SECONDS",
    "LeafViewKey",
    "LeafViewParts",
    "leaf_view_key",
    "moved_inputs",
    "remember",
    "remembered",
]

CAPACITY: Final = 4
MAX_AGE_SECONDS: Final = gate_memo.MAX_AGE_SECONDS


@dataclass(frozen=True)
class LeafViewKey:
    """Everything the leaf-wide view's kept parts are a function of, as exact identities."""

    gate: gate_memo.GateMemoKey
    code_base: str
    memory_base: str
    memory_before: str
    task_root: str
    task_reads: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LeafViewParts:
    """The two computed parts of one leaf-wide view: the knowledge diff and the worklist view."""

    knowledge_diff: ReviewKnowledgeTreeDiff | None
    worklist: ReviewWorklistView


@dataclass(frozen=True)
class _Kept:
    """One kept view: when it was computed, and the files outside the trees it read, as read."""

    at: float
    parts: LeafViewParts
    reads: tuple[tuple[str, str], ...]

    def still_read_the_same(self) -> bool:
        return not changed_observations(dict(self.reads))


LEAF_VIEW_MEMO: Final[BoundedMemo[LeafViewKey, _Kept]] = BoundedMemo(CAPACITY)
"""The kept views of the process that serves the reviewer."""


def leaf_view_key(contract: WorktreeContract, trees: ReviewTrees) -> LeafViewKey | None:
    """The key of one live comparison's view, or ``None`` when an input cannot be identified.

    Without a key nothing is looked up and nothing is kept: the view is computed, as it always was.
    """

    record = trees.record
    worktree = contract.memory_worktree
    if not trees.live or worktree is None:
        return None
    try:
        parent_tip = parent_memory_tip(contract)
        leaf_head = head_commit(worktree)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None
    if parent_tip is None:
        return None  # the line K_B pairs on cannot be named, so the view is not a function of a key
    with recorded_reads() as reads:
        gate = gate_memo.memo_key(
            contract,
            CandidateTrees(code=record.code_candidate.tree, memory=record.memory_candidate.tree),
            parent_tip,
            leaf_head,
        )
    if gate is None:
        return None
    return LeafViewKey(
        gate=gate,
        code_base=record.code_base.tree,
        memory_base=record.memory_base.tree,
        memory_before=trees.before.wire.tree,
        task_root=contract.task_root.resolve().as_posix(),
        task_reads=_task_reads(contract.task_root.resolve().as_posix(), reads),
    )


def remembered(key: LeafViewKey, *, now: float | None = None) -> LeafViewParts | None:
    """The parts kept for ``key`` if they are young enough and every recorded file reads the same."""

    held = LEAF_VIEW_MEMO.get(key)
    if held is None:
        return None
    kept = held[0]
    if (time.monotonic() if now is None else now) - kept.at > MAX_AGE_SECONDS:
        return None
    if not kept.still_read_the_same():
        return None  # a file outside the trees changed since: compute again
    replay_reads(dict(kept.reads))
    return kept.parts


def moved_inputs(key: LeafViewKey | None, reads: dict[str, str]) -> tuple[str, ...]:
    """Actual reads that differ from the request's task inputs or from their bytes now.

    A task document that the key named and the computation never opened is *not read*, which is
    not a change (a worklist that applies to no leaf stops before it reads the task).
    """

    # L40-R1-F3: a task can change and return to its original bytes during the computation.
    # Compare the bytes actually parsed by both lookups with the request's input snapshot.
    expected, actual = (
        ({}, {}) if key is None else (dict(key.task_reads), dict(_task_reads(key.task_root, reads)))
    )
    moved = {
        path for path, seen in actual.items() if path not in expected or expected[path] != seen
    }
    moved.update(changed_observations(reads))
    return tuple(sorted(moved))


def _task_reads(root: str, reads: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (path, identity)
            for path, identity in reads.items()
            if Path(path).parent.as_posix() == root and Path(path).suffix == ".json"
        )
    )


def remember(key: LeafViewKey, parts: LeafViewParts, reads: dict[str, str]) -> None:
    """Keep the parts of a view over complete inputs, with the files it read outside the trees."""

    if (
        has_failed_observation(reads)
        or ABSENT in dict(_task_reads(key.task_root, reads)).values()
        or moved_inputs(key, reads)
    ):
        return
    LEAF_VIEW_MEMO.put(key, _Kept(time.monotonic(), parts, tuple(sorted(reads.items()))))
