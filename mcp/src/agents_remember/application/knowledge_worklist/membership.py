"""MIK-R53 rule 6's ``unassigned_invariant`` item kind: invariants no family lists.

**Registration (MIK-R08 rule 1).**

* kind: ``unassigned_invariant``;
* subject: ``membership:<invariant ID>``, the subject of the row that answers it;
* facts: the invariant's revision in the candidate;
* satisfying row: the leaf's ``membership`` row about the invariant at its current revision;
* owner: MIK-R53.

The package ``__init__`` imports this module, so the kind is registered whenever the worklist is.

**Raised.** Every live invariant of the candidate that no family lists and that is new (absent
from the base), or that a family of the memory base listed (newly unlisted). An invariant in a
family raises nothing, and neither does an old orphan unlisted on both sides: the migration, not
a leaf, owns those. Listing the invariant in a family removes the item.
"""

from __future__ import annotations

from typing import Any, Final

from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.registry import (
    ItemKind,
    item_id,
    register_item_kind,
    subject_row,
)
from agents_remember.models.knowledge_files.history import (
    MEMBERSHIP_SUBJECT_PATTERN,
    membership_subject,
)

__all__ = ["UNASSIGNED_INVARIANT_KIND", "membership_items"]

ITEM_KIND: Final = "unassigned_invariant"

UNASSIGNED_INVARIANT_KIND: Final = register_item_kind(
    ItemKind(
        name=ITEM_KIND,
        subject="invariant no family lists (membership:<invariant ID>)",
        subject_pattern=MEMBERSHIP_SUBJECT_PATTERN,
        facts=("revision",),
        satisfying_row=(
            "the leaf's membership row with this subject at the invariant's current "
            "revision in K_C (facts.revision), disposition no_family (MIK-R53 rule 6)"
        ),
        row_lookup=subject_row("membership"),
        owner="MIK-R53",
    )
)


def membership_items(base: KnowledgeSide, candidate: KnowledgeSide) -> list[dict[str, Any]]:
    """One ``unassigned_invariant`` item per new or newly unlisted live invariant of K_C."""

    listed = {member for family in candidate.families.values() for member in family.members}
    base_listed = {member for family in base.families.values() for member in family.members}
    items = []
    for invariant_id in sorted(candidate.invariants):
        record = candidate.invariants[invariant_id]
        if record.status == "retired" or invariant_id in listed:
            continue
        if invariant_id not in base.invariants or invariant_id in base_listed:
            items.append(
                {
                    "id": item_id(ITEM_KIND, membership_subject(invariant_id), [record.revision]),
                    "kind": ITEM_KIND,
                    "subject": membership_subject(invariant_id),
                    "facts": {"revision": record.revision},
                }
            )
        # Else an old orphan, unlisted on both sides: the migration owns it.
    return items
