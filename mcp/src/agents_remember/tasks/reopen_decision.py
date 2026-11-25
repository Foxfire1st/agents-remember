"""The decision a reopened leaf's task document records, in one wording.

``task_reopen`` appends it to the leaf's decision log, and readers that must know whether a leaf
was reopened (the one-time state conversion among them) count it; both use this module so that the
wording cannot drift between the writer and its readers.
"""

from __future__ import annotations

from typing import Final

REOPENED_LEAF_DECISION: Final = "Leaf {leaf} reopened under its original id."


def reopened_leaf_decision(leaf: str) -> str:
    """The decision text ``task_reopen`` writes for ``leaf``."""

    return REOPENED_LEAF_DECISION.format(leaf=leaf)
