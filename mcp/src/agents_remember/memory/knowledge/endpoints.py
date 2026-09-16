"""Endpoint existence checks shared by the relation writes.

A membership and a realization claim both name an exact revision, and both must refuse a missing
endpoint with the offending identity named before any row is written. One module answers that
question so the two operations cannot drift into reporting different codes for the same failure,
and so "the endpoint does not exist" stays a single definition per endpoint kind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import families
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    missing_relation_endpoint_refusal,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

RelationWrite = Literal["create_family_member", "create_realization_claim"]


def require_family_revision_endpoint(
    store: OpenedKnowledgeStore, revision_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the family revision a relation names is not stored in this namespace."""

    if families.family_id_of_revision(store, revision_id) is None:
        raise KnowledgeRefused(
            missing_relation_endpoint_refusal(
                operation=operation,
                table="family_revision",
                relation_id=relation_id,
                endpoint_id=revision_id,
                endpoint_kind="family revision",
            )
        )


def require_invariant_revision_endpoint(
    store: OpenedKnowledgeStore, revision_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the invariant revision a relation names is not stored in this namespace."""

    if store.get_revision(revision_id) is None:
        raise KnowledgeRefused(
            missing_relation_endpoint_refusal(
                operation=operation,
                table="invariant_revision",
                relation_id=relation_id,
                endpoint_id=revision_id,
                endpoint_kind="invariant revision",
            )
        )


__all__ = [
    "RelationWrite",
    "require_family_revision_endpoint",
    "require_invariant_revision_endpoint",
]
