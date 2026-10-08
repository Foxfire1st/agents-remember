"""The family reads: family identity and sealed family revisions.

A family is a stable subject whose revision carries an authored joint guarantee about a set of
invariant revisions. A family revision is one sealed aggregate -- the guarantee, the origin state,
the acceptance reference, the provenance and the sorted predecessor set -- and every read
re-derives the stored revision's payload seal on the way out.

These functions only read. The rows come from the derived index of a memory tree
(:mod:`agents_remember.memory.knowledge_index.projection`); the database write path was retired
with the canonical database (MIK-R26).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import records
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.models.knowledge.family import (
    FamilyIdentity,
    StoredFamilyRevision,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


_FAMILY_COLUMNS = "repository_id, family_id, display_label, label_provenance"

_FAMILY_REVISION_COLUMNS = (
    "repository_id, family_id, revision_id, display_version, joint_guarantee, state_at_origin, "
    "acceptance_ref, provenance, payload_digest"
)


def get_family(store: OpenedKnowledgeStore, family_id: str) -> FamilyIdentity | None:
    """Return one family identity, or ``None`` when it is not in this namespace."""

    row = fetch_one(
        store.connection,
        f"SELECT {_FAMILY_COLUMNS} FROM family WHERE repository_id = ? AND family_id = ?",
        (store.repository_id, family_id),
    )
    return None if row is None else records.decode_family_row(row)


def get_family_revision(
    store: OpenedKnowledgeStore, revision_id: str
) -> StoredFamilyRevision | None:
    """Return one family revision aggregate, verifying its stored seal before returning it."""

    row = fetch_one(
        store.connection,
        f"SELECT {_FAMILY_REVISION_COLUMNS} FROM family_revision "
        "WHERE repository_id = ? AND revision_id = ?",
        (store.repository_id, revision_id),
    )
    if row is None:
        return None
    return records.decode_family_revision_row(
        row, _family_predecessors(store, str(row[1]), revision_id)
    )


def list_family_revision_ids(store: OpenedKnowledgeStore, family_id: str) -> tuple[str, ...]:
    """Return every revision identity of one family, in stable order."""

    return tuple(
        str(row[0])
        for row in store.connection.execute(
            "SELECT revision_id FROM family_revision "
            "WHERE repository_id = ? AND family_id = ? ORDER BY revision_id",
            (store.repository_id, family_id),
        )
    )


def _family_predecessors(
    store: OpenedKnowledgeStore, family_id: str, revision_id: str
) -> tuple[str, ...]:
    return records.decode_predecessor_rows(
        list(
            store.connection.execute(
                "SELECT parent_revision_id FROM family_predecessor "
                "WHERE repository_id = ? AND family_id = ? AND child_revision_id = ?",
                (store.repository_id, family_id, revision_id),
            )
        )
    )


__all__ = [
    "get_family",
    "get_family_revision",
    "list_family_revision_ids",
]
