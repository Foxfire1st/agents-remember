"""Stale invariants flagged at read time (MIK-R03@v2).

Every read that returns an invariant from a converted memory tree also returns its currentness at
the requested code tree: ``stale`` when an entry's code no longer matches its recorded content or
can no longer be found, ``unverifiable`` when currentness cannot be observed, ``unrealized`` when it
has no realization entries, otherwise ``current``. A stale invariant stays visible and names each
differing entry; reads never write, re-anchor or judge.

* :mod:`.observe` -- one entry's state at one code tree, and the observation cache (rules 1, 5);
* :mod:`.state` -- :func:`invariant_currentness`, the one function of (code tree, memory tree)
  (rules 2 to 4), reused by the reviewer (MIK-R25) per side and the reader (MIK-R29);
* :mod:`.surface` -- the ``currentness`` block ``knowledge_read`` and the published-intent block
  attach.
"""

from agents_remember.application.knowledge_currentness.observe import (
    EXTRACTOR_VERSION,
    OBSERVATIONS,
    CodeTree,
    EntryObservation,
    EntryState,
    observation_key,
    observe_entry,
    open_code_tree,
)
from agents_remember.application.knowledge_currentness.state import (
    INVARIANT_STATES,
    Currentness,
    FamilyCurrentness,
    InvariantCurrentness,
    InvariantState,
    invariant_currentness,
    invariant_state,
)
from agents_remember.application.knowledge_currentness.surface import (
    read_currentness,
    requested_code_tree,
    returned_records,
)

__all__ = [
    "EXTRACTOR_VERSION",
    "INVARIANT_STATES",
    "OBSERVATIONS",
    "CodeTree",
    "Currentness",
    "EntryObservation",
    "EntryState",
    "FamilyCurrentness",
    "InvariantCurrentness",
    "InvariantState",
    "invariant_currentness",
    "invariant_state",
    "observation_key",
    "observe_entry",
    "open_code_tree",
    "read_currentness",
    "requested_code_tree",
    "returned_records",
]
