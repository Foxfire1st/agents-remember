"""Row codecs between the typed knowledge vocabulary and the declared columns.

Every conversion in both directions lives here, so the column order, the canonical JSON text
form of a typed column and the digest recomputation have exactly one owner. A reader that
decodes a row without recomputing its digest would accept a rewritten payload as its own
identity, so :func:`decode_revision_row` and :func:`decode_family_revision_row` verify the seal
on the way out.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter

from agents_remember.kernel.canonical_json import decoded_json, sha256_digest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.digest import (
    family_revision_payload_digest,
    revision_payload_digest,
)
from agents_remember.models.knowledge.family import (
    FamilyIdentity,
    FamilyRevision,
    StoredFamilyRevision,
)
from agents_remember.models.knowledge.graph import (
    FamilyMember,
)
from agents_remember.models.knowledge.invariant import (
    InvariantIdentity,
    InvariantRevision,
    StoredInvariantRevision,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.source import (
    SourceLocator,
)
from agents_remember.models.knowledge_files.record_state import RECORD_STATES, RecordState

_LOCATOR_ADAPTER: TypeAdapter[Any] = TypeAdapter(SourceLocator)


def decode_typed_column(text: str) -> Any:
    """Decode one stored typed column, refusing ambiguous JSON."""

    return decoded_json(text)


def decode_authorship(text: str) -> Authorship:
    return Authorship.model_validate(decode_typed_column(text))


def decode_state(value: object) -> RecordState:
    """Narrow a stored revision state to the derived states (proposed, reviewed, consolidated)."""

    text = str(value)
    for state in RECORD_STATES:
        if text == state:
            return state
    raise KnowledgeStorageError(
        f"stored revision declares state {text!r}, which is not one of {list(RECORD_STATES)}"
    )


def decode_repository_row(row: Sequence[Any]) -> RepositoryIdentity:
    return RepositoryIdentity(repository_id=str(row[0]), authority_home=str(row[1]))


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
        state=decode_state(row[8]),
        provenance=decode_authorship(str(row[9])),
        predecessors=tuple(sorted(predecessors)),
        payload_digest=str(row[10]),
    )
    recomputed = revision_payload_digest(revision)
    if recomputed != revision.payload_digest:
        raise KnowledgeStorageError(
            f"stored revision {revision.revision_id} does not match its payload digest: "
            f"stored {revision.payload_digest}, recomputed {recomputed}. The row was altered "
            "behind its identity; treat the store as damaged and recover the revision from an "
            "intact snapshot."
        )
    return StoredInvariantRevision(revision=revision)


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
        state=decode_state(row[5]),
        provenance=decode_authorship(str(row[6])),
        predecessors=tuple(sorted(predecessors)),
        payload_digest=str(row[7]),
    )
    recomputed = family_revision_payload_digest(revision)
    if recomputed != revision.payload_digest:
        raise KnowledgeStorageError(
            f"stored family revision {revision.revision_id} does not match its payload digest: "
            f"stored {revision.payload_digest}, recomputed {recomputed}. The row was altered "
            "behind its identity; treat the store as damaged and recover the revision from an "
            "intact snapshot."
        )
    return StoredFamilyRevision(revision=revision)


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
