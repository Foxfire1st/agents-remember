"""The supporting records' write path: resolve every link, then write the sealed aggregate.

Both records are written through the one operation that writes the knowledge graph. In a batch they
arrive as the two authored commands this leaf appends to the closed union
(:class:`…evidence.AddEvidenceClaim`, :class:`…evidence.AddVerificationObservation`); as a standalone
call they arrive through :func:`add_evidence_claim` and :func:`add_verification_observation`, which
are the same driver shape the facet writes use -- one candidate lock, one ``BEGIN IMMEDIATE``
transaction, one in-transaction step that raises a typed refusal for the batch path to roll back.

Five rules shape the module and each is a clause of ``KS-R12@v1``:

* **Code resolves the links; code does not judge what the evidence demonstrates.** The subject, the
  evidence anchor and every claimed-coverage endpoint are resolved against the rows that exist in
  this namespace, and a link that does not resolve is a typed refusal *before* any row is written.
  Nothing here computes whether the evidence is adequate, sufficient, convincing or relevant, and no
  function in this module returns anything but rows, digests and refusals.
* **The author envelope comes from the admission, never from the caller.** The provenance written
  into every row is the one the admitted boundary built; no command and no payload carries a field
  that could become it.
* **Persisting a record endorses nothing.** Both records store ``proposed`` origin data. A command
  that would store accepted origin data is refused with the shipped ``promotion_not_supported``,
  before any row exists.
* **The artifact digest is compared against bytes when the caller supplies the bytes' root.** §7.3
  does not decide between verifying and asserting; it decides that the record states which happened.
  This path therefore records ``digest_checked_against_bytes`` truthfully -- ``True`` only after it
  read the artifact and found the recorded sha256 and size, ``False`` when no root was available to
  read -- and it **refuses** a digest that disagrees with the bytes at the recorded path rather than
  writing a record whose stated digest is one it observed to be false. There is no path here that
  re-pins a stored digest to whatever bytes are present now.
* **The generation gate is a refusal, not a migration.** A dataset whose recorded generation predates
  this leaf's tables is refused with the observed and required versions as facts. Nothing is
  migrated, widened, repaired or extended in place.

Nothing in this module copies artifact bytes into the database. The substrate has no second content
store and this leaf does not add one: the reference is an identity of an artifact that lives
somewhere else.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import endpoints, evidence_records, routes
from agents_remember.memory.knowledge.evidence_refusals import (
    artifact_digest_mismatch_refusal,
    artifact_root_escape_refusal,
    evidence_promotion_not_supported_refusal,
)
from agents_remember.memory.knowledge.record_envelope import validate_record_payload
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    SqliteFailureContext,
    generation_mismatch_refusal,
    missing_expected_row_refusal,
    refusal,
    scope_refusal,
)
from agents_remember.memory.knowledge.schema_generations import GENERATION_7
from agents_remember.models.knowledge.authorship import PROPOSED_STATE, Authorship
from agents_remember.models.knowledge.evidence import (
    EVIDENCE_CLAIM_KIND,
    EVIDENCE_CLAIM_SCHEMA,
    EVIDENCE_RECORD_LIFECYCLE,
    VERIFICATION_OBSERVATION_KIND,
    VERIFICATION_OBSERVATION_SCHEMA,
    AddEvidenceClaim,
    AddVerificationObservation,
    EvidenceCommand,
    EvidenceWriteIdentity,
    EvidenceWriteRequest,
    EvidenceWriteResult,
    InvariantRevisionSubject,
    KnowledgeFacetRevisionSubject,
    ResultArtifactReference,
    VerificationObservationPayload,
    coverage_identity,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The generation whose tables these writes need. ``KS-R12@v1`` §6.5: a dataset that predates them is
# refused, never migrated or widened.
REQUIRED_EVIDENCE_GENERATION = GENERATION_7

_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_CLAIM_INSERT = (
    "INSERT INTO evidence_claim (repository_id, claim_id, evidence_anchor_id, provenance) "
    "VALUES (?, ?, ?, ?)"
)
_INVARIANT_SUBJECT_INSERT = (
    "INSERT INTO evidence_claim_invariant_subject (repository_id, claim_id, invariant_revision_id) "
    "VALUES (?, ?, ?)"
)
_FACET_SUBJECT_INSERT = (
    "INSERT INTO evidence_claim_facet_subject (repository_id, claim_id, facet_revision_id) "
    "VALUES (?, ?, ?)"
)
_COVERAGE_INSERT = (
    "INSERT INTO evidence_claim_coverage (repository_id, claim_id, coverage_kind, "
    "covered_identity, claim_id_endpoint, anchor_id_endpoint) VALUES (?, ?, ?, ?, ?, ?)"
)
# The observation's insert, built from the columns the codec declares rather than from a second
# spelling of the same order. ``OBSERVATION_COLUMNS`` is the generation's own order after the two key
# columns, so the statement and the row it is given cannot describe different orders.
_OBSERVATION_INSERT_COLUMNS: tuple[str, ...] = (
    "repository_id",
    "observation_id",
    *evidence_records.OBSERVATION_COLUMNS,
)
_OBSERVATION_INSERT = (
    "INSERT INTO verification_observation "
    f"({', '.join(_OBSERVATION_INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in _OBSERVATION_INSERT_COLUMNS)})"
)


# ---------------------------------------------------------------------------
# The standalone operation entry points. One driver, two named operations: each names the act it
# performs so a refusal is reported under the operation the caller asked for.


def add_evidence_claim(
    store: OpenedKnowledgeStore, request: EvidenceWriteRequest
) -> EvidenceWriteResult:
    """Record one authored evidence claim, holding the candidate lock and one transaction."""

    if not isinstance(request.command, AddEvidenceClaim):  # pragma: no cover - the union is closed
        return _refused(
            request,
            _command_mismatch_refusal("add_evidence_claim", request.command.kind),
            "add_evidence_claim",
        )
    return _evidence_operation(store, request, "add_evidence_claim")


def add_verification_observation(
    store: OpenedKnowledgeStore, request: EvidenceWriteRequest
) -> EvidenceWriteResult:
    """Record one authored verification observation, holding the candidate lock and one transaction."""

    if not isinstance(request.command, AddVerificationObservation):
        # pragma: no cover - the union is closed, so this is unreachable by construction
        return _refused(
            request,
            _command_mismatch_refusal("add_verification_observation", request.command.kind),
            "add_verification_observation",
        )
    return _evidence_operation(store, request, "add_verification_observation")


def apply_evidence_command(
    store: OpenedKnowledgeStore,
    command: EvidenceCommand,
    authorship: Authorship,
) -> tuple[EvidenceWriteIdentity, ...]:
    """Apply one evidence command inside the caller's open transaction.

    This is the batch path's in-transaction step: it raises a typed refusal for the batch to roll
    back, and returns the rows it really wrote.
    """

    if isinstance(command, AddEvidenceClaim):
        return _apply_add_claim(store, command, authorship)
    return _apply_add_observation(store, command, authorship)


# ---------------------------------------------------------------------------
# The two in-transaction steps.


def _apply_add_claim(
    store: OpenedKnowledgeStore, command: AddEvidenceClaim, authorship: Authorship
) -> tuple[EvidenceWriteIdentity, ...]:
    """Record one claim ledger row, its subject edge, its claimed coverage and its sealed aggregate."""

    require_proposed_origin(command.payload.state_at_origin, command.claim_id)
    require_evidence_generation(store, "add_evidence_claim")
    require_claim_links(store, command)
    validated = validate_record_payload(
        EVIDENCE_CLAIM_KIND,
        EVIDENCE_CLAIM_SCHEMA,
        command.payload.model_dump(mode="json"),
        operation="add_evidence_claim",
        record_id=command.claim_id,
    )
    if isinstance(validated, KnowledgeRefusal):  # pragma: no cover - the model already validated
        raise KnowledgeRefused(validated)
    payload = validated.model_dump(mode="json")
    route = command.governing_route_id
    _require_governing_route(store, route, "add_evidence_claim")
    store.write(
        _CLAIM_INSERT,
        (
            store.repository_id,
            *evidence_records.claim_row(command.claim_id, command.evidence_anchor_id, authorship),
        ),
    )
    _write_subject(store, command)
    for endpoint in command.coverage:
        store.write(
            _COVERAGE_INSERT,
            (store.repository_id, *evidence_records.coverage_row(command.claim_id, endpoint)),
        )
    _write_envelope(
        store,
        evidence_records.EnvelopeDraft(
            record_id=command.claim_id,
            kind=EVIDENCE_CLAIM_KIND,
            record_schema=EVIDENCE_CLAIM_SCHEMA,
            authority_home=_authority_home(store, "add_evidence_claim"),
            lifecycle=EVIDENCE_RECORD_LIFECYCLE,
            governing_route_id=route,
        ),
        evidence_records.RevisionDraft(
            record_id=command.claim_id,
            revision_id=command.revision_id,
            record_schema=EVIDENCE_CLAIM_SCHEMA,
            payload=payload,
        ),
        authorship,
        "add_evidence_claim",
    )
    written = [
        _written(
            "evidence_claim",
            command.claim_id,
            evidence_records.evidence_claim_row_digest(
                store.repository_id, command.claim_id, command.evidence_anchor_id, authorship
            ),
        ),
        _written(
            "evidence_claim_invariant_subject"
            if isinstance(command.subject, InvariantRevisionSubject)
            else "evidence_claim_facet_subject",
            command.claim_id,
            evidence_records.subject_row_digest(
                store.repository_id, command.claim_id, command.subject
            ),
        ),
    ]
    written.extend(
        _written(
            "evidence_claim_coverage",
            command.claim_id,
            evidence_records.coverage_row_digest(store.repository_id, command.claim_id, endpoint),
        )
        for endpoint in command.coverage
    )
    written.extend(_envelope_entries(store, command.claim_id, command.revision_id, authorship))
    return tuple(written)


def _apply_add_observation(
    store: OpenedKnowledgeStore, command: AddVerificationObservation, authorship: Authorship
) -> tuple[EvidenceWriteIdentity, ...]:
    """Record one observation row and its sealed aggregate, checked against the artifact's bytes."""

    require_proposed_origin(command.payload.state_at_origin, command.observation_id)
    require_evidence_generation(store, "add_verification_observation")
    payload = _checked_observation_payload(store, command)
    route = command.governing_route_id
    _require_governing_route(store, route, "add_verification_observation")
    store.write(
        _OBSERVATION_INSERT,
        (
            store.repository_id,
            command.observation_id,
            *evidence_records.observation_row(payload),
        ),
    )
    _write_envelope(
        store,
        evidence_records.EnvelopeDraft(
            record_id=command.observation_id,
            kind=VERIFICATION_OBSERVATION_KIND,
            record_schema=VERIFICATION_OBSERVATION_SCHEMA,
            authority_home=_authority_home(store, "add_verification_observation"),
            lifecycle=EVIDENCE_RECORD_LIFECYCLE,
            governing_route_id=route,
        ),
        evidence_records.RevisionDraft(
            record_id=command.observation_id,
            revision_id=command.revision_id,
            record_schema=VERIFICATION_OBSERVATION_SCHEMA,
            payload=payload.model_dump(mode="json"),
        ),
        authorship,
        "add_verification_observation",
    )
    written = [
        _written(
            "verification_observation",
            command.observation_id,
            evidence_records.observation_row_digest(
                store.repository_id, command.observation_id, payload.model_dump(mode="json")
            ),
        )
    ]
    written.extend(
        _envelope_entries(store, command.observation_id, command.revision_id, authorship)
    )
    return tuple(written)


def _write_subject(store: OpenedKnowledgeStore, command: AddEvidenceClaim) -> None:
    """Write the one subject edge the claim's subject kind populates."""

    subject = command.subject
    statement = (
        _INVARIANT_SUBJECT_INSERT
        if isinstance(subject, InvariantRevisionSubject)
        else _FACET_SUBJECT_INSERT
    )
    store.write(
        statement, (store.repository_id, *evidence_records.subject_row(subject, command.claim_id))
    )


def _write_envelope(
    store: OpenedKnowledgeStore,
    draft: evidence_records.EnvelopeDraft,
    revision: evidence_records.RevisionDraft,
    authorship: Authorship,
    operation: KnowledgeOperation,
) -> None:
    """Write one record envelope and its first sealed revision.

    The payload is validated through the envelope seam by the caller before this runs, because that
    function is the only place a write path decides whether a payload is admissible. What this
    writes is the row the seam admitted, not a re-derivation of it. ``operation`` names the act whose
    authority home the envelope inherits, so a refusal inside the transaction is reported under the
    operation the caller asked for.
    """

    del operation
    store.write(
        _RECORD_INSERT,
        (store.repository_id, *evidence_records.envelope_record_row(draft, authorship)),
    )
    store.write(
        _REVISION_INSERT,
        (store.repository_id, *evidence_records.record_revision_row(revision, authorship)),
    )


def _envelope_entries(
    store: OpenedKnowledgeStore, record_id: str, revision_id: str, authorship: Authorship
) -> tuple[EvidenceWriteIdentity, ...]:
    """Return the receipt entries for one written envelope and its sealed revision."""

    return (
        _written(
            "knowledge_record",
            record_id,
            evidence_records.envelope_record_row_digest(
                store.repository_id, _stored_envelope(store, record_id), authorship
            ),
        ),
        _written(
            "record_revision",
            revision_id,
            _stored_revision_digest(store, revision_id),
        ),
    )


def _stored_envelope(store: OpenedKnowledgeStore, record_id: str) -> evidence_records.EnvelopeDraft:
    """Return the envelope row the write just stored, as the draft its digest is taken over."""

    rows = tuple(
        store.connection.execute(evidence_records.RECORD_BY_ID, (store.repository_id, record_id))
    )
    if not rows:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"record {record_id} was not stored")
    row = rows[0]
    return evidence_records.EnvelopeDraft(
        record_id=str(row[1]),
        kind=str(row[2]),
        record_schema=str(row[6]),
        authority_home=str(row[3]),
        lifecycle=str(row[4]),
        governing_route_id=None if row[5] is None else str(row[5]),
    )


def _stored_revision_digest(store: OpenedKnowledgeStore, revision_id: str) -> str:
    rows = tuple(
        store.connection.execute(
            evidence_records.REVISION_BY_ID, (store.repository_id, revision_id)
        )
    )
    if not rows:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"record revision {revision_id} was not stored")
    return str(rows[0][6])


# ---------------------------------------------------------------------------
# The link resolution. Three questions, each refused before any row is written.


def require_claim_links(store: OpenedKnowledgeStore, command: AddEvidenceClaim) -> None:
    """Refuse a claim whose subject, anchor or claimed-coverage endpoint does not resolve."""

    require_evidence_subject(store, command.subject, command.claim_id)
    endpoints.require_source_anchor_endpoint(
        store, command.evidence_anchor_id, command.claim_id, "add_evidence_claim"
    )
    for endpoint in command.coverage:
        if endpoint.kind == "realization_claim":
            endpoints.require_realization_claim_endpoint(
                store, coverage_identity(endpoint), command.claim_id, "add_evidence_claim"
            )
            continue
        endpoints.require_source_anchor_endpoint(
            store, coverage_identity(endpoint), command.claim_id, "add_evidence_claim"
        )


def require_evidence_subject(store: OpenedKnowledgeStore, subject: object, claim_id: str) -> None:
    """Refuse a claim subject that does not resolve to the kind of revision it names.

    The two subject kinds are checked by the same shared module every other relation write uses, so
    two relation writes cannot drift into different codes for one failure. The facet kind is the
    stricter of the two: naming *any* record revision is not enough, because the subject is a
    ``KnowledgeFacet`` revision specifically -- so the envelope the revision belongs to must carry one
    of the eight authored-judgment kinds ``KS-R11@v1`` declares.
    """

    if isinstance(subject, InvariantRevisionSubject):
        endpoints.require_invariant_revision_endpoint(
            store, subject.revision_id, claim_id, "add_evidence_claim"
        )
        return
    if isinstance(subject, KnowledgeFacetRevisionSubject):
        require_facet_revision_subject(store, subject.revision_id, claim_id)
        return
    # Unreachable by construction: the union is closed at the two kinds above, which is what makes
    # "a subject naming two kinds" a shape error rather than a runtime question.
    raise KnowledgeStorageError(  # pragma: no cover - the union is closed over the two kinds
        f"evidence claim {claim_id} names a subject kind this build cannot check"
    )


def require_facet_revision_subject(
    store: OpenedKnowledgeStore, revision_id: str, claim_id: str
) -> None:
    """Refuse a facet subject whose revision is not stored, or is not a facet revision.

    The question itself belongs to the shared endpoint module, because it is an endpoint-kind
    question and this leaf must not grow a second answer to one. What this function adds is the only
    thing the shared module cannot know: which *relation* is asking.
    """

    endpoints.require_facet_revision_endpoint(store, revision_id, claim_id, "add_evidence_claim")


def _require_governing_route(
    store: OpenedKnowledgeStore, route_id: str | None, operation: KnowledgeOperation
) -> None:
    """Refuse a record whose declared governing route is not authored in this repository.

    An ungoverned record is the explicit ``None`` state and is never refused; a *named* route that
    does not exist is a dangling reference and is refused rather than stored.
    """

    if route_id is None:
        return
    if routes.route_exists(store.connection, store.repository_id, route_id):
        return
    raise KnowledgeRefused(
        missing_expected_row_refusal(operation=operation, table="route", record_id=route_id)
    )


# ---------------------------------------------------------------------------
# The generation gate and the promotion refusal.


def require_evidence_generation(store: OpenedKnowledgeStore, operation: KnowledgeOperation) -> None:
    """Refuse an evidence write against a dataset whose recorded generation predates its tables.

    §6.5. The dataset's own generation is read from the open store, so this is a comparison against
    what the file declares rather than against what the build supports, and the refusal carries both
    numbers as facts. Nothing is migrated, widened or written through; the dataset stays byte
    identical.
    """

    observed = store.generation.user_version
    required = REQUIRED_EVIDENCE_GENERATION.user_version
    if observed >= required:
        return
    raise KnowledgeRefused(
        generation_mismatch_refusal(
            operation,
            "the evidence-claim and verification-observation tables are registered by generation "
            f"{REQUIRED_EVIDENCE_GENERATION.schema_name} (user_version {required})",
            required=required,
            observed=observed,
        )
    )


def require_proposed_origin(state_at_origin: str, record_id: str) -> None:
    """Refuse a command that would store accepted origin data.

    A candidate batch authors proposals, so storing accepted origin data would make this operation a
    promotion path -- which it deliberately is not. The code is the shipped
    ``promotion_not_supported``, and both entry points owe the same refusal.
    """

    if state_at_origin == PROPOSED_STATE:
        return
    raise KnowledgeRefused(evidence_promotion_not_supported_refusal(record_id))


# ---------------------------------------------------------------------------
# The artifact digest check.


def checked_artifact_reference(
    payload: VerificationObservationPayload,
    artifact_root: str | None,
) -> tuple[ResultArtifactReference | None, KnowledgeRefusal | None]:
    """Return the artifact reference to store plus a refusal if its bytes contradict the digest.

    Three outcomes, and the record states which one happened:

    * no artifact reference, or no root to read it from: the reference is stored exactly as authored
      with ``digest_checked_against_bytes=False``. Nothing was read, so nothing is claimed.
    * the reference resolves under the root and the bytes hash to the recorded sha256 with the
      recorded size: the reference is stored with ``digest_checked_against_bytes=True``.
    * the reference resolves but the bytes disagree, or the path escapes the root: a typed refusal,
      and **no row is written**. Writing the record anyway would either launder a false digest into an
      immutable row under the weaker flag, or leave a hole in §3.4's required group; the caller's
      remedy is a corrected digest or a corrected artifact, both of which are new authored input.

    A path that does not exist under the root is *not* a refusal: a record is written about a run that
    already happened, and an artifact the run later moved is a read-time resolution fact rather than a
    reason the record cannot be written.
    """

    artifact = payload.result_artifact
    if artifact is None or artifact_root is None:
        return (artifact, None)
    root = Path(artifact_root)
    resolved = (root / artifact.path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        return (
            None,
            artifact_root_escape_refusal(
                path=artifact.path, recorded=artifact.sha256, observed=str(resolved)
            ),
        )
    if not resolved.is_file():
        return (artifact, None)
    observed_bytes = resolved.read_bytes()
    digest = hashlib.sha256(observed_bytes).hexdigest()
    if digest == artifact.sha256 and len(observed_bytes) == artifact.size_bytes:
        return (
            artifact.model_copy(update={"digest_checked_against_bytes": True}),
            None,
        )
    return (
        None,
        artifact_digest_mismatch_refusal(
            path=artifact.path,
            recorded=artifact.sha256,
            observed=f"{digest} ({len(observed_bytes)} bytes)",
        ),
    )


def _checked_observation_payload(
    store: OpenedKnowledgeStore, command: AddVerificationObservation
) -> VerificationObservationPayload:
    """Return the payload to store, with the artifact reference the bytes were checked against."""

    reference, refused = checked_artifact_reference(command.payload, command.artifact_root)
    if refused is not None:
        raise KnowledgeRefused(refused)
    validated = validate_record_payload(
        VERIFICATION_OBSERVATION_KIND,
        VERIFICATION_OBSERVATION_SCHEMA,
        command.payload.model_copy(update={"result_artifact": reference}).model_dump(mode="json"),
        operation="add_verification_observation",
        record_id=command.observation_id,
    )
    if isinstance(validated, KnowledgeRefusal):  # pragma: no cover - the model already validated
        raise KnowledgeRefused(validated)
    del store
    return VerificationObservationPayload.model_validate(validated.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# The driver, the refusal carrier and the small shared helpers.


def _evidence_operation(
    store: OpenedKnowledgeStore,
    request: EvidenceWriteRequest,
    operation: KnowledgeOperation,
) -> EvidenceWriteResult:
    """Run one standalone evidence write under the candidate lock and one transaction."""

    denied = scope_refusal(operation, store.repository_id, request.repository_id)
    if denied is None:
        denied = _generation_refusal(store, operation)
    if denied is None:
        denied = _origin_refusal(request.command)
    if denied is not None:
        return _refused(request, denied, operation)
    with store.exclusive_candidate_lock(operation) as lock_refusal:
        if lock_refusal is not None:
            return _refused(request, lock_refusal, operation)
        return store.within_immediate(
            lambda: _applied(store, request, operation),
            on_refusal=lambda refused_value: _refused(request, refused_value, operation),
            failure=SqliteFailureContext(
                operation=operation,
                table="knowledge_record",
                record_id=_command_record_id(request.command),
            ),
        )


def _generation_refusal(
    store: OpenedKnowledgeStore, operation: KnowledgeOperation
) -> KnowledgeRefusal | None:
    """Return the generation refusal one standalone write earns, or ``None``."""

    try:
        require_evidence_generation(store, operation)
    except KnowledgeRefused as refused:
        return refused.refusal
    return None


def _origin_refusal(command: EvidenceCommand) -> KnowledgeRefusal | None:
    """Return the promotion refusal one standalone write earns, or ``None``."""

    try:
        require_proposed_origin(command.payload.state_at_origin, _command_record_id(command))
    except KnowledgeRefused as refused:
        return refused.refusal
    return None


def _applied(
    store: OpenedKnowledgeStore, request: EvidenceWriteRequest, operation: KnowledgeOperation
) -> EvidenceWriteResult:
    """Run the in-transaction step and wrap its rows as the operation's receipt."""

    written = apply_evidence_command(store, request.command, request.provenance)
    del operation
    return EvidenceWriteResult(
        state="applied", repository_id=request.repository_id, written=written
    )


def _refused(
    request: EvidenceWriteRequest, refusal_value: KnowledgeRefusal, operation: KnowledgeOperation
) -> EvidenceWriteResult:
    del operation
    return EvidenceWriteResult(
        state="refused", repository_id=request.repository_id, refusal=refusal_value
    )


def _command_record_id(command: EvidenceCommand) -> str:
    if isinstance(command, AddEvidenceClaim):
        return command.claim_id
    return command.observation_id


def _command_mismatch_refusal(operation: KnowledgeOperation, kind: str) -> KnowledgeRefusal:
    """Refuse a request whose command is not the act its operation names."""

    return refusal(
        "invalid_reference",
        operation,
        f"this operation records one authored act and the request carries {kind!r}",
        next_action=(
            "Submit the command the operation names, or call the operation that performs the act "
            "the command expresses. Nothing was written."
        ),
    )


def _authority_home(store: OpenedKnowledgeStore, operation: KnowledgeOperation) -> str:
    """Return the authority home the bound repository declares."""

    repository = store.get_repository()
    if repository is None:  # pragma: no cover - an unbound store cannot reach a write
        raise KnowledgeStorageError(
            f"the store is not bound to repository namespace {store.repository_id} ({operation})"
        )
    return repository.authority_home


def _written(table: str, record_id: str, digest: str) -> EvidenceWriteIdentity:
    """Build one receipt entry without re-validating it: the store computed every field here."""

    return EvidenceWriteIdentity.model_construct(
        state="written", table=table, record_id=record_id, digest=digest
    )


__all__ = [
    "REQUIRED_EVIDENCE_GENERATION",
    "add_evidence_claim",
    "add_verification_observation",
    "apply_evidence_command",
    "checked_artifact_reference",
    "require_claim_links",
    "require_evidence_generation",
    "require_evidence_subject",
    "require_facet_revision_subject",
    "require_proposed_origin",
]
