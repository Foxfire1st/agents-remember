"""The memory cutover preflight of one sync: judge the exact sides before anything moves.

A memory side that would leave or bypass the text format is refused by name
before any ref, journal or branch moves.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.cutover_lock import (
    CUTOVER_LOCK_CODE,
    legacy_format_refusal,
)
from agents_remember.worktrees.knowledge_crossing import CrossingSyncError, merge_base
from agents_remember.worktrees.knowledge_validation import (
    LayoutProbeError,
    has_layout_marker,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_authority import command_result
from agents_remember.worktrees.sync_transaction_state import SyncSideRecord


def sync_side_outcome(repository: Path, memory: SyncSideRecord) -> tuple[str, str]:
    """How the preflight judges the sync's exact sides, with the judged sides named.

    Returns the outcome (``admit``, ``legacy`` or ``out-of-format``) and the exact
    judged sides as ``preSyncHead <sha>, sourceCommit <sha>`` plus
    ``merge base <sha>`` for a merge, so refusals name what was judged.
    ``admit``: already current or ``skip-memory`` never reach here; a fast-forward that takes a
    converted source converts by taking it; a merge with a converted side is the crossing sync;
    all converted sides run as today. ``legacy``: a fast-forward of two unconverted sides, or a
    merge of three unconverted sides, refuses with the one ``legacy-format`` message.
    ``out-of-format``: a fast-forward from an unconverted source onto a converted target refuses,
    since the source would take the line out of the text format. Probes may raise
    :class:`LayoutProbeError` or :class:`CrossingSyncError`, which refuse by name.
    """

    if memory.plan == "merge":
        base = merge_base(repository, memory.preSyncHead, memory.sourceCommit)
        sides = (
            f"preSyncHead {memory.preSyncHead}, sourceCommit {memory.sourceCommit}, "
            f"merge base {base}"
        )
        if any(
            has_layout_marker(repository, side)
            for side in (base, memory.preSyncHead, memory.sourceCommit)
        ):
            return "admit", sides  # the crossing sync into the text format, or all converted
        return "legacy", sides  # the plain Git merge of memory is gone
    sides = f"preSyncHead {memory.preSyncHead}, sourceCommit {memory.sourceCommit}"
    states = {
        side: has_layout_marker(repository, side)
        for side in (memory.preSyncHead, memory.sourceCommit)
    }
    if all(states.values()):
        return "admit", sides
    if not states[memory.preSyncHead] and states[memory.sourceCommit]:
        return "admit", sides  # the line converts by taking the converted source
    if states[memory.preSyncHead] and not states[memory.sourceCommit]:
        return "out-of-format", sides
    return "legacy", sides


def cutover_locked(
    memory: SyncSideRecord | None, fetch: dict[str, object]
) -> WorktreeCommandResult | None:
    """Judge the exact sides of the sync before anything moves.

    The preflight probes ``preSyncHead``, ``sourceCommit`` and, for a merge, their merge base
    (:func:`sync_side_outcome`):

    * already current, or ``skip-memory``: any sides, as today (no memory side moves);
    * fast-forward, target unconverted and source converted: admitted, as today -- the line
      converts by taking the converted source;
    * fast-forward, both unconverted: refused, ``legacy-format``;
    * fast-forward, target converted and source unconverted: refused -- the source would take
      the line out of the text format;
    * merge, base/own/incoming all unconverted: refused, ``legacy-format`` -- the plain Git
      merge of memory is gone;
    * merge, at least one unconverted and one converted: the crossing sync, as today;
    * fast-forward or merge, all converted: as today.

    A sync that was already in flight is continued or cancelled, as today. A probe Git cannot
    answer refuses by name and is never read as converted or unconverted.
    """

    if memory is None or memory.plan in {"already-current", "skip"}:
        return None
    repository = Path(memory.repository)
    line = f"{memory.workBranch} ({memory.worktree})"
    try:
        outcome, sides = sync_side_outcome(repository, memory)
    except (LayoutProbeError, CrossingSyncError) as error:
        return command_result(
            2,
            f"sync-{CUTOVER_LOCK_CODE}",
            "The memory sync is refused: it cannot tell whether a memory side holds the layout "
            f"marker ({error}), and an unanswered probe is never read as converted or unconverted "
            "memory",
            fetch,
        )
    if outcome == "admit":
        return None
    if outcome == "out-of-format":
        return command_result(
            2,
            f"sync-{CUTOVER_LOCK_CODE}",
            "The memory sync is refused: its source is unconverted and would take a "
            "converted line out of the text format.",
            fetch,
            detail=legacy_format_refusal(
                repository,
                operation="worktree_sync",
                subject=(
                    f"the memory sync of {line} ({sides}): its target holds converted memory "
                    "(knowledge/layout.json) and its source does not, so a fast-forward "
                    "would take the line out of the text format -- convert the source "
                    "line first"
                ),
            ),
        )
    detail = legacy_format_refusal(
        repository,
        operation="worktree_sync",
        subject=f"the memory sync of {line} ({sides})",
    )
    return command_result(
        2,
        f"sync-{CUTOVER_LOCK_CODE}",
        "The memory sync is refused: its memory is unconverted and must cross the boundary first.",
        fetch,
        detail=detail,
    )
