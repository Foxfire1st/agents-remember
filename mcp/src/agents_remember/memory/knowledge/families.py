"""The family half of the graph: family identity, sealed revisions and their lineage.

A family is a stable subject whose revision carries an authored joint guarantee about a set of
invariant revisions. Two rules shape this module:

* **A family revision is immutable and its payload is sealed.** The guarantee, the origin state,
  the acceptance reference, the provenance and the sorted predecessor set are one aggregate, so a
  changed guarantee is a separately identified successor and a membership citing the original
  revision keeps resolving the original text.
* **Family lineage reuses the shared rule.** ``family_predecessor`` edges are checked by
  :mod:`agents_remember.memory.knowledge.lineage`, the same module the invariant lineage uses, so
  the acyclic rule, its two branches and its reach cannot drift between the two graphs.

Every mutation runs inside the candidate's one resource lock and one immediate transaction, which
the store owns. Reads select from the canonical tables directly and re-derive a stored revision's
payload seal on the way out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agents_remember.memory.knowledge import lineage, records
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    SqliteFailureContext,
    cross_family_predecessor_refusal,
    dangling_family_predecessor_refusal,
    duplicate_family_refusal,
    duplicate_family_revision_refusal,
    family_lineage_cycle_refusal,
    invalid_family_payload_refusal,
    scope_refusal,
    unknown_family_refusal,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.family import (
    FamilyIdentity,
    FamilyRevision,
    StoredFamilyRevision,
)
from agents_remember.models.knowledge.result import (
    CreateFamilyResult,
    CreateFamilyRevisionResult,
    FamilyRequest,
    FamilyRevisionRequest,
    KnowledgeRefusal,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore


_FAMILY_COLUMNS = "repository_id, family_id, display_label, label_provenance"

_FAMILY_REVISION_COLUMNS = (
    "repository_id, family_id, revision_id, display_version, joint_guarantee, state_at_origin, "
    "acceptance_ref, provenance, payload_digest"
)

_FAMILY_INSERT = (
    "INSERT INTO family (repository_id, family_id, display_label, label_provenance) "
    "VALUES (?, ?, ?, ?)"
)

_FAMILY_REVISION_INSERT = (
    "INSERT INTO family_revision "
    "(repository_id, family_id, revision_id, display_version, joint_guarantee, state_at_origin, "
    "acceptance_ref, provenance, payload_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_FAMILY_PREDECESSOR_INSERT = (
    "INSERT INTO family_predecessor "
    "(repository_id, family_id, child_revision_id, parent_revision_id) VALUES (?, ?, ?, ?)"
)


def create_family(store: OpenedKnowledgeStore, request: FamilyRequest) -> CreateFamilyResult:
    """Insert one family identity into the bound namespace."""

    denied = scope_refusal("create_family", store.repository_id, request.repository_id)
    if denied is not None:
        return _family_refusal(request, denied)
    with store.exclusive_candidate_lock("create_family") as lock_refusal:
        if lock_refusal is not None:
            return _family_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _insert_family(store, request),
            on_refusal=lambda refused: _family_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="create_family", table="family", record_id=request.family_id
            ),
        )


def _insert_family(store: OpenedKnowledgeStore, request: FamilyRequest) -> CreateFamilyResult:
    existing = get_family(store, request.family_id)
    if existing is not None and existing.display_label == request.display_label.strip():
        return _family_result(request, "no_change")
    insert_family(
        store,
        family_id=request.family_id,
        display_label=request.display_label,
        provenance=request.provenance,
    )
    return _family_result(request, "created")


def insert_family(
    store: OpenedKnowledgeStore,
    *,
    family_id: str,
    display_label: str,
    provenance: Authorship,
) -> None:
    """Insert one family identity inside the caller's open transaction.

    Raises :class:`KnowledgeRefused` when the identity is already stored, whatever it now holds: a
    reused identity inside one committed namespace is a contradiction the caller has to resolve by
    reading, not an update to apply.
    """

    existing = get_family(store, family_id)
    label = display_label.strip()
    if existing is not None:
        raise KnowledgeRefused(duplicate_family_refusal(family_id, existing.display_label, label))
    store.write(
        _FAMILY_INSERT, records.family_row(store.repository_id, family_id, label, provenance)
    )


def create_family_revision(
    store: OpenedKnowledgeStore, request: FamilyRevisionRequest
) -> CreateFamilyRevisionResult:
    """Insert one whole family revision aggregate atomically, or refuse without any row."""

    denied = scope_refusal("create_family_revision", store.repository_id, request.repository_id)
    if denied is not None:
        return _family_revision_refusal(request, denied)
    try:
        revision = records.sealed_family_revision_from_draft(
            request.repository_id, request.revision
        )
    except ValueError as error:
        return _family_revision_refusal(
            request, invalid_family_payload_refusal(request.revision.family_id, str(error))
        )
    with store.exclusive_candidate_lock("create_family_revision") as lock_refusal:
        if lock_refusal is not None:
            return _family_revision_refusal(request, lock_refusal)
        return store.within_immediate(
            lambda: _insert_family_revision(store, request, revision),
            on_refusal=lambda refused: _family_revision_refusal(request, refused),
            failure=SqliteFailureContext(
                operation="create_family_revision",
                table="family_revision",
                record_id=revision.revision_id,
            ),
        )


def _insert_family_revision(
    store: OpenedKnowledgeStore, request: FamilyRevisionRequest, revision: FamilyRevision
) -> CreateFamilyRevisionResult:
    existing = get_family_revision(store, revision.revision_id)
    if existing is not None and existing.revision.payload_digest == revision.payload_digest:
        return _family_revision_result(request, "no_change", payload_digest=revision.payload_digest)
    insert_family_revision(store, revision)
    return _family_revision_result(request, "created", payload_digest=revision.payload_digest)


def insert_family_revision(
    store: OpenedKnowledgeStore,
    revision: FamilyRevision,
    *,
    pending: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Insert one sealed family revision aggregate inside the caller's open transaction.

    The aggregate is the revision row plus its declared predecessor edges, and the shared lineage
    rule is applied over the post-insert family graph before any row is written.
    """

    if (
        get_family(store, revision.family_id) is None
        and (
            "family",
            revision.family_id,
        )
        not in pending
    ):
        raise KnowledgeRefused(unknown_family_refusal(revision.family_id))
    existing = get_family_revision(store, revision.revision_id)
    if existing is not None:
        raise KnowledgeRefused(
            duplicate_family_revision_refusal(
                revision.revision_id,
                existing.revision.payload_digest,
                revision.payload_digest,
            )
        )
    _require_same_family_predecessors(store, revision, pending=pending)
    _require_acyclic_family(store, revision)
    store.write(_FAMILY_REVISION_INSERT, records.family_revision_row(revision))
    for edge in records.family_predecessor_rows(revision):
        store.write(_FAMILY_PREDECESSOR_INSERT, edge)
    store.require_referential_integrity()


def _require_same_family_predecessors(
    store: OpenedKnowledgeStore,
    revision: FamilyRevision,
    *,
    pending: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Every declared predecessor must be a stored family revision, or one of the batch's.

    A predecessor a command in the same batch declares has no row yet, so it is admitted from the
    batch's own declaration; the batch validates the completed graph before it writes and re-proves
    both graphs after.
    """

    for parent_id in sorted(revision.predecessors):
        if ("family_revision", parent_id) in pending:
            continue
        parent_family_id = family_id_of_revision(store, parent_id)
        if parent_family_id is None:
            raise KnowledgeRefused(
                dangling_family_predecessor_refusal(parent_id, revision.family_id)
            )
        if parent_family_id != revision.family_id:
            raise KnowledgeRefused(
                cross_family_predecessor_refusal(parent_id, revision.family_id, parent_family_id)
            )


def _require_acyclic_family(store: OpenedKnowledgeStore, revision: FamilyRevision) -> None:
    """Apply the shared lineage rule over the post-insert family graph, before writing."""

    finding = lineage.find_cycle(
        candidate_id=revision.revision_id,
        predecessors=revision.predecessors,
        edges=lineage.family_edges(store.connection, revision.repository_id, revision.family_id),
    )
    if finding is not None:
        raise KnowledgeRefused(
            family_lineage_cycle_refusal(
                revision.revision_id,
                finding.members,
                candidate_on_cycle=finding.candidate_on_cycle,
            )
        )


def get_family(store: OpenedKnowledgeStore, family_id: str) -> FamilyIdentity | None:
    """Return one family identity, or ``None`` when it is not in this namespace."""

    row = fetch_one(
        store.connection,
        f"SELECT {_FAMILY_COLUMNS} FROM family WHERE repository_id = ? AND family_id = ?",
        (store.repository_id, family_id),
    )
    return None if row is None else records.decode_family_row(row)


def get_family_revision(
    store: OpenedKnowledgeStore, revision_id: str
) -> StoredFamilyRevision | None:
    """Return one family revision aggregate, verifying its stored seal before returning it."""

    row = fetch_one(
        store.connection,
        f"SELECT {_FAMILY_REVISION_COLUMNS} FROM family_revision "
        "WHERE repository_id = ? AND revision_id = ?",
        (store.repository_id, revision_id),
    )
    if row is None:
        return None
    return records.decode_family_revision_row(
        row, _family_predecessors(store, str(row[1]), revision_id)
    )


def family_id_of_revision(store: OpenedKnowledgeStore, revision_id: str) -> str | None:
    """Return the family a stored revision belongs to, without decoding the aggregate.

    The write path's endpoint check answers an ownership question -- does this revision exist, and
    is it in this family -- so it reads the column directly, exactly as the invariant predecessor
    check does. Decoding the whole aggregate here would make the lineage rule's reach depend on an
    ancestor row's seal, so a store whose ancestor edges were edited behind their seal would
    surface as a storage defect instead of the graph fact the rule reports. The payload seal is
    still verified wherever a revision aggregate is actually served.
    """

    row = fetch_one(
        store.connection,
        "SELECT family_id FROM family_revision WHERE repository_id = ? AND revision_id = ?",
        (store.repository_id, revision_id),
    )
    return None if row is None else str(row[0])


def list_family_revision_ids(store: OpenedKnowledgeStore, family_id: str) -> tuple[str, ...]:
    """Return every revision identity of one family, in stable order."""

    return tuple(
        str(row[0])
        for row in store.connection.execute(
            "SELECT revision_id FROM family_revision "
            "WHERE repository_id = ? AND family_id = ? ORDER BY revision_id",
            (store.repository_id, family_id),
        )
    )


def _family_predecessors(
    store: OpenedKnowledgeStore, family_id: str, revision_id: str
) -> tuple[str, ...]:
    return records.decode_predecessor_rows(
        list(
            store.connection.execute(
                "SELECT parent_revision_id FROM family_predecessor "
                "WHERE repository_id = ? AND family_id = ? AND child_revision_id = ?",
                (store.repository_id, family_id, revision_id),
            )
        )
    )


def _family_result(
    request: FamilyRequest,
    state: Literal["created", "no_change"],
) -> CreateFamilyResult:
    return CreateFamilyResult(
        state=state,
        repository_id=request.repository_id,
        family_id=request.family_id,
        stored=state == "created",
    )


def _family_refusal(request: FamilyRequest, refused: KnowledgeRefusal) -> CreateFamilyResult:
    return CreateFamilyResult(
        state="refused",
        repository_id=request.repository_id,
        family_id=request.family_id,
        stored=False,
        refusal=refused,
    )


def _family_revision_result(
    request: FamilyRevisionRequest,
    state: Literal["created", "no_change"],
    *,
    payload_digest: str | None = None,
) -> CreateFamilyRevisionResult:
    return CreateFamilyRevisionResult(
        state=state,
        repository_id=request.repository_id,
        family_id=request.revision.family_id,
        revision_id=request.revision.revision_id,
        payload_digest=payload_digest,
        stored=state == "created",
    )


def _family_revision_refusal(
    request: FamilyRevisionRequest, refused: KnowledgeRefusal
) -> CreateFamilyRevisionResult:
    return CreateFamilyRevisionResult(
        state="refused",
        repository_id=request.repository_id,
        family_id=request.revision.family_id,
        revision_id=request.revision.revision_id,
        stored=False,
        refusal=refused,
    )


__all__ = [
    "create_family",
    "create_family_revision",
    "family_id_of_revision",
    "get_family",
    "get_family_revision",
    "insert_family",
    "insert_family_revision",
    "list_family_revision_ids",
]
