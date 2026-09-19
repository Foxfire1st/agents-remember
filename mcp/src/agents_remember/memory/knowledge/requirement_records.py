"""Row codecs for the requirement-revision record group.

The record group stores nothing of its own. A requirement revision *is* an envelope record: one
``knowledge_record`` row naming the ``requirement_revision`` kind and the route that governs it, plus
one ``record_revision`` row per immutable revision holding the payload and its content digest. Every
conversion in both directions therefore has an owner, and it is not this module:

* the generic ``(repository_id, revision_id, record_id, record_schema, payload,
  predecessor_revision_id, content_digest, provenance)`` codec is reused from
  :mod:`agents_remember.memory.knowledge.facet_records`, which is the envelope's row codec for the
  authored-judgment record group. A second implementation of that tuple, or of the digest the seal is
  verified against, would be a second place the same identity could be computed two ways -- so this
  module reuses it and adds only what is specific to a requirement revision;
* the payload is decoded through the **frozen shape the envelope registry resolves**, not through a
  shape restated here, so a stored payload is read back as the model the write path validated it
  against;
* the stored ``content_digest`` is recomputed on the way out. A payload rewritten behind its identity
  would otherwise be served as the revision that identity names, and the whole point of a sealed
  revision is that it cannot be.

The two statements this module *does* own are the ones that name the requirement kind: a record group
that read another kind's rows would be reading a table it does not own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agents_remember.kernel.canonical_json import decoded_json
from agents_remember.memory.knowledge.facet_records import (
    RecordRevisionDraft,
    record_revision_digest,
    record_revision_row,
)
from agents_remember.memory.knowledge.record_envelope import PAYLOAD_MODELS
from agents_remember.memory.knowledge.records import decode_authorship, encode_authorship
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.requirement import (
    REQUIREMENT_RECORD_LIFECYCLE,
    REQUIREMENT_REVISION_KIND,
    REQUIREMENT_REVISION_SCHEMA,
    RequirementRevisionPayload,
)

REQUIREMENT_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

REQUIREMENT_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

REQUIREMENT_RECORD_ROW = (
    "SELECT repository_id, record_id, kind, governing_route_id, record_schema, provenance "
    "FROM knowledge_record WHERE repository_id = ? AND record_id = ? AND kind = ?"
)

REQUIREMENT_RECORD_ROWS_OF_KIND = (
    "SELECT repository_id, record_id, kind, governing_route_id, record_schema, provenance "
    "FROM knowledge_record WHERE repository_id = ? AND kind = ? ORDER BY record_id"
)

REQUIREMENT_REVISION_ROWS = (
    "SELECT revision_id, record_id, record_schema, payload, predecessor_revision_id, "
    "content_digest, provenance FROM record_revision "
    "WHERE repository_id = ? AND record_id = ? ORDER BY revision_id"
)


@dataclass(frozen=True)
class StoredRequirementRecord:
    """One decoded ``knowledge_record`` row of the requirement kind, as a value."""

    repository_id: str
    record_id: str
    kind: str
    record_schema: str
    governing_route_id: str | None
    provenance: Authorship


@dataclass(frozen=True)
class StoredRequirementRevision:
    """One decoded ``record_revision`` row of the requirement kind, with its seal verified.

    ``payload`` is the validated frozen model rather than the mapping it was decoded from, so a
    reader works with the shape the registry admitted instead of re-deriving one.
    """

    revision_id: str
    record_id: str
    record_schema: str
    payload: RequirementRevisionPayload
    predecessor_revision_id: str | None
    content_digest: str
    provenance: Authorship


def requirement_record_row(
    record_id: str,
    authority_home: str,
    governing_route_id: str | None,
    authorship: Authorship,
) -> tuple[Any, ...]:
    """Return the ``knowledge_record`` column tuple for one requirement record.

    ``kind`` and ``record_schema`` are declared by this record group rather than supplied, so a
    caller cannot store a pair the envelope's registry would refuse. ``authority_home`` is the bound
    namespace's own home, read from the store's repository row rather than accepted from a caller --
    it keeps the shipped meaning that column already has.

    The lifecycle column is the *storage* lifecycle of this record group (``proposed``), not the
    owner's acceptance state: the owner's state travels in the payload's ``state_at_origin``.
    """

    return (
        record_id,
        REQUIREMENT_REVISION_KIND,
        authority_home,
        REQUIREMENT_RECORD_LIFECYCLE,
        governing_route_id,
        REQUIREMENT_REVISION_SCHEMA,
        encode_authorship(authorship),
    )


def requirement_revision_row(
    revision_id: str,
    record_id: str,
    payload: Mapping[str, Any],
    predecessor_revision_id: str | None,
    authorship: Authorship,
) -> tuple[Any, ...]:
    """Return the ``record_revision`` column tuple for one sealed requirement revision."""

    return record_revision_row(
        RecordRevisionDraft(
            record_id=record_id,
            revision_id=revision_id,
            record_schema=REQUIREMENT_REVISION_SCHEMA,
            predecessor_revision_id=predecessor_revision_id,
        ),
        payload,
        authorship,
    )


def decode_requirement_record_row(row: Sequence[Any]) -> StoredRequirementRecord:
    """Decode one ``knowledge_record`` row, refusing a row this record group does not own."""

    repository_id, record_id = str(row[0]), str(row[1])
    kind, route, record_schema = str(row[2]), row[3], str(row[4])
    if kind != REQUIREMENT_REVISION_KIND or record_schema != REQUIREMENT_REVISION_SCHEMA:
        raise KnowledgeStorageError(
            f"record {record_id} declares kind {kind!r} and schema {record_schema!r} rather than "
            f"{REQUIREMENT_REVISION_KIND!r} / {REQUIREMENT_REVISION_SCHEMA!r}; it is not a "
            "requirement revision of this record group"
        )
    return StoredRequirementRecord(
        repository_id=repository_id,
        record_id=record_id,
        kind=kind,
        record_schema=record_schema,
        governing_route_id=None if route is None else str(route),
        provenance=decode_authorship(str(row[5])),
    )


def decode_requirement_revision_row(row: Sequence[Any]) -> StoredRequirementRevision:
    """Decode one ``record_revision`` row and verify that its stored seal still holds."""

    revision_id, record_id = str(row[0]), str(row[1])
    record_schema = str(row[2])
    if record_schema != REQUIREMENT_REVISION_SCHEMA:
        raise KnowledgeStorageError(
            f"revision {revision_id} declares record_schema {record_schema!r} rather than "
            f"{REQUIREMENT_REVISION_SCHEMA!r}; the row was written outside this record group"
        )
    payload = decoded_json(str(row[3]))
    if not isinstance(payload, Mapping):
        raise KnowledgeStorageError(
            f"requirement revision {revision_id} carries a payload that is not a JSON object"
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
            f"stored requirement revision {revision_id} does not match its content digest: stored "
            f"{stored_digest}, recomputed {recomputed}. The row was altered behind its identity; "
            "treat the store as damaged and recover the revision from an intact snapshot."
        )
    return StoredRequirementRevision(
        revision_id=revision_id,
        record_id=record_id,
        record_schema=record_schema,
        payload=_validated_payload(revision_id, payload),
        predecessor_revision_id=predecessor,
        content_digest=stored_digest,
        provenance=decode_authorship(str(row[6])),
    )


def _validated_payload(revision_id: str, payload: Mapping[str, Any]) -> RequirementRevisionPayload:
    """Validate one stored payload against the frozen shape the registry resolves for its kind.

    The model is read from the registry rather than named here, so a stored payload is read back as
    the shape the write path admitted it against and this module cannot resolve a shape the registry
    would not. A payload that no longer validates is a store damaged outside the operation, so it is
    reported as one rather than raised as a bare validation failure.
    """

    model = PAYLOAD_MODELS[(REQUIREMENT_REVISION_KIND, REQUIREMENT_REVISION_SCHEMA)]
    if model is not RequirementRevisionPayload:  # pragma: no cover - the registry is the source
        raise KnowledgeStorageError(
            f"the envelope registry resolves the requirement kind to {model.__name__} while "
            f"revision {revision_id} was written as {RequirementRevisionPayload.__name__}"
        )
    try:
        return RequirementRevisionPayload.model_validate(dict(payload))
    except ValidationError as error:
        raise KnowledgeStorageError(
            f"stored requirement revision {revision_id} does not validate against "
            f"{REQUIREMENT_REVISION_SCHEMA}: {error}"
        ) from error
