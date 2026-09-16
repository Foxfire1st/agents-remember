"""The composition seam between admitted authority and the concrete knowledge store.

The application layer is where a destination becomes authorized and where the required
provenance envelope is actually assigned. It holds no schema and no durable state: it admits,
delegates to :mod:`agents_remember.memory.knowledge`, and returns the typed result unchanged.

The direction matters. Storage ranks below application, and the worktree and memory-quality
owners rank below storage, so a lower owner receives :mod:`agents_remember.models.knowledge`
values -- never an import of this module or of the store. This is the only place the two are
wired together.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agents_remember.memory.knowledge import anchors, families, memberships, realizations
from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.family import FamilyDraft, FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft, RealizationClaimDraft
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    AnchorEndpoint,
    CreateFamilyMemberResult,
    CreateFamilyResult,
    CreateFamilyRevisionResult,
    CreateRealizationClaimResult,
    CreateRevisionResult,
    CreateSourceAnchorResult,
    FamilyMemberRequest,
    FamilyRequest,
    FamilyRevisionRequest,
    RealizationClaimRequest,
    RemoveFamilyMemberRequest,
    RemoveFamilyMemberResult,
    RemoveRealizationClaimRequest,
    RemoveRealizationClaimResult,
    RemoveSourceAnchorRequest,
    RemoveSourceAnchorResult,
    RepositoryCreationResult,
    RevisionDraft,
    RevisionRequest,
    SourceAnchorRequest,
)
from agents_remember.models.knowledge.source import SourceAnchorDraft

__all__ = [
    "admitted_anchor_removal",
    "admitted_anchor_request",
    "admitted_claim_removal",
    "admitted_claim_request",
    "admitted_family_request",
    "admitted_family_revision_request",
    "admitted_knowledge_destination",
    "admitted_member_removal",
    "admitted_member_request",
    "admitted_revision_request",
    "create_knowledge_anchor",
    "create_knowledge_family",
    "create_knowledge_family_member",
    "create_knowledge_family_revision",
    "create_knowledge_realization_claim",
    "create_knowledge_revision",
    "initialize_knowledge_namespace",
    "open_admitted_knowledge_store",
    "remove_knowledge_anchor",
    "remove_knowledge_family_member",
    "remove_knowledge_realization_claim",
    "write_authorship",
]


def write_authorship(
    *,
    actor_ref: str,
    authorization_ref: str,
    origin_refs: Sequence[str] = (),
    recorded_at: str | None = None,
) -> Authorship:
    """Build the provenance envelope for one admitted write.

    The operation identity and the recorded instant are assigned here, not accepted from a
    caller: a payload must not be able to claim it was written at a time, or under an
    operation, that never happened.
    """

    return Authorship(
        actor_ref=actor_ref,
        authorization_ref=authorization_ref,
        operation_id=uuid4(),
        recorded_at=recorded_at or datetime.now(UTC).isoformat(),
        origin_refs=tuple(origin_refs),
    )


def admitted_knowledge_destination(
    database_path: Path,
    repository: RepositoryIdentity,
    authorship: Authorship,
) -> AdmittedKnowledgeDestination:
    """Bind one resolved destination to its namespace and write provenance.

    This is the constructor the admitted authority path calls after its own checks. It confers
    no authority by itself: it exists so the store receives a typed handle rather than a bare
    path, which is what keeps a deserialized request from becoming authorized input.
    """

    return AdmittedKnowledgeDestination(
        database_path=Path(database_path),
        repository=repository,
        authorship=authorship,
    )


def admitted_revision_request(
    destination: AdmittedKnowledgeDestination, draft: RevisionDraft
) -> RevisionRequest:
    """Attach one authored draft to its admitted destination.

    The caller supplies what was authored; the repository identity and the provenance envelope
    come from the admitted destination. That split is the point: provenance is not a parameter,
    so a request cannot claim an actor or an authorization the admission did not establish.
    """

    return RevisionRequest(
        repository_id=destination.repository.repository_id,
        revision=draft.model_copy(update={"provenance": destination.authorship}),
    )


def initialize_knowledge_namespace(
    destination: AdmittedKnowledgeDestination,
) -> RepositoryCreationResult:
    """Initialize an empty candidate database for this admitted namespace.

    An existing destination is a resume attempt, never permission to initialize over it, so
    this refuses an occupied path outright. The resume path is
    :func:`create_knowledge_revision` against the same destination.
    """

    if destination.database_path.exists():
        return RepositoryCreationResult(
            state="refused",
            repository=destination.repository,
            refusal=refusal(
                "destination_occupied",
                "create_repository",
                "the destination already exists, so it is a resume attempt rather than an "
                "initialization target",
                facts=RefusalFacts(table="repository", record_id=str(destination.database_path)),
                next_action=(
                    "Open the existing candidate through create_knowledge_revision, or admit a "
                    "new absent destination for a new candidate."
                ),
            ),
        )
    store = open_knowledge_store(destination.database_path, destination.repository.repository_id)
    try:
        return store.create_repository(destination.repository)
    finally:
        store.close()


def open_admitted_knowledge_store(
    destination: AdmittedKnowledgeDestination,
) -> OpenedKnowledgeStore:
    """Open the admitted destination for reads, without creating or repairing it.

    The caller owns the returned store and must close it. Its bound namespace is the
    destination's, so every read and write through it is scoped to the admitted repository.
    """

    return open_existing_knowledge_store(
        destination.database_path, destination.repository.repository_id
    )


def create_knowledge_revision(
    destination: AdmittedKnowledgeDestination, request: RevisionRequest
) -> CreateRevisionResult:
    """Insert one revision aggregate into the admitted destination.

    The operation is insert-only and atomic. It refuses rather than repairs: a request
    addressed to another namespace, an identity reuse, a lineage violation and a schema this
    code does not recognize all return a typed refusal with the row count unchanged.
    """

    store = open_admitted_knowledge_store(destination)
    try:
        return store.create_revision(request)
    finally:
        store.close()


# -- the family, anchor and relation half --------------------------------------------------
#
# The graph operations are module functions over one opened store rather than methods on it, so
# these wrappers are how a caller reaches them without handling a namespace, a path or a
# provenance envelope itself. Each one opens the admitted destination, runs one atomic operation
# and closes the handle; nothing here decides authority, and nothing here writes a row directly.


def admitted_family_request(
    destination: AdmittedKnowledgeDestination, draft: FamilyDraft
) -> FamilyRequest:
    """Attach one authored family identity to its admitted destination."""

    return FamilyRequest(
        repository_id=destination.repository.repository_id,
        family_id=draft.family_id,
        display_label=draft.display_label,
        provenance=destination.authorship,
    )


def admitted_family_revision_request(
    destination: AdmittedKnowledgeDestination, draft: FamilyRevisionDraft
) -> FamilyRevisionRequest:
    """Attach one authored family revision aggregate to its admitted destination."""

    return FamilyRevisionRequest(
        repository_id=destination.repository.repository_id,
        revision=draft.model_copy(update={"provenance": destination.authorship}),
    )


def admitted_anchor_request(
    destination: AdmittedKnowledgeDestination, draft: SourceAnchorDraft
) -> SourceAnchorRequest:
    """Attach one authored source anchor to its admitted destination."""

    return SourceAnchorRequest(
        repository_id=destination.repository.repository_id,
        anchor=draft,
        provenance=destination.authorship,
    )


def admitted_member_request(
    destination: AdmittedKnowledgeDestination, draft: FamilyMemberDraft
) -> FamilyMemberRequest:
    """Attach one authored membership to its admitted destination."""

    return FamilyMemberRequest(
        repository_id=destination.repository.repository_id,
        member=draft.model_copy(update={"provenance": destination.authorship}),
    )


def admitted_claim_request(
    destination: AdmittedKnowledgeDestination,
    draft: RealizationClaimDraft,
    anchor: AnchorEndpoint,
) -> RealizationClaimRequest:
    """Attach one authored realization claim and its anchor endpoint to its destination."""

    return RealizationClaimRequest(
        repository_id=destination.repository.repository_id,
        claim=draft,
        anchor=anchor,
        provenance=destination.authorship,
    )


def admitted_anchor_removal(
    destination: AdmittedKnowledgeDestination, anchor_id: str
) -> RemoveSourceAnchorRequest:
    """Address one explicit anchor removal to its admitted destination."""

    return RemoveSourceAnchorRequest(
        repository_id=destination.repository.repository_id, anchor_id=anchor_id
    )


def admitted_member_removal(
    destination: AdmittedKnowledgeDestination, member_id: str, expected_row_digest: str
) -> RemoveFamilyMemberRequest:
    """Address one explicit membership removal, with its expected row digest, to its destination."""

    return RemoveFamilyMemberRequest(
        repository_id=destination.repository.repository_id,
        member_id=member_id,
        expected_row_digest=expected_row_digest,
    )


def admitted_claim_removal(
    destination: AdmittedKnowledgeDestination, claim_id: str, expected_row_digest: str
) -> RemoveRealizationClaimRequest:
    """Address one explicit claim removal, with its expected row digest, to its destination."""

    return RemoveRealizationClaimRequest(
        repository_id=destination.repository.repository_id,
        claim_id=claim_id,
        expected_row_digest=expected_row_digest,
    )


def create_knowledge_family(
    destination: AdmittedKnowledgeDestination, request: FamilyRequest
) -> CreateFamilyResult:
    """Insert one family identity into the admitted destination."""

    store = open_admitted_knowledge_store(destination)
    try:
        return families.create_family(store, request)
    finally:
        store.close()


def create_knowledge_family_revision(
    destination: AdmittedKnowledgeDestination, request: FamilyRevisionRequest
) -> CreateFamilyRevisionResult:
    """Insert one family revision aggregate into the admitted destination."""

    store = open_admitted_knowledge_store(destination)
    try:
        return families.create_family_revision(store, request)
    finally:
        store.close()


def create_knowledge_anchor(
    destination: AdmittedKnowledgeDestination, request: SourceAnchorRequest
) -> CreateSourceAnchorResult:
    """Insert one source anchor into the admitted destination, resolving nothing."""

    store = open_admitted_knowledge_store(destination)
    try:
        return anchors.create_source_anchor(store, request)
    finally:
        store.close()


def remove_knowledge_anchor(
    destination: AdmittedKnowledgeDestination, request: RemoveSourceAnchorRequest
) -> RemoveSourceAnchorResult:
    """Remove one unreferenced source anchor from the admitted destination."""

    store = open_admitted_knowledge_store(destination)
    try:
        return anchors.remove_source_anchor(store, request)
    finally:
        store.close()


def create_knowledge_family_member(
    destination: AdmittedKnowledgeDestination, request: FamilyMemberRequest
) -> CreateFamilyMemberResult:
    """Insert one membership into the admitted destination."""

    store = open_admitted_knowledge_store(destination)
    try:
        return memberships.create_family_member(store, request)
    finally:
        store.close()


def remove_knowledge_family_member(
    destination: AdmittedKnowledgeDestination, request: RemoveFamilyMemberRequest
) -> RemoveFamilyMemberResult:
    """Remove one membership from the admitted destination by its expected row digest."""

    store = open_admitted_knowledge_store(destination)
    try:
        return memberships.remove_family_member(store, request)
    finally:
        store.close()


def create_knowledge_realization_claim(
    destination: AdmittedKnowledgeDestination, request: RealizationClaimRequest
) -> CreateRealizationClaimResult:
    """Insert one realization claim, and any new anchor it carries, into the destination."""

    store = open_admitted_knowledge_store(destination)
    try:
        return realizations.create_realization_claim(store, request)
    finally:
        store.close()


def remove_knowledge_realization_claim(
    destination: AdmittedKnowledgeDestination, request: RemoveRealizationClaimRequest
) -> RemoveRealizationClaimResult:
    """Remove one realization claim from the admitted destination by its expected row digest."""

    store = open_admitted_knowledge_store(destination)
    try:
        return realizations.remove_realization_claim(store, request)
    finally:
        store.close()
