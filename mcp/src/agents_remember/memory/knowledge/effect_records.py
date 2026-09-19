"""Row codecs for the authored-effect record group.

The record group stores nothing of its own beyond one succession edge. An effect claim, a preservation
claim, an unresolved question and a change set *are* envelope records: one ``knowledge_record`` row
naming the kind and the route that governs it, plus one ``record_revision`` row per immutable revision
holding the payload and its content digest. Every conversion in both directions therefore has an
owner, and it is not this module:

* the generic ``(repository_id, revision_id, record_id, record_schema, payload,
  predecessor_revision_id, content_digest, provenance)`` codec is reused from
  :mod:`agents_remember.memory.knowledge.facet_records`, which is the envelope's row codec for the
  authored record groups. A second implementation of that tuple, or of the digest the seal is verified
  against, would be a second place the same identity could be computed two ways -- so this module
  reuses it and adds only what is specific to this record group;
* the payload is decoded through the **frozen shape the envelope registry resolves for its kind**, not
  through a shape restated here, so a stored payload is read back as the model the write path
  validated it against and a record of one kind can never be decoded as another;
* the stored ``content_digest`` is recomputed on the way out. A payload rewritten behind its identity
  would otherwise be served as the revision that identity names, and the whole point of a sealed
  revision is that it cannot be.

The statements this module owns are the ones that name this record group's kinds and its one edge
table: a record group that read another kind's rows would be reading records it does not own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from agents_remember.kernel.canonical_json import decoded_json, sha256_digest
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_digest,
    record_revision_row,
)
from agents_remember.memory.knowledge.record_envelope import PAYLOAD_MODELS
from agents_remember.memory.knowledge.records import decode_authorship, encode_authorship
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.change_set import (
    SEMANTIC_CHANGE_SET_KIND,
    SemanticChangeSetPayload,
)
from agents_remember.models.knowledge.effect import (
    MEMBER_KINDS,
    InvariantEffectClaimPayload,
    PreservationClaimPayload,
    UnresolvedQuestionPayload,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The four record kinds this record group owns, in the order the vocabulary declares them. Derived
# from the two declarations rather than restated, so a kind cannot join the registry without joining
# the group's own readers and its after-batch seal pass.
EFFECT_RECORD_KINDS: tuple[str, ...] = (*MEMBER_KINDS, SEMANTIC_CHANGE_SET_KIND)

# The four frozen payload shapes this record group's kinds resolve to. The alias exists so a decoder's
# return type says "one of this group's payloads" rather than repeating the union, and so a fifth kind
# added to the vocabulary is a type error here rather than a silent omission.
EffectPayload = (
    InvariantEffectClaimPayload
    | PreservationClaimPayload
    | UnresolvedQuestionPayload
    | SemanticChangeSetPayload
)

EFFECT_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

EFFECT_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

CHANGE_SET_PREDECESSOR_INSERT = (
    "INSERT INTO change_set_predecessor (repository_id, successor_change_set_id, "
    "predecessor_change_set_id, provenance) VALUES (?, ?, ?, ?)"
)

EFFECT_RECORD_BY_ID = (
    "SELECT repository_id, record_id, kind, lifecycle, governing_route_id, record_schema, provenance "
    "FROM knowledge_record WHERE repository_id = ? AND record_id = ?"
)

EFFECT_RECORDS_OF_KIND = (
    "SELECT repository_id, record_id, kind, lifecycle, governing_route_id, record_schema, provenance "
    "FROM knowledge_record WHERE repository_id = ? AND kind = ? ORDER BY record_id"
)

EFFECT_REVISIONS_OF_RECORD = (
    "SELECT revision_id, record_id, record_schema, payload, predecessor_revision_id, content_digest, "
    "provenance FROM record_revision WHERE repository_id = ? AND record_id = ? ORDER BY revision_id"
)

# Every stored revision of one kind, joined through the envelope that names the kind. The join is the
# reason this record group can answer "every stored effect claim" without reading another group's
# rows: the kind is the envelope's, and the payload is the revision's.
EFFECT_REVISIONS_OF_KIND = (
    "SELECT revision.revision_id, revision.record_id, revision.record_schema, revision.payload, "
    "revision.predecessor_revision_id, revision.content_digest, revision.provenance "
    "FROM record_revision AS revision "
    "JOIN knowledge_record AS envelope ON envelope.repository_id = revision.repository_id "
    "AND envelope.record_id = revision.record_id "
    "WHERE revision.repository_id = ? AND envelope.kind = ? ORDER BY revision.revision_id"
)

CHANGE_SET_PREDECESSOR_EDGES = (
    "SELECT successor_change_set_id, predecessor_change_set_id FROM change_set_predecessor "
    "WHERE repository_id = ? ORDER BY successor_change_set_id, predecessor_change_set_id"
)

CHANGE_SET_PREDECESSORS_OF = (
    "SELECT predecessor_change_set_id FROM change_set_predecessor "
    "WHERE repository_id = ? AND successor_change_set_id = ? ORDER BY predecessor_change_set_id"
)


@dataclass(frozen=True)
class EffectRecordDraft:
    """One authored-effect envelope, as a value rather than seven positional arguments."""

    record_id: str
    kind: str
    record_schema: str
    authority_home: str
    lifecycle: str
    governing_route_id: str | None = None


@dataclass(frozen=True)
class StoredEffectRecord:
    """One decoded ``knowledge_record`` row of an authored-effect kind, as a value."""

    repository_id: str
    record_id: str
    kind: str
    lifecycle: str
    record_schema: str
    governing_route_id: str | None
    provenance: Authorship


@dataclass(frozen=True)
class StoredEffectRevision:
    """One decoded ``record_revision`` row of an authored-effect kind, with its seal verified.

    ``payload`` is the validated frozen model rather than the mapping it was decoded from, so a reader
    works with the shape the registry admitted instead of re-deriving one -- and a stored row carrying
    a field the vocabulary does not declare could not be decoded into one at all.
    """

    revision_id: str
    record_id: str
    kind: str
    record_schema: str
    payload: EffectPayload
    predecessor_revision_id: str | None
    content_digest: str
    provenance: Authorship


def effect_record_row(draft: EffectRecordDraft, authorship: Authorship) -> tuple[Any, ...]:
    """Return the ``knowledge_record`` column tuple for one authored-effect record.

    ``kind`` and ``record_schema`` are declared by the caller's own command rather than supplied by a
    payload, so a caller cannot store a pair the envelope's registry would refuse, and
    ``authority_home`` is the bound namespace's own home -- it keeps the shipped meaning that column
    already has. The lifecycle column is the storage lifecycle of this record group, which is
    ``proposed`` for every row it writes.
    """

    return (
        draft.record_id,
        draft.kind,
        draft.authority_home,
        draft.lifecycle,
        draft.governing_route_id,
        draft.record_schema,
        encode_authorship(authorship),
    )


def effect_revision_row(
    revision_id: str,
    record_id: str,
    record_schema: str,
    payload: Mapping[str, Any],
    authorship: Authorship,
) -> tuple[Any, ...]:
    """Return the ``record_revision`` column tuple for one sealed authored-effect revision."""

    return record_revision_row(
        RecordRevisionDraft(
            record_id=record_id,
            revision_id=revision_id,
            record_schema=record_schema,
            predecessor_revision_id=None,
        ),
        payload,
        authorship,
    )


def change_set_predecessor_row(
    successor_change_set_id: str, predecessor_change_set_id: str, authorship: Authorship
) -> tuple[str, ...]:
    """Return the ``change_set_predecessor`` column tuple for one succession edge."""

    return (
        successor_change_set_id,
        predecessor_change_set_id,
        encode_authorship(authorship),
    )


def effect_record_row_digest(
    repository_id: str, draft: EffectRecordDraft, provenance: Authorship
) -> str:
    """Digest one authored-effect envelope row, so a receipt can name the value it reported.

    It is computed from the same fields the row stores, in the shipped shape the facet codec uses for
    the same table, so the two record groups report one table's row digest the same way and a caller
    carries a receipt entry straight into an expectation.
    """

    return sha256_digest(
        {
            "table": "knowledge_record",
            "repository_id": repository_id,
            "record_id": draft.record_id,
            "kind": draft.kind,
            "record_schema": draft.record_schema,
            "lifecycle": draft.lifecycle,
            "authority_home": draft.authority_home,
            "governing_route_id": draft.governing_route_id,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_effect_record_row(row: Sequence[Any]) -> StoredEffectRecord:
    """Decode one ``knowledge_record`` row of this record group."""

    return StoredEffectRecord(
        repository_id=str(row[0]),
        record_id=str(row[1]),
        kind=str(row[2]),
        lifecycle=str(row[3]),
        governing_route_id=None if row[4] is None else str(row[4]),
        record_schema=str(row[5]),
        provenance=decode_authorship(str(row[6])),
    )


def decode_effect_revision_row(row: Sequence[Any], kind: str) -> StoredEffectRevision:
    """Decode one ``record_revision`` row, verifying its seal and its declared kind.

    The kind is supplied by the caller because a revision row does not carry one: the kind lives on
    the envelope. A row whose ``record_schema`` is not the shape that kind declares was written
    outside this record group and is reported as a damaged store rather than decoded anyway.
    """

    revision_id, record_id = str(row[0]), str(row[1])
    record_schema = str(row[2])
    payload = decoded_json(str(row[3]))
    if not isinstance(payload, Mapping):
        raise KnowledgeStorageError(
            f"authored-effect revision {revision_id} carries a payload that is not a JSON object"
        )
    model = PAYLOAD_MODELS.get((kind, record_schema))
    if model is None:
        raise KnowledgeStorageError(
            f"revision {revision_id} declares kind {kind!r} and schema {record_schema!r}, which the "
            "envelope registry does not resolve; the row was written outside this record group"
        )
    predecessor = None if row[4] is None else str(row[4])
    stored_digest = str(row[5])
    recomputed = record_revision_digest(
        RecordRevisionDraft(
            record_id=record_id,
            revision_id=revision_id,
            record_schema=record_schema,
            predecessor_revision_id=predecessor,
        ),
        payload,
    )
    if recomputed != stored_digest:
        raise KnowledgeStorageError(
            f"stored authored-effect revision {revision_id} does not match its content digest: "
            f"stored {stored_digest}, recomputed {recomputed}. The row was altered behind its "
            "identity; treat the store as damaged and recover the revision from an intact snapshot."
        )
    return StoredEffectRevision(
        revision_id=revision_id,
        record_id=record_id,
        kind=kind,
        record_schema=record_schema,
        payload=_validated_payload(model, payload, revision_id),
        predecessor_revision_id=predecessor,
        content_digest=stored_digest,
        provenance=decode_authorship(str(row[6])),
    )


def _validated_payload(
    model: type[Any], payload: Mapping[str, Any], revision_id: str
) -> EffectPayload:
    """Validate one stored payload against the frozen shape the registry resolves for its kind.

    The model is read from the registry rather than named here, so a stored payload is read back as
    the shape the write path admitted it against and this module cannot resolve a shape the registry
    would not. A payload that no longer validates is a store damaged outside the operation, so it is
    reported as one rather than raised as a bare validation failure.
    """

    try:
        return model.model_validate(dict(payload))  # type: ignore[no-any-return]
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"stored authored-effect revision {revision_id} does not validate against its declared "
            f"payload shape: {error}"
        ) from error


def stored_revisions_of_kind(
    store: OpenedKnowledgeStore, kind: str
) -> tuple[StoredEffectRevision, ...]:
    """Return every stored revision of one authored-effect kind, in stored order.

    The reader lives here, beside the decoder, because two callers need the same rows for different
    reasons -- the write path's duplicate scan and the after-batch seal pass -- and a per-caller query
    would let the two disagree about which rows belong to a kind.
    """

    return tuple(
        decode_effect_revision_row(row, kind)
        for row in store.connection.execute(EFFECT_REVISIONS_OF_KIND, (store.repository_id, kind))
    )
