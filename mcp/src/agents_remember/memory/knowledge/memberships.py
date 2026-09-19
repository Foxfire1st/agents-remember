"""The membership relation: one exact invariant revision in one exact family revision.

A membership cites revisions rather than identities, so nobody has to guess which statement it was
authored against, and a newer family revision needs its own explicitly authored membership instead
of inheriting one through a moving current pointer. The declared unique tuple means one pair of
endpoints is related exactly once; the declared foreign keys mean a wrong endpoint kind is
unrepresentable rather than merely rejected.

Removal is explicit: by identity and the digest of the row the caller expects, so a stale caller
cannot delete whatever now carries the identity it remembered. The earlier dataset keeps the
removed row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import records
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.endpoints import (
    require_family_revision_endpoint,
    require_invariant_revision_endpoint,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    SqliteFailureContext,
    duplicate_relation_identity_refusal,
    duplicate_relationship_refusal,
    missing_expected_row_refusal,
    scope_refusal,
    stale_expected_row_refusal,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.graph import (
    FamilyMember,
    FamilyMemberDraft,
    FamilyMembers,
    InvariantFamilies,
)
from agents_remember.models.knowledge.result import (
    CreateFamilyMemberResult,
    FamilyMemberRequest,
    KnowledgeRefusal,
    RemoveFamilyMemberRequest,
    RemoveFamilyMemberResult,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


_MEMBER_COLUMNS = "repository_id, member_id, family_revision_id, invariant_revision_id, provenance"

_MEMBER_INSERT = (
    "INSERT INTO family_member "
    "(repository_id, member_id, family_revision_id, invariant_revision_id, provenance) "
    "VALUES (?, ?, ?, ?, ?)"
)


def insert_family_member_draft(
    store: OpenedKnowledgeStore, draft: FamilyMemberDraft, provenance: Authorship
) -> FamilyMember:
    """Insert one authored membership draft inside the caller's open transaction.

    The row digest is computed here, from the admitted provenance, so a draft that arrived carrying
    its own envelope cannot make the stored row digest something the caller chose.
    """

    member = FamilyMember(
        repository_id=store.repository_id,
        member_id=draft.member_id,
        family_revision_id=draft.family_revision_id,
        invariant_revision_id=draft.invariant_revision_id,
        provenance=provenance,
        row_digest=records.member_row_digest(
            store.repository_id,
            draft.member_id,
            draft.family_revision_id,
            draft.invariant_revision_id,
            provenance,
        ),
    )
    insert_family_member(store, member)
    return member


def create_family_member(
    store: OpenedKnowledgeStore, request: FamilyMemberRequest
) -> CreateFamilyMemberResult:
    """Insert one membership of an exact invariant revision in an exact family revision."""

    denied = scope_refusal("create_family_member", store.repository_id, request.repository_id)
    if denied is not None:
        return _member_refusal(request, denied)
    with store.exclusive_candidate_lock("create_family_member") as lock_refusal:
        if lock_refusal is not None:
            return _member_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _insert_member(store, request),
            on_refusal=lambda refused: _member_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="create_family_member",
                table="family_member",
                record_id=request.member.member_id,
            ),
        )


def _insert_member(
    store: OpenedKnowledgeStore, request: FamilyMemberRequest
) -> CreateFamilyMemberResult:
    member = _stored_member(store, request)
    existing = get_family_member(store, member.member_id)
    if existing is not None and existing == member:
        return _member_result(request, "no_change")
    insert_family_member(store, member)
    return _member_result(request, "created")


def insert_family_member(store: OpenedKnowledgeStore, member: FamilyMember) -> None:
    """Insert one membership inside the caller's open transaction.

    Raises :class:`KnowledgeRefused` when the identity is already stored with a different payload,
    when either endpoint is absent, or when the pair is already related: the declared unique tuple
    means one pair of endpoints is related exactly once, so a second row for it would be a second
    statement of the same fact rather than a new one.
    """

    existing = get_family_member(store, member.member_id)
    if existing is not None:
        raise KnowledgeRefused(
            duplicate_relation_identity_refusal(
                operation="create_family_member",
                table="family_member",
                record_id=member.member_id,
                expected=existing.row_digest,
                observed=member.row_digest,
            )
        )
    require_family_revision_endpoint(
        store, member.family_revision_id, member.member_id, "create_family_member"
    )
    require_invariant_revision_endpoint(
        store, member.invariant_revision_id, member.member_id, "create_family_member"
    )
    duplicate = find_membership_by_pair(
        store, member.family_revision_id, member.invariant_revision_id
    )
    if duplicate is not None:
        raise KnowledgeRefused(
            duplicate_relationship_refusal(
                operation="create_family_member",
                table="family_member",
                record_id=member.member_id,
                existing_id=duplicate.member_id,
                detail="this family revision and invariant revision are already related by a "
                "stored membership",
            )
        )
    store.write(_MEMBER_INSERT, records.member_row(member, store.repository_id))
    store.require_referential_integrity()


def remove_family_member(
    store: OpenedKnowledgeStore, request: RemoveFamilyMemberRequest
) -> RemoveFamilyMemberResult:
    """Remove one membership by its identity and the digest of the row the caller expects."""

    denied = scope_refusal("remove_family_member", store.repository_id, request.repository_id)
    if denied is not None:
        return _member_removal_refusal(request, denied)
    with store.exclusive_candidate_lock("remove_family_member") as lock_refusal:
        if lock_refusal is not None:
            return _member_removal_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _delete_member(store, request),
            on_refusal=lambda refused: _member_removal_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="remove_family_member",
                table="family_member",
                record_id=request.member_id,
            ),
        )


def _delete_member(
    store: OpenedKnowledgeStore, request: RemoveFamilyMemberRequest
) -> RemoveFamilyMemberResult:
    delete_family_member(store, request.member_id, request.expected_row_digest)
    return RemoveFamilyMemberResult(
        state="removed", repository_id=request.repository_id, member_id=request.member_id
    )


def delete_family_member(
    store: OpenedKnowledgeStore, member_id: str, expected_row_digest: str
) -> None:
    """Remove one membership inside the caller's open transaction.

    Raises :class:`KnowledgeRefused` when the row is not stored, or when it is not the row the
    caller read: a stale caller must not delete whatever now carries the identity it remembered.
    """

    existing = get_family_member(store, member_id)
    if existing is None:
        raise KnowledgeRefused(
            missing_expected_row_refusal(
                operation="remove_family_member",
                table="family_member",
                record_id=member_id,
            )
        )
    if existing.row_digest != expected_row_digest:
        raise KnowledgeRefused(
            stale_expected_row_refusal(
                operation="remove_family_member",
                table="family_member",
                record_id=member_id,
                expected=expected_row_digest,
                observed=existing.row_digest,
            )
        )
    store.write(
        "DELETE FROM family_member WHERE repository_id = ? AND member_id = ?",
        (store.repository_id, member_id),
    )


def get_family_member(store: OpenedKnowledgeStore, member_id: str) -> FamilyMember | None:
    """Return one stored membership, or ``None`` when it is not in this namespace."""

    row = fetch_one(
        store.connection,
        f"SELECT {_MEMBER_COLUMNS} FROM family_member WHERE repository_id = ? AND member_id = ?",
        (store.repository_id, member_id),
    )
    return None if row is None else records.decode_member_row(row)


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


def list_members(store: OpenedKnowledgeStore, family_revision_id: str) -> FamilyMembers:
    """Return the memberships of one exact family revision."""

    rows = store.connection.execute(
        f"SELECT {_MEMBER_COLUMNS} FROM family_member WHERE repository_id = ? "
        "AND family_revision_id = ? ORDER BY member_id",
        (store.repository_id, family_revision_id),
    )
    return FamilyMembers(
        repository_id=store.repository_id,
        family_revision_id=family_revision_id,
        members=tuple(records.decode_member_row(row) for row in rows),
    )


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


def _stored_member(store: OpenedKnowledgeStore, request: FamilyMemberRequest) -> FamilyMember:
    member = request.member
    return FamilyMember(
        repository_id=store.repository_id,
        member_id=member.member_id,
        family_revision_id=member.family_revision_id,
        invariant_revision_id=member.invariant_revision_id,
        provenance=member.provenance,
        row_digest=records.member_row_digest(
            store.repository_id,
            member.member_id,
            member.family_revision_id,
            member.invariant_revision_id,
            member.provenance,
        ),
    )


def _member_result(
    request: FamilyMemberRequest,
    state: Literal["created", "no_change"],
) -> CreateFamilyMemberResult:
    return CreateFamilyMemberResult(
        state=state,
        repository_id=request.repository_id,
        member_id=request.member.member_id,
        stored=state == "created",
    )


def _member_refusal(
    request: FamilyMemberRequest, refused: KnowledgeRefusal
) -> CreateFamilyMemberResult:
    return CreateFamilyMemberResult(
        state="refused",
        repository_id=request.repository_id,
        member_id=request.member.member_id,
        stored=False,
        refusal=refused,
    )


def _member_removal_refusal(
    request: RemoveFamilyMemberRequest, refused: KnowledgeRefusal
) -> RemoveFamilyMemberResult:
    return RemoveFamilyMemberResult(
        state="refused",
        repository_id=request.repository_id,
        member_id=request.member_id,
        refusal=refused,
    )


__all__ = [
    "create_family_member",
    "delete_family_member",
    "find_membership_by_pair",
    "get_family_member",
    "insert_family_member",
    "insert_family_member_draft",
    "list_families_for_invariant_revision",
    "list_members",
    "remove_family_member",
]
