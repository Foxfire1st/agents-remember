"""The requirement-revision record group: a database home for requirement *meaning*.

This module is the whole operation surface of ``RequirementRevision``. It adds no table: a
requirement revision is an envelope record, written through the one payload seam
:func:`agents_remember.memory.knowledge.record_envelope.validate_record_payload` and stored in the
shipped ``knowledge_record`` / ``record_revision`` tables. What it adds is the *rules* a requirement
record obeys, and every one of them is a rule the packet states as a refusal or a state:

* **The owner is never re-resolved here.** This module never reads a packet. The three components
  are stored as given, and the owner's resolution result is consumed as data
  (:mod:`agents_remember.memory.knowledge.requirement_owner` is the only place that asks the owner,
  and it asks the owner's own resolver rather than re-implementing it). A failed resolution is a
  *state*: the record stores the reference unrewritten and the owner's own refusal beside it.
* **No promotion operation exists.** There is no update path and no member that changes a stored
  revision. Re-recording a stored ``revision_id`` so that it would say ``accepted`` where it said
  ``proposed`` is refused with the shipped ``promotion_not_supported``, naming the owner that holds
  the acceptance; every other re-use of a stored revision identity is refused as a duplicate.
* **The governing route is authored once and never repointed.** ``None`` is the explicit ungoverned
  state, never a default; a named route must exist; and a later revision that declares a different
  route for the same record is refused rather than silently ignored, because a route association this
  record group rewrote in place would be a second, unrecorded authority over scope.
* **Lineage is checked in-transaction, by the shared rule.** :func:`…lineage.find_cycle` decides,
  over the stored predecessor edges plus this revision's own declared edge, so a self-predecessor and
  a stored cycle are the *same* refusal from the *same* rule rather than a second check beside it. A
  refusal rolls the whole transaction back, leaving no record, no revision and no edge behind.
* **A predecessor is a revision of this record.** A stored identity belonging to another record is
  not a predecessor of this one, and a dangling identity is not a predecessor at all.

The forbidden set is enforced by absence at both planes and this module adds to neither: the payload
model refuses an undeclared field, and neither table has a column a task-status, seat-ownership or
lifecycle-gate value could land in. No read of this module is rendered as task state -- a scope view
reports a recorded state with its provenance and its resolution, and never relabels a stored
obligation or its absence as an implementation, an approval or a satisfaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.memory.knowledge import lineage, routes
from agents_remember.memory.knowledge.record_envelope import validate_record_payload
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    SqliteFailureContext,
    missing_expected_row_refusal,
    refusal,
    scope_refusal,
)
from agents_remember.memory.knowledge.requirement_records import (
    REQUIREMENT_RECORD_INSERT,
    REQUIREMENT_RECORD_ROW,
    REQUIREMENT_RECORD_ROWS_OF_KIND,
    REQUIREMENT_REVISION_INSERT,
    REQUIREMENT_REVISION_ROWS,
    StoredRequirementRecord,
    StoredRequirementRevision,
    decode_requirement_record_row,
    decode_requirement_revision_row,
    requirement_record_row,
    requirement_revision_row,
)
from agents_remember.memory.knowledge.requirement_views import revision_scope
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.authorship import ACCEPTED_STATE
from agents_remember.models.knowledge.base import PROPOSED_STATE
from agents_remember.models.knowledge.requirement import (
    REQUIREMENT_REVISION_KIND,
    REQUIREMENT_REVISION_SCHEMA,
    CurrentnessBasis,
    RequirementOwnerRef,
    RequirementRecordedState,
    RequirementReferenceResolution,
    RequirementRevisionOperation,
    RequirementRevisionRequest,
    RequirementRevisionResult,
    RequirementRevisionScope,
)
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal

# The narrow operation vocabulary, so a receipt names one of exactly these two acts. Both are
# members of the shipped ``KnowledgeOperation`` vocabulary, which is what a refusal carries.
RECORD_OPERATION: RequirementRevisionOperation = "record_requirement_revision"
READ_OPERATION: RequirementRevisionOperation = "read_requirement_revisions"


def record_requirement_revision(
    store: OpenedKnowledgeStore, request: RequirementRevisionRequest
) -> RequirementRevisionResult:
    """Record one immutable revision of one requirement obligation, or return one typed refusal."""

    denied = scope_refusal(RECORD_OPERATION, store.repository_id, request.repository_id)
    if denied is None:
        denied = require_governing_route(store, RECORD_OPERATION, request.governing_route_id)
    if denied is not None:
        return _refused(request.repository_id, denied, RECORD_OPERATION)
    with store.exclusive_candidate_lock(RECORD_OPERATION) as lock_refusal:
        if lock_refusal is not None:
            return _refused(request.repository_id, lock_refusal, RECORD_OPERATION)
        return store.within_immediate(
            lambda: _write_revision(store, request),
            on_refusal=lambda refused_value: _refused(
                request.repository_id, refused_value, RECORD_OPERATION
            ),
            failure=SqliteFailureContext(
                operation=RECORD_OPERATION, table="record_revision", record_id=request.revision_id
            ),
        )


def _write_revision(
    store: OpenedKnowledgeStore, request: RequirementRevisionRequest
) -> RequirementRevisionResult:
    """Apply one requirement revision inside the caller's open transaction.

    Every refusal below is raised, not returned, so the transaction that carries the record and its
    revision is aborted whole -- a refused revision never leaves a record row behind.
    """

    stored_record = _stored_record(store, request.record_id)
    require_promotion_not_attempted(store, request)
    require_acyclic_lineage(store, request)
    require_predecessor(store, request)
    require_record_route_unchanged(stored_record, request)
    frozen = _admissible_payload(request)
    if stored_record is None:
        store.write(
            REQUIREMENT_RECORD_INSERT,
            (
                store.repository_id,
                *requirement_record_row(
                    request.record_id,
                    _authority_home(store),
                    request.governing_route_id,
                    request.provenance,
                ),
            ),
        )
    store.write(
        REQUIREMENT_REVISION_INSERT,
        (
            store.repository_id,
            *requirement_revision_row(
                request.revision_id,
                request.record_id,
                frozen,
                request.predecessor_revision_id,
                request.provenance,
            ),
        ),
    )
    return RequirementRevisionResult(
        state="recorded",
        operation=RECORD_OPERATION,
        repository_id=request.repository_id,
        scope=_scope_of(store, request.record_id),
    )


def _admissible_payload(request: RequirementRevisionRequest) -> Mapping[str, Any]:
    """Validate one payload through the envelope seam, which is the only admissibility decision."""

    validated = validate_record_payload(
        REQUIREMENT_REVISION_KIND,
        REQUIREMENT_REVISION_SCHEMA,
        request.payload.model_dump(mode="json"),
        operation=RECORD_OPERATION,
        record_id=request.revision_id,
    )
    if isinstance(validated, KnowledgeRefusal):  # pragma: no cover - the request already validated
        raise KnowledgeRefused(validated)
    return validated.model_dump(mode="json")


def read_requirement_revisions(
    store: OpenedKnowledgeStore,
    record_id: str,
    *,
    owner_recorded: RequirementRecordedState | None = None,
    basis: CurrentnessBasis = "not-declared",
) -> RequirementRevisionResult:
    """Serve every stored revision of one requirement obligation, as a derived scope.

    ``owner_recorded`` is the state the *owner* records, consumed by the caller from the owner. It is
    optional because a read must be answerable without it; when it is absent the scope reports that
    nothing was compared rather than claiming agreement.
    """

    record = _stored_record(store, record_id)
    if record is None:
        return _refused(
            store.repository_id,
            missing_expected_row_refusal(
                operation=READ_OPERATION, table="knowledge_record", record_id=record_id
            ),
            READ_OPERATION,
        )
    return RequirementRevisionResult(
        state="read",
        operation=READ_OPERATION,
        repository_id=store.repository_id,
        scope=revision_scope(record, _stored_revisions(store, record_id), owner_recorded, basis),
    )


def resolve_requirement_reference(
    store: OpenedKnowledgeStore, reference: Mapping[str, Any]
) -> RequirementReferenceResolution:
    """Resolve another record group's reference to a requirement obligation (requirement 2.6).

    The reference resolves to a ``RequirementRevision`` of **this** record group or it does not
    resolve: this reads the stored requirement records and the references their own payloads carry,
    so it never consults a second requirement table, never consults the task plane's storage, and
    never re-reads a packet as the operand. More than one record carrying one reference is reported
    as ambiguous rather than resolved to a chosen one, for the same reason a fork is reported as more
    than one head.
    """

    try:
        wanted = RequirementOwnerRef.model_validate(dict(reference))
    except ValueError as error:
        return RequirementReferenceResolution(
            reference=None,
            state="unresolved",
            record_ids=(),
            detail=(
                "the supplied reference is not a well-formed requirement owner reference "
                f"({type(error).__name__}), so it names no record of this record group"
            ),
        )
    holders = [
        row.record_id
        for row in _stored_records(store)
        if any(
            revision.payload.owner == wanted for revision in _stored_revisions(store, row.record_id)
        )
    ]
    if not holders:
        return RequirementReferenceResolution(
            reference=wanted,
            state="unresolved",
            record_ids=(),
            detail=(
                "no record of this record group carries this reference; the reference stays the "
                "explicit unresolved state in the record that holds it, and this record group "
                "invents no record to satisfy it"
            ),
        )
    state = "resolved" if len(holders) == 1 else "ambiguous"
    return RequirementReferenceResolution(
        reference=wanted,
        state=state,
        record_ids=tuple(sorted(holders)),
        detail=(
            "the reference resolves to one requirement record of this record group"
            if state == "resolved"
            else f"{len(holders)} records of this record group carry this reference, so it is "
            "reported as ambiguous rather than resolved to a chosen one"
        ),
    )


# ---------------------------------------------------------------------------
# The guards. Each returns or raises one refusal per distinct fact, so a caller branches on the code.


def require_promotion_not_attempted(
    store: OpenedKnowledgeStore, request: RequirementRevisionRequest
) -> None:
    """Refuse a request that would change what an already-stored revision says.

    A revision is immutable, so there is no update path and this is the only way a promotion could be
    asked for: re-recording a stored ``revision_id`` so that the record would read ``accepted`` where
    it read ``proposed``. That is refused with the shipped ``promotion_not_supported``, and the
    refusal names the owner that holds the acceptance -- this record group records one the owner
    made and never produces one. Any other re-use of a stored revision identity is a duplicate.

    A ``revision_id`` that is *not* stored is not this guard's business at all, including when it
    names itself as its own predecessor: that is a lineage cycle, and the shared rule refuses it
    with the code and wording the cycle deserves rather than this one's.
    """

    stored = {revision.revision_id for revision in _stored_revisions(store, request.record_id)}
    if request.revision_id not in stored:
        return
    if request.payload.state_at_origin == ACCEPTED_STATE:
        raise KnowledgeRefused(
            refusal(
                "promotion_not_supported",
                RECORD_OPERATION,
                "a proposed requirement revision is not promoted in place; the owner's own next "
                "version is recorded as a new revision",
                facts=RefusalFacts(
                    table="record_revision",
                    record_id=request.revision_id,
                    expected=PROPOSED_STATE,
                    observed=ACCEPTED_STATE,
                ),
                next_action=(
                    "Record the owner's acceptance where the owner records it, and record a "
                    "successor revision carrying the owner's own acceptance reference. This record "
                    "group grants, withholds and transfers no approval."
                ),
            )
        )
    raise KnowledgeRefused(
        refusal(
            "duplicate_identity",
            RECORD_OPERATION,
            "a stored requirement revision cannot be recorded again under the same identity",
            facts=RefusalFacts(
                table="record_revision", record_id=request.revision_id, expected=request.revision_id
            ),
            next_action=(
                "Record the change as a new revision under a new identity, naming the stored "
                "revision as its predecessor."
            ),
        )
    )


def require_acyclic_lineage(
    store: OpenedKnowledgeStore, request: RequirementRevisionRequest
) -> None:
    """Refuse a revision whose lineage would leave it on, or below, a cycle.

    The rule is the shared :func:`…lineage.find_cycle`, decided over the stored predecessor edges
    plus this revision's own declared edge, and it runs here -- inside the write's transaction --
    exactly as the shipped lineage tables run theirs. A refusal aborts the transaction, so a cycle
    leaves no partial lineage behind.
    """

    predecessor = request.predecessor_revision_id
    finding = lineage.find_cycle(
        candidate_id=request.revision_id,
        predecessors=() if predecessor is None else (predecessor,),
        edges=_lineage_edges(store, request.record_id),
    )
    if finding is None:
        return
    raise KnowledgeRefused(
        refusal(
            "lineage_cycle",
            RECORD_OPERATION,
            (
                "the revision would be on a lineage cycle"
                if finding.candidate_on_cycle
                else "the revision descends from a stored lineage cycle"
            ),
            facts=RefusalFacts(
                table="record_revision",
                record_id=request.revision_id,
                observed=", ".join(finding.members),
            ),
            next_action=(
                "Re-author the revision so its predecessor chain is acyclic; a stored cycle has to "
                "be resolved before anything may descend from it."
            ),
        )
    )


def require_predecessor(store: OpenedKnowledgeStore, request: RequirementRevisionRequest) -> None:
    """Refuse a predecessor that is not a stored revision of this same requirement record.

    A revision's predecessor is its own record's earlier revision. An identity stored under a
    different record is not a predecessor of this one, and refusing it is what keeps a successor
    chain from silently becoming a cross-record graph.
    """

    predecessor = request.predecessor_revision_id
    if predecessor is None or predecessor == request.revision_id:
        return
    holder = _record_holding_revision(store, predecessor)
    if holder == request.record_id:
        return
    raise KnowledgeRefused(
        refusal(
            "missing_expected_row" if holder is None else "invalid_reference",
            RECORD_OPERATION,
            (
                "the declared predecessor revision is not stored in this namespace"
                if holder is None
                else "the declared predecessor revision belongs to another requirement record"
            ),
            facts=RefusalFacts(
                table="record_revision",
                record_id=request.revision_id,
                expected=request.record_id,
                observed=predecessor,
            ),
            next_action=(
                "Name a stored revision of this same requirement record as the predecessor, or "
                "record the predecessor first."
            ),
        )
    )


def require_record_route_unchanged(
    stored_record: StoredRequirementRecord | None, request: RequirementRevisionRequest
) -> None:
    """Refuse a successor revision that declares a different governing route for its record.

    The route is authored with the record and is part of what the record *is*; a later revision that
    named a different one would be repointing a sealed association in place. It is refused rather
    than ignored, because ignoring it would report the record as governed by a route the caller did
    not name.
    """

    if stored_record is None or stored_record.governing_route_id == request.governing_route_id:
        return
    raise KnowledgeRefused(
        refusal(
            "immutable_revision",
            RECORD_OPERATION,
            "a requirement record's governing route is authored with the record and is never "
            "repointed by a later revision",
            facts=RefusalFacts(
                table="knowledge_record",
                record_id=request.record_id,
                expected=str(stored_record.governing_route_id),
                observed=str(request.governing_route_id),
            ),
            next_action=(
                "Record the revision against the route the record was authored with. A different "
                "scope is a different authored association, and it is never derived or inferred."
            ),
        )
    )


def require_governing_route(
    store: OpenedKnowledgeStore, operation: KnowledgeOperation, route_id: str | None
) -> KnowledgeRefusal | None:
    """Refuse a record whose declared governing route is not authored in this repository.

    ``None`` is the explicit ungoverned state and is never refused; a *named* route that does not
    exist is a dangling reference and is refused rather than stored.
    """

    if route_id is None or routes.route_exists(store.connection, store.repository_id, route_id):
        return None
    return missing_expected_row_refusal(operation=operation, table="route", record_id=route_id)


# ---------------------------------------------------------------------------
# Stored-row readers. Every one of them scopes to the requirement kind, so this record group never
# reads a row it does not own.


def _stored_record(store: OpenedKnowledgeStore, record_id: str) -> StoredRequirementRecord | None:
    for row in store.connection.execute(
        REQUIREMENT_RECORD_ROW, (store.repository_id, record_id, REQUIREMENT_REVISION_KIND)
    ):
        return decode_requirement_record_row(row)
    return None


def _stored_records(store: OpenedKnowledgeStore) -> tuple[StoredRequirementRecord, ...]:
    return tuple(
        decode_requirement_record_row(row)
        for row in store.connection.execute(
            REQUIREMENT_RECORD_ROWS_OF_KIND, (store.repository_id, REQUIREMENT_REVISION_KIND)
        )
    )


def _stored_revisions(
    store: OpenedKnowledgeStore, record_id: str
) -> tuple[StoredRequirementRevision, ...]:
    return tuple(
        decode_requirement_revision_row(row)
        for row in store.connection.execute(
            REQUIREMENT_REVISION_ROWS, (store.repository_id, record_id)
        )
    )


def _record_holding_revision(store: OpenedKnowledgeStore, revision_id: str) -> str | None:
    rows = tuple(
        store.connection.execute(
            "SELECT record_id FROM record_revision WHERE repository_id = ? AND revision_id = ?",
            (store.repository_id, revision_id),
        )
    )
    if not rows:
        return None
    return str(rows[0][0])


def _lineage_edges(store: OpenedKnowledgeStore, record_id: str) -> tuple[lineage.LineageEdge, ...]:
    """Return this record's stored predecessor edges, as the shared rule's edge set."""

    return tuple(
        (revision.revision_id, revision.predecessor_revision_id)
        for revision in _stored_revisions(store, record_id)
        if revision.predecessor_revision_id is not None
    )


def _authority_home(store: OpenedKnowledgeStore) -> str:
    """Return the authority home the bound namespace declares, as the shipped column means it."""

    repository = store.get_repository()
    if repository is None:  # pragma: no cover - an opened store always has its row
        raise KnowledgeStorageError(
            "the store holds no repository row, so no authority home describes it"
        )
    return repository.authority_home


def _scope_of(store: OpenedKnowledgeStore, record_id: str) -> RequirementRevisionScope:
    record = _stored_record(store, record_id)
    if record is None:  # pragma: no cover - the caller just wrote it in this transaction
        raise KnowledgeStorageError(f"requirement record {record_id} disappeared during its write")
    return revision_scope(record, _stored_revisions(store, record_id))


def _refused(
    repository_id: str, refusal_value: KnowledgeRefusal, operation: RequirementRevisionOperation
) -> RequirementRevisionResult:
    return RequirementRevisionResult(
        state="refused", operation=operation, repository_id=repository_id, refusal=refusal_value
    )
