"""The membership reads: one exact invariant revision in one exact family revision.

A membership cites revisions rather than identities, so nobody has to guess which statement it was
authored against. The declared unique tuple means one pair of endpoints is related exactly once;
the declared foreign keys mean a wrong endpoint kind is unrepresentable rather than merely rejected.

These functions only read. The rows come from the derived index of a memory tree
(:mod:`agents_remember.memory.knowledge_index.projection`); the database write path was retired
with the canonical database (MIK-R26).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import records
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.models.knowledge.graph import (
    FamilyMember,
    InvariantFamilies,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


_MEMBER_COLUMNS = "repository_id, member_id, family_revision_id, invariant_revision_id, provenance"


def find_membership_by_pair(
    store: OpenedKnowledgeStore, family_revision_id: str, invariant_revision_id: str
) -> FamilyMember | None:
    """Return the one membership relating this family revision to this invariant revision."""

    row = fetch_one(
        store.connection,
        f"SELECT {_MEMBER_COLUMNS} FROM family_member WHERE repository_id = ? "
        "AND family_revision_id = ? AND invariant_revision_id = ?",
        (store.repository_id, family_revision_id, invariant_revision_id),
    )
    return None if row is None else records.decode_member_row(row)


def list_families_for_invariant_revision(
    store: OpenedKnowledgeStore, invariant_revision_id: str
) -> InvariantFamilies:
    """Return the memberships that place one exact invariant revision in families."""

    rows = store.connection.execute(
        f"SELECT {_MEMBER_COLUMNS} FROM family_member WHERE repository_id = ? "
        "AND invariant_revision_id = ? ORDER BY member_id",
        (store.repository_id, invariant_revision_id),
    )
    return InvariantFamilies(
        repository_id=store.repository_id,
        invariant_revision_id=invariant_revision_id,
        members=tuple(records.decode_member_row(row) for row in rows),
    )


__all__ = [
    "find_membership_by_pair",
    "list_families_for_invariant_revision",
]
