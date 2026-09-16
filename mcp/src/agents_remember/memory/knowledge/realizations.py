"""The realization relation: what an exact source location does for an exact obligation.

A realization claim cites one invariant revision and one source anchor and carries the author's
role and rationale. Neither is inferred: the role comes from the authored vocabulary and the
rationale is the author's sentence, so a graph-valid claim is still only a claim.

A newly authored anchor and the claim about it are one act of authorship, so the claim operation
records a new anchor inside its own transaction: a refusal raised after that write rolls the anchor
back instead of leaving an orphan location behind. Reads select the same rows the writes store, so
a forward query (by invariant revision) and a reverse query (by anchor) answer with the same claim
identities instead of two independently maintained lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import anchors, records
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.endpoints import require_invariant_revision_endpoint
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    SqliteFailureContext,
    duplicate_anchor_refusal,
    duplicate_relation_identity_refusal,
    duplicate_relationship_refusal,
    missing_expected_row_refusal,
    missing_relation_endpoint_refusal,
    scope_refusal,
    stale_expected_row_refusal,
)
from agents_remember.models.knowledge.graph import (
    AnchorRealizations,
    RealizationClaim,
    RealizationClaims,
)
from agents_remember.models.knowledge.result import (
    AnchorReference,
    CreateRealizationClaimResult,
    KnowledgeRefusal,
    RealizationClaimRequest,
    RemoveRealizationClaimRequest,
    RemoveRealizationClaimResult,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


_CLAIM_COLUMNS = (
    "repository_id, claim_id, invariant_revision_id, anchor_id, role, rationale, provenance"
)

_CLAIM_INSERT = (
    "INSERT INTO realization_claim "
    "(repository_id, claim_id, invariant_revision_id, anchor_id, role, rationale, provenance) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def create_realization_claim(
    store: OpenedKnowledgeStore, request: RealizationClaimRequest
) -> CreateRealizationClaimResult:
    """Insert one realization claim, recording a new anchor in the same transaction."""

    denied = scope_refusal("create_realization_claim", store.repository_id, request.repository_id)
    if denied is not None:
        return _claim_refusal(request, _requested_anchor_id(request), denied)
    anchor_id = _requested_anchor_id(request)
    with store.exclusive_candidate_lock("create_realization_claim") as lock_refusal:
        if lock_refusal is not None:
            return _claim_refusal(request, anchor_id, lock_refusal)
        return store.within_immediate(
            lambda: _insert_claim(store, request),
            on_refusal=lambda refused: _claim_refusal(request, anchor_id, refused),
            failure=SqliteFailureContext(
                operation="create_realization_claim",
                table="realization_claim",
                record_id=request.claim.claim_id,
            ),
        )


def _insert_claim(
    store: OpenedKnowledgeStore, request: RealizationClaimRequest
) -> CreateRealizationClaimResult:
    stored = insert_realization_claim(store, request)
    if stored is None:
        return _claim_result(request, _requested_anchor_id(request), "no_change")
    return _claim_result(request, str(stored.anchor_id), "created")


def insert_realization_claim(
    store: OpenedKnowledgeStore, request: RealizationClaimRequest
) -> RealizationClaim | None:
    """Insert one realization claim inside the caller's open transaction.

    Returns ``None`` when the exact same claim is already stored, which is the one case where the
    write is a no-op rather than an insertion. Raises :class:`KnowledgeRefused` for every other
    conflict: a reused identity with a different payload, a missing invariant-revision endpoint, a
    pair that is already related, or an anchor endpoint this request cannot record.

    A newly authored anchor is recorded here, before the claim's own duplicate checks, because a
    location and the claim about it are one act of authorship: a refusal raised after that write
    rolls the anchor back instead of leaving an orphan location behind.
    """

    claim = request.claim
    require_invariant_revision_endpoint(
        store, claim.invariant_revision_id, claim.claim_id, "create_realization_claim"
    )
    anchor_id = _record_anchor(store, request)
    stored = _stored_claim(store, request, anchor_id)
    existing = get_realization_claim(store, claim.claim_id)
    if existing is not None:
        if existing == stored:
            return None
        raise KnowledgeRefused(
            duplicate_relation_identity_refusal(
                operation="create_realization_claim",
                table="realization_claim",
                record_id=claim.claim_id,
                expected=existing.row_digest,
                observed=stored.row_digest,
            )
        )
    duplicate = find_claim_by_pair(store, claim.invariant_revision_id, anchor_id)
    if duplicate is not None:
        raise KnowledgeRefused(
            duplicate_relationship_refusal(
                operation="create_realization_claim",
                table="realization_claim",
                record_id=claim.claim_id,
                existing_id=duplicate.claim_id,
                detail="this invariant revision and anchor are already related by a stored "
                "realization claim",
            )
        )
    store.write(_CLAIM_INSERT, records.claim_row(stored, store.repository_id))
    store.require_referential_integrity()
    return stored


def _record_anchor(store: OpenedKnowledgeStore, request: RealizationClaimRequest) -> str:
    """Return the anchor this claim cites, recording a new one inside the same transaction.

    A newly authored location and the claim about it share one provenance envelope because they
    are one act of authorship. Writing the anchor here, before the claim's own duplicate checks,
    is what makes the grouping observable: a refusal raised after this point rolls the anchor back.
    """

    endpoint = request.anchor
    if isinstance(endpoint, AnchorReference):
        if anchors.get_anchor(store, endpoint.anchor_id) is None:
            raise KnowledgeRefused(
                missing_relation_endpoint_refusal(
                    operation="create_realization_claim",
                    table="source_anchor",
                    relation_id=request.claim.claim_id,
                    endpoint_id=endpoint.anchor_id,
                    endpoint_kind="source anchor",
                )
            )
        return endpoint.anchor_id
    anchor = anchors.source_anchor_from_draft(endpoint.anchor, request.provenance)
    existing = anchors.get_anchor(store, str(anchor.anchor_id))
    if existing is None:
        anchors.insert_anchor_row(store, anchor)
        return str(anchor.anchor_id)
    if existing == anchor:
        return str(anchor.anchor_id)
    raise KnowledgeRefused(
        duplicate_anchor_refusal(
            str(anchor.anchor_id),
            records.anchor_row_digest(existing, store.repository_id),
            records.anchor_row_digest(anchor, store.repository_id),
        )
    )


def remove_realization_claim(
    store: OpenedKnowledgeStore, request: RemoveRealizationClaimRequest
) -> RemoveRealizationClaimResult:
    """Remove one realization claim by its identity and expected row digest."""

    denied = scope_refusal("remove_realization_claim", store.repository_id, request.repository_id)
    if denied is not None:
        return _claim_removal_refusal(request, denied)
    with store.exclusive_candidate_lock("remove_realization_claim") as lock_refusal:
        if lock_refusal is not None:
            return _claim_removal_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _delete_claim(store, request),
            on_refusal=lambda refused: _claim_removal_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="remove_realization_claim",
                table="realization_claim",
                record_id=request.claim_id,
            ),
        )


def _delete_claim(
    store: OpenedKnowledgeStore, request: RemoveRealizationClaimRequest
) -> RemoveRealizationClaimResult:
    delete_realization_claim(store, request.claim_id, request.expected_row_digest)
    return RemoveRealizationClaimResult(
        state="removed", repository_id=request.repository_id, claim_id=request.claim_id
    )


def delete_realization_claim(
    store: OpenedKnowledgeStore, claim_id: str, expected_row_digest: str
) -> None:
    """Remove one realization claim inside the caller's open transaction.

    Raises :class:`KnowledgeRefused` when the row is not stored, or when it is not the row the
    caller read.
    """

    existing = get_realization_claim(store, claim_id)
    if existing is None:
        raise KnowledgeRefused(
            missing_expected_row_refusal(
                operation="remove_realization_claim",
                table="realization_claim",
                record_id=claim_id,
            )
        )
    if existing.row_digest != expected_row_digest:
        raise KnowledgeRefused(
            stale_expected_row_refusal(
                operation="remove_realization_claim",
                table="realization_claim",
                record_id=claim_id,
                expected=expected_row_digest,
                observed=existing.row_digest,
            )
        )
    store.write(
        "DELETE FROM realization_claim WHERE repository_id = ? AND claim_id = ?",
        (store.repository_id, claim_id),
    )


def get_realization_claim(store: OpenedKnowledgeStore, claim_id: str) -> RealizationClaim | None:
    """Return one stored realization claim, or ``None`` when it is not in this namespace."""

    row = fetch_one(
        store.connection,
        f"SELECT {_CLAIM_COLUMNS} FROM realization_claim WHERE repository_id = ? AND claim_id = ?",
        (store.repository_id, claim_id),
    )
    return None if row is None else records.decode_claim_row(row)


def find_claim_by_pair(
    store: OpenedKnowledgeStore, invariant_revision_id: str, anchor_id: str
) -> RealizationClaim | None:
    """Return the one realization claim relating this invariant revision to this anchor."""

    row = fetch_one(
        store.connection,
        f"SELECT {_CLAIM_COLUMNS} FROM realization_claim WHERE repository_id = ? "
        "AND invariant_revision_id = ? AND anchor_id = ?",
        (store.repository_id, invariant_revision_id, anchor_id),
    )
    return None if row is None else records.decode_claim_row(row)


def list_claims_for_invariant_revision(
    store: OpenedKnowledgeStore, invariant_revision_id: str
) -> RealizationClaims:
    """Return the realization claims recorded for one exact invariant revision."""

    rows = store.connection.execute(
        f"SELECT {_CLAIM_COLUMNS} FROM realization_claim WHERE repository_id = ? "
        "AND invariant_revision_id = ? ORDER BY claim_id",
        (store.repository_id, invariant_revision_id),
    )
    return RealizationClaims(
        repository_id=store.repository_id,
        invariant_revision_id=invariant_revision_id,
        claims=tuple(records.decode_claim_row(row) for row in rows),
    )


def list_claims_for_anchor(store: OpenedKnowledgeStore, anchor_id: str) -> AnchorRealizations:
    """Return the realization claims that cite one anchor -- the reverse of the same rows."""

    rows = store.connection.execute(
        f"SELECT {_CLAIM_COLUMNS} FROM realization_claim WHERE repository_id = ? "
        "AND anchor_id = ? ORDER BY claim_id",
        (store.repository_id, anchor_id),
    )
    return AnchorRealizations(
        repository_id=store.repository_id,
        anchor_id=anchor_id,
        claims=tuple(records.decode_claim_row(row) for row in rows),
    )


def _stored_claim(
    store: OpenedKnowledgeStore, request: RealizationClaimRequest, anchor_id: str
) -> RealizationClaim:
    claim = request.claim
    return RealizationClaim(
        repository_id=store.repository_id,
        claim_id=claim.claim_id,
        invariant_revision_id=claim.invariant_revision_id,
        anchor_id=anchor_id,
        role=claim.role,
        rationale=claim.rationale,
        provenance=request.provenance,
        row_digest=records.claim_row_digest(
            store.repository_id, claim, anchor_id, request.provenance
        ),
    )


def _requested_anchor_id(request: RealizationClaimRequest) -> str:
    endpoint = request.anchor
    if isinstance(endpoint, AnchorReference):
        return endpoint.anchor_id
    return str(endpoint.anchor.anchor_id)


def _claim_result(
    request: RealizationClaimRequest,
    anchor_id: str,
    state: Literal["created", "no_change"],
) -> CreateRealizationClaimResult:
    return CreateRealizationClaimResult(
        state=state,
        repository_id=request.repository_id,
        claim_id=request.claim.claim_id,
        anchor_id=anchor_id,
        stored=state == "created",
    )


def _claim_refusal(
    request: RealizationClaimRequest, anchor_id: str, refused: KnowledgeRefusal
) -> CreateRealizationClaimResult:
    return CreateRealizationClaimResult(
        state="refused",
        repository_id=request.repository_id,
        claim_id=request.claim.claim_id,
        anchor_id=anchor_id,
        stored=False,
        refusal=refused,
    )


def _claim_removal_refusal(
    request: RemoveRealizationClaimRequest, refused: KnowledgeRefusal
) -> RemoveRealizationClaimResult:
    return RemoveRealizationClaimResult(
        state="refused",
        repository_id=request.repository_id,
        claim_id=request.claim_id,
        refusal=refused,
    )


__all__ = [
    "create_realization_claim",
    "delete_realization_claim",
    "find_claim_by_pair",
    "get_realization_claim",
    "insert_realization_claim",
    "list_claims_for_anchor",
    "list_claims_for_invariant_revision",
    "remove_realization_claim",
]
