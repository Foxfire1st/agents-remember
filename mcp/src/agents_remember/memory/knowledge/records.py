"""Row codecs between the typed knowledge vocabulary and the declared columns.

Every conversion in both directions lives here, so the column order, the canonical JSON text
form of a typed column and the digest recomputation have exactly one owner. A reader that
decodes a row without recomputing its digest would accept a rewritten payload as its own
identity, so :func:`decode_revision_row` verifies the seal on the way out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json, sha256_digest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import ACCEPTED_STATE, PROPOSED_STATE, Authorship
from agents_remember.models.knowledge.base import KnowledgeState
from agents_remember.models.knowledge.digest import revision_payload_digest, sealed_revision
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
