"""The composition seam between admitted authority and the snapshot half of the knowledge store.

This is the second application module the storage design anticipated: the candidate lifecycle and
snapshot publication are a different composed operation from the single candidate write, and
keeping them apart leaves each entry point readable as one intent.

Nothing here decides authority, and nothing here holds durable state. It admits a candidate
directory and an explicit resolution into typed values, delegates to
:mod:`agents_remember.memory.knowledge`, and returns the typed result unchanged. Storage ranks
below application, so a lower owner -- the worktree or memory-quality package that captures the
published database -- receives :mod:`agents_remember.models.knowledge` values and never an import
of this module or of the store.

The writable candidate belongs to this experimental master; a landing on the IAS parent sprint is
a separate decision and is not reachable from anything here. The published snapshot is a closed
file, and this module creates no Git commit: capturing it into a memory tree is the existing
candidate-tree owner's operation.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.memory.knowledge.candidate_workspace import (
    authorize_candidate_disposal,
    clone_candidate,
    create_candidate,
    open_candidate,
)
from agents_remember.memory.knowledge.materialization import publication_state
from agents_remember.memory.knowledge.publication import (
    publish_candidate_snapshot,
    publish_prepared_snapshot,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import CandidateResolution
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    CandidateBaseline,
    CandidateDisposalResult,
    CandidateDisposition,
    CandidateResult,
    PreparedKnowledgeSnapshot,
    PublicationState,
    PublishSnapshotRequest,
    SnapshotDestinationRequest,
    SnapshotPublicationResult,
    candidate_database_path,
)

__all__ = [
    "admitted_candidate_destination",
    "authorize_knowledge_candidate_disposal",
    "candidate_write_destination",
    "clone_knowledge_candidate",
    "create_knowledge_candidate",
    "knowledge_publication_state",
    "open_knowledge_candidate",
    "publish_knowledge_snapshot",
    "publish_prepared_knowledge_snapshot",
]


def admitted_candidate_destination(
    directory: Path,
    repository: RepositoryIdentity,
    resolution: CandidateResolution,
) -> AdmittedCandidateDestination:
    """Bind one candidate directory to its namespace and its explicitly resolved inputs.

    This is the constructor the admitted authority path calls after its own checks. It confers no
    authority by itself: it exists so the lifecycle and publication operations receive a typed
    handle rather than a bare path, which is what keeps a deserialized request from becoming
    admitted input.
    """

    return AdmittedCandidateDestination(
        directory=Path(directory), repository=repository, resolution=resolution
    )


def candidate_write_destination(
    candidate: AdmittedCandidateDestination, authorship: Authorship
) -> AdmittedKnowledgeDestination:
    """Address the single-record and batch write operations at this candidate's database.

    The path is derived from the candidate layout rather than passed twice, so a caller cannot
    write into one database and publish another. ``authorship`` is the admitted provenance
    envelope for the writes; the receipt binds the candidate, not the writer.
    """

    return AdmittedKnowledgeDestination(
        database_path=candidate_database_path(candidate.directory),
        repository=candidate.repository,
        authorship=authorship,
    )


def create_knowledge_candidate(
    candidate: AdmittedCandidateDestination,
) -> CandidateResult:
    """Create one empty declared candidate at an admitted absent destination."""

    return create_candidate(candidate)


def clone_knowledge_candidate(
    candidate: AdmittedCandidateDestination, baseline: CandidateBaseline
) -> CandidateResult:
    """Create one candidate from an explicitly selected closed knowledge database."""

    return clone_candidate(candidate, baseline)


def open_knowledge_candidate(
    candidate: AdmittedCandidateDestination,
) -> CandidateResult:
    """Reopen an existing candidate, retaining its journals, and verify its receipt."""

    return open_candidate(candidate)


def authorize_knowledge_candidate_disposal(
    candidate: AdmittedCandidateDestination, disposition: CandidateDisposition
) -> CandidateDisposalResult:
    """Decide whether this candidate's working state may be disposed of."""

    return authorize_candidate_disposal(candidate, disposition)


def publish_knowledge_snapshot(
    candidate: AdmittedCandidateDestination, request: PublishSnapshotRequest
) -> SnapshotPublicationResult:
    """Publish one candidate's frozen point into the selected memory worktree."""

    return publish_candidate_snapshot(candidate, request)


def publish_prepared_knowledge_snapshot(
    prepared: PreparedKnowledgeSnapshot, request: SnapshotDestinationRequest
) -> SnapshotPublicationResult:
    """Install one already-frozen closed snapshot through the same publication contract."""

    return publish_prepared_snapshot(prepared, request)


def knowledge_publication_state(
    candidate: OpenedKnowledgeStore, published_path: Path
) -> PublicationState:
    """Compare one live candidate with the closed snapshot a read is about to answer from."""

    return publication_state(candidate, published_path)
