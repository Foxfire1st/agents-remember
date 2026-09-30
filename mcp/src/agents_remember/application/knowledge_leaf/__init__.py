"""The family-complete leaf read of a converted memory tree (MIK-R01).

A read seeded with one source path returns, from the tree's derived index, the path's own
invariants, every family containing them with its guarantee and routes, every member's statement,
conditions and entries, and the advertised frontier -- in one response when it fits the shared
threshold, otherwise as pages that ``knowledge_read`` continues (MIK-R02).

* :mod:`.selection` -- what a seed path selects, in which order, and the rows at a code tree;
* :mod:`.chain` -- the path's route-chain families, appended after that content (MIK-R05), and the
  ``served_earlier`` rendering only ``read_ar_files`` applies to them;
* :mod:`.pages` -- the paged response both surfaces emit (the ``read_ar_files`` block and
  ``knowledge_read``'s ``source_context`` view), through the shared paging seam.

A family seed (MIK-R05 rule 3) reads one family's full content under the same policy.

Reads of an unconverted database do not reach this package: they keep the recorded-scope read.
"""

from __future__ import annotations

from agents_remember.application.knowledge_leaf.pages import (
    LEAF_VIEW,
    LeafRequest,
    PreparedLeaf,
    absent_chain,
    prepare_leaf,
)
from agents_remember.application.knowledge_leaf.selection import (
    LEAF_POLICY,
    LEAF_POLICY_VERSION,
    family_names,
    select_family,
    select_leaf,
)

__all__ = [
    "LEAF_POLICY",
    "LEAF_POLICY_VERSION",
    "LEAF_VIEW",
    "LeafRequest",
    "PreparedLeaf",
    "absent_chain",
    "family_names",
    "prepare_leaf",
    "select_family",
    "select_leaf",
]
