"""Row codecs between the facet vocabulary and generation 3's declared columns.

Every conversion in both directions lives here, so the column order, the canonical JSON text form
of a typed column and the digest recomputation have exactly one owner per table. Two seals are
recomputed on the way *out* rather than trusted, on the shipped rule that a row whose text was
rewritten behind its identity must not be served as the revision that identity names:

* a facet revision's ``content_digest`` is re-derived from the stored payload;
* an explanation revision's ``payload_digest`` is re-derived from the stored body.

The two mutable-field digests are the other half of the same idea. A facet attachment and an
explanation identity row are not sealed revisions -- one may be explicitly removed, the other
carries the recorded designation -- so an operation that names one of them by digest computes that
digest from the row as it stands. :func:`attachment_row_digest` and :func:`explanation_row_digest`
are what those guards compare against, and the read path exposes the same values, so a caller
carries an expectation straight from a read instead of deriving a second identity scheme.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.records import (
    decode_authorship,
    decode_typed_column,
    encode_authorship,
    encode_typed_column,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.facet import (
    FACET_KINDS,
    FAMILY_GUARANTEE_SUBJECT_KIND,
    FAMILY_REVISION_ENDPOINT_KIND,
    INVARIANT_REVISION_ENDPOINT_KIND,
    INVARIANT_STATEMENT_SUBJECT_KIND,
    REALIZATION_CLAIM_ENDPOINT_KIND,
    SOURCE_ANCHOR_ENDPOINT_KIND,
    AttachmentEndpoint,
    ExplanationSubject,
    FamilyJointGuaranteeSubject,
    FamilyRevisionEndpoint,
    InvariantRevisionEndpoint,
    InvariantStatementSubject,
    RealizationClaimEndpoint,
    SourceAnchorEndpoint,
    endpoint_identity,
    facet_record_schema,
)
from agents_remember.models.knowledge.facet_read import (
    DecisionSupersession,
    ExplanationRecord,
    ExplanationRevision,
    FacetAttachment,
    FacetRecord,
    FacetRevision,
)

# Bumped only when the sealed field set changes: a different payload version is a different identity
# computation, so it is recorded rather than inferred from the presence of fields.
if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

RECORD_REVISION_PAYLOAD_VERSION = "record-revision-payload/v1"
EXPLANATION_REVISION_PAYLOAD_VERSION = "explanation-revision-payload/v1"


# -- facet envelope and its revisions ---------------------------------------------------------


@dataclass(frozen=True)
class FacetEnvelopeDraft:
    """One authored facet envelope, as a value rather than six positional arguments."""

    record_id: str
    facet_kind: str
    authority_home: str
    lifecycle: str
    governing_route_id: str | None = None


@dataclass(frozen=True)
class RecordRevisionDraft:
    """One authored facet revision, as a value rather than a column tuple."""

    record_id: str
    revision_id: str
    record_schema: str
    predecessor_revision_id: str | None = None


@dataclass(frozen=True)
class AttachmentDraft:
    """One authored attachment, as a value rather than a column tuple."""

    attachment_id: str
    facet_revision_id: str
    endpoint: AttachmentEndpoint


# Each row builder below returns one INSERT's positional parameters, so its members are
# ``str | None``: ``None`` is a fact the schema records -- an ungoverned envelope's route, a first
# revision's absent predecessor, the endpoint columns an attachment's own kind leaves unpopulated --
# and the connection binds it as SQL NULL rather than as an omitted column.
def facet_record_row(draft: FacetEnvelopeDraft, authorship: Authorship) -> tuple[str | None, ...]:
    """Return the ``knowledge_record`` column tuple for one facet envelope.

    ``record_schema`` is derived from the facet kind rather than supplied, so the pair the payload
    seam resolves with is the pair the kind declares.
    """

    return (
        draft.record_id,
        draft.facet_kind,
        draft.authority_home,
        draft.lifecycle,
        draft.governing_route_id,
        facet_record_schema_of(draft.facet_kind),
        encode_authorship(authorship),
    )


def facet_record_schema_of(facet_kind: str) -> str:
    """Return the record schema one facet kind stores, refusing a kind with no frozen shape."""

    for declared in FACET_KINDS:
        if facet_kind == declared:
            return facet_record_schema(declared)
    raise KnowledgeStorageError(
        f"facet kind {facet_kind!r} has no frozen payload shape, so no record_schema describes it"
    )


def facet_record_row_digest(
    repository_id: str, draft: FacetEnvelopeDraft, provenance: Authorship
) -> str:
    """Digest one facet envelope row, so an expectation can name it.

    ``record_schema`` is derived from the draft's kind rather than passed, so the digest covers the
    schema the row must store and a caller cannot make the two disagree.
    """

    return sha256_digest(
        {
            "table": "knowledge_record",
            "repository_id": repository_id,
            "record_id": draft.record_id,
            "kind": draft.facet_kind,
            "record_schema": facet_record_schema_of(draft.facet_kind),
            "lifecycle": draft.lifecycle,
            "authority_home": draft.authority_home,
            "governing_route_id": draft.governing_route_id,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_facet_record_row(row: Sequence[Any]) -> FacetRecord:
    """Decode one ``knowledge_record`` row that carries a facet kind."""

    repository_id, record_id = str(row[0]), str(row[1])
    facet_kind, authority_home = str(row[2]), str(row[3])
    lifecycle, governing_route_id = str(row[4]), row[5]
    record_schema = str(row[6])
    provenance = decode_authorship(str(row[7]))
    expected_schema = facet_record_schema_of(facet_kind)
    if record_schema != expected_schema:
        raise KnowledgeStorageError(
            f"facet record {record_id} stores record_schema {record_schema!r} for kind "
            f"{facet_kind!r}, which declares {expected_schema!r}; the row was altered outside the "
            "operation"
        )
    route = None if governing_route_id is None else str(governing_route_id)
    draft = FacetEnvelopeDraft(
        record_id=record_id,
        facet_kind=facet_kind,
        authority_home=authority_home,
        lifecycle=lifecycle,
        governing_route_id=route,
    )
    return FacetRecord(
        repository_id=repository_id,
        record_id=record_id,
        facet_kind=facet_kind,
        record_schema=record_schema,
        lifecycle=lifecycle,
        authority_home=authority_home,
        governing_route_id=route,
        provenance=provenance,
        row_digest=facet_record_row_digest(repository_id, draft, provenance),
    )


def record_revision_row(
    draft: RecordRevisionDraft, payload: Mapping[str, Any], authorship: Authorship
) -> tuple[str | None, ...]:
    """Return the ``record_revision`` column tuple for one sealed facet revision."""

    return (
        draft.revision_id,
        draft.record_id,
        draft.record_schema,
        encode_typed_column(dict(payload)),
        draft.predecessor_revision_id,
        record_revision_digest(draft, payload),
        encode_authorship(authorship),
    )


def record_revision_digest(draft: RecordRevisionDraft, payload: Mapping[str, Any]) -> str:
    """Return the seal of one record revision aggregate.

    The payload is inside the seal, so a revision's identity is a fact about its authored content
    and a rewritten body would no longer match it. The digest excludes only itself.
    """

    return sha256_digest(
        {
            "payload_version": RECORD_REVISION_PAYLOAD_VERSION,
            "record_id": draft.record_id,
            "record_schema": draft.record_schema,
            "payload": dict(payload),
            "predecessor_revision_id": draft.predecessor_revision_id,
        }
    )


def decode_facet_revision_row(row: Sequence[Any], facet_kind: str) -> FacetRevision:
    """Decode one ``record_revision`` row and verify that its stored seal still holds."""

    repository_id, revision_id = str(row[0]), str(row[1])
    record_id, record_schema = str(row[2]), str(row[3])
    payload = decode_typed_column(str(row[4]))
    predecessor_revision_id = row[5]
    content_digest, provenance_text = str(row[6]), str(row[7])
    expected_schema = facet_record_schema_of(facet_kind)
    if record_schema != expected_schema:
        raise KnowledgeStorageError(
            f"facet revision {revision_id} declares record_schema {record_schema!r} but its record "
            f"kind {facet_kind!r} declares {expected_schema!r}"
        )
    if not isinstance(payload, Mapping):
        raise KnowledgeStorageError(
            f"stored facet revision {revision_id} carries a payload that is not a JSON object"
        )
    predecessor = None if predecessor_revision_id is None else str(predecessor_revision_id)
    recomputed = record_revision_digest(
        RecordRevisionDraft(
            record_id=record_id,
            revision_id=revision_id,
            record_schema=record_schema,
            predecessor_revision_id=predecessor,
        ),
        payload,
    )
    if recomputed != content_digest:
        raise KnowledgeStorageError(
            f"stored facet revision {revision_id} does not match its content digest: stored "
            f"{content_digest}, recomputed {recomputed}. The row was altered behind its identity; "
            "treat the store as damaged and recover the revision from an intact snapshot."
        )
    return FacetRevision(
        repository_id=repository_id,
        record_id=record_id,
        revision_id=revision_id,
        facet_kind=facet_kind,
        record_schema=record_schema,
        payload=payload,
        predecessor_revision_id=predecessor,
        content_digest=content_digest,
        provenance=decode_authorship(provenance_text),
    )


# -- attachments ------------------------------------------------------------------------------


def attachment_endpoint_columns(endpoint: AttachmentEndpoint) -> dict[str, str | None]:
    """Return the four endpoint columns one typed endpoint populates.

    Exactly one of them is non-null, which is the group the table's ``CHECK`` requires to match the
    stored ``endpoint_kind``. The other three are explicitly ``None`` rather than omitted, so the
    row's shape does not depend on a default.
    """

    columns: dict[str, str | None] = {
        "invariant_revision_id": None,
        "family_revision_id": None,
        "anchor_id": None,
        "claim_id": None,
    }
    if isinstance(endpoint, InvariantRevisionEndpoint):
        columns["invariant_revision_id"] = endpoint.revision_id
        return columns
    if isinstance(endpoint, FamilyRevisionEndpoint):
        columns["family_revision_id"] = endpoint.revision_id
        return columns
    if isinstance(endpoint, SourceAnchorEndpoint):
        columns["anchor_id"] = endpoint.anchor_id
        return columns
    columns["claim_id"] = endpoint.claim_id
    return columns


def decode_attachment_endpoint(
    endpoint_kind: str,
    invariant_revision_id: object,
    family_revision_id: object,
    anchor_id: object,
    claim_id: object,
) -> AttachmentEndpoint:
    """Decode one stored attachment's typed endpoint, refusing a kind this build cannot check."""

    populated = {
        INVARIANT_REVISION_ENDPOINT_KIND: invariant_revision_id,
        FAMILY_REVISION_ENDPOINT_KIND: family_revision_id,
        SOURCE_ANCHOR_ENDPOINT_KIND: anchor_id,
        REALIZATION_CLAIM_ENDPOINT_KIND: claim_id,
    }
    if endpoint_kind not in populated:
        raise KnowledgeStorageError(
            f"stored attachment declares endpoint kind {endpoint_kind!r}, which is not one of "
            f"{' | '.join(sorted(populated))}; the row was altered outside the operation"
        )
    identity = populated[endpoint_kind]
    if identity is None:
        raise KnowledgeStorageError(
            f"stored attachment declares endpoint kind {endpoint_kind!r} but populates no column "
            "of that kind; the table's own constraint was bypassed"
        )
    text = str(identity)
    if endpoint_kind == INVARIANT_REVISION_ENDPOINT_KIND:
        return InvariantRevisionEndpoint(revision_id=text)
    if endpoint_kind == FAMILY_REVISION_ENDPOINT_KIND:
        return FamilyRevisionEndpoint(revision_id=text)
    if endpoint_kind == SOURCE_ANCHOR_ENDPOINT_KIND:
        return SourceAnchorEndpoint(anchor_id=text)
    return RealizationClaimEndpoint(claim_id=text)


def attachment_row(draft: AttachmentDraft, authorship: Authorship) -> tuple[str | None, ...]:
    """Return the ``facet_attachment`` column tuple for one authored attachment."""

    columns = attachment_endpoint_columns(draft.endpoint)
    return (
        draft.attachment_id,
        draft.facet_revision_id,
        draft.endpoint.kind,
        columns["invariant_revision_id"],
        columns["family_revision_id"],
        columns["anchor_id"],
        columns["claim_id"],
        encode_authorship(authorship),
    )


def attachment_row_digest(
    repository_id: str, draft: AttachmentDraft, provenance: Authorship
) -> str:
    """Digest one attachment row, so an explicit removal can name the row it expects."""

    return sha256_digest(
        {
            "table": "facet_attachment",
            "repository_id": repository_id,
            "attachment_id": draft.attachment_id,
            "facet_revision_id": draft.facet_revision_id,
            "endpoint_kind": draft.endpoint.kind,
            "endpoint_id": endpoint_identity(draft.endpoint),
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_attachment_row(row: Sequence[Any]) -> FacetAttachment:
    """Decode one ``facet_attachment`` row."""

    repository_id, attachment_id = str(row[0]), str(row[1])
    facet_revision_id, endpoint_kind = str(row[2]), str(row[3])
    endpoint = decode_attachment_endpoint(endpoint_kind, row[4], row[5], row[6], row[7])
    provenance = decode_authorship(str(row[8]))
    return FacetAttachment(
        repository_id=repository_id,
        attachment_id=attachment_id,
        facet_revision_id=facet_revision_id,
        endpoint=endpoint,
        provenance=provenance,
        row_digest=attachment_row_digest(
            repository_id,
            AttachmentDraft(
                attachment_id=attachment_id,
                facet_revision_id=facet_revision_id,
                endpoint=endpoint,
            ),
            provenance,
        ),
    )


# -- decision supersession --------------------------------------------------------------------


def supersession_row(
    superseding_revision_id: str, superseded_revision_id: str, authorship: Authorship
) -> tuple[str, ...]:
    """Return the ``facet_decision_supersession`` column tuple for one recorded edge."""

    return (superseding_revision_id, superseded_revision_id, encode_authorship(authorship))


def supersession_row_digest(
    repository_id: str,
    superseding_revision_id: str,
    superseded_revision_id: str,
    provenance: Authorship,
) -> str:
    """Digest one supersession edge row."""

    return sha256_digest(
        {
            "table": "facet_decision_supersession",
            "repository_id": repository_id,
            "superseding_revision_id": superseding_revision_id,
            "superseded_revision_id": superseded_revision_id,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_supersession_row(row: Sequence[Any]) -> DecisionSupersession:
    """Decode one ``facet_decision_supersession`` row."""

    repository_id = str(row[0])
    superseding, superseded = str(row[1]), str(row[2])
    provenance = decode_authorship(str(row[3]))
    return DecisionSupersession(
        repository_id=repository_id,
        superseding_revision_id=superseding,
        superseded_revision_id=superseded,
        provenance=provenance,
        row_digest=supersession_row_digest(repository_id, superseding, superseded, provenance),
    )


# -- the explanation pair ---------------------------------------------------------------------


def subject_columns(subject: ExplanationSubject) -> dict[str, str | None]:
    """Return the four subject columns one typed subject populates, the others explicitly null."""

    if isinstance(subject, InvariantStatementSubject):
        return {
            "subject_invariant_id": subject.invariant_id,
            "subject_invariant_revision_id": subject.revision_id,
            "subject_family_id": None,
            "subject_family_revision_id": None,
        }
    return {
        "subject_invariant_id": None,
        "subject_invariant_revision_id": None,
        "subject_family_id": subject.family_id,
        "subject_family_revision_id": subject.revision_id,
    }


def decode_explanation_subject(
    subject_kind: str,
    invariant_id: object,
    invariant_revision_id: object,
    family_id: object,
    family_revision_id: object,
) -> ExplanationSubject:
    """Decode one explanation's typed subject, refusing a kind this build cannot check.

    Both halves of the group are required for the kind that declares them: the identity and the
    revision are one key, and a row that populated only one of them would be a subject the table's
    own constraint was bypassed to store.
    """

    if subject_kind == INVARIANT_STATEMENT_SUBJECT_KIND:
        return InvariantStatementSubject(
            invariant_id=_required_subject_part(
                invariant_id, "invariant identity", INVARIANT_STATEMENT_SUBJECT_KIND
            ),
            revision_id=_required_subject_part(
                invariant_revision_id, "invariant revision", INVARIANT_STATEMENT_SUBJECT_KIND
            ),
        )
    if subject_kind == FAMILY_GUARANTEE_SUBJECT_KIND:
        return FamilyJointGuaranteeSubject(
            family_id=_required_subject_part(
                family_id, "family identity", FAMILY_GUARANTEE_SUBJECT_KIND
            ),
            revision_id=_required_subject_part(
                family_revision_id, "family revision", FAMILY_GUARANTEE_SUBJECT_KIND
            ),
        )
    raise KnowledgeStorageError(
        f"stored explanation declares subject kind {subject_kind!r}, which is not one of "
        f"{INVARIANT_STATEMENT_SUBJECT_KIND} | {FAMILY_GUARANTEE_SUBJECT_KIND}; the row was altered "
        "outside the operation"
    )


def _required_subject_part(value: object, part: str, subject_kind: str) -> str:
    if value is None:
        raise KnowledgeStorageError(
            f"stored explanation declares subject kind {subject_kind!r} but its {part} column is "
            "null; the table's own constraint was bypassed"
        )
    return str(value)


def explanation_row(
    explanation_id: str,
    subject: ExplanationSubject,
    current_revision_id: str | None,
    authorship: Authorship,
) -> tuple[str | None, ...]:
    """Return the ``explanation`` column tuple for one authored explanation record."""

    columns = subject_columns(subject)
    return (
        explanation_id,
        subject.kind,
        columns["subject_invariant_id"],
        columns["subject_invariant_revision_id"],
        columns["subject_family_id"],
        columns["subject_family_revision_id"],
        current_revision_id,
        encode_authorship(authorship),
    )


def explanation_row_digest(
    repository_id: str,
    explanation_id: str,
    subject: ExplanationSubject,
    current_revision_id: str | None,
    provenance: Authorship,
) -> str:
    """Digest one explanation identity row, the value its designation guard compares against.

    The recorded designation is inside the digest, so every designation names the row it expects --
    including the designation it is replacing.
    """

    return sha256_digest(
        {
            "table": "explanation",
            "repository_id": repository_id,
            "explanation_id": explanation_id,
            "subject_kind": subject.kind,
            "subject_id": _subject_record_id(subject),
            "subject_revision_id": subject.revision_id,
            "current_revision_id": current_revision_id,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def _subject_record_id(subject: ExplanationSubject) -> str:
    if isinstance(subject, InvariantStatementSubject):
        return subject.invariant_id
    return subject.family_id


def decode_explanation_row(row: Sequence[Any]) -> ExplanationRecord:
    """Decode one ``explanation`` row."""

    repository_id, explanation_id = str(row[0]), str(row[1])
    subject_kind = str(row[2])
    subject = decode_explanation_subject(subject_kind, row[3], row[4], row[5], row[6])
    current_revision_id = row[7]
    provenance = decode_authorship(str(row[8]))
    current = None if current_revision_id is None else str(current_revision_id)
    return ExplanationRecord(
        repository_id=repository_id,
        explanation_id=explanation_id,
        subject=subject,
        current_revision_id=current,
        provenance=provenance,
        row_digest=explanation_row_digest(
            repository_id, explanation_id, subject, current, provenance
        ),
    )


def explanation_revision_row(
    explanation_id: str,
    revision_id: str,
    predecessor_revision_id: str | None,
    body: str,
    authorship: Authorship,
) -> tuple[str | None, ...]:
    """Return the ``explanation_revision`` column tuple for one sealed explanation revision."""

    return (
        explanation_id,
        revision_id,
        predecessor_revision_id,
        body,
        explanation_revision_digest(
            explanation_id=explanation_id,
            predecessor_revision_id=predecessor_revision_id,
            body=body,
        ),
        encode_authorship(authorship),
    )


def explanation_revision_digest(
    *, explanation_id: str, predecessor_revision_id: str | None, body: str
) -> str:
    """Return the seal of one explanation revision.

    The body and the exact predecessor are inside it, so an edited body or a changed predecessor
    would no longer match the revision that identity names.
    """

    return sha256_digest(
        {
            "payload_version": EXPLANATION_REVISION_PAYLOAD_VERSION,
            "explanation_id": explanation_id,
            "predecessor_revision_id": predecessor_revision_id,
            "body": body,
        }
    )


def decode_explanation_revision_row(row: Sequence[Any]) -> ExplanationRevision:
    """Decode one ``explanation_revision`` row and verify that its stored seal still holds."""

    repository_id, explanation_id = str(row[0]), str(row[1])
    revision_id, predecessor_revision_id = str(row[2]), row[3]
    body, payload_digest = str(row[4]), str(row[5])
    provenance = decode_authorship(str(row[6]))
    predecessor = None if predecessor_revision_id is None else str(predecessor_revision_id)
    recomputed = explanation_revision_digest(
        explanation_id=explanation_id, predecessor_revision_id=predecessor, body=body
    )
    if recomputed != payload_digest:
        raise KnowledgeStorageError(
            f"stored explanation revision {revision_id} does not match its payload digest: stored "
            f"{payload_digest}, recomputed {recomputed}. The row was altered behind its identity; "
            "treat the store as damaged and recover the revision from an intact snapshot."
        )
    return ExplanationRevision(
        repository_id=repository_id,
        explanation_id=explanation_id,
        revision_id=revision_id,
        predecessor_revision_id=predecessor,
        body=body,
        payload_digest=payload_digest,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# The one-row lookups the stored-value readers ask: each names one canonical table and its own key.

ATTACHMENT_BY_ID = "SELECT * FROM facet_attachment WHERE repository_id = ? AND attachment_id = ?"
RECORD_BY_ID = "SELECT * FROM knowledge_record WHERE repository_id = ? AND record_id = ?"
REVISION_BY_ID = "SELECT * FROM record_revision WHERE repository_id = ? AND revision_id = ?"
SUPERSESSION_BY_SUPERSEDING = (
    "SELECT * FROM facet_decision_supersession WHERE repository_id = ? AND "
    "superseding_revision_id = ?"
)
EXPLANATION_BY_ID = "SELECT * FROM explanation WHERE repository_id = ? AND explanation_id = ?"
EXPLANATION_REVISION_BY_ID = (
    "SELECT * FROM explanation_revision WHERE repository_id = ? AND revision_id = ?"
)

# ---------------------------------------------------------------------------
# The stored-value readers. Each returns the same digest the read projection exposes, so a caller
# carries an expectation straight from a read instead of deriving a second identity scheme. They
# live beside the row codecs rather than with the write path because they answer a question about a
# stored row and nothing else.


def attachment_endpoint_digest(store: OpenedKnowledgeStore, attachment_id: str) -> str | None:
    """Return one stored attachment's row digest, or ``None`` when it is not stored.

    This is the reader the batch's expectation machinery uses, and it is the same value the read
    path exposes, so a caller carries a removal's expectation straight from a read.
    """

    rows = tuple(store.connection.execute(ATTACHMENT_BY_ID, (store.repository_id, attachment_id)))
    return None if not rows else decode_attachment_row(rows[0]).row_digest


def facet_record_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    """Return one stored facet envelope's row digest, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(RECORD_BY_ID, (store.repository_id, record_id)))
    return None if not rows else decode_facet_record_row(rows[0]).row_digest


def record_revision_content_digest(store: OpenedKnowledgeStore, revision_id: str) -> str | None:
    """Return one stored record revision's seal, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(REVISION_BY_ID, (store.repository_id, revision_id)))
    return None if not rows else str(rows[0][6])


def supersession_digest(store: OpenedKnowledgeStore, superseding_revision_id: str) -> str | None:
    """Return one stored supersession edge's digest, or ``None`` when it is not stored."""

    rows = tuple(
        store.connection.execute(
            SUPERSESSION_BY_SUPERSEDING, (store.repository_id, superseding_revision_id)
        )
    )
    return None if not rows else decode_supersession_row(rows[0]).row_digest


def explanation_record_digest(store: OpenedKnowledgeStore, explanation_id: str) -> str | None:
    """Return one stored explanation identity row's digest, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(EXPLANATION_BY_ID, (store.repository_id, explanation_id)))
    return None if not rows else decode_explanation_row(rows[0]).row_digest


def explanation_revision_payload_digest(
    store: OpenedKnowledgeStore, revision_id: str
) -> str | None:
    """Return one stored explanation revision's seal, or ``None`` when it is not stored."""

    rows = tuple(
        store.connection.execute(EXPLANATION_REVISION_BY_ID, (store.repository_id, revision_id))
    )
    return None if not rows else str(rows[0][5])
