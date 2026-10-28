"""An invariant's cards carried to a newer comparison when everything they were made from holds (D574).

A card read names one comparison (``trees?comparison=N&invariants=K``), so a newer comparison of
the same leaf used to compute every invariant's cards again, though most of them changed nothing.
The cards of K are its entries on both sides with their memory values, around the comparison's own
record and sides. Every read the entries were made from is one of:

* the two memory trees and the converted base: the knowledge index K's entries come from, their
  memory values and their memory-side currentness;
* the code blob at each path an entry names, in the code base tree (the before side) and the code
  candidate tree (the after side): the excerpt, the lines, the change and the code-side
  currentness are all read from that blob.

So when a newer comparison has the same three memory trees and the same blob -- or the same absence
-- at every path K's entries name, on each side, K's entries re-observe identical (D570) and are
carried: the newer comparison's answer is its own record and sides around the kept entries. Any other
difference computes them again. The invariant's review cannot be carried this way: it names its
comparison's trees.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
    ReviewTreesResult,
)

__all__ = ["CARRY_CAPACITY", "CardCarry", "carried_entries"]

logger = logging.getLogger(__name__)

#: How many invariants' last cards are held for a carry; the least recently made is computed again.
CARRY_CAPACITY = 2048

type _Leaf = tuple[str, str, str]


def _memory_trees(record: ReviewTreeComparisonRecord) -> tuple[str, str, str | None]:
    converted = record.converted_base.tree if record.converted_base is not None else None
    return (record.memory_base.tree, record.memory_candidate.tree, converted)


def _blobs(side: ReviewTreeSide, paths: list[str]) -> dict[str, str] | None:
    """The blob at each of ``paths`` in ``side``'s tree (an absent path is absent); ``None``: unreadable."""

    if not paths:
        return {}
    try:
        listed = run_git(Path(side.repository), ["ls-tree", "-z", side.tree, "--", *paths])
    except (OSError, RuntimeError):
        return None
    if listed.returncode != 0:
        return None
    blobs: dict[str, str] = {}
    for row in listed.stdout.split("\0"):
        head, _, path = row.partition("\t")
        fields = head.split()
        if len(fields) == 3 and fields[1] == "blob":
            blobs[path] = fields[2]
    return blobs


def carried_entries(kept: ReviewTreesResult, newer: ReviewTreeComparisonRecord) -> str | None:
    """Why ``kept``'s entries cannot be carried to ``newer``; ``None`` when every read holds."""

    made = kept.comparison
    if kept.state != "trees" or made is None or kept.entry_memory is None:
        return "nothing kept to carry"
    if _memory_trees(made) != _memory_trees(newer):
        return "the memory trees differ"
    for side, made_tree, newer_tree in (
        ("before", made.code_base, newer.code_base),
        ("after", made.code_candidate, newer.code_candidate),
    ):
        named = {getattr(entry, side).path: getattr(entry, side).blob for entry in kept.entries}
        if made_tree.repository != newer_tree.repository:
            return f"the {side} side's code repository differs"
        if made_tree.tree == newer_tree.tree:
            continue
        blobs = _blobs(newer_tree, sorted(named))
        if blobs is None:
            return f"the {side} side's code tree cannot be listed"
        moved = [path for path, blob in named.items() if blobs.get(path) != blob]
        if moved:
            return f"the {side} side's {moved[0]} differs"
    return None


class CardCarry:
    """The last cards made for each invariant set of each leaf, and their carry to a newer number."""

    def __init__(self, capacity: int = CARRY_CAPACITY) -> None:
        self._kept: BoundedMemo[tuple[_Leaf, tuple[str, ...]], ReviewTreesResult] = BoundedMemo(
            capacity
        )
        self._lock = threading.Lock()

    def carry(
        self,
        leaf: _Leaf,
        invariants: tuple[str, ...],
        newer: ReviewTreeComparisonRecord,
        answer: Callable[[ReviewTreesResult], ReviewTreesResult],
    ) -> ReviewTreesResult | None:
        """The cards of ``invariants`` for ``newer``, carried from the last ones made, or ``None``.

        ``answer`` builds the newer comparison's answer around the kept entries.
        """

        with self._lock:
            held = self._kept.get((leaf, invariants))
        if held is None:
            return None
        (kept,) = held
        made = kept.comparison
        if made is None or made.number == newer.number:
            return None
        reason = carried_entries(kept, newer)
        if reason is not None:
            logger.info(
                "computed the cards of %s for comparison #%d (not carried from #%d: %s)",
                ",".join(invariants),
                newer.number,
                made.number,
                reason,
            )
            return None
        logger.info(
            "carried the cards of %s from comparison #%d to #%d",
            ",".join(invariants),
            made.number,
            newer.number,
        )
        return answer(kept)

    def made(self, leaf: _Leaf, invariants: tuple[str, ...], result: ReviewTreesResult) -> None:
        """Hold ``result`` as the last cards of ``invariants`` made for ``leaf``."""

        if result.state == "trees" and result.comparison is not None:
            with self._lock:
                self._kept.put((leaf, invariants), result)
