"""Endpoint existence checks shared by the relation writes.

A membership, a realization claim, a facet attachment and an evidence claim all name an exact
endpoint, and all must refuse a missing endpoint with the offending identity named before any row is
written. One module answers that question so the operations cannot drift into reporting different
codes for the same failure, and so "the endpoint does not exist" stays a single definition per
endpoint kind.

The endpoint kinds a relation write may name are checked the same way as the shipped relation
endpoints, and the enumeration below is the one place that says which kinds a relation write can
name. Adding a kind here is how a new relation joins the rule; growing a parallel check beside this
module is what the shipped doctrine forbids.

``KS-R12@v1``'s finding ``CR12-3`` recorded that this vocabulary was closed at two members. It is
closed at six now, and every widening was a leaf naming a kind its own relation needed:
``KS-R11@v1`` added ``attach_facet`` and ``add_facet``, and ``KS-R12@v1`` adds
``add_evidence_claim`` -- the one operation that resolves a claim's subject, its evidence anchor and
every claimed-coverage endpoint. The two *endpoint kinds* the subject adds are
``invariant_revision``, which this module already checked, and ``knowledge facet revision``, which
is the new one and is the stricter of the two: naming any record revision is not enough, because the
subject is a ``KnowledgeFacet`` revision specifically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import anchors, families, routes
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    missing_relation_endpoint_refusal,
)
from agents_remember.models.knowledge.change_set import SEMANTIC_CHANGE_SET_KIND
from agents_remember.models.knowledge.facet import (
    FACET_KINDS,
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

# The lookup behind the facet-revision endpoint kind: which record kind the envelope owning one
# stored revision carries.
_KIND_OF_REVISION = (
    "SELECT envelope.kind FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND revision.revision_id = ?"
)

# The existence lookup for an authored facet revision: a sealed ``record_revision`` row whose
# envelope names one of the eight declared facet kinds. The kind list is interpolated from the
# vocabulary's own declaration rather than written here, so a ninth subtype cannot be added without
# this check admitting it, and it reaches the statement as declared identifiers rather than as caller
# text.
_FACET_KIND_PLACEHOLDERS = ", ".join("?" for _ in FACET_KINDS)
_FACET_REVISION_EXISTS = (
    "SELECT revision.revision_id FROM record_revision AS revision "
    "JOIN knowledge_record AS envelope ON envelope.repository_id = revision.repository_id "
    "AND envelope.record_id = revision.record_id "
    f"WHERE revision.repository_id = ? AND revision.revision_id = ? "
    f"AND envelope.kind IN ({_FACET_KIND_PLACEHOLDERS})"
)

# The existence lookup for a change set: the envelope's own one-key lookup, narrowed to the kind, so
# a record of another kind that happens to carry this identity is not a change set.
_CHANGE_SET_EXISTS = (
    "SELECT record_id FROM knowledge_record WHERE repository_id = ? AND record_id = ? AND kind = ?"
)

RelationWrite = Literal[
    "create_family_member",
    "create_realization_claim",
    "attach_facet",
    "add_facet",
    "create_composition",
    "set_family_revision_route",
    "add_evidence_claim",
    # The authored-effect record group performs its writes under the candidate batch's own operation:
    # unlike the authored-facet group, it has no standalone operation beside the batch, so naming a
    # command kind here would report a refusal under an operation that cannot be asked for.
    "change_candidate",
]


def require_route_endpoint(
    store: OpenedKnowledgeStore, route_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the route a relation names is not authored in this namespace.

    A named route that does not exist is a dangling reference to refuse; ``None`` is a different
    fact -- the explicit ungoverned state -- and the two must not collapse into one answer. The
    existence question is asked through :func:`…routes.route_exists`, which is the same one-key
    lookup the governing-route write path performs, so "this route is authored here" stays one
    definition for every governed row rather than one per relation kind.
    """

    if routes.route_exists(store.connection, store.repository_id, route_id):
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation=operation,
            table="route",
            relation_id=relation_id,
            endpoint_id=route_id,
            endpoint_kind="route",
        )
    )


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


def require_facet_revision_endpoint(
    store: OpenedKnowledgeStore, revision_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the knowledge facet revision a relation names is not stored in this namespace.

    This is the endpoint kind ``KS-R12@v1`` adds: the subject of an evidence claim may be a
    ``KnowledgeFacet`` revision owned by ``KS-R11@v1``. The check is deliberately stricter than the
    revision-table lookups beside it. A facet revision is a ``record_revision`` row whose owning
    record envelope carries one of the eight authored-judgment kinds, so a stored revision of a
    *detection* record or of another evidence claim is not a facet revision and is refused as the
    missing endpoint it is -- naming the kind the caller asked for and the identity it named, rather
    than resolving to "some revision exists".
    """

    kind = fetch_one(store.connection, _KIND_OF_REVISION, (store.repository_id, revision_id))
    if kind is not None and str(kind[0]) in FACET_KINDS:
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation=operation,
            table="record_revision",
            relation_id=relation_id,
            endpoint_id=revision_id,
            endpoint_kind="knowledge facet revision",
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
    "require_change_set_endpoint",
    "require_effect_revision_endpoint",
    "require_facet_revision_endpoint",
    "require_family_revision_endpoint",
    "require_invariant_revision_endpoint",
    "require_realization_claim_endpoint",
    "require_route_endpoint",
    "require_source_anchor_endpoint",
]


def require_effect_revision_endpoint(
    store: OpenedKnowledgeStore, revision_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when a revision an effect claim names as an input or an output is not stored here.

    An effect claim's input and output each name an *exact stored revision*, and the admitted kinds
    are an invariant revision and an authored facet revision. The two are **one** check rather than
    two because the author names a revision and not a kind: which kind is stored under that identity
    is a fact about the store, and both branches ask the same question of the table that owns the
    revision. Requiring the author to declare the kind as well would be a second addressing scheme
    beside the stored key, and resolving it anywhere but here would be a second resolution path for
    the same reference -- which is what this module exists to prevent.

    A refusal names the offending identity, and it is the shipped ``invalid_reference``: the
    reference is not re-pointed to a similarly named revision and not dropped to make the record
    valid.
    """

    if store.get_revision(revision_id) is not None:
        return
    facet_parameters: tuple[str, ...] = (store.repository_id, revision_id, *FACET_KINDS)
    if fetch_one(store.connection, _FACET_REVISION_EXISTS, facet_parameters) is not None:
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation=operation,
            table="record_revision",
            relation_id=relation_id,
            endpoint_id=revision_id,
            endpoint_kind="recorded revision (an invariant revision or an authored facet revision)",
        )
    )


def require_change_set_endpoint(
    store: OpenedKnowledgeStore, change_set_id: str, relation_id: str, operation: RelationWrite
) -> None:
    """Refuse when the change set a member or a succession edge names is not stored here.

    A member declares the one change set it belongs to and a successor declares its exact
    predecessors, so both are references to a stored ``semantic_change_set`` record and both are
    resolved by the envelope's own one-key lookup narrowed to that kind. A record of another kind
    carrying this identity is not a change set, and a change set that is not stored is
    ``invalid_reference`` naming the offending identity rather than a member stored without its
    group.
    """

    parameters = (store.repository_id, change_set_id, SEMANTIC_CHANGE_SET_KIND)
    if fetch_one(store.connection, _CHANGE_SET_EXISTS, parameters) is not None:
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation=operation,
            table="knowledge_record",
            relation_id=relation_id,
            endpoint_id=change_set_id,
            endpoint_kind="semantic change set",
        )
    )
