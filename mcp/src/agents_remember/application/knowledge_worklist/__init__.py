"""The change-to-knowledge worklist (MIK-R08@v2).

For a leaf, the worklist is the complete list of knowledge items its change requires a disposition
for, computed from the exact code and memory trees of the leaf's base and candidate (B, K_B, C, K_C;
MIK-R07 rule 0) and persisted as ``knowledge-worklist/v1`` in the leaf's enclosure.

* :mod:`.code` -- zero-context hunks, line-range mapping, anchor ranges and content identities;
* :mod:`.knowledge` -- K_B and K_C parsed through the derived index's parser (MIK-R23);
* :mod:`.classify` -- entry classes and the knowledge-side changes (definitions 4, 6, 7);
* :mod:`.registry` -- the item-kind registry and stable item IDs (rules 1-3);
* :mod:`.compute` -- one run: inventory, one-pass scope, items, gate linkage, digest;
* :mod:`.leaf` -- a leaf's sides from its contract, its run, and the persisted file;
* :mod:`.surface` -- what ``knowledge_integrity_check`` returns for a leaf;
* :mod:`.planned_effects` -- MIK-R11's ``planned_untouched`` kind (registered on import), the
  ``planned``/``unplanned`` marks and the reconciliation of declared effects against rows;
* :mod:`.onboarding_trace` -- MIK-R30's ``onboarding_trace`` kind (registered on import), the
  gate's sides and its items in the worklist;
* :mod:`.route_conditions` -- MIK-R06's ``family_route_condition`` kind (registered on import by
  :mod:`.compute`): the family route conditions and their satisfying rule;
* :mod:`.unexplained` -- MIK-R10's ``unexplained_hunk`` and ``unexplained_file`` kinds (registered
  on import), the coverage lookup and the items for every unlinked change.
* :mod:`.reconsideration` -- MIK-R14's ``reconsideration_candidate`` kind (registered on import by
  :mod:`.compute`): a decision whose ``reconsider_on`` target changed.

An entry is raised only when changed lines intersect its own range, when it moved or disappeared,
or when it changed outside the managed flow; a change elsewhere in the same file raises nothing.
The worklist carries no verdict: what an open item means is the gate's (MIK-R09).
"""

from __future__ import annotations

from agents_remember.application.knowledge_worklist.compute import (
    WORKLIST_SCHEMA,
    Incomplete,
    Item,
    WorklistInputs,
    compute_worklist,
    incomplete_worklist,
)
from agents_remember.application.knowledge_worklist.leaf import (
    WORKLIST_FILE_NAME,
    ExplicitSides,
    LeafWorklistRecompute,
    leaf_onboarding_trace_sides,
    leaf_worklist,
    persist_worklist,
    read_leaf_worklist,
    recompute_leaf_worklist,
    worklist_for_sides,
    worklist_path,
)
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    ONBOARDING_TRACE_KIND,
)
from agents_remember.application.knowledge_worklist.planned_effects import (
    PLANNED_UNTOUCHED_KIND,
)
from agents_remember.application.knowledge_worklist.reconsideration import (
    RECONSIDERATION_KIND,
)
from agents_remember.application.knowledge_worklist.registry import (
    ITEM_KINDS,
    ItemKind,
    item_id,
    register_item_kind,
    satisfying_row,
)
from agents_remember.application.knowledge_worklist.route_conditions import (
    FAMILY_ROUTE_CONDITION_KIND,
    family_route_item_open,
)
from agents_remember.application.knowledge_worklist.unexplained import (
    UNEXPLAINED_FILE_KIND,
    UNEXPLAINED_HUNK_KIND,
    answering_trace_subjects,
)

__all__ = [
    "FAMILY_ROUTE_CONDITION_KIND",
    "ITEM_KINDS",
    "ONBOARDING_TRACE_KIND",
    "PLANNED_UNTOUCHED_KIND",
    "RECONSIDERATION_KIND",
    "UNEXPLAINED_FILE_KIND",
    "UNEXPLAINED_HUNK_KIND",
    "WORKLIST_FILE_NAME",
    "WORKLIST_SCHEMA",
    "ExplicitSides",
    "Incomplete",
    "Item",
    "ItemKind",
    "LeafWorklistRecompute",
    "WorklistInputs",
    "answering_trace_subjects",
    "compute_worklist",
    "family_route_item_open",
    "incomplete_worklist",
    "item_id",
    "leaf_onboarding_trace_sides",
    "leaf_worklist",
    "persist_worklist",
    "read_leaf_worklist",
    "recompute_leaf_worklist",
    "register_item_kind",
    "satisfying_row",
    "worklist_for_sides",
    "worklist_path",
]
