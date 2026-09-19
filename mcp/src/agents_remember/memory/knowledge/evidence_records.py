"""Row codecs between the supporting-record vocabulary and generation 5's declared columns.

Every conversion in both directions lives here, so the column order, the canonical JSON text form of
a typed column and the digest recomputation have exactly one owner per table. Two seals are
recomputed on the way *out* rather than trusted, on the shipped rule that a row whose text was
rewritten behind its identity must not be served as the revision that identity names:

* a claim revision's ``content_digest`` is re-derived from the stored payload;
* an observation revision's ``content_digest`` is re-derived from the stored payload.

The observable-row digests are the other half of the same idea. A claim's ledger row, a subject edge,
a claimed-coverage edge and an observation's own row are not sealed revisions, so an operation that
names one of them by digest computes that digest from the row as it stands.
:func:`evidence_claim_row_digest`, :func:`subject_row_digest`, :func:`coverage_row_digest` and
:func:`observation_row_digest` are what those guards compare against, and the read path exposes the
same values, so a caller carries an expectation straight from a read instead of deriving a second
identity scheme.

**The envelope digests are computed here rather than borrowed from the facet codecs.** This leaf
appends its own tables and its own record kinds; a shared digest helper would have to be a new
function in the facet module, which is another leaf's file, and an evidence claim's identity would
then be defined by a module that does not own it. The formula is the shipped one, stated in
:mod:`agents_remember.memory.knowledge.facet_records` for a facet revision and restated here for the
same reason a generation restates the DDL it extends: the *value* is part of this record's contract,
and a case asserts the two agree field by field.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import apsw

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.records import (
    decode_authorship,
    decode_typed_column,
    encode_authorship,
    encode_typed_column,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.evidence import (
    COVERAGE_ENDPOINT_KINDS,
    SUBJECT_KINDS,
    AnchorCoverage,
    CoverageEndpoint,
    EvidenceClaimPayload,
    EvidenceSubject,
    InvariantRevisionSubject,
    KnowledgeFacetRevisionSubject,
    PublicationReference,
    RealizationClaimCoverage,
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
    coverage_identity,
)
from agents_remember.models.knowledge.evidence_read import (
    ClaimCoverage,
    ClaimSubject,
    EvidenceClaimRecord,
    EvidenceClaimRevision,
    VerificationObservationRecord,
    VerificationObservationRevision,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# Bumped only when the sealed field set changes: a different payload version is a different identity
# computation, so it is recorded rather than inferred from the presence of fields.
RECORD_REVISION_PAYLOAD_VERSION = "record-revision-payload/v1"


# -- the envelope rows --------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvelopeDraft:
    """One evidence envelope row, as the value the writer stores.

    ``kind`` and ``record_schema`` travel together with the identity because the envelope's registry
    resolves on the *pair*: a payload admitted for one kind is not automatically admitted for
    another, so a writer handed them separately could store a pair the registry would have refused.
    """

    record_id: str
    kind: str
    record_schema: str
    authority_home: str
    lifecycle: str
    governing_route_id: str | None = None


def envelope_record_row(draft: EnvelopeDraft, authorship: Authorship) -> tuple[Any, ...]:
    """Return the ``knowledge_record`` column tuple for one evidence envelope."""

    return (
        draft.record_id,
        draft.kind,
        draft.authority_home,
        draft.lifecycle,
        draft.governing_route_id,
        draft.record_schema,
        encode_authorship(authorship),
    )


def envelope_record_row_digest(
    repository_id: str, draft: EnvelopeDraft, provenance: Authorship
) -> str:
    """Digest one evidence envelope row, so an expectation can name it.

    The formula is the shipped one for an envelope row: the table, the namespace, the record
    identity, the kind and the schema the row must store, the recorded lifecycle, the authority home,
    the governing route and the provenance. Every field that is stored is inside the digest and
    nothing else is, so a rewritten envelope no longer matches the identity a caller read.
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


@dataclass(frozen=True)
class RevisionDraft:
    """One evidence record revision, as the value the writer stores."""

    record_id: str
    revision_id: str
    record_schema: str
    payload: Mapping[str, Any]
    predecessor_revision_id: str | None = None


def record_revision_row(draft: RevisionDraft, authorship: Authorship) -> tuple[Any, ...]:
    """Return the ``record_revision`` column tuple for one sealed evidence revision."""

    return (
        draft.revision_id,
        draft.record_id,
        draft.record_schema,
        encode_typed_column(dict(draft.payload)),
        draft.predecessor_revision_id,
        record_revision_digest(draft),
        encode_authorship(authorship),
    )


def record_revision_digest(draft: RevisionDraft) -> str:
    """Return the seal of one record revision aggregate.

    The payload is inside the seal, so a revision's identity is a fact about its authored content and
    a rewritten body would no longer match it. The digest excludes only itself.
    """

    return sha256_digest(
        {
            "payload_version": RECORD_REVISION_PAYLOAD_VERSION,
            "record_id": draft.record_id,
            "record_schema": draft.record_schema,
            "payload": dict(draft.payload),
            "predecessor_revision_id": draft.predecessor_revision_id,
        }
    )


def decode_record_revision_row(row: Sequence[Any]) -> tuple[str, str, Mapping[str, Any], str, str]:
    """Decode one ``record_revision`` row and verify that its stored seal still holds.

    Returns ``(record_id, record_schema, payload, content_digest, provenance_text)``. Every caller
    that serves a revision goes through this function, so a row whose text was rewritten behind its
    identity is a defect report rather than a value a reader would believe.
    """

    revision_id, record_id = str(row[1]), str(row[2])
    record_schema = str(row[3])
    payload = decode_typed_column(str(row[4]))
    predecessor_revision_id = row[5]
    content_digest, provenance_text = str(row[6]), str(row[7])
    if not isinstance(payload, Mapping):
        raise KnowledgeStorageError(
            f"stored record revision {revision_id} carries a payload that is not a JSON object"
        )
    predecessor = None if predecessor_revision_id is None else str(predecessor_revision_id)
    recomputed = record_revision_digest(
        RevisionDraft(
            record_id=record_id,
            revision_id=revision_id,
            record_schema=record_schema,
            payload=payload,
            predecessor_revision_id=predecessor,
        )
    )
    if recomputed != content_digest:
        raise KnowledgeStorageError(
            f"stored record revision {revision_id} does not match its content digest: stored "
            f"{content_digest}, recomputed {recomputed}. The row was altered behind its identity; "
            "treat the store as damaged and recover the revision from an intact snapshot."
        )
    return (record_id, record_schema, payload, content_digest, provenance_text)


# -- the claim's ledger row ---------------------------------------------------------------------


def claim_row(claim_id: str, evidence_anchor_id: str, authorship: Authorship) -> tuple[str, ...]:
    """Return the ``evidence_claim`` column tuple for one authored claim."""

    return (claim_id, evidence_anchor_id, encode_authorship(authorship))


def evidence_claim_row_digest(
    repository_id: str, claim_id: str, evidence_anchor_id: str, provenance: Authorship
) -> str:
    """Digest one claim ledger row, so an expectation can name it."""

    return sha256_digest(
        {
            "table": "evidence_claim",
            "repository_id": repository_id,
            "claim_id": claim_id,
            "evidence_anchor_id": evidence_anchor_id,
            "provenance": provenance.model_dump(mode="json"),
        }
    )


def decode_claim_row(row: Sequence[Any]) -> tuple[str, str, Authorship, str]:
    """Decode one ``evidence_claim`` row.

    Returns ``(claim_id, evidence_anchor_id, provenance, row_digest)``.
    """

    repository_id, claim_id = str(row[0]), str(row[1])
    evidence_anchor_id = str(row[2])
    provenance = decode_authorship(str(row[3]))
    return (
        claim_id,
        evidence_anchor_id,
        provenance,
        evidence_claim_row_digest(repository_id, claim_id, evidence_anchor_id, provenance),
    )


# -- the claim's subject ------------------------------------------------------------------------


def subject_row(subject: EvidenceSubject, claim_id: str) -> tuple[str, ...]:
    """Return the subject join table's column tuple for one typed subject.

    The table the row goes into is the subject *kind*, and the table IS the kind check: a claim whose
    subject is an invariant revision cannot reach the facet table, because that table's only identity
    column references ``record_revision``.
    """

    return (claim_id, subject.revision_id)


def subject_table(subject: EvidenceSubject) -> str:
    """Return the join table one subject kind populates."""

    if isinstance(subject, InvariantRevisionSubject):
        return "evidence_claim_invariant_subject"
    return "evidence_claim_facet_subject"


def subject_revision_column(subject: EvidenceSubject) -> str:
    """Return the identity column one subject kind populates."""

    if isinstance(subject, InvariantRevisionSubject):
        return "invariant_revision_id"
    return "facet_revision_id"


def subject_row_digest(repository_id: str, claim_id: str, subject: EvidenceSubject) -> str:
    """Digest one subject edge row, so an expectation can name it."""

    return sha256_digest(
        {
            "table": subject_table(subject),
            "repository_id": repository_id,
            "claim_id": claim_id,
            "subject_kind": subject.kind,
            "revision_id": subject.revision_id,
        }
    )


def decode_subject_row(subject_kind: str, row: Sequence[Any]) -> ClaimSubject:
    """Decode one stored subject edge, refusing a kind this build cannot check."""

    repository_id, claim_id = str(row[0]), str(row[1])
    revision_id = str(row[2])
    if subject_kind == "invariant_revision":
        subject: EvidenceSubject = InvariantRevisionSubject(revision_id=revision_id)
    elif subject_kind == "facet_revision":
        subject = KnowledgeFacetRevisionSubject(revision_id=revision_id)
    else:
        raise KnowledgeStorageError(
            f"stored evidence claim {claim_id} declares subject kind {subject_kind!r}, which is not "
            f"one of {' | '.join(SUBJECT_KINDS)}; the row was altered outside the operation"
        )
    return ClaimSubject(
        repository_id=repository_id,
        claim_id=claim_id,
        subject=subject,
        row_digest=subject_row_digest(repository_id, claim_id, subject),
    )


# -- the claim's claimed coverage ---------------------------------------------------------------


def coverage_row(claim_id: str, endpoint: CoverageEndpoint) -> tuple[Any, ...]:
    """Return the claimed-coverage table's column tuple for one typed endpoint.

    Exactly one of the two endpoint columns is populated, which is the group the table's ``CHECK``
    requires: the other is explicitly ``None`` rather than omitted, so the row's shape does not
    depend on a default. ``covered_identity`` is the same identity the populated column carries, and
    the table's second ``CHECK`` ties the two together -- it is carried separately because it is what
    the row's primary key uses, and a key column cannot be null.
    """

    if isinstance(endpoint, RealizationClaimCoverage):
        return (claim_id, endpoint.kind, endpoint.claim_id, endpoint.claim_id, None)
    return (claim_id, endpoint.kind, endpoint.anchor_id, None, endpoint.anchor_id)


def coverage_row_digest(repository_id: str, claim_id: str, endpoint: CoverageEndpoint) -> str:
    """Digest one claimed-coverage row, so an expectation can name it."""

    return sha256_digest(
        {
            "table": "evidence_claim_coverage",
            "repository_id": repository_id,
            "claim_id": claim_id,
            "endpoint_kind": endpoint.kind,
            "endpoint_id": coverage_identity(endpoint),
        }
    )


def decode_coverage_row(row: Sequence[Any]) -> ClaimCoverage:
    """Decode one stored claimed-coverage row, refusing a row its own constraint should have stopped."""

    repository_id, claim_id, coverage_kind = str(row[0]), str(row[1]), str(row[2])
    populated = {
        "realization_claim": row[4],
        "source_anchor": row[5],
    }
    present = [kind for kind, value in populated.items() if value is not None]
    if len(present) != 1:
        raise KnowledgeStorageError(
            f"stored claimed coverage of {claim_id} populates {len(present)} endpoint columns, which "
            f"the table's own constraint forbids; the row was altered outside the operation"
        )
    kind = present[0]
    if kind not in COVERAGE_ENDPOINT_KINDS:  # pragma: no cover - the table's constraint names them
        raise KnowledgeStorageError(
            f"stored claimed coverage of {claim_id} names an unknown endpoint kind {kind!r}"
        )
    if kind != coverage_kind:
        raise KnowledgeStorageError(
            f"stored claimed coverage of {claim_id} declares kind {coverage_kind!r} but populates "
            f"the {kind!r} column; the table's own constraint was bypassed"
        )
    endpoint: CoverageEndpoint = (
        RealizationClaimCoverage(claim_id=str(populated[kind]))
        if kind == "realization_claim"
        else AnchorCoverage(anchor_id=str(populated[kind]))
    )
    return ClaimCoverage(
        repository_id=repository_id,
        claim_id=claim_id,
        endpoint=endpoint,
        row_digest=coverage_row_digest(repository_id, claim_id, endpoint),
    )


# -- the observation's own row ------------------------------------------------------------------


def observation_cells(payload: VerificationObservationPayload) -> dict[str, Any]:
    """Return one observation's stored cells, keyed by the column that holds each.

    The mapping is keyed rather than positional on purpose. The table's column order is declared in
    the generation, the INSERT statement names its columns, and this is the third place the same
    order would have had to be repeated -- so it is not repeated. The write path builds its statement
    from :data:`OBSERVATION_COLUMNS` and its parameters from this mapping, which means a mismatch is
    not expressible rather than merely unlikely.

    Every cell is derived from the frozen payload, so the row and the sealed revision cannot disagree
    about what the run recorded. The knowledge snapshot's three columns are ``NULL`` together when the
    run tested no knowledge dataset, which is the table's own constraint rather than a convention the
    writer remembers.
    """

    snapshot = payload.knowledge_candidate
    artifact = payload.result_artifact
    publication = payload.publication
    return {
        "knowledge_snapshot_repository_id": None if snapshot is None else snapshot.repository_id,
        "knowledge_snapshot_schema_version": None if snapshot is None else snapshot.schema_version,
        "knowledge_snapshot_logical_digest": None if snapshot is None else snapshot.logical_digest,
        "code_candidate_tree_id": payload.code_candidate_tree_id,
        "command_name": payload.command_name,
        "command_identity": payload.command_identity,
        "artifact_path": None if artifact is None else artifact.path,
        "artifact_sha256": None if artifact is None else artifact.sha256,
        "artifact_size_bytes": None if artifact is None else artifact.size_bytes,
        "digest_checked_at_write": (
            1 if (artifact is not None and artifact.digest_checked_against_bytes) else 0
        ),
        "execution_result": payload.execution_result,
        "environment_host": payload.environment.host,
        "environment_interpreter": payload.environment.interpreter,
        "environment_toolchain": encode_typed_column(list(payload.environment.toolchain)),
        "publication_destination": None if publication is None else publication.destination,
        "publication_sha256": None if publication is None else publication.sha256,
        "publication_recorded_at": None if publication is None else publication.published_at,
    }


# The observation's own columns, in the order the generation declares them after the two key
# columns the write path supplies. Declared here, next to the cells, so the builder and the statement
# read the same list; a case asserts it equals the generation's declared order minus the key.
OBSERVATION_COLUMNS: tuple[str, ...] = (
    "knowledge_snapshot_repository_id",
    "knowledge_snapshot_schema_version",
    "knowledge_snapshot_logical_digest",
    "code_candidate_tree_id",
    "command_name",
    "command_identity",
    "artifact_path",
    "artifact_sha256",
    "artifact_size_bytes",
    "digest_checked_at_write",
    "execution_result",
    "environment_host",
    "environment_interpreter",
    "environment_toolchain",
    "publication_destination",
    "publication_sha256",
    "publication_recorded_at",
)


def observation_row(payload: VerificationObservationPayload) -> tuple[Any, ...]:
    """Return the ``verification_observation`` column tuple for one authored observation."""

    cells = observation_cells(payload)
    return tuple(cells[column] for column in OBSERVATION_COLUMNS)


def observation_row_digest(
    repository_id: str, observation_id: str, payload: Mapping[str, Any]
) -> str:
    """Digest one observation row, so an expectation can name it.

    The digest covers the row's own stored facts -- the namespace, the observation identity and the
    exact payload the columns were projected from -- and nothing derived from them. It is not a
    digest of the artifact's bytes and does not stand in for one: the artifact's identity is
    ``artifact_sha256``.
    """

    return sha256_digest(
        {
            "table": "verification_observation",
            "repository_id": repository_id,
            "observation_id": observation_id,
            "payload": dict(payload),
        }
    )


def _snapshot_of_columns(
    repository_id: object, schema_version: object, logical_digest: object, observation_id: str
) -> SnapshotIdentity | None:
    """Rebuild one stored knowledge snapshot identity, refusing a partially populated group."""

    populated = [value is not None for value in (repository_id, schema_version, logical_digest)]
    if not any(populated):
        return None
    if not all(populated):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} populates part of its knowledge snapshot identity; "
            "the table's own constraint was bypassed"
        )
    return SnapshotIdentity.model_construct(
        repository_id=str(repository_id),
        schema_version=str(schema_version),
        logical_digest=str(logical_digest),
    )


def _artifact_of_columns(
    observation_id: str,
    path: object,
    sha256: object,
    size_bytes: object,
    digest_checked_at_write: object,
) -> ResultArtifactReference | None:
    """Rebuild one stored result-artifact reference, refusing a partially populated group."""

    populated = [value is not None for value in (path, sha256, size_bytes)]
    if not any(populated):
        return None
    if not all(populated):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} populates part of its result-artifact reference; "
            "the table's own constraint was bypassed"
        )
    return ResultArtifactReference.model_construct(
        path=str(path),
        sha256=str(sha256),
        size_bytes=int(str(size_bytes)),
        digest_checked_against_bytes=bool(digest_checked_at_write),
    )


def _publication_of_columns(
    observation_id: str, destination: object, sha256: object, published_at: object
) -> PublicationReference | None:
    """Rebuild one stored publication reference, refusing a partially populated group."""

    populated = [value is not None for value in (destination, sha256, published_at)]
    if not any(populated):
        return None
    if not all(populated):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} populates part of its publication reference; "
            "the table's own constraint was bypassed"
        )
    return PublicationReference.model_construct(
        destination=str(destination),
        sha256=str(sha256),
        published_at=str(published_at),
    )


def decode_observation_row(row: Sequence[Any]) -> VerificationObservationRecord:
    """Decode one ``verification_observation`` row into its stored value."""

    repository_id, observation_id = str(row[0]), str(row[1])
    payload = VerificationObservationPayload.model_construct(
        command_name=str(row[6]),
        command_identity=str(row[7]),
        knowledge_candidate=_snapshot_of_columns(row[2], row[3], row[4], observation_id),
        code_candidate_tree_id=None if row[5] is None else str(row[5]),
        result_artifact=_artifact_of_columns(observation_id, row[8], row[9], row[10], row[11]),
        execution_result=str(row[12]),
        environment=RunEnvironment.model_construct(
            host=str(row[13]),
            interpreter=str(row[14]),
            toolchain=_toolchain_of_column(row[15], observation_id),
        ),
        publication=_publication_of_columns(observation_id, row[16], row[17], row[18]),
        state_at_origin="proposed",
        acceptance_ref=None,
    )
    return VerificationObservationRecord(
        repository_id=repository_id,
        observation_id=observation_id,
        payload=payload,
        row_digest=observation_row_digest(
            repository_id, observation_id, payload.model_dump(mode="json")
        ),
    )


def _toolchain_of_column(value: object, observation_id: str) -> tuple[tuple[str, str], ...]:
    """Decode the stored toolchain column, refusing anything that is not a list of pairs."""

    decoded = decode_typed_column(str(value))
    if not isinstance(decoded, list):
        raise KnowledgeStorageError(
            f"stored observation {observation_id} carries a toolchain that is not a JSON array"
        )
    pairs: list[tuple[str, str]] = []
    for component in decoded:
        if not isinstance(component, list) or len(component) != 2:
            raise KnowledgeStorageError(
                f"stored observation {observation_id} carries a malformed toolchain component"
            )
        pairs.append((str(component[0]), str(component[1])))
    return tuple(pairs)


# -- the two ledger readers the batch's expectation machinery asks ------------------------------


def claim_digest(store: OpenedKnowledgeStore, claim_id: str) -> str | None:
    """Return one stored claim ledger row's digest, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(CLAIM_BY_ID, (store.repository_id, claim_id)))
    return None if not rows else decode_claim_row(rows[0])[3]


def observation_digest(store: OpenedKnowledgeStore, observation_id: str) -> str | None:
    """Return one stored observation row's digest, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(OBSERVATION_BY_ID, (store.repository_id, observation_id)))
    return None if not rows else decode_observation_row(rows[0]).row_digest


def subject_digest(store: OpenedKnowledgeStore, claim_id: str) -> str | None:
    """Return one stored subject edge's digest, or ``None`` when the claim has no subject row."""

    for subject_kind, statement in (
        ("invariant_revision", INVARIANT_SUBJECT_BY_CLAIM),
        ("facet_revision", FACET_SUBJECT_BY_CLAIM),
    ):
        rows = tuple(store.connection.execute(statement, (store.repository_id, claim_id)))
        if rows:
            return decode_subject_row(subject_kind, rows[0]).row_digest
    return None


def coverage_digest(store: OpenedKnowledgeStore, row_identity: str) -> str | None:
    """Return one stored coverage row's digest, addressed by the pair's own spelling.

    ``row_identity`` is ``<claim_id>/<endpoint_kind>/<endpoint_id>`` -- the same spelling
    :func:`agents_remember.models.knowledge.evidence.claimed_coverage_row_identity` produces, so a
    caller can name one coverage edge of a claim rather than only the claim's whole list.
    """

    parts = row_identity.split("/")
    if len(parts) != 3:
        return None
    claim_id, endpoint_kind, endpoint_id = parts
    statement = (
        COVERAGE_BY_CLAIM_ENDPOINT
        if endpoint_kind == "realization_claim"
        else COVERAGE_BY_CLAIM_ANCHOR
    )
    rows = tuple(store.connection.execute(statement, (store.repository_id, claim_id, endpoint_id)))
    return None if not rows else decode_coverage_row(rows[0]).row_digest


# ---------------------------------------------------------------------------
# The stored-value readers the read projection exposes. Each returns the same digest the write
# path's guards compare against, so a caller carries an expectation straight from a read.

CLAIM_BY_ID = "SELECT * FROM evidence_claim WHERE repository_id = ? AND claim_id = ?"
INVARIANT_SUBJECT_BY_CLAIM = (
    "SELECT * FROM evidence_claim_invariant_subject WHERE repository_id = ? AND claim_id = ?"
)
FACET_SUBJECT_BY_CLAIM = (
    "SELECT * FROM evidence_claim_facet_subject WHERE repository_id = ? AND claim_id = ?"
)
COVERAGE_BY_CLAIM = (
    "SELECT * FROM evidence_claim_coverage WHERE repository_id = ? AND claim_id = ? "
    "ORDER BY claim_id_endpoint, anchor_id_endpoint"
)
COVERAGE_BY_CLAIM_ENDPOINT = (
    "SELECT * FROM evidence_claim_coverage WHERE repository_id = ? AND claim_id = ? "
    "AND claim_id_endpoint = ?"
)
COVERAGE_BY_CLAIM_ANCHOR = (
    "SELECT * FROM evidence_claim_coverage WHERE repository_id = ? AND claim_id = ? "
    "AND anchor_id_endpoint = ?"
)
OBSERVATION_BY_ID = (
    "SELECT * FROM verification_observation WHERE repository_id = ? AND observation_id = ?"
)
RECORD_BY_ID = "SELECT * FROM knowledge_record WHERE repository_id = ? AND record_id = ?"
REVISION_BY_ID = "SELECT * FROM record_revision WHERE repository_id = ? AND revision_id = ?"


def decode_claim_record(row: Sequence[Any], payload: Mapping[str, Any]) -> EvidenceClaimRecord:
    """Decode one claim envelope row, together with the payload its revision carries."""

    repository_id, record_id = str(row[0]), str(row[1])
    kind, authority_home, lifecycle = str(row[2]), str(row[3]), str(row[4])
    governing_route_id, record_schema = row[5], str(row[6])
    provenance = decode_authorship(str(row[7]))
    if kind != "evidence_claim":
        raise KnowledgeStorageError(
            f"stored record {record_id} carries kind {kind!r}, which is not an evidence claim"
        )
    route = None if governing_route_id is None else str(governing_route_id)
    validated = EvidenceClaimPayload.model_validate(dict(payload))
    return EvidenceClaimRecord(
        repository_id=repository_id,
        claim_id=record_id,
        record_schema=record_schema,
        authority_home=authority_home,
        state_at_origin=lifecycle,
        governing_route_id=route,
        payload=validated,
        provenance=provenance,
        row_digest=envelope_record_row_digest(
            repository_id,
            EnvelopeDraft(
                record_id=record_id,
                kind=kind,
                record_schema=record_schema,
                authority_home=authority_home,
                lifecycle=lifecycle,
                governing_route_id=route,
            ),
            provenance,
        ),
    )


def decode_claim_revision(
    row: Sequence[Any], record_row: Sequence[Any], payload: Mapping[str, Any]
) -> EvidenceClaimRevision:
    """Decode one claim revision row, verifying its seal against the stored payload."""

    repository_id, revision_id = str(row[0]), str(row[1])
    record_id, record_schema = str(row[2]), str(row[3])
    predecessor_revision_id, content_digest = row[5], str(row[6])
    provenance = decode_authorship(str(row[7]))
    predecessor = None if predecessor_revision_id is None else str(predecessor_revision_id)
    del record_row
    return EvidenceClaimRevision(
        repository_id=repository_id,
        claim_id=record_id,
        revision_id=revision_id,
        record_schema=record_schema,
        payload=EvidenceClaimPayload.model_validate(dict(payload)),
        predecessor_revision_id=predecessor,
        content_digest=content_digest,
        provenance=provenance,
    )


def decode_observation_record(
    row: Sequence[Any], payload: Mapping[str, Any]
) -> VerificationObservationRecord:
    """Decode one observation envelope row, together with the payload its revision carries."""

    repository_id, record_id = str(row[0]), str(row[1])
    kind = str(row[2])
    if kind != "verification_observation":
        raise KnowledgeStorageError(
            f"stored record {record_id} carries kind {kind!r}, which is not a verification observation"
        )
    validated = VerificationObservationPayload.model_validate(dict(payload))
    return VerificationObservationRecord(
        repository_id=repository_id,
        observation_id=record_id,
        payload=validated,
        row_digest=observation_row_digest(
            repository_id, record_id, validated.model_dump(mode="json")
        ),
    )


def decode_observation_revision(
    row: Sequence[Any], payload: Mapping[str, Any]
) -> VerificationObservationRevision:
    """Decode one observation revision row, verifying its seal against the stored payload."""

    repository_id, revision_id = str(row[0]), str(row[1])
    record_id, record_schema = str(row[2]), str(row[3])
    predecessor_revision_id, content_digest = row[5], str(row[6])
    provenance = decode_authorship(str(row[7]))
    predecessor = None if predecessor_revision_id is None else str(predecessor_revision_id)
    return VerificationObservationRevision(
        repository_id=repository_id,
        observation_id=record_id,
        revision_id=revision_id,
        record_schema=record_schema,
        payload=VerificationObservationPayload.model_validate(dict(payload)),
        predecessor_revision_id=predecessor,
        content_digest=content_digest,
        provenance=provenance,
    )


def envelope_kind_of_revision(
    store: OpenedKnowledgeStore, revision_id: str
) -> tuple[str, str] | None:
    """Return ``(kind, record_schema)`` of the envelope one stored revision belongs to."""

    rows = tuple(store.connection.execute(ENVELOPE_OF_REVISION, (store.repository_id, revision_id)))
    if not rows:
        return None
    return (str(rows[0][2]), str(rows[0][6]))


ENVELOPE_OF_REVISION = (
    "SELECT envelope.* FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND revision.revision_id = ?"
)


def record_digest(store: OpenedKnowledgeStore, record_id: str) -> str | None:
    """Return one stored record envelope's row digest, or ``None`` when it is not stored.

    This is the reader the batch's expectation machinery uses for the ``knowledge_record`` table, and
    it is kind-agnostic on purpose: the envelope row's digest covers the row's own stored fields, so
    it is the same question for a facet record and for an evidence claim. What it does **not** do is
    decide which record kinds exist -- a row carrying a kind this leaf does not register still
    digests as itself, which is what lets one expectation address a table four record groups share.
    """

    rows = tuple(store.connection.execute(RECORD_BY_ID, (store.repository_id, record_id)))
    if not rows:
        return None
    row = rows[0]
    return envelope_record_row_digest(
        store.repository_id,
        EnvelopeDraft(
            record_id=str(row[1]),
            kind=str(row[2]),
            record_schema=str(row[6]),
            authority_home=str(row[3]),
            lifecycle=str(row[4]),
            governing_route_id=None if row[5] is None else str(row[5]),
        ),
        decode_authorship(str(row[7])),
    )


def revision_content_digest(store: OpenedKnowledgeStore, revision_id: str) -> str | None:
    """Return one stored record revision's seal, or ``None`` when it is not stored."""

    rows = tuple(store.connection.execute(REVISION_BY_ID, (store.repository_id, revision_id)))
    return None if not rows else str(rows[0][6])


CLAIM_IDS_OF_REPOSITORY = (
    "SELECT record_id FROM knowledge_record WHERE repository_id = ? AND kind = 'evidence_claim' "
    "ORDER BY record_id"
)
OBSERVATION_IDS_OF_REPOSITORY = (
    "SELECT record_id FROM knowledge_record WHERE repository_id = ? "
    "AND kind = 'verification_observation' ORDER BY record_id"
)
CLAIMS_OF_REPOSITORY = (
    "SELECT revision.record_id, revision.revision_id, revision.record_schema, revision.payload, "
    "revision.content_digest, revision.provenance "
    "FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND envelope.kind = 'evidence_claim' "
    "ORDER BY revision.record_id, revision.revision_id"
)
OBSERVATIONS_OF_REPOSITORY = (
    "SELECT revision.record_id, revision.revision_id, revision.record_schema, revision.payload, "
    "revision.content_digest, revision.provenance "
    "FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND envelope.kind = 'verification_observation' "
    "ORDER BY revision.record_id, revision.revision_id"
)
OBSERVATIONS_OF_SNAPSHOT = (
    "SELECT revision.record_id, revision.revision_id, revision.record_schema, revision.payload, "
    "revision.content_digest, revision.provenance "
    "FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "JOIN verification_observation AS observation ON observation.repository_id = envelope.repository_id "
    "AND observation.observation_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND envelope.kind = 'verification_observation' "
    "AND observation.knowledge_snapshot_logical_digest = ? "
    "ORDER BY revision.record_id, revision.revision_id"
)
OBSERVATIONS_OF_CODE_TREE = (
    "SELECT revision.record_id, revision.revision_id, revision.record_schema, revision.payload, "
    "revision.content_digest, revision.provenance "
    "FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "JOIN verification_observation AS observation ON observation.repository_id = envelope.repository_id "
    "AND observation.observation_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND envelope.kind = 'verification_observation' "
    "AND observation.code_candidate_tree_id = ? "
    "ORDER BY revision.record_id, revision.revision_id"
)


def observation_records(
    store: OpenedKnowledgeStore, statement: str, parameter: str
) -> tuple[VerificationObservationRecord, ...]:
    """Return every observation one candidate selection addressed, decoding each stored row."""

    seen: dict[str, VerificationObservationRecord] = {}
    for row in store.connection.execute(statement, (store.repository_id, parameter)):
        record_id = str(row[0])
        if record_id in seen:
            continue
        seen[record_id] = decode_observation_row(
            _one(
                store,
                OBSERVATION_BY_ID,
                (store.repository_id, record_id),
                f"observation {record_id} has a revision but no recorded row",
            )
        )
    return tuple(seen[record_id] for record_id in sorted(seen))


def _one(
    store: OpenedKnowledgeStore,
    statement: str,
    parameters: tuple[Any, ...],
    detail: str,
) -> tuple[Any, ...]:
    row = next(iter(store.connection.execute(statement, parameters)), None)
    if row is None:  # pragma: no cover - the join above selected this identity
        raise KnowledgeStorageError(detail)
    return row


def claim_record(store: OpenedKnowledgeStore, claim_id: str) -> EvidenceClaimRecord | None:
    """Return one stored claim envelope with its sealed payload, or ``None``."""

    return claim_record_at(store.connection, store.repository_id, claim_id)


def claim_record_at(
    connection: apsw.Connection, repository_id: str, claim_id: str
) -> EvidenceClaimRecord | None:
    """Return one stored claim envelope read through a caller's own connection.

    The connection-taking form exists because the evidence selection opens a read-only handle and
    never a store: the snapshot it reads is verified before anything is selected, and a store object
    would resolve the namespace a second time from the file rather than from the context the read
    already checked. The row decoding is the same code either way.
    """

    row = next(iter(connection.execute(RECORD_BY_ID, (repository_id, claim_id))), None)
    if row is None:
        return None
    payload = _payload_of_record(connection, repository_id, claim_id)
    return decode_claim_record(row, payload)


def claim_revisions(
    store: OpenedKnowledgeStore, claim_id: str
) -> tuple[EvidenceClaimRevision, ...]:
    """Return every retained revision of one claim, in declared order."""

    return claim_revisions_at(store.connection, store.repository_id, claim_id)


def claim_revisions_at(
    connection: apsw.Connection, repository_id: str, claim_id: str
) -> tuple[EvidenceClaimRevision, ...]:
    """Return every retained revision of one claim, read through a caller's own connection."""

    return tuple(
        _claim_revision(connection, repository_id, row)
        for row in _revisions_of(connection, repository_id, claim_id)
    )


def observation_revisions(
    store: OpenedKnowledgeStore, observation_id: str
) -> tuple[VerificationObservationRevision, ...]:
    """Return every retained revision of one observation, in declared order."""

    return observation_revisions_at(store.connection, store.repository_id, observation_id)


def observation_revisions_at(
    connection: apsw.Connection, repository_id: str, observation_id: str
) -> tuple[VerificationObservationRevision, ...]:
    """Return every retained revision of one observation, read through a caller's connection."""

    return tuple(
        _observation_revision(row)
        for row in _revisions_of(connection, repository_id, observation_id)
    )


def _revisions_of(
    connection: apsw.Connection, repository_id: str, record_id: str
) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        connection.execute(
            "SELECT * FROM record_revision WHERE repository_id = ? AND record_id = ? "
            "ORDER BY revision_id",
            (repository_id, record_id),
        )
    )


def _payload_of_record(
    connection: apsw.Connection, repository_id: str, record_id: str
) -> Mapping[str, Any]:
    """Return the payload of a record's first retained revision.

    A record envelope is written with its first revision in the same transaction, so a record with no
    revision is not a state this store produces; if one is found the store is damaged and this says
    so rather than serving an envelope with no content.
    """

    rows = _revisions_of(connection, repository_id, record_id)
    if not rows:  # pragma: no cover - an envelope is written with its first revision
        raise KnowledgeStorageError(f"record {record_id} has no revision to carry its payload")
    payload = decode_typed_column(str(rows[0][4]))
    if not isinstance(payload, Mapping):
        raise KnowledgeStorageError(
            f"stored record {record_id} carries a payload that is not a JSON object"
        )
    return payload


def _claim_revision(
    connection: apsw.Connection, repository_id: str, row: Sequence[Any]
) -> EvidenceClaimRevision:
    """Decode one claim revision, re-deriving its seal from the row as stored."""

    record_id, _schema, payload, _digest, _provenance = decode_record_revision_row(row)
    record_row = next(iter(connection.execute(RECORD_BY_ID, (repository_id, record_id))), None)
    if record_row is None:  # pragma: no cover - a revision cannot exist without its envelope
        raise KnowledgeStorageError(f"record {record_id} is absent for its own revision")
    return decode_claim_revision(row, record_row, payload)


def _observation_revision(row: Sequence[Any]) -> VerificationObservationRevision:
    """Decode one observation revision, re-deriving its seal from the row as stored."""

    _record_id, _schema, payload, _digest, _provenance = decode_record_revision_row(row)
    return decode_observation_revision(row, payload)


def all_claims(store: OpenedKnowledgeStore) -> tuple[EvidenceClaimRecord, ...]:
    """Return every stored claim identity in this namespace, in declared order."""

    return _identities_of(store, CLAIM_IDS_OF_REPOSITORY, claim_record, "claim")


def all_observations(store: OpenedKnowledgeStore) -> tuple[VerificationObservationRecord, ...]:
    """Return every stored observation identity in this namespace, in declared order."""

    return _identities_of(store, OBSERVATION_IDS_OF_REPOSITORY, _observation_at, "observation")


def _observation_at(
    store: OpenedKnowledgeStore, observation_id: str
) -> VerificationObservationRecord | None:
    rows = tuple(store.connection.execute(OBSERVATION_BY_ID, (store.repository_id, observation_id)))
    return None if not rows else decode_observation_row(rows[0])


def _identities_of(
    store: OpenedKnowledgeStore,
    statement: str,
    reader: Callable[[OpenedKnowledgeStore, str], Any | None],
    noun: str,
) -> tuple[Any, ...]:
    """Read every identity one statement selects through one decoder.

    The list statement and the decoder are passed together because they answer one question -- "which
    identities of this kind are stored, and what does each hold" -- and splitting them would let the
    two disagree about which rows the selection covers.
    """

    records: list[Any] = []
    for row in store.connection.execute(statement, (store.repository_id,)):
        record = reader(store, str(row[0]))
        if record is None:  # pragma: no cover - the identity came from that same table
            raise KnowledgeStorageError(f"{noun} {row[0]} is absent for its own recorded row")
        records.append(record)
    return tuple(records)


def subject_of_claim(store: OpenedKnowledgeStore, claim_id: str) -> ClaimSubject | None:
    """Return one claim's stored subject edge, or ``None`` when the claim carries none."""

    return subject_of_claim_at(store.connection, store.repository_id, claim_id)


def subject_of_claim_at(
    connection: apsw.Connection, repository_id: str, claim_id: str
) -> ClaimSubject | None:
    """Return one claim's stored subject edge, read through a caller's own connection.

    The two subject tables are the kind check, so the question is asked of both and the table that
    answers is the kind the claim declares. A claim carrying a row in neither is a claim with no
    subject, which the selection reports as the absent state rather than resolving to something close.
    """

    for subject_kind, statement in (
        ("invariant_revision", INVARIANT_SUBJECT_BY_CLAIM),
        ("facet_revision", FACET_SUBJECT_BY_CLAIM),
    ):
        row = next(iter(connection.execute(statement, (repository_id, claim_id))), None)
        if row is not None:
            return decode_subject_row(subject_kind, row)
    return None


def claimed_coverage_of_claim(
    store: OpenedKnowledgeStore, claim_id: str
) -> tuple[ClaimCoverage, ...]:
    """Return one claim's stored claimed coverage, in the table's declared order."""

    return tuple(
        decode_coverage_row(row)
        for row in store.connection.execute(COVERAGE_BY_CLAIM, (store.repository_id, claim_id))
    )


def observation_count_for_command(store: OpenedKnowledgeStore, command_name: str) -> int:
    """Return how many observations this namespace recorded for one command name."""

    rows = tuple(
        store.connection.execute(
            "SELECT COUNT(*) FROM verification_observation WHERE repository_id = ? "
            "AND command_name = ?",
            (store.repository_id, command_name),
        )
    )
    return int(rows[0][0])


__all__ = [
    "CLAIMS_OF_REPOSITORY",
    "CLAIM_BY_ID",
    "CLAIM_IDS_OF_REPOSITORY",
    "COVERAGE_BY_CLAIM",
    "ENVELOPE_OF_REVISION",
    "OBSERVATIONS_OF_CODE_TREE",
    "OBSERVATIONS_OF_REPOSITORY",
    "OBSERVATIONS_OF_SNAPSHOT",
    "OBSERVATION_BY_ID",
    "OBSERVATION_COLUMNS",
    "OBSERVATION_IDS_OF_REPOSITORY",
    "RECORD_BY_ID",
    "RECORD_REVISION_PAYLOAD_VERSION",
    "REVISION_BY_ID",
    "EnvelopeDraft",
    "RevisionDraft",
    "all_claims",
    "all_observations",
    "claim_digest",
    "claim_record",
    "claim_record_at",
    "claim_revisions",
    "claim_revisions_at",
    "claim_row",
    "claimed_coverage_of_claim",
    "coverage_digest",
    "coverage_row",
    "coverage_row_digest",
    "decode_claim_record",
    "decode_claim_revision",
    "decode_claim_row",
    "decode_coverage_row",
    "decode_observation_record",
    "decode_observation_revision",
    "decode_observation_row",
    "decode_record_revision_row",
    "decode_subject_row",
    "envelope_kind_of_revision",
    "envelope_record_row",
    "envelope_record_row_digest",
    "evidence_claim_row_digest",
    "observation_cells",
    "observation_count_for_command",
    "observation_digest",
    "observation_records",
    "observation_revisions",
    "observation_revisions_at",
    "observation_row",
    "observation_row_digest",
    "record_digest",
    "record_revision_digest",
    "record_revision_row",
    "revision_content_digest",
    "subject_digest",
    "subject_of_claim",
    "subject_of_claim_at",
    "subject_revision_column",
    "subject_row",
    "subject_row_digest",
    "subject_table",
]
