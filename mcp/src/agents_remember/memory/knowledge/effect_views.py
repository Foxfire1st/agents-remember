"""The authored-effect record group's derived views: pure functions of the stored rows, and nothing else.

Every view this record group serves is *derived and regenerable from the rows a caller already read*,
disposable, and forbidden from becoming an authority. The strongest way to hold that is not to store a
view at all, and this module stores none: each builder below is a pure function of the frozen payloads
and envelope values it is handed, so

* deleting a view changes no claim, no question and no change set -- there is nothing to delete;
* rebuilding from the same rows reproduces the view byte for byte, because its only inputs are those
  rows;
* a view that *could* disagree with the records is not expressible, because there is no second place a
  value could live.

Four of this module's jobs are the places the requirement's *states* become readable, and each is a
state rather than a refusal:

* **Membership is computed.** Which effect claims, preservation claims and open questions belong to a
  change set is read from the members' own ``change_set_id``. Nothing is inferred from a payload's
  content, and an empty member list means exactly that no member declares this change set. This is the
  membership fact the requirement allows code to compute, and it is the only thing computed here.
* **An unresolved assessment reference is reported, never filled in.** The assessment record belongs
  to another leaf, so the reference is stored verbatim and reported verbatim: never a refused write,
  never a placeholder, and never presented as an assessment that happened.
* **An unresolved requirement-revision reference is reported, never resolved.** Requirement meaning has
  one canonical owner, and this record group holds the reference and no requirement authority. The
  reference is reported exactly as it was written, and no code path in this module parses it,
  canonicalises it, splits it on a separator, infers a requirement identity from it, or resolves it
  against prose.
* **An unresolved preservation subject is reported, never substituted.** A subject names one exact
  knowledge reference of a declared kind; whether anything is stored under that identity is a fact
  about the store, asked of the table that would hold it. A subject that resolves to nothing is
  reported unresolved rather than refused or re-pointed.

The views are also where the record group proves the forbidden set is absent at the *stored* plane:
every view is built from a validated payload model, so a stored row carrying a truth verdict, a
severity, a generated summary or a preservation flag could not have been decoded into one at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.effect_records import (
    CHANGE_SET_PREDECESSORS_OF,
    EFFECT_RECORDS_OF_KIND,
    EFFECT_REVISIONS_OF_RECORD,
    StoredEffectRecord,
    StoredEffectRevision,
    decode_effect_record_row,
    decode_effect_revision_row,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.base import KnowledgeState
from agents_remember.models.knowledge.change_set import (
    SEMANTIC_CHANGE_SET_KIND,
    AuthoredEffectScope,
    ChangeSetMembership,
    InvariantEffectClaimView,
    PreservationClaimView,
    SemanticChangeSetPayload,
    SemanticChangeSetView,
    UnresolvedQuestionView,
    UnresolvedReference,
)
from agents_remember.models.knowledge.effect import (
    INVARIANT_EFFECT_CLAIM_KIND,
    PRESERVATION_CLAIM_KIND,
    UNRESOLVED_QUESTION_KIND,
    InvariantEffectClaimPayload,
    PreservationClaimPayload,
    PreservationSubject,
    UnresolvedQuestionPayload,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# Where a stored reference can be found when it does not resolve. The alias mirrors the view model's
# own closed set, so the builder cannot report an unresolved fact under a field the model would refuse.
UnresolvedField = Literal[
    "assessment_refs",
    "requirement_revision_refs",
    "preservation_subject",
    "candidate_realization_claim_ids",
]

# Why an assessment reference is unresolved, and it is not "the identity is wrong": no assessment
# record exists in this substrate yet, so every stored assessment reference is unresolved by
# construction and the fact reported is that one, not a lookup failure.
ASSESSMENT_ABSENT_DETAIL = (
    "no assessment record kind exists in this substrate, so this stored reference resolves to "
    "nothing; the reference is reported verbatim and is not a refusal, a placeholder or a claim that "
    "an assessment happened"
)

# Why a requirement-revision reference is unresolved, stated as the boundary rather than as a lookup
# that failed.
REQUIREMENT_REFERENCE_DETAIL = (
    "the requirement-revision reference is stored verbatim under the opaque-reference clause and this "
    "record group holds no requirement authority: no code path parsed it, canonicalised it, split it "
    "on a separator, inferred a requirement identity from it, or resolved it against prose, so it is "
    "reported unresolved exactly as it was written"
)

# Why a preservation subject is unresolved: the named identity is simply not stored here.
SUBJECT_UNRESOLVED_DETAIL = (
    "no record of the declared kind is stored under this subject identity in this namespace, so the "
    "subject is reported unresolved; the reference is kept verbatim and is neither refused nor "
    "re-pointed"
)

_SUBJECT_EXISTS: dict[str, str] = {
    "invariant": "SELECT invariant_id FROM invariant WHERE repository_id = ? AND invariant_id = ?",
    "invariant_revision": (
        "SELECT revision_id FROM invariant_revision WHERE repository_id = ? AND revision_id = ?"
    ),
    "source_anchor": (
        "SELECT anchor_id FROM source_anchor WHERE repository_id = ? AND anchor_id = ?"
    ),
    "semantic_change_set": (
        "SELECT record_id FROM knowledge_record WHERE repository_id = ? AND record_id = ? "
        "AND kind = ?"
    ),
}

# The member kinds this module groups, in the order the scope reports them.
_MEMBER_KINDS: tuple[str, ...] = (
    INVARIANT_EFFECT_CLAIM_KIND,
    PRESERVATION_CLAIM_KIND,
    UNRESOLVED_QUESTION_KIND,
)


def effect_scope(store: OpenedKnowledgeStore) -> AuthoredEffectScope:
    """Build the whole derived scope for one namespace from its stored rows."""

    members = {kind: _member_pairs(store, kind) for kind in _MEMBER_KINDS}
    change_sets = _change_set_pairs(store)
    membership = _membership_by_change_set(members)
    precedence = _precedence_by_successor(store)
    claims = tuple(_claim_view(record, revision) for record, revision in members[_MEMBER_KINDS[0]])
    preservations = tuple(
        _preservation_view(store, record, revision)
        for record, revision in members[_MEMBER_KINDS[1]]
    )
    questions = tuple(
        _question_view(record, revision) for record, revision in members[_MEMBER_KINDS[2]]
    )
    views = tuple(
        _change_set_view(store, record, revision, membership, precedence)
        for record, revision in change_sets
    )
    return AuthoredEffectScope(
        repository_id=store.repository_id,
        change_sets=views,
        effect_claims=claims,
        preservation_claims=preservations,
        unresolved_questions=questions,
        # The scope-level list is the union of the per-record lists, in the order the scope's own
        # fields declare their groups, so a caller can reconcile the two without a second ordering
        # rule and no reference is reported at one plane and not the other.
        unresolved_references=(
            *[reference for view in views for reference in view.unresolved_references],
            *[reference for view in claims for reference in view.unresolved_references],
            *[reference for view in preservations for reference in view.unresolved_references],
        ),
        detail=(
            f"{len(views)} change set(s), {len(claims)} effect claim(s), {len(preservations)} "
            f"preservation claim(s) and {len(questions)} unresolved question(s), derived from the "
            "stored rows on this read"
        ),
    )


def _member_pairs(
    store: OpenedKnowledgeStore, kind: str
) -> tuple[tuple[StoredEffectRecord, StoredEffectRevision], ...]:
    """Return every stored record of one member kind paired with its first sealed revision."""

    return tuple(
        (record, revision)
        for record in _records_of_kind(store, kind)
        for revision in _revisions_of(store, kind, record.record_id)
    )


def _change_set_pairs(
    store: OpenedKnowledgeStore,
) -> tuple[tuple[StoredEffectRecord, StoredEffectRevision], ...]:
    """Return every stored change set paired with its first sealed revision."""

    return tuple(
        (record, revision)
        for record in _records_of_kind(store, SEMANTIC_CHANGE_SET_KIND)
        for revision in _revisions_of(store, SEMANTIC_CHANGE_SET_KIND, record.record_id)
    )


def _records_of_kind(store: OpenedKnowledgeStore, kind: str) -> tuple[StoredEffectRecord, ...]:
    """Return every stored envelope row of one authored-effect kind, in stored order."""

    return tuple(
        decode_effect_record_row(row)
        for row in store.connection.execute(EFFECT_RECORDS_OF_KIND, (store.repository_id, kind))
    )


def _revisions_of(
    store: OpenedKnowledgeStore, kind: str, record_id: str
) -> tuple[StoredEffectRevision, ...]:
    """Return every stored revision of one record, in stored order."""

    return tuple(
        decode_effect_revision_row(row, kind)
        for row in store.connection.execute(
            EFFECT_REVISIONS_OF_RECORD, (store.repository_id, record_id)
        )
    )


def _membership_by_change_set(
    members: dict[str, tuple[tuple[StoredEffectRecord, StoredEffectRevision], ...]],
) -> dict[str, ChangeSetMembership]:
    """Compute each change set's members from the members' own declarations.

    This is the membership *fact* and nothing else: the lists are read out of each member's stored
    ``change_set_id``, so no content is inspected and no meaning is inferred.
    """

    grouped: dict[str, dict[str, list[str]]] = {}
    for kind, key in (
        (INVARIANT_EFFECT_CLAIM_KIND, "effect_claim_ids"),
        (PRESERVATION_CLAIM_KIND, "preservation_claim_ids"),
        (UNRESOLVED_QUESTION_KIND, "unresolved_question_ids"),
    ):
        for record, revision in members[kind]:
            change_set_id = _change_set_id_of(revision)
            if change_set_id is None:  # pragma: no cover - every member payload declares one
                continue
            bucket = grouped.setdefault(change_set_id, {})
            bucket.setdefault(key, []).append(record.record_id)
    return {
        change_set_id: ChangeSetMembership(
            effect_claim_ids=tuple(sorted(bucket.get("effect_claim_ids", ()))),
            preservation_claim_ids=tuple(sorted(bucket.get("preservation_claim_ids", ()))),
            unresolved_question_ids=tuple(sorted(bucket.get("unresolved_question_ids", ()))),
        )
        for change_set_id, bucket in grouped.items()
    }


def _change_set_id_of(revision: StoredEffectRevision) -> str | None:
    """Return the change set one member revision declares, or ``None`` for a change set itself."""

    payload = revision.payload
    if isinstance(
        payload, InvariantEffectClaimPayload | PreservationClaimPayload | UnresolvedQuestionPayload
    ):
        return payload.change_set_id
    return None


def _precedence_by_successor(store: OpenedKnowledgeStore) -> dict[str, tuple[str, ...]]:
    """Return each change set's stored predecessor identities, keyed by successor."""

    grouped: dict[str, tuple[str, ...]] = {}
    for record in _records_of_kind(store, SEMANTIC_CHANGE_SET_KIND):
        rows = store.connection.execute(
            CHANGE_SET_PREDECESSORS_OF, (store.repository_id, record.record_id)
        )
        predecessors = tuple(str(row[0]) for row in rows)
        if predecessors:
            grouped[record.record_id] = predecessors
    return grouped


def _claim_view(
    record: StoredEffectRecord, revision: StoredEffectRevision
) -> InvariantEffectClaimView:
    """Build one effect claim's view, with every stored assessment reference reported."""

    payload = revision.payload
    if not isinstance(payload, InvariantEffectClaimPayload):  # pragma: no cover - registry-typed
        raise KnowledgeStorageError(
            f"record {record.record_id} carries the kind {record.kind!r} but a payload of another "
            "shape"
        )
    return InvariantEffectClaimView(
        record_id=record.record_id,
        revision_id=revision.revision_id,
        kind=record.kind,
        lifecycle=_lifecycle(record),
        governing_route_id=record.governing_route_id,
        provenance=revision.provenance,
        content_digest=revision.content_digest,
        change_set_id=payload.change_set_id,
        effect=payload.effect,
        inputs=payload.inputs,
        outputs=payload.outputs,
        rationale=payload.rationale,
        assessment_refs=payload.assessment_refs,
        unresolved_references=tuple(
            _unresolved(
                record,
                revision,
                field="assessment_refs",
                reference=reference,
                detail=ASSESSMENT_ABSENT_DETAIL,
            )
            for reference in payload.assessment_refs
        ),
    )


def _preservation_view(
    store: OpenedKnowledgeStore, record: StoredEffectRecord, revision: StoredEffectRevision
) -> PreservationClaimView:
    """Build one preservation claim's view, resolving its subject only as an existence fact."""

    payload = revision.payload
    if not isinstance(payload, PreservationClaimPayload):  # pragma: no cover - registry-typed
        raise KnowledgeStorageError(
            f"record {record.record_id} carries the kind {record.kind!r} but a payload of another "
            "shape"
        )
    unresolved = (
        ()
        if subject_resolves(store, payload.subject)
        else (
            _unresolved(
                record,
                revision,
                field="preservation_subject",
                reference=payload.subject.reference_id,
                detail=SUBJECT_UNRESOLVED_DETAIL,
            ),
        )
    )
    return PreservationClaimView(
        record_id=record.record_id,
        revision_id=revision.revision_id,
        kind=record.kind,
        lifecycle=_lifecycle(record),
        governing_route_id=record.governing_route_id,
        provenance=revision.provenance,
        content_digest=revision.content_digest,
        change_set_id=payload.change_set_id,
        subject=payload.subject,
        statement=payload.statement,
        unresolved_references=unresolved,
    )


def _question_view(
    record: StoredEffectRecord, revision: StoredEffectRevision
) -> UnresolvedQuestionView:
    """Build one open question's view. A question is reported open; nothing records an answer."""

    payload = revision.payload
    if not isinstance(payload, UnresolvedQuestionPayload):  # pragma: no cover - registry-typed
        raise KnowledgeStorageError(
            f"record {record.record_id} carries the kind {record.kind!r} but a payload of another "
            "shape"
        )
    return UnresolvedQuestionView(
        record_id=record.record_id,
        revision_id=revision.revision_id,
        kind=record.kind,
        lifecycle=_lifecycle(record),
        governing_route_id=record.governing_route_id,
        provenance=revision.provenance,
        content_digest=revision.content_digest,
        change_set_id=payload.change_set_id,
        statement=payload.statement,
    )


def _change_set_view(
    store: OpenedKnowledgeStore,
    record: StoredEffectRecord,
    revision: StoredEffectRevision,
    membership: dict[str, ChangeSetMembership],
    precedence: dict[str, tuple[str, ...]],
) -> SemanticChangeSetView:
    """Build one change set's view with all six declared parts readable."""

    payload = revision.payload
    if not isinstance(payload, SemanticChangeSetPayload):  # pragma: no cover - registry-typed
        raise KnowledgeStorageError(
            f"record {record.record_id} carries the kind {record.kind!r} but a payload of another "
            "shape"
        )
    unresolved = tuple(
        _unresolved(
            record,
            revision,
            field="requirement_revision_refs",
            reference=reference,
            detail=REQUIREMENT_REFERENCE_DETAIL,
        )
        for reference in payload.requirement_revision_refs
    )
    for claim_id in payload.candidate_realization_claim_ids:
        if realization_claim_resolves(store, claim_id):
            continue
        unresolved = (
            *unresolved,
            _unresolved(
                record,
                revision,
                field="candidate_realization_claim_ids",
                reference=claim_id,
                detail=(
                    "the named realization claim is not stored in this namespace; the reference is "
                    "reported verbatim and no second copy of the claim was authored to satisfy it"
                ),
            ),
        )
    return SemanticChangeSetView(
        record_id=record.record_id,
        revision_id=revision.revision_id,
        kind=record.kind,
        lifecycle=_lifecycle(record),
        governing_route_id=record.governing_route_id,
        provenance=revision.provenance,
        content_digest=revision.content_digest,
        baseline=payload.baseline,
        candidate=payload.candidate,
        requirement_revision_refs=payload.requirement_revision_refs,
        candidate_realization_claim_ids=payload.candidate_realization_claim_ids,
        members=membership.get(record.record_id, ChangeSetMembership()),
        predecessor_change_set_ids=precedence.get(record.record_id, ()),
        unresolved_references=unresolved,
    )


def _lifecycle(record: StoredEffectRecord) -> KnowledgeState:
    """Return the stored lifecycle state as the shipped vocabulary's own value.

    The column is ``knowledge_record.lifecycle``, written from the command's ``state_at_origin``, which
    the batch refuses to store as anything but ``proposed``. Returning the stored value rather than a
    constant keeps the read honest about what the row says rather than about what this leaf expects,
    and a value outside the shipped vocabulary is reported as a damaged store rather than coerced into
    one -- a third lifecycle state is exactly what requirement 2.5 forbids this record group to mint.
    """

    if record.lifecycle == "proposed":
        return "proposed"
    if record.lifecycle == "accepted":
        return "accepted"
    raise KnowledgeStorageError(
        f"record {record.record_id} stores lifecycle {record.lifecycle!r}, which is not a member of "
        "the shipped proposed|accepted vocabulary; treat the store as damaged"
    )


def _unresolved(
    record: StoredEffectRecord,
    revision: StoredEffectRevision,
    *,
    field: UnresolvedField,
    reference: str,
    detail: str,
) -> UnresolvedReference:
    """Build one unresolved-reference fact naming its holder and the stored text verbatim."""

    return UnresolvedReference(
        holder_record_id=record.record_id,
        holder_revision_id=revision.revision_id,
        field=field,
        reference=reference,
        detail=detail,
    )


def subject_resolves(store: OpenedKnowledgeStore, subject: PreservationSubject) -> bool:
    """Whether anything is stored under one preservation subject's declared identity and kind."""

    statement = _SUBJECT_EXISTS[subject.kind]
    parameters: tuple[str, ...] = (store.repository_id, subject.reference_id)
    if subject.kind == "semantic_change_set":
        parameters = (store.repository_id, subject.reference_id, SEMANTIC_CHANGE_SET_KIND)
    return fetch_one(store.connection, statement, parameters) is not None


def realization_claim_resolves(store: OpenedKnowledgeStore, claim_id: str) -> bool:
    """Whether one exact realization-claim identity is stored in this namespace."""

    statement = "SELECT claim_id FROM realization_claim WHERE repository_id = ? AND claim_id = ?"
    return fetch_one(store.connection, statement, (store.repository_id, claim_id)) is not None
