"""Row codecs between the typed knowledge vocabulary and the declared columns.

Every conversion in both directions lives here, so the column order, the canonical JSON text
form of a typed column and the digest recomputation have exactly one owner. A reader that
decodes a row without recomputing its digest would accept a rewritten payload as its own
identity, so :func:`decode_revision_row` and :func:`decode_family_revision_row` verify the seal
on the way out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, get_args
from uuid import UUID

from pydantic import TypeAdapter

from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json, sha256_digest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import ACCEPTED_STATE, PROPOSED_STATE, Authorship
from agents_remember.models.knowledge.base import KnowledgeState
from agents_remember.models.knowledge.digest import (
    family_revision_payload_digest,
    revision_payload_digest,
    sealed_family_revision,
    sealed_revision,
)
from agents_remember.models.knowledge.family import (
    FamilyIdentity,
    FamilyRevision,
    FamilyRevisionDraft,
    StoredFamilyRevision,
)
from agents_remember.models.knowledge.graph import (
    FamilyMember,
    RealizationClaim,
    RealizationClaimDraft,
    RealizationRole,
)
from agents_remember.models.knowledge.invariant import (
    InvariantIdentity,
    InvariantRevision,
    StoredInvariantRevision,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import RevisionDraft
from agents_remember.models.knowledge.source import (
    SourceAnchor,
    SourceIdentity,
    SourceLocator,
)

_LOCATOR_ADAPTER: TypeAdapter[Any] = TypeAdapter(SourceLocator)


def encode_typed_column(value: Any) -> str:
    """Encode one typed-column value as canonical JSON text for storage."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return canonical_json_bytes(value).decode("utf-8")


def decode_typed_column(text: str) -> Any:
    """Decode one stored typed column, refusing ambiguous JSON."""

    return decoded_json(text)


def encode_authorship(authorship: Authorship) -> str:
    return encode_typed_column(authorship)


def decode_authorship(text: str) -> Authorship:
    return Authorship.model_validate(decode_typed_column(text))


def decode_state_at_origin(value: object) -> KnowledgeState:
    """Narrow a stored origin state to the two authored states this schema admits."""

    text = str(value)
    if text == PROPOSED_STATE:
        return PROPOSED_STATE
    if text == ACCEPTED_STATE:
        return ACCEPTED_STATE
    raise KnowledgeStorageError(
        f"stored revision declares acceptance state {text!r}, which is not one of "
        f"{PROPOSED_STATE!r} or {ACCEPTED_STATE!r}"
    )


def repository_row(identity: RepositoryIdentity) -> tuple[str, str]:
    return (identity.repository_id, identity.authority_home)


def decode_repository_row(row: Sequence[Any]) -> RepositoryIdentity:
    return RepositoryIdentity(repository_id=str(row[0]), authority_home=str(row[1]))


def invariant_row(
    repository_id: str, invariant_id: str, display_label: str, provenance: Authorship
) -> tuple[str, str, str, str]:
    return (repository_id, invariant_id, display_label, encode_authorship(provenance))


def invariant_row_digest(
    repository_id: str, invariant_id: str, display_label: str, provenance: Authorship
) -> str:
    """Digest one invariant identity row so a label edit can name the row it expects."""

    return sha256_digest(
        {
            "table": "invariant",
            "repository_id": repository_id,
            "invariant_id": invariant_id,
            "display_label": display_label,
            "label_provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_invariant_row(row: Sequence[Any]) -> InvariantIdentity:
    repository_id, invariant_id, display_label = str(row[0]), str(row[1]), str(row[2])
    provenance = decode_authorship(str(row[3]))
    return InvariantIdentity(
        repository_id=repository_id,
        invariant_id=invariant_id,
        display_label=display_label,
        label_provenance=provenance,
        row_digest=invariant_row_digest(repository_id, invariant_id, display_label, provenance),
    )


def revision_row(revision: InvariantRevision) -> tuple[Any, ...]:
    """Return the exact ``invariant_revision`` column tuple for one sealed revision."""

    return (
        revision.repository_id,
        revision.invariant_id,
        revision.revision_id,
        revision.display_version,
        revision.statement,
        revision.applicability,
        encode_typed_column(list(revision.conditions)),
        encode_typed_column(list(revision.exclusions)),
        revision.state_at_origin,
        revision.acceptance_ref,
        encode_authorship(revision.provenance),
        revision.payload_digest,
    )


def decode_revision_row(
    row: Sequence[Any], predecessors: tuple[str, ...]
) -> StoredInvariantRevision:
    """Decode one revision row and verify that its stored seal still holds.

    The verification is the point: a row whose text was rewritten behind its identity would
    otherwise be served as if it were the revision that identity names.
    """

    revision = InvariantRevision(
        repository_id=str(row[0]),
        invariant_id=str(row[1]),
        revision_id=str(row[2]),
        display_version=str(row[3]),
        statement=str(row[4]),
        applicability=str(row[5]),
        conditions=tuple(str(item) for item in decode_typed_column(str(row[6]))),
        exclusions=tuple(str(item) for item in decode_typed_column(str(row[7]))),
        state_at_origin=decode_state_at_origin(row[8]),
        acceptance_ref=None if row[9] is None else str(row[9]),
        provenance=decode_authorship(str(row[10])),
        predecessors=tuple(sorted(predecessors)),
        payload_digest=str(row[11]),
    )
    recomputed = revision_payload_digest(revision)
    if recomputed != revision.payload_digest:
        raise KnowledgeStorageError(
            f"stored revision {revision.revision_id} does not match its payload digest: "
            f"stored {revision.payload_digest}, recomputed {recomputed}. The row was altered "
            "behind its identity; treat the store as damaged and recover the revision from an "
            "intact snapshot."
        )
    return StoredInvariantRevision(
        revision=revision, predecessors_sorted=tuple(sorted(revision.predecessors))
    )


def sealed_revision_from_draft(repository_id: str, draft: RevisionDraft) -> InvariantRevision:
    """Seal one authored draft into a complete revision aggregate.

    The predecessor set is sorted here because it enters the digest; the ordering of the set
    is not authored information, while the order of the clause tuples is.
    """

    revision = InvariantRevision(
        repository_id=repository_id,
        invariant_id=draft.invariant_id,
        revision_id=draft.revision_id,
        display_version=draft.display_version,
        statement=draft.statement,
        applicability=draft.applicability,
        conditions=draft.conditions,
        exclusions=draft.exclusions,
        state_at_origin=draft.state_at_origin,
        acceptance_ref=draft.acceptance_ref,
        provenance=draft.provenance,
        predecessors=tuple(sorted(draft.predecessors)),
        payload_digest="0" * 64,
    )
    return sealed_revision(revision)


def predecessor_rows(revision: InvariantRevision) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (revision.repository_id, revision.invariant_id, revision.revision_id, parent_id)
        for parent_id in sorted(revision.predecessors)
    )


def decode_predecessor_rows(rows: Sequence[Sequence[Any]]) -> tuple[str, ...]:
    return tuple(sorted(str(row[0]) for row in rows))


def anchor_row(anchor: SourceAnchor, repository_id: str) -> tuple[str, str, str, str, str, str]:
    return (
        repository_id,
        str(anchor.anchor_id),
        anchor.path,
        encode_typed_column(anchor.source_identity),
        encode_typed_column(anchor.locator),
        encode_authorship(anchor.provenance),
    )


def decode_anchor_row(row: Sequence[Any]) -> SourceAnchor:
    return SourceAnchor(
        anchor_id=UUID(str(row[1])),
        path=str(row[2]),
        source_identity=SourceIdentity.model_validate(decode_typed_column(str(row[3]))),
        locator=_LOCATOR_ADAPTER.validate_python(decode_typed_column(str(row[4]))),
        provenance=decode_authorship(str(row[5])),
    )


def row_mapping(columns: Sequence[str], row: Sequence[Any]) -> Mapping[str, Any]:
    """Pair one row with its declared column names (used by diagnostics and tests)."""

    return dict(zip(columns, row, strict=True))


def family_row(
    repository_id: str, family_id: str, display_label: str, provenance: Authorship
) -> tuple[str, str, str, str]:
    return (repository_id, family_id, display_label, encode_authorship(provenance))


def family_row_digest(
    repository_id: str, family_id: str, display_label: str, provenance: Authorship
) -> str:
    """Digest one family identity row so a label edit can name the row it expects."""

    return sha256_digest(
        {
            "table": "family",
            "repository_id": repository_id,
            "family_id": family_id,
            "display_label": display_label,
            "label_provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_family_row(row: Sequence[Any]) -> FamilyIdentity:
    repository_id, family_id, display_label = str(row[0]), str(row[1]), str(row[2])
    provenance = decode_authorship(str(row[3]))
    return FamilyIdentity(
        repository_id=repository_id,
        family_id=family_id,
        display_label=display_label,
        label_provenance=provenance,
        row_digest=family_row_digest(repository_id, family_id, display_label, provenance),
    )


def family_revision_row(revision: FamilyRevision) -> tuple[Any, ...]:
    """Return the exact ``family_revision`` column tuple for one sealed family revision."""

    return (
        revision.repository_id,
        revision.family_id,
        revision.revision_id,
        revision.display_version,
        revision.joint_guarantee,
        revision.state_at_origin,
        revision.acceptance_ref,
        encode_authorship(revision.provenance),
        revision.payload_digest,
    )


def sealed_family_revision_from_draft(
    repository_id: str, draft: FamilyRevisionDraft
) -> FamilyRevision:
    """Seal one authored family draft into a complete revision aggregate.

    The predecessor set is sorted here because it enters the digest; the ordering of the set is
    not authored information, while the guarantee text is preserved exactly as authored.
    """

    revision = FamilyRevision(
        repository_id=repository_id,
        family_id=draft.family_id,
        revision_id=draft.revision_id,
        display_version=draft.display_version,
        joint_guarantee=draft.joint_guarantee,
        state_at_origin=draft.state_at_origin,
        acceptance_ref=draft.acceptance_ref,
        provenance=draft.provenance,
        predecessors=tuple(sorted(draft.predecessors)),
        payload_digest="0" * 64,
    )
    return sealed_family_revision(revision)


def decode_family_revision_row(
    row: Sequence[Any], predecessors: tuple[str, ...]
) -> StoredFamilyRevision:
    """Decode one family revision row and verify that its stored seal still holds."""

    revision = FamilyRevision(
        repository_id=str(row[0]),
        family_id=str(row[1]),
        revision_id=str(row[2]),
        display_version=str(row[3]),
        joint_guarantee=str(row[4]),
        state_at_origin=decode_state_at_origin(row[5]),
        acceptance_ref=None if row[6] is None else str(row[6]),
        provenance=decode_authorship(str(row[7])),
        predecessors=tuple(sorted(predecessors)),
        payload_digest=str(row[8]),
    )
    recomputed = family_revision_payload_digest(revision)
    if recomputed != revision.payload_digest:
        raise KnowledgeStorageError(
            f"stored family revision {revision.revision_id} does not match its payload digest: "
            f"stored {revision.payload_digest}, recomputed {recomputed}. The row was altered "
            "behind its identity; treat the store as damaged and recover the revision from an "
            "intact snapshot."
        )
    return StoredFamilyRevision(
        revision=revision, predecessors_sorted=tuple(sorted(revision.predecessors))
    )


def family_predecessor_rows(
    revision: FamilyRevision,
) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (revision.repository_id, revision.family_id, revision.revision_id, parent_id)
        for parent_id in sorted(revision.predecessors)
    )


def anchor_row_digest(anchor: SourceAnchor, repository_id: str) -> str:
    """Digest one stored anchor row so an identity reuse can name both payloads."""

    return sha256_digest(
        {
            "table": "source_anchor",
            "repository_id": repository_id,
            "anchor_id": str(anchor.anchor_id),
            "path": anchor.path,
            "source_identity": anchor.source_identity.model_dump(mode="json"),
            "locator": anchor.locator.model_dump(mode="json"),
            "provenance": anchor.provenance.model_dump(mode="json"),
        }
    )


def member_row(member: FamilyMember, repository_id: str) -> tuple[str, str, str, str, str]:
    return (
        repository_id,
        member.member_id,
        member.family_revision_id,
        member.invariant_revision_id,
        encode_authorship(member.provenance),
    )


def member_row_digest(
    repository_id: str,
    member_id: str,
    family_revision_id: str,
    invariant_revision_id: str,
    provenance: Authorship,
) -> str:
    """Digest one membership row so an explicit removal can name the row it expects."""

    return sha256_digest(
        {
            "table": "family_member",
            "repository_id": repository_id,
            "member_id": member_id,
            "family_revision_id": family_revision_id,
            "invariant_revision_id": invariant_revision_id,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_member_row(row: Sequence[Any]) -> FamilyMember:
    repository_id, member_id = str(row[0]), str(row[1])
    family_revision_id, invariant_revision_id = str(row[2]), str(row[3])
    provenance = decode_authorship(str(row[4]))
    return FamilyMember(
        repository_id=repository_id,
        member_id=member_id,
        family_revision_id=family_revision_id,
        invariant_revision_id=invariant_revision_id,
        provenance=provenance,
        row_digest=member_row_digest(
            repository_id, member_id, family_revision_id, invariant_revision_id, provenance
        ),
    )


def claim_row(claim: RealizationClaim, repository_id: str) -> tuple[str, ...]:
    return (
        repository_id,
        claim.claim_id,
        claim.invariant_revision_id,
        claim.anchor_id,
        claim.role,
        claim.rationale,
        encode_authorship(claim.provenance),
    )


def claim_row_digest(
    repository_id: str,
    claim: RealizationClaimDraft,
    anchor_id: str,
    provenance: Authorship,
) -> str:
    """Digest one realization claim row so an explicit removal can name the row it expects."""

    return sha256_digest(
        {
            "table": "realization_claim",
            "repository_id": repository_id,
            "claim_id": claim.claim_id,
            "invariant_revision_id": claim.invariant_revision_id,
            "anchor_id": anchor_id,
            "role": claim.role,
            "rationale": claim.rationale,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_claim_row(row: Sequence[Any]) -> RealizationClaim:
    repository_id, anchor_id = str(row[0]), str(row[3])
    provenance = decode_authorship(str(row[6]))
    draft = RealizationClaimDraft(
        claim_id=str(row[1]),
        invariant_revision_id=str(row[2]),
        role=stored_realization_role(str(row[4])),
        rationale=str(row[5]),
    )
    return RealizationClaim(
        repository_id=repository_id,
        claim_id=draft.claim_id,
        invariant_revision_id=draft.invariant_revision_id,
        anchor_id=anchor_id,
        role=draft.role,
        rationale=draft.rationale,
        provenance=provenance,
        row_digest=claim_row_digest(repository_id, draft, anchor_id, provenance),
    )


def stored_realization_role(text: str) -> RealizationRole:
    """Narrow one stored role to the authored vocabulary, refusing an unknown one.

    Every role this store writes is already in the vocabulary, so an unknown value means the row
    was edited outside the operation. Serving it as if it were authored vocabulary would let a
    reader render a role nobody chose.
    """

    for role in get_args(RealizationRole):
        if text == role:
            return role
    raise KnowledgeStorageError(
        f"stored realization role {text!r} is not one of the authored role vocabulary "
        f"{get_args(RealizationRole)}; the row was altered outside the operation"
    )
