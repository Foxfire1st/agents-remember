"""The authored facet write path: two entry points per write and the refusals between them.

Six authored acts live here -- record a facet, attach it to an exact endpoint, remove one
attachment, author an explanation, edit an explanation, and record a designation -- and each has
the shipped pair of entry points, exactly as the label edits do:

* the **operation**, which owns the candidate lock and one ``BEGIN IMMEDIATE`` transaction, and
  returns a typed :class:`FacetWriteResult`;
* the **in-transaction step**, which writes inside a caller's open transaction and raises
  :class:`KnowledgeRefused` so the batch path rolls the whole batch back.

That split is what makes "a refused facet write leaves the dataset exactly as it was" a property of
the transaction boundary rather than a promise about statement order: every refusal below is raised
inside the transaction, and the step that raises it has already written nothing it does not undo.

Four rules shape the write path:

* **The payload seam is the only payload decision point.** A facet payload is validated by
  :func:`…record_envelope.validate_facet_payload`, so an unknown or ninth subtype, a payload that
  omits a required meaning, an undeclared field and a payload that arrives carrying its own
  ``actor_ref`` are all the same shipped ``invalid_payload`` refusal, raised before any row exists.
* **Provenance comes from the admission.** The authorship envelope is a parameter of these
  functions, never a field of a command, so no part of a submitted payload can become the record's
  author, authorization or instant.
* **A facet is authored as proposed origin data.** A command carrying an accepted origin state is
  refused with the shipped ``promotion_not_supported``; there is no promotion operation, and the
  accepted-origin consistency rule is inherited from the vocabulary base rather than re-implemented.
* **Every reference is checked before the row it belongs to.** An attachment names an exact endpoint
  that must be stored, an explanation names an exact statement revision that must be stored, and a
  supersession names an exact earlier decision revision that must be stored -- so an attachment, a
  supersession or a designation is never inferred from a name, a path or prose, and a dangling
  reference is refused rather than stored.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import endpoints, families, lineage, routes
from agents_remember.memory.knowledge.facet_records import (
    ATTACHMENT_BY_ID,
    EXPLANATION_BY_ID,
    EXPLANATION_REVISION_BY_ID,
    REVISION_BY_ID,
    AttachmentDraft,
    FacetEnvelopeDraft,
    RecordRevisionDraft,
    attachment_row,
    attachment_row_digest,
    decode_attachment_row,
    decode_explanation_row,
    decode_facet_record_row,
    explanation_revision_row,
    explanation_row,
    facet_record_row,
    facet_record_row_digest,
    facet_record_schema_of,
    record_revision_row,
    supersession_row,
    supersession_row_digest,
)
from agents_remember.memory.knowledge.record_envelope import validate_facet_payload
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    SqliteFailureContext,
    explanation_revision_refusal,
    facet_promotion_not_supported_refusal,
    facet_supersession_cycle_refusal,
    generation_mismatch_refusal,
    missing_expected_row_refusal,
    missing_relation_endpoint_refusal,
    refusal,
    scope_refusal,
    stale_expected_row_refusal,
)
from agents_remember.memory.knowledge.schema_generations import GENERATION_3
from agents_remember.models.knowledge.authorship import PROPOSED_STATE, Authorship
from agents_remember.models.knowledge.facet import (
    AddExplanationRevision,
    AddFacet,
    AttachFacet,
    AuthorExplanation,
    DesignateExplanation,
    ExplanationSubject,
    FacetCommand,
    FacetWriteIdentity,
    FacetWriteRequest,
    FacetWriteResult,
    RemoveFacetAttachment,
    subject_identity,
)
from agents_remember.models.knowledge.facet_read import ExplanationRecord
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The generation whose tables a facet write needs. Requirement 8.4: a dataset that predates them is
# refused, never migrated or widened.
REQUIRED_FACET_GENERATION = GENERATION_3

_RECORD_INSERT = (
    "INSERT INTO knowledge_record (repository_id, record_id, kind, authority_home, lifecycle, "
    "governing_route_id, record_schema, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_REVISION_INSERT = (
    "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, payload, "
    "predecessor_revision_id, content_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_SUPERSESSION_INSERT = (
    "INSERT INTO facet_decision_supersession (repository_id, superseding_revision_id, "
    "superseded_revision_id, provenance) VALUES (?, ?, ?, ?)"
)
_ATTACHMENT_INSERT = (
    "INSERT INTO facet_attachment (repository_id, attachment_id, facet_revision_id, endpoint_kind, "
    "invariant_revision_id, family_revision_id, anchor_id, claim_id, provenance) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_EXPLANATION_INSERT = (
    "INSERT INTO explanation (repository_id, explanation_id, subject_kind, subject_invariant_id, "
    "subject_invariant_revision_id, subject_family_id, subject_family_revision_id, "
    "current_revision_id, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_EXPLANATION_REVISION_INSERT = (
    "INSERT INTO explanation_revision (repository_id, explanation_id, revision_id, "
    "predecessor_revision_id, body, payload_digest, provenance) VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_RECORD_KIND_OF_REVISION = (
    "SELECT envelope.* FROM knowledge_record AS envelope "
    "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
    "AND revision.record_id = envelope.record_id "
    "WHERE envelope.repository_id = ? AND revision.revision_id = ?"
)
_ATTACHMENT_DELETE = "DELETE FROM facet_attachment WHERE repository_id = ? AND attachment_id = ?"
_EXPLANATION_DESIGNATE = (
    "UPDATE explanation SET current_revision_id = ? WHERE repository_id = ? AND explanation_id = ?"
)
_EXPLANATION_ATTACHED_REVISIONS = (
    "SELECT revision_id FROM explanation_revision WHERE repository_id = ? AND explanation_id = ?"
)


# ---------------------------------------------------------------------------
# The operation entry points. One driver, six named operations: each names the act it performs so a
# refusal is reported under the operation the caller asked for, and each delegates to the same lock
# and transaction boundary the shipped operations use.


def add_facet(store: OpenedKnowledgeStore, request: FacetWriteRequest) -> FacetWriteResult:
    """Record one authored facet, holding the candidate lock and one transaction."""

    return _facet_operation(store, request, "add_facet")


def attach_facet(store: OpenedKnowledgeStore, request: FacetWriteRequest) -> FacetWriteResult:
    """Attach one exact facet revision to one exact endpoint."""

    return _facet_operation(store, request, "attach_facet")


def remove_facet_attachment(
    store: OpenedKnowledgeStore, request: FacetWriteRequest
) -> FacetWriteResult:
    """Remove one attachment by identity and expected row digest."""

    return _facet_operation(store, request, "remove_facet_attachment")


def author_explanation(store: OpenedKnowledgeStore, request: FacetWriteRequest) -> FacetWriteResult:
    """Author the first revision of one separable explanation record."""

    return _facet_operation(store, request, "author_explanation")


def add_explanation_revision(
    store: OpenedKnowledgeStore, request: FacetWriteRequest
) -> FacetWriteResult:
    """Edit an explanation by authoring a successor naming its exact predecessor."""

    return _facet_operation(store, request, "add_explanation_revision")


def designate_explanation(
    store: OpenedKnowledgeStore, request: FacetWriteRequest
) -> FacetWriteResult:
    """Record which explanation revision is designated, guarded by the expected row digest."""

    return _facet_operation(store, request, "designate_explanation")


# ---------------------------------------------------------------------------
# The in-transaction steps. Each raises KnowledgeRefused for the batch path to roll back, and each
# returns the rows it really wrote -- a step whose requested effect was already stored writes
# nothing and reports nothing.


def apply_facet_command(
    store: OpenedKnowledgeStore,
    command: FacetCommand,
    authorship: Authorship,
    pending: PendingIdentities = frozenset(),
) -> tuple[FacetWriteIdentity, ...]:
    """Apply one facet command inside the caller's open transaction.

    ``pending`` is the batch's declared identity set. A reference that resolves against it is left
    to the batch's own completed-graph preconditions, which is the same split the shipped lineage
    checks use: the preconditions decide what the batch *declares*, and this step decides what is
    already stored.
    """

    return _STEPS[command.kind](store, command, authorship, pending)


def apply_add_facet(
    store: OpenedKnowledgeStore,
    command: AddFacet,
    authorship: Authorship,
    pending: PendingIdentities = frozenset(),
) -> tuple[FacetWriteIdentity, ...]:
    """Record one facet envelope and its first sealed revision."""

    require_proposed_origin(command)
    validated = validate_facet_payload(
        command.facet_kind, command.payload, operation="add_facet", record_id=command.record_id
    )
    if isinstance(validated, KnowledgeRefusal):
        raise KnowledgeRefused(validated)
    payload = validated.model_dump(mode="json")
    draft = FacetEnvelopeDraft(
        record_id=command.record_id,
        facet_kind=command.facet_kind,
        authority_home=_authority_home(store),
        lifecycle=command.state_at_origin,
        governing_route_id=command.governing_route_id,
    )
    _require_governing_route(store, command.governing_route_id)
    store.write(_RECORD_INSERT, (store.repository_id, *facet_record_row(draft, authorship)))
    store.write(
        _REVISION_INSERT,
        (
            store.repository_id,
            *record_revision_row(
                RecordRevisionDraft(
                    record_id=command.record_id,
                    revision_id=command.revision_id,
                    record_schema=facet_record_schema_of(command.facet_kind),
                    predecessor_revision_id=None,
                ),
                payload,
                authorship,
            ),
        ),
    )
    written = [
        _written(
            "knowledge_record",
            command.record_id,
            facet_record_row_digest(store.repository_id, draft, authorship),
        ),
        _written(
            "record_revision",
            command.revision_id,
            _stored_revision_digest(store, command.revision_id),
        ),
    ]
    if command.supersedes_revision_id is not None:
        written.append(_record_supersession(store, command, authorship, pending))
    return tuple(written)


def require_proposed_origin(command: AddFacet) -> None:
    """Refuse a facet command that would store accepted origin data (requirement 3.2).

    A shared step rather than a batch-only check, because both entry points owe the same refusal:
    the batch refuses it in its preconditions, before any row exists, and the standalone operation
    refuses it inside its transaction. The code is the shipped ``promotion_not_supported``.
    """

    if command.state_at_origin == PROPOSED_STATE:
        return
    raise KnowledgeRefused(facet_promotion_not_supported_refusal(command.record_id))


def _record_supersession(
    store: OpenedKnowledgeStore,
    command: AddFacet,
    authorship: Authorship,
    pending: PendingIdentities,
) -> FacetWriteIdentity:
    """Record one supersession edge and refuse a graph that reaches itself."""

    superseded = command.supersedes_revision_id
    if superseded is None:  # pragma: no cover - the caller checks before calling
        raise KnowledgeStorageError("a supersession edge needs a superseded revision")
    if ("record_revision", superseded) not in pending:
        require_decision_revision(store, superseded, command.revision_id)
    store.write(
        _SUPERSESSION_INSERT,
        (store.repository_id, *supersession_row(command.revision_id, superseded, authorship)),
    )
    cycle = require_acyclic_supersessions(store, command.revision_id)
    if cycle is not None:
        raise KnowledgeRefused(cycle)
    return _written(
        "facet_decision_supersession",
        command.revision_id,
        supersession_row_digest(store.repository_id, command.revision_id, superseded, authorship),
    )


def require_decision_revision(
    store: OpenedKnowledgeStore, revision_id: str, relation_id: str
) -> None:
    """Refuse a supersession whose endpoint is not a stored decision revision.

    Requirement 5.2: the edge names an exact earlier *decision* revision. A revision that is not
    stored, and a stored revision of another facet kind, are both "there is no decision revision
    with this identity in this namespace" -- which is the shipped ``missing_relation_endpoint``
    refusal, naming the endpoint kind and identity before any row is written.
    """

    kind = _kind_of_revision(store, revision_id)
    if kind == "decision":
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation="add_facet",
            table="facet_decision_supersession",
            relation_id=relation_id,
            endpoint_id=revision_id,
            endpoint_kind="decision revision",
        )
    )


def _kind_of_revision(store: OpenedKnowledgeStore, revision_id: str) -> str | None:
    rows = tuple(
        store.connection.execute(_RECORD_KIND_OF_REVISION, (store.repository_id, revision_id))
    )
    if not rows:
        return None
    return decode_facet_record_row(rows[0]).facet_kind


def require_acyclic_supersessions(
    store: OpenedKnowledgeStore, revision_id: str
) -> KnowledgeRefusal | None:
    """Refuse a supersession graph that reaches itself, naming the involved decisions.

    The rule that decides is the shared one (:mod:`…lineage`), over this record kind's own edge
    table: the walk is the same strongly-connected-component scan the two predecessor graphs use,
    so no second cycle rule grows beside the first.
    """

    edges = lineage.supersession_edges(store.connection, store.repository_id)
    cycle = lineage.cycle_vertices(_graph_of(edges))
    if not cycle:
        return None
    members = tuple(sorted(cycle))
    return facet_supersession_cycle_refusal(revision_id, members)


def _graph_of(edges: Sequence[tuple[str, str]]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for child, parent in edges:
        graph.setdefault(child, set()).add(parent)
        graph.setdefault(parent, set())
    return graph


def apply_attach_facet(
    store: OpenedKnowledgeStore,
    command: AttachFacet,
    authorship: Authorship,
    pending: PendingIdentities = frozenset(),
) -> tuple[FacetWriteIdentity, ...]:
    """Attach one exact facet revision to one exact endpoint."""

    if ("record_revision", command.facet_revision_id) not in pending:
        _require_facet_revision(store, command.facet_revision_id, command.attachment_id)
    endpoints.require_attachment_endpoint(store, command.endpoint, command.attachment_id)
    draft = AttachmentDraft(
        attachment_id=command.attachment_id,
        facet_revision_id=command.facet_revision_id,
        endpoint=command.endpoint,
    )
    store.write(_ATTACHMENT_INSERT, (store.repository_id, *attachment_row(draft, authorship)))
    return (
        _written(
            "facet_attachment",
            command.attachment_id,
            attachment_row_digest(store.repository_id, draft, authorship),
        ),
    )


def _require_facet_revision(
    store: OpenedKnowledgeStore, revision_id: str, relation_id: str
) -> None:
    """Refuse an attachment whose facet revision is not stored as a facet revision."""

    if _kind_of_revision(store, revision_id) is not None:
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation="attach_facet",
            table="record_revision",
            relation_id=relation_id,
            endpoint_id=revision_id,
            endpoint_kind="facet revision",
        )
    )


def apply_remove_facet_attachment(
    store: OpenedKnowledgeStore, command: RemoveFacetAttachment
) -> tuple[FacetWriteIdentity, ...]:
    """Remove one attachment by identity and expected row digest.

    The removal deletes that attachment row and nothing else: never the facet revision it named,
    never the record the endpoint named (requirement 4.6).
    """

    rows = tuple(
        store.connection.execute(ATTACHMENT_BY_ID, (store.repository_id, command.attachment_id))
    )
    if not rows:
        raise KnowledgeRefused(
            missing_expected_row_refusal(
                operation="remove_facet_attachment",
                table="facet_attachment",
                record_id=command.attachment_id,
            )
        )
    stored = decode_attachment_row(rows[0])
    if stored.row_digest != command.expected_row_digest:
        raise KnowledgeRefused(
            stale_expected_row_refusal(
                operation="remove_facet_attachment",
                table="facet_attachment",
                record_id=command.attachment_id,
                expected=command.expected_row_digest,
                observed=stored.row_digest,
            )
        )
    store.write(_ATTACHMENT_DELETE, (store.repository_id, command.attachment_id))
    entry = FacetWriteIdentity.model_construct(
        state="removed",
        table="facet_attachment",
        record_id=command.attachment_id,
        digest=stored.row_digest,
    )
    return (entry,)


def apply_author_explanation(
    store: OpenedKnowledgeStore,
    command: AuthorExplanation,
    authorship: Authorship,
    pending: PendingIdentities = frozenset(),
) -> tuple[FacetWriteIdentity, ...]:
    """Author the first revision of one separable explanation record."""

    if (command.subject.kind, command.subject.revision_id) not in pending:
        require_explanation_subject(store, command.subject)
    store.write(
        _EXPLANATION_INSERT,
        (
            store.repository_id,
            *explanation_row(command.explanation_id, command.subject, None, authorship),
        ),
    )
    store.write(
        _EXPLANATION_REVISION_INSERT,
        (
            store.repository_id,
            *explanation_revision_row(
                command.explanation_id, command.revision_id, None, command.body, authorship
            ),
        ),
    )
    return (
        _written(
            "explanation",
            command.explanation_id,
            _explanation_digest(store, command.explanation_id),
        ),
        _written(
            "explanation_revision",
            command.revision_id,
            _stored_explanation_revision_digest(store, command.revision_id),
        ),
    )


def require_explanation_subject(store: OpenedKnowledgeStore, subject: ExplanationSubject) -> None:
    """Refuse an explanation whose subject statement revision is not stored.

    Requirement 6.1: the subject is an exact statement revision, recorded as an explicit reference.
    The two subject kinds are the closed set the design names, and each resolves against its own
    canonical table -- so a family subject can never be satisfied by an invariant revision.
    """

    subject_id, revision_id = subject_identity(subject)
    stored_identity = _stored_subject_identity(store, subject)
    if stored_identity == subject_id:
        return
    raise KnowledgeRefused(
        missing_relation_endpoint_refusal(
            operation="author_explanation",
            table=(
                "invariant_revision" if subject.kind == "invariant_revision" else "family_revision"
            ),
            relation_id=revision_id,
            endpoint_id=revision_id,
            endpoint_kind=f"{subject.kind} statement revision",
        )
    )


def _stored_subject_identity(
    store: OpenedKnowledgeStore, subject: ExplanationSubject
) -> str | None:
    if subject.kind == "invariant_revision":
        stored = store.get_revision(subject.revision_id)
        return None if stored is None else stored.revision.invariant_id
    stored_family = families.get_family_revision(store, subject.revision_id)
    return None if stored_family is None else stored_family.revision.family_id


def apply_add_explanation_revision(
    store: OpenedKnowledgeStore,
    command: AddExplanationRevision,
    authorship: Authorship,
    pending: PendingIdentities = frozenset(),
) -> tuple[FacetWriteIdentity, ...]:
    """Author one successor explanation revision naming its exact predecessor.

    The predecessor must already be stored for **this** explanation. That is what makes the chain
    append-only in practice rather than only in intent: a revision can only name a revision that
    exists, so two revisions authored in one transaction cannot name each other and no predecessor
    cycle is constructible.
    """

    del pending
    explanation = _require_explanation(store, command.explanation_id, "add_explanation_revision")
    attached = {
        str(row[0])
        for row in store.connection.execute(
            _EXPLANATION_ATTACHED_REVISIONS, (store.repository_id, command.explanation_id)
        )
    }
    if command.predecessor_revision_id not in attached:
        raise KnowledgeRefused(
            explanation_revision_refusal(
                "add_explanation_revision",
                "the named predecessor is not a revision of this explanation",
                explanation_id=explanation.explanation_id,
                revision_id=command.predecessor_revision_id,
                expected=f"a stored revision of explanation {command.explanation_id}",
            )
        )
    store.write(
        _EXPLANATION_REVISION_INSERT,
        (
            store.repository_id,
            *explanation_revision_row(
                command.explanation_id,
                command.revision_id,
                command.predecessor_revision_id,
                command.body,
                authorship,
            ),
        ),
    )
    return (
        _written(
            "explanation_revision",
            command.revision_id,
            _stored_explanation_revision_digest(store, command.revision_id),
        ),
    )


def apply_designate_explanation(
    store: OpenedKnowledgeStore, command: DesignateExplanation
) -> tuple[FacetWriteIdentity, ...]:
    """Record which explanation revision is designated, guarded by the expected row digest.

    The designation is the one mutable field of an explanation identity row, so it is guarded the
    way a label edit is: the caller names the row it read, a mismatch is the shipped
    ``stale_precondition`` and is never applied silently, and re-stating the stored designation
    writes nothing.
    """

    existing = _require_explanation(store, command.explanation_id, "designate_explanation")
    if existing.row_digest != command.expected_row_digest:
        raise KnowledgeRefused(
            stale_expected_row_refusal(
                operation="designate_explanation",
                table="explanation",
                record_id=command.explanation_id,
                expected=command.expected_row_digest,
                observed=existing.row_digest,
            )
        )
    attached = {
        str(row[0])
        for row in store.connection.execute(
            _EXPLANATION_ATTACHED_REVISIONS, (store.repository_id, command.explanation_id)
        )
    }
    if command.revision_id not in attached:
        raise KnowledgeRefused(
            explanation_revision_refusal(
                "designate_explanation",
                "the designated revision is not a revision of this explanation",
                explanation_id=command.explanation_id,
                revision_id=command.revision_id,
                expected=f"a stored revision of explanation {command.explanation_id}",
            )
        )
    if existing.current_revision_id == command.revision_id:
        return ()
    store.write(
        _EXPLANATION_DESIGNATE,
        (command.revision_id, store.repository_id, command.explanation_id),
    )
    return (
        _written(
            "explanation",
            command.explanation_id,
            _explanation_digest(store, command.explanation_id),
        ),
    )


def _require_explanation(
    store: OpenedKnowledgeStore, explanation_id: str, operation: str
) -> ExplanationRecord:
    """Return one stored explanation record, or refuse the operation that named it."""

    rows = tuple(store.connection.execute(EXPLANATION_BY_ID, (store.repository_id, explanation_id)))
    if not rows:
        raise KnowledgeRefused(
            missing_expected_row_refusal(
                operation=operation, table="explanation", record_id=explanation_id
            )
        )
    return decode_explanation_row(rows[0])


# ---------------------------------------------------------------------------
# The dispatch table, declared after the steps it names so the two cannot disagree about what a
# command kind maps to.


_FacetStep = Callable[
    ["OpenedKnowledgeStore", FacetCommand, Authorship, "PendingIdentities"],
    "tuple[FacetWriteIdentity, ...]",
]

# The identities a batch declares, so a command may cite a record any command in the same batch
# creates -- wherever in the sequence that command appears. The standalone path passes the empty
# set: it has no batch, so every reference it makes must already be stored.
PendingIdentities = frozenset


def _step_taking_authorship(step: _FacetStep) -> _FacetStep:
    """Adapt a step that needs the admitted envelope to the table's uniform signature."""

    return step


def _step_ignoring_authorship(
    step: Callable[[OpenedKnowledgeStore, FacetCommand], tuple[FacetWriteIdentity, ...]],
) -> _FacetStep:
    """Adapt a removal or designation step, which needs neither the envelope nor the pending set."""

    return lambda store, command, _authorship, _pending: step(store, command)


_STEPS: Mapping[str, _FacetStep] = {
    "add_facet": apply_add_facet,
    "attach_facet": apply_attach_facet,
    "remove_facet_attachment": _step_ignoring_authorship(apply_remove_facet_attachment),
    "author_explanation": apply_author_explanation,
    "add_explanation_revision": apply_add_explanation_revision,
    "designate_explanation": _step_ignoring_authorship(apply_designate_explanation),
}


def _facet_operation(
    store: OpenedKnowledgeStore, request: FacetWriteRequest, operation: KnowledgeOperation
) -> FacetWriteResult:
    """Run one standalone facet write under one lock and one immediate transaction."""

    denied = scope_refusal(operation, store.repository_id, request.repository_id)
    if denied is None:
        denied = require_facet_generation(store, operation)
    if denied is None:
        denied = _command_matches_operation(request.command, operation)
    if denied is not None:
        return _refused_result(request, denied)
    with store.exclusive_candidate_lock(operation) as lock_refusal:
        if lock_refusal is not None:
            return _refused_result(request, lock_refusal)
        return store.within_immediate(
            lambda: _apply_facet_command(store, request, operation),
            on_refusal=lambda refused: _refused_result(request, refused),
            failure=SqliteFailureContext(
                operation=operation,
                table="knowledge_record",
                record_id=_command_record_id(request.command),
            ),
        )


def _apply_facet_command(
    store: OpenedKnowledgeStore, request: FacetWriteRequest, operation: KnowledgeOperation
) -> FacetWriteResult:
    """Apply one standalone facet command inside the caller's open transaction."""

    del operation
    written = apply_facet_command(store, request.command, request.provenance, frozenset())
    if not written:
        return FacetWriteResult(state="no_change", repository_id=request.repository_id)
    return FacetWriteResult(state="applied", repository_id=request.repository_id, written=written)


def _refused_result(
    request: FacetWriteRequest, refusal_value: KnowledgeRefusal
) -> FacetWriteResult:
    """Build the receipt for a refusal: nothing was written, so nothing is reported as written."""

    return FacetWriteResult(
        state="refused", repository_id=request.repository_id, refusal=refusal_value
    )


def _command_matches_operation(
    command: FacetCommand, operation: KnowledgeOperation
) -> KnowledgeRefusal | None:
    """Refuse a command presented to another command's operation.

    The driver dispatches on the command's own kind, so this is not a second dispatch: it is the
    check that makes "the operation a caller named is the operation that ran" true, and it refuses
    rather than silently performing the command's own act under the wrong name.
    """

    if _OPERATION_OF_KIND[command.kind] == operation:
        return None
    return refusal(
        "invalid_reference",
        operation,
        "the request carries a command this operation does not perform",
        facts=RefusalFacts(
            table="knowledge_record",
            record_id=_command_record_id(command),
            expected=operation,
            observed=command.kind,
        ),
        next_action=(
            "Address the command to its own operation; a write is reported under the operation the "
            "caller asked for, so an act performed under another name would be a false receipt."
        ),
    )


_OPERATION_OF_KIND: Mapping[str, KnowledgeOperation] = {
    "add_facet": "add_facet",
    "attach_facet": "attach_facet",
    "remove_facet_attachment": "remove_facet_attachment",
    "author_explanation": "author_explanation",
    "add_explanation_revision": "add_explanation_revision",
    "designate_explanation": "designate_explanation",
}


def _command_record_id(command: FacetCommand) -> str:
    if isinstance(command, AddFacet):
        return command.record_id
    if isinstance(command, AttachFacet):
        return command.attachment_id
    if isinstance(command, RemoveFacetAttachment):
        return command.attachment_id
    if isinstance(command, AuthorExplanation | AddExplanationRevision | DesignateExplanation):
        return command.explanation_id
    return "<unknown command>"  # pragma: no cover - the union is closed over the six above


def require_facet_generation(
    store: OpenedKnowledgeStore, operation: KnowledgeOperation
) -> KnowledgeRefusal | None:
    """Refuse a facet write against a dataset whose recorded generation predates its tables.

    Requirement 8.4. The dataset's own generation is read from the open store, so this is a
    comparison against what the file declares rather than against what the build supports, and the
    refusal carries both numbers as facts. Nothing is migrated, widened or written through.
    """

    observed = store.generation.user_version
    required = REQUIRED_FACET_GENERATION.user_version
    if observed >= required:
        return None
    return generation_mismatch_refusal(
        operation,
        "the facet tables are registered by generation "
        f"{REQUIRED_FACET_GENERATION.schema_name} (user_version {required})",
        required=required,
        observed=observed,
    )


# ---------------------------------------------------------------------------
# Small shared helpers.


def _authority_home(store: OpenedKnowledgeStore) -> str:
    """Return the authority home the bound repository declares.

    A facet envelope's ``authority_home`` is a fact about the namespace it was written into, not a
    field a caller authors: the destination resolved that namespace, so the record inherits it.
    """

    repository = store.get_repository()
    if repository is None:  # pragma: no cover - an unbound store cannot reach a write
        raise KnowledgeStorageError(
            f"the store is not bound to repository namespace {store.repository_id}"
        )
    return repository.authority_home


def _require_governing_route(store: OpenedKnowledgeStore, route_id: str | None) -> None:
    """Refuse a record whose declared governing route is not authored in this repository.

    An ungoverned record is the explicit ``None`` state and is never refused (requirement 4.2); a
    *named* route that does not exist is a dangling reference and is refused rather than stored.
    """

    if route_id is None:
        return
    if routes.route_exists(store.connection, store.repository_id, route_id):
        return
    raise KnowledgeRefused(
        missing_expected_row_refusal(operation="add_facet", table="route", record_id=route_id)
    )


def _written(table: str, record_id: str, digest: str) -> FacetWriteIdentity:
    """Build one receipt entry without re-validating it: the store computed every field here."""

    return FacetWriteIdentity.model_construct(
        state="written", table=table, record_id=record_id, digest=digest
    )


def _stored_revision_digest(store: OpenedKnowledgeStore, revision_id: str) -> str:
    rows = tuple(store.connection.execute(REVISION_BY_ID, (store.repository_id, revision_id)))
    if not rows:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"facet revision {revision_id} was not stored")
    return str(rows[0][6])


def _stored_explanation_revision_digest(store: OpenedKnowledgeStore, revision_id: str) -> str:
    rows = tuple(
        store.connection.execute(EXPLANATION_REVISION_BY_ID, (store.repository_id, revision_id))
    )
    if not rows:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"explanation revision {revision_id} was not stored")
    return str(rows[0][5])


def _explanation_digest(store: OpenedKnowledgeStore, explanation_id: str) -> str:
    rows = tuple(store.connection.execute(EXPLANATION_BY_ID, (store.repository_id, explanation_id)))
    if not rows:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeStorageError(f"explanation {explanation_id} was not stored")
    return decode_explanation_row(rows[0]).row_digest


__all__ = [
    "REQUIRED_FACET_GENERATION",
    "add_explanation_revision",
    "add_facet",
    "apply_add_explanation_revision",
    "apply_add_facet",
    "apply_attach_facet",
    "apply_author_explanation",
    "apply_designate_explanation",
    "apply_facet_command",
    "apply_remove_facet_attachment",
    "attach_facet",
    "author_explanation",
    "designate_explanation",
    "remove_facet_attachment",
    "require_acyclic_supersessions",
    "require_decision_revision",
    "require_explanation_subject",
    "require_facet_generation",
    "require_proposed_origin",
]
