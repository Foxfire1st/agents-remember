"""Freeze one consistent, closed, self-contained copy of a candidate's pinned dataset.

A SQLite main file is not a database on its own: committed content may still live in a journal
or WAL beside it, so a file copied while the database is live can reopen to *older* records than
the ones it was copied from. Everything this module does exists to make one copy that cannot be
in that state, and to prove it rather than assume it.

The procedure and why each step is where it is:

1. **Pin the view.** A read transaction on the source pins the exact logical view that is
   verified. The identity check and the copy therefore describe one dataset, not two readings
   of a moving one.
2. **Copy through SQLite.** ``Connection.backup`` reads database state through the engine,
   including whatever is committed in the journal or WAL, so an uncommitted writer's rows are
   excluded and a committed-but-WAL-resident batch is included.
3. **Normalize on a fresh connection.** A backup destination **inherits the source's journal
   mode**: a WAL source leaves a staged file whose header still says ``wal`` even when the
   destination connection was set to ``delete`` *before* the copy. The mode is therefore
   established on a newly opened connection to the finished copy.
4. **Prove it by reopening read-only.** The staged file is reopened through a connection that
   cannot write it, and must report the delete journal mode, carry the declared schema, hold the
   pinned logical dataset, and have no journal/WAL peer beside it. Then the file's own data is
   flushed, because the directory fsync in the atomic replace records the new *name* and says
   nothing about the bytes it points at.

Nothing here deletes a journal to make a database look clean. The only files this module removes
are the peers of a private stage it abandoned, and never a peer of a destination.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager, suppress
from pathlib import Path

import apsw

from agents_remember.kernel.atomic_write import fsync_file
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    journal_mode,
    open_read_only_database,
)
from agents_remember.memory.knowledge.logical import logical_digest, snapshot_identity
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    candidate_binding_changed_refusal,
    refusal,
    snapshot_incomplete_refusal,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import PreparedKnowledgeSnapshot

# The only journal mode a published snapshot may carry. ``delete`` is SQLite's own rollback
# journal: it is created and removed around a write and never holds committed state once the
# writer is gone, so the published file is a complete database on its own. A ``wal`` header
# instead makes the file's completeness depend on a peer that a copy does not carry.
_CLOSED_JOURNAL_MODE = "delete"
_JOURNAL_PEER_SUFFIXES = ("-wal", "-shm", "-journal")


def freeze_closed_snapshot(
    store: OpenedKnowledgeStore, expected: SnapshotIdentity, stage_path: Path
) -> PreparedKnowledgeSnapshot:
    """Copy ``store``'s pinned dataset into a closed snapshot at ``stage_path``.

    Raises :class:`KnowledgeRefused` with ``stale_precondition`` when the candidate is no longer
    the identity the caller admitted, and ``snapshot_incomplete`` when the copy, its verification
    or its flush did not complete -- a filesystem that refuses the flush is that same failure, not
    an escaping error. Either way the stage is removed: it is this operation's own temporary
    output, and the destination it was destined for is untouched.
    """

    if expected.repository_id != store.repository_id:
        raise KnowledgeRefused(
            candidate_binding_changed_refusal(
                "publish_snapshot",
                "the store is bound to a repository namespace the admitted identity does not name",
                expected=expected.repository_id,
                observed=store.repository_id,
            )
        )
    stage = Path(stage_path)
    if stage.exists():
        raise KnowledgeStorageError(
            f"the snapshot stage {stage} already exists; a stage path is created, never reused"
        )
    try:
        _copy_pinned_view(store, expected, stage)
        _normalize_stage(stage)
        _verify_closed_stage(stage, expected)
        fsync_file(stage)
        return PreparedKnowledgeSnapshot(
            stage_path=stage,
            identity=expected,
            file_digest=_file_digest(stage),
        )
    except KnowledgeRefused:
        _discard_owned_stage(stage)
        raise
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        _discard_owned_stage(stage)
        raise KnowledgeRefused(
            snapshot_incomplete_refusal("publish_snapshot", str(error), stage_ref=str(stage))
        ) from error


def _copy_pinned_view(store: OpenedKnowledgeStore, expected: SnapshotIdentity, stage: Path) -> None:
    """Copy the read transaction's view of the source into ``stage``."""

    connection = store.connection
    with _pinned_read_view(connection):
        repository = store.get_repository()
        if repository is None:
            raise KnowledgeStorageError(
                "the candidate database holds no repository namespace row to freeze"
            )
        observed = snapshot_identity(connection, repository, store.schema.schema_name)
        if observed.logical_digest != expected.logical_digest:
            raise KnowledgeRefused(_stale_candidate_refusal(expected, observed))
        _run_backup(connection, stage)


def _run_backup(source: apsw.Connection, stage: Path) -> None:
    """Run SQLite's backup from the source into a fresh destination file."""

    destination = apsw.Connection(str(stage))
    try:
        backup = destination.backup("main", source, "main")
        try:
            backup.step(-1)
        finally:
            backup.close()
    finally:
        destination.close()


def _normalize_stage(stage: Path) -> None:
    """Establish the closed journal mode on a fresh connection to the finished copy.

    This is the step that cannot be replaced by setting the mode before the copy: the backup
    destination inherits the source's journal mode regardless, so the mode has to be set — and
    then re-read — after the copy on a connection of its own.
    """

    connection = apsw.Connection(str(stage))
    try:
        mode = str(next(iter(connection.execute("PRAGMA journal_mode=DELETE")))[0]).lower()
        if mode != _CLOSED_JOURNAL_MODE:
            raise KnowledgeStorageError(
                f"the staged copy did not accept the {_CLOSED_JOURNAL_MODE} journal mode; it "
                f"reported {mode!r}"
            )
    finally:
        connection.close()


def _verify_closed_stage(stage: Path, expected: SnapshotIdentity) -> None:
    """Reopen the stage read-only and prove it is a complete, independent database."""

    peers = journal_peers(stage)
    if peers:
        raise KnowledgeStorageError(
            "the staged copy still depends on journal state beside it: "
            f"{', '.join(name.name for name in peers)}"
        )
    connection = open_read_only_database(stage)
    try:
        mode = journal_mode(connection)
        if mode != _CLOSED_JOURNAL_MODE:
            raise KnowledgeStorageError(
                f"the staged copy reopens in journal mode {mode!r}, so its completeness would "
                f"depend on a peer file; {_CLOSED_JOURNAL_MODE!r} is required"
            )
        schema = inspect_schema(connection)
        observed = logical_digest(connection, schema.schema_name)
    finally:
        connection.close()
    if observed != expected.logical_digest:
        raise KnowledgeStorageError(
            "the staged copy does not hold the logical dataset that was pinned and verified: "
            f"expected {expected.logical_digest}, observed {observed}"
        )
    if schema.schema_name != expected.schema_version:
        raise KnowledgeStorageError(
            f"the staged copy declares schema {schema.schema_name!r}, expected "
            f"{expected.schema_version!r}"
        )


def journal_peers(database_path: Path) -> list[Path]:
    """Return the journal/WAL peers currently beside ``database_path``."""

    path = Path(database_path)
    return [
        path.with_name(path.name + suffix)
        for suffix in _JOURNAL_PEER_SUFFIXES
        if path.with_name(path.name + suffix).exists()
    ]


def discard_stage(stage_path: Path) -> None:
    """Remove one private stage and its peers, and nothing else."""

    _discard_owned_stage(Path(stage_path))


@contextmanager
def _pinned_read_view(connection: apsw.Connection):
    """Hold one read transaction so the verified identity and the copy describe one view."""

    connection.execute("BEGIN")
    try:
        yield
    finally:
        with suppress(apsw.Error):
            connection.execute("ROLLBACK")


def _stale_candidate_refusal(
    expected: SnapshotIdentity, observed: SnapshotIdentity
) -> KnowledgeRefusal:
    """The refusal for a candidate that moved between admission and acquisition."""

    return refusal(
        "stale_precondition",
        "publish_snapshot",
        "the candidate's logical dataset is not the identity the publication was admitted against",
        facts=RefusalFacts(expected=expected.logical_digest, observed=observed.logical_digest),
        next_action=(
            "Reresolve the candidate identity and publish that state explicitly. A fresher "
            "candidate is never published silently in place of the selected one."
        ),
    )


def _discard_owned_stage(stage: Path) -> None:
    """Remove one private stage file and its peers; never a destination's.

    A removal failure is suppressed rather than raised. This runs on the way out of a failed
    freeze, and the caller's outcome is the typed refusal that says *why* the freeze failed; a
    secondary filesystem error here would replace that refusal with an untyped one and lose the
    distinction between "the copy did not complete" and "the cleanup did not complete".
    """

    for suffix in ("", *_JOURNAL_PEER_SUFFIXES):
        with suppress(OSError):
            stage.with_name(stage.name + suffix).unlink(missing_ok=True)


def _file_digest(path: Path) -> str:
    """Return the sha256 of one file's exact bytes."""

    return hashlib.sha256(path.read_bytes()).hexdigest()
