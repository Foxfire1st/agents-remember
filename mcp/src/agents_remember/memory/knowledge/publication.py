"""Install one closed snapshot at an admitted destination, atomically and verifiably.

The destination is a *file other readers open*: a memory tree is captured from it, a reader
reopens it, and Git may commit it. Two consequences shape this module.

* **Publication is replace-or-nothing.** The install is the repository's atomic replace of an
  already-written, already-fsynced stage, so a reader sees the previous snapshot or the new one
  and never a partial file. Any failure before the replace leaves the previous destination
  exactly as it was; the private stage is this operation's own temporary output and is removed.
* **A no-op does not rewrite bytes.** When the destination already holds the same logical dataset
  -- the same records, whatever its SQLite page layout happens to be -- the existing bytes are
  retained and ``no_change`` is returned. Page layout is not knowledge, and rewriting the file
  would dirty a tree for a reason no reader could observe in the data.

The destination is serialized by one lock, whose path is derived from the destination. It lives
beside the destination (hidden, under the same atomic-write convention as the temporary files
this package writes) because a lock keyed anywhere else would let two processes disagree about
which resource they are excluding.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import apsw

from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.atomic_write import atomic_replace
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.memory.knowledge.candidate_workspace import open_candidate
from agents_remember.memory.knowledge.closed_snapshot import (
    discard_stage,
    freeze_closed_snapshot,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    destination_stale_refusal,
    lock_capability_refusal,
    publication_durability_unconfirmed_refusal,
    publication_failed_refusal,
    refusal,
    snapshot_incomplete_refusal,
)
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    PreparedKnowledgeSnapshot,
    PublishSnapshotRequest,
    SnapshotDestinationRequest,
    SnapshotPublicationResult,
)

# The one file name a private stage is written under inside its own directory.
_STAGE_FILE_NAME = "snapshot.sqlite"


def publish_candidate_snapshot(
    destination: AdmittedCandidateDestination, request: PublishSnapshotRequest
) -> SnapshotPublicationResult:
    """Publish one candidate's frozen point to the admitted destination.

    The candidate is re-verified against its receipt and its identity before anything is copied,
    the copy is frozen under the candidate's single resource lock, that lock is released, and the
    install happens under the destination's own lock. Two locks, never nested: the candidate lock
    protects the working database, and the destination lock protects the file being replaced.

    Every way the frozen point can fail to be produced arrives here as a typed refusal, including
    a filesystem that refuses the stage directory, the copy or the flush: the caller branches on a
    code, and ``publication_failed`` is reserved for a failure of the install itself.
    """

    opened = open_candidate(destination)
    if opened.state == "refused" or opened.identity is None:
        return SnapshotPublicationResult(state="refused", refusal=opened.refusal)
    if opened.identity != request.expected_candidate:
        return SnapshotPublicationResult(
            state="refused",
            refusal=_stale_candidate_refusal(request.expected_candidate, opened.identity),
        )
    stage_directory: Path | None = None
    try:
        stage_directory = _private_stage_directory(request.destination.destination_path)
        prepared = _freeze_under_candidate_lock(
            destination, request.expected_candidate, stage_directory
        )
    except KnowledgeRefused as refused:
        _discard_stage_directory(stage_directory)
        return SnapshotPublicationResult(state="refused", refusal=refused.refusal)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        _discard_stage_directory(stage_directory)
        return SnapshotPublicationResult(
            state="refused",
            refusal=snapshot_incomplete_refusal(
                "publish_snapshot",
                f"the candidate could not be frozen into a stage: {error}",
                stage_ref=str(stage_directory or request.destination.destination_path),
            ),
        )
    try:
        return publish_prepared_snapshot(prepared, request.destination)
    finally:
        _discard_stage_directory(stage_directory)


def publish_prepared_snapshot(
    prepared: PreparedKnowledgeSnapshot, request: SnapshotDestinationRequest
) -> SnapshotPublicationResult:
    """Install one already-frozen closed snapshot, or report why it was not installed.

    This is the reusable half of the contract: a caller that produced a validated closed database
    -- a merged result, an import, a restored artifact -- reaches the destination through exactly
    this path, so every published file was replaced atomically against an expected identity.
    """

    if not prepared.stage_path.is_file():
        return SnapshotPublicationResult(
            state="refused",
            refusal=snapshot_incomplete_refusal(
                "publish_snapshot",
                "the closed snapshot stage is not present",
                stage_ref=str(prepared.stage_path),
            ),
        )
    try:
        current_digest = _file_digest(prepared.stage_path)
    except OSError as error:
        return SnapshotPublicationResult(
            state="refused",
            refusal=snapshot_incomplete_refusal(
                "publish_snapshot",
                f"the closed snapshot stage could not be read back: {error}",
                stage_ref=str(prepared.stage_path),
            ),
        )
    if current_digest != prepared.file_digest:
        return SnapshotPublicationResult(
            state="refused",
            refusal=snapshot_incomplete_refusal(
                "publish_snapshot",
                "the closed snapshot stage is not the file that was frozen and verified",
                stage_ref=str(prepared.stage_path),
            ),
        )
    try:
        with exclusive_file_lock(
            _publication_lock_resource(request.destination_path), "knowledge snapshot destination"
        ):
            return _install(prepared, request)
    except LockCapabilityError as error:
        return SnapshotPublicationResult(
            state="refused",
            refusal=lock_capability_refusal("publish_snapshot", str(error)),
        )
    except OSError as error:
        return SnapshotPublicationResult(
            state="refused",
            refusal=lock_capability_refusal(
                "publish_snapshot",
                f"the destination publication lock could not be taken: {error}",
            ),
        )


# -- the install -----------------------------------------------------------------------


def _install(
    prepared: PreparedKnowledgeSnapshot, request: SnapshotDestinationRequest
) -> SnapshotPublicationResult:
    """Replace the destination under its lock, or retain the bytes already there."""

    destination_path = request.destination_path
    observation = _observe_destination(destination_path)
    if observation.detail:
        return _stale_destination(request, observed=f"<unreadable: {observation.detail}>")
    if not _matches_expected(request.expected_destination, observation.identity):
        return _stale_destination(request, observed=_render_identity(observation.identity))
    installed_identity = observation.identity
    if (
        installed_identity is not None
        and installed_identity.logical_digest == prepared.identity.logical_digest
    ):
        discard_stage(prepared.stage_path)
        return SnapshotPublicationResult(
            state="no_change",
            identity=installed_identity,
            previous_identity=installed_identity,
            destination_ref=str(destination_path),
        )
    try:
        atomic_replace(prepared.stage_path, destination_path)
    except OSError as error:
        discard_stage(prepared.stage_path)
        return SnapshotPublicationResult(
            state="refused",
            refusal=publication_failed_refusal(
                "publish_snapshot",
                f"the atomic replacement failed: {error}",
                destination_ref=str(destination_path),
                observed=_render_identity(observation.identity),
            ),
        )
    return _readback(destination_path, prepared, observation.identity)


def _readback(
    destination_path: Path,
    prepared: PreparedKnowledgeSnapshot,
    previous_identity: SnapshotIdentity | None,
) -> SnapshotPublicationResult:
    """Reopen the installed file and report what is actually there."""

    try:
        installed = dataset_identity(destination_path)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return SnapshotPublicationResult(
            state="refused",
            refusal=publication_durability_unconfirmed_refusal(
                "publish_snapshot",
                str(error),
                destination_ref=str(destination_path),
                observed="<the replacement completed but the destination could not be re-read>",
            ),
        )
    if installed.logical_digest != prepared.identity.logical_digest:
        return SnapshotPublicationResult(
            state="refused",
            refusal=publication_failed_refusal(
                "publish_snapshot",
                "the installed file does not carry the identity that was frozen",
                destination_ref=str(destination_path),
                observed=installed.logical_digest,
            ),
        )
    return SnapshotPublicationResult(
        state="published",
        identity=installed,
        previous_identity=previous_identity,
        destination_ref=str(destination_path),
    )


@dataclass(frozen=True)
class _DestinationObservation:
    """What one destination holds: its identity, or why it could not be read."""

    identity: SnapshotIdentity | None
    detail: str = ""


def _observe_destination(destination_path: Path) -> _DestinationObservation:
    """Read the destination's current logical identity without writing it."""

    if not destination_path.is_file():
        return _DestinationObservation(identity=None)
    try:
        return _DestinationObservation(identity=dataset_identity(destination_path))
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _DestinationObservation(identity=None, detail=str(error))


def _matches_expected(expected: SnapshotIdentity | None, observed: SnapshotIdentity | None) -> bool:
    """Whether the destination is exactly what the publication was admitted against."""

    if expected is None:
        return observed is None
    return observed is not None and observed == expected


def _stale_destination(
    request: SnapshotDestinationRequest, *, observed: str
) -> SnapshotPublicationResult:
    """Return the destination-stale refusal with the identity that was actually there."""

    expected = request.expected_destination
    return SnapshotPublicationResult(
        state="refused",
        refusal=destination_stale_refusal(
            "publish_snapshot",
            destination_ref=str(request.destination_path),
            expected=None if expected is None else expected.logical_digest,
            observed=observed,
        ),
    )


def _stale_candidate_refusal(
    expected: SnapshotIdentity, observed: SnapshotIdentity
) -> KnowledgeRefusal:
    """The refusal for a candidate that moved between admission and publication."""

    return refusal(
        "stale_precondition",
        "publish_snapshot",
        "the candidate's logical dataset is not the identity the publication was admitted against",
        facts=RefusalFacts(
            record_id=observed.repository_id,
            expected=expected.logical_digest,
            observed=observed.logical_digest,
        ),
        next_action=(
            "Reresolve the candidate identity and publish that state explicitly. A fresher "
            "candidate is never published silently in place of the selected one."
        ),
    )


def _render_identity(identity: SnapshotIdentity | None) -> str:
    """Render one destination identity for a refusal's observed fact."""

    return "<absent>" if identity is None else identity.logical_digest


def _freeze_under_candidate_lock(
    destination: AdmittedCandidateDestination,
    expected: SnapshotIdentity,
    stage_directory: Path,
) -> PreparedKnowledgeSnapshot:
    """Freeze the candidate into a private stage while holding its single resource lock."""

    store = open_existing_knowledge_store(
        destination.database_path, destination.repository.repository_id
    )
    try:
        with store.exclusive_candidate_lock("publish_snapshot") as denied:
            if denied is not None:
                raise KnowledgeRefused(denied)
            return freeze_closed_snapshot(store, expected, stage_directory / _STAGE_FILE_NAME)
    finally:
        store.connection.close()


def _private_stage_directory(destination_path: Path) -> Path:
    """Return a fresh private directory beside the destination for one freeze."""

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    return Path(
        tempfile.mkdtemp(
            prefix=f".{destination_path.name}.", suffix=".stage", dir=destination_path.parent
        )
    )


def _discard_stage_directory(stage_directory: Path | None) -> None:
    """Remove one private stage directory, without replacing the caller's outcome with an error.

    This runs on the way out of both success and failure. A removal failure here would otherwise
    mask the refusal that says why the publication did not happen, which is the only result the
    caller can act on.
    """

    if stage_directory is not None:
        shutil.rmtree(stage_directory, ignore_errors=True)


def _publication_lock_resource(destination_path: Path) -> Path:
    """Return the resource whose lock file serializes publication to ``destination_path``.

    ``exclusive_file_lock`` derives ``<resource>.lock``, so handing it the destination's hidden
    stem yields ``.<name>.lock``: colocated with the resource it excludes, and hidden under the
    same convention as the temporary files this package writes into a destination directory.
    """

    return destination_path.with_name(f".{destination_path.name}")


def _file_digest(path: Path) -> str:
    """Return the sha256 of one file's exact bytes."""

    return hashlib.sha256(path.read_bytes()).hexdigest()
