"""Endpoint existence checks shared by the relation writes.

A membership, a realization claim and a facet attachment all name an exact endpoint, and all must
refuse a missing endpoint with the offending identity named before any row is written. One module
answers that question so the operations cannot drift into reporting different codes for the same
failure, and so "the endpoint does not exist" stays a single definition per endpoint kind.

The endpoint kinds a facet attachment may name are checked the same way as the shipped relation
endpoints, and the enumeration below is the one place that says which kinds a relation write can
name. Adding a kind here is how a new relation joins the rule; growing a parallel check beside this
module is what the shipped doctrine forbids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import anchors, families
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    missing_relation_endpoint_refusal,
)
from agents_remember.models.knowledge.facet import (
    FAMILY_REVISION_ENDPOINT_KIND,
    INVARIANT_REVISION_ENDPOINT_KIND,
    REALIZATION_CLAIM_ENDPOINT_KIND,
    SOURCE_ANCHOR_ENDPOINT_KIND,
    AttachmentEndpoint,
    endpoint_identity,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The one-key existence lookup for the realization-claim endpoint kind. It is a statement rather
# than a call into the claim reader for the reason that function's docstring records.
_CLAIM_EXISTS = "SELECT claim_id FROM realization_claim WHERE repository_id = ? AND claim_id = ?"

RelationWrite = Literal[
    "create_family_member",
    "create_realization_claim",
    "attach_facet",
    "add_facet",
]


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


def require_source_anchor_endpoint(
    store: OpenedKnowledgeStore, anchor_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the source anchor a relation names is not stored in this namespace."""

    if anchors.get_anchor(store, anchor_id) is None:
        raise KnowledgeRefused(
            missing_relation_endpoint_refusal(
                operation=operation,
                table="source_anchor",
                relation_id=relation_id,
                endpoint_id=anchor_id,
                endpoint_kind="source anchor",
            )
        )


def require_realization_claim_endpoint(
    store: OpenedKnowledgeStore, claim_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the realization claim a relation names is not stored in this namespace.

    The question is asked of the canonical table through the shipped ``fetch_one`` helper rather
    than through :mod:`…realizations`, and that is deliberate: ``realizations`` imports this module
    for the shipped relation endpoint check, so importing it back would be a cycle. The question
    asked is the same one-key lookup it performs -- this repository, this claim identity -- and the
    refusal, its code and the endpoint kind it names are this module's single definition for the
    kind.
    """

    exists = fetch_one(store.connection, _CLAIM_EXISTS, (store.repository_id, claim_id))
    if exists is None:
        raise KnowledgeRefused(
            missing_relation_endpoint_refusal(
                operation=operation,
                table="realization_claim",
                relation_id=relation_id,
                endpoint_id=claim_id,
                endpoint_kind="realization claim",
            )
        )


def require_attachment_endpoint(
    store: OpenedKnowledgeStore, endpoint: AttachmentEndpoint, relation_id: str
) -> None:
    """Refuse when the exact endpoint a facet attachment names is not stored in this namespace.

    One dispatch over the typed endpoint rather than four call sites: the endpoint kind is a
    property of the value the caller supplied, so the check that applies is the value's own and a
    caller cannot reach the wrong one.
    """

    endpoint_id = endpoint_identity(endpoint)
    if endpoint.kind == INVARIANT_REVISION_ENDPOINT_KIND:
        require_invariant_revision_endpoint(store, endpoint_id, relation_id, "attach_facet")
        return
    if endpoint.kind == FAMILY_REVISION_ENDPOINT_KIND:
        require_family_revision_endpoint(store, endpoint_id, relation_id, "attach_facet")
        return
    if endpoint.kind == SOURCE_ANCHOR_ENDPOINT_KIND:
        require_source_anchor_endpoint(store, endpoint_id, relation_id, "attach_facet")
        return
    if endpoint.kind == REALIZATION_CLAIM_ENDPOINT_KIND:
        require_realization_claim_endpoint(store, endpoint_id, relation_id, "attach_facet")
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation="attach_facet",
            table="facet_attachment",
            relation_id=relation_id,
            endpoint_id=endpoint_id,
            endpoint_kind="supported endpoint kind",
        )
    )


__all__ = [
    "RelationWrite",
    "require_attachment_endpoint",
    "require_family_revision_endpoint",
    "require_invariant_revision_endpoint",
    "require_realization_claim_endpoint",
    "require_source_anchor_endpoint",
]
