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

from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    CreateRevisionResult,
    RepositoryCreationResult,
    RevisionDraft,
    RevisionRequest,
)

__all__ = [
    "admitted_knowledge_destination",
    "admitted_revision_request",
    "create_knowledge_revision",
    "initialize_knowledge_namespace",
    "open_admitted_knowledge_store",
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
