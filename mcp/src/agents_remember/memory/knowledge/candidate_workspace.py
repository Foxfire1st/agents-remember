"""One candidate's local working state: creation, resumption, cloning and disposal authority.

A candidate is a **directory** holding one writable SQLite database, the immutable receipt that
binds it to its admission, and the database's own resource lock. That layout is fixed by
:mod:`agents_remember.models.knowledge.snapshot` so the admission that opens the candidate for
writes and the publication that reads it cannot disagree about which file is the working
database, and so no absolute path is ever stored as knowledge. An operation that owns the
candidate may add its own local record beside those two -- the curator ingest writes the
allocations it has made into ``curator-allocation-journal.json`` there -- because that is the same
kind of fact the receipt is: a local, operation-scoped record, never knowledge the repository holds.

Four properties are load-bearing:

* **Two-phase creation.** The database and the receipt are built in a private staging directory
  and verified there; only then is the complete directory exposed at the admitted destination.
  A half-created candidate therefore never appears at the path a later lease would open.
* **An existing destination is a resume attempt.** Creation refuses an occupied destination; the
  resume path reopens the same database, retains its journal/WAL state and verifies the receipt
  against the admission instead of overwriting the unpublished work it holds.
* **A clone comes from a closed snapshot.** Cloning copies a *closed* representation produced by
  the same freeze procedure publication uses, so a clone can never inherit a WAL-dependent main
  file from the database it started from.
* **Disposal is a verdict, not a deletion.** This module decides whether the authored work is
  provably retained or explicitly discarded; removing the directory belongs to the enclosure
  owner that already owns cleanup.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import apsw

from agents_remember.kernel.atomic_write import atomic_replace, fsync_file
from agents_remember.memory.knowledge.candidate_receipt import (
    build_receipt_for_candidate,
    read_candidate_receipt,
    receipt_binding_refusal,
    write_candidate_receipt,
)
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.logical import logical_digest
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    RefusalFacts,
    candidate_binding_changed_refusal,
    candidate_busy_refusal,
    publication_failed_refusal,
    refusal,
    selected_input_unavailable_refusal,
    snapshot_incomplete_refusal,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.result import KnowledgeOperation, KnowledgeRefusal
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    CandidateBaseline,
    CandidateDisposalResult,
    CandidateDisposition,
    CandidateReceipt,
    CandidateResult,
    candidate_database_path,
    candidate_receipt_path,
)

__all__ = [
    "authorize_candidate_disposal",
    "clone_candidate",
    "create_candidate",
    "open_candidate",
]


def create_candidate(destination: AdmittedCandidateDestination) -> CandidateResult:
    """Create one empty declared candidate for an admitted destination.

    An occupied destination is refused rather than replaced: the directory that is already there
    may hold unpublished authored work, and only an explicit separately authorized reconciliation
    may touch it.
    """

    return _two_phase_create(
        "create_candidate",
        destination,
        lambda stage: _build_empty_candidate(destination, stage),
    )


def clone_candidate(
    destination: AdmittedCandidateDestination, baseline: CandidateBaseline
) -> CandidateResult:
    """Create one candidate from an explicitly selected closed knowledge database.

    The clone is a database-level backup of the baseline, produced by the same freeze procedure
    publication uses and verified against the identity the caller admitted. No filesystem lock is
    taken on the baseline: SQLite's own read transaction is what makes the copy consistent, and a
    baseline that moved while it was frozen is caught by the identity comparison rather than
    serialized against -- a lock file beside a published snapshot would also land inside a
    directory whose contents are captured as memory.
    """

    if not baseline.database_path.is_file():
        return _refused(
            "clone_candidate",
            selected_input_unavailable_refusal(
                "clone_candidate",
                f"the selected baseline database does not exist: {baseline.database_path}",
                record_id=str(baseline.database_path),
            ),
        )
    return _two_phase_create(
        "clone_candidate",
        destination,
        lambda stage: _build_cloned_candidate(stage, baseline),
    )


def open_candidate(destination: AdmittedCandidateDestination) -> CandidateResult:
    """Reopen an existing candidate, retaining its journals, and verify its receipt.

    This is the restart and branch-switch path. Nothing is repaired and nothing is deleted: the
    last committed batch is read straight out of the database -- through whatever journal or WAL
    state SQLite needs to recover -- and the receipt is compared with the admission that selected
    it. A candidate whose receipt disagrees is refused with its bytes untouched.
    """

    return _read_candidate("open_candidate", destination)


def authorize_candidate_disposal(
    destination: AdmittedCandidateDestination, disposition: CandidateDisposition
) -> CandidateDisposalResult:
    """Decide whether one candidate's working state may be disposed of.

    The verdict is deliberately narrow. A ``discard`` disposition must name the identity the
    candidate holds now, so an authorization written for an earlier state cannot be replayed
    against newer unpublished work. A ``published`` disposition must point at a database that
    reopens to that same logical identity, because a stale published snapshot, a loose object or
    a successful read does not establish that no unique authored data remains.
    """

    opened = _read_candidate("dispose_candidate", destination)
    if opened.state == "refused" or opened.identity is None:
        return CandidateDisposalResult(
            state="refused", disposition_kind=disposition.kind, refusal=opened.refusal
        )
    observation = opened.identity
    if disposition.candidate != observation:
        return CandidateDisposalResult(
            state="refused",
            disposition_kind=disposition.kind,
            refusal=_disposal_identity_refusal(observation, disposition),
        )
    if disposition.kind == "published":
        retained = _published_identity_refusal(disposition.published_path, observation)
        if retained is not None:
            return CandidateDisposalResult(
                state="refused", disposition_kind="published", refusal=retained
            )
    return CandidateDisposalResult(
        state="disposable", disposition_kind=disposition.kind, observed=observation
    )


# -- the two-phase creation path -------------------------------------------------------


def _two_phase_create(
    operation: KnowledgeOperation,
    destination: AdmittedCandidateDestination,
    build: Callable[[Path], CandidateResult | None],
) -> CandidateResult:
    """Build a candidate privately, expose it, and report the identity that is now there.

    Every way the private stage can fail to be produced -- the directory, the schema, the baseline
    copy, the receipt, the flush, the expose -- is returned as a typed refusal *before* the
    destination exists, so ``created`` is only ever reported for a candidate that is present, bound
    and durable at its admitted path.
    """

    occupied = _occupied_refusal(operation, destination)
    if occupied is not None:
        return _refused(operation, occupied)
    try:
        stage = _stage_directory(destination)
    except OSError as error:
        return _refused(
            operation,
            snapshot_incomplete_refusal(
                operation,
                f"the private candidate stage could not be created: {error}",
                stage_ref=str(destination.directory),
            ),
        )
    exposed = False
    try:
        built = build(stage)
        if built is not None:
            return built
        denial = _expose_candidate(stage, destination)
        if denial is not None:
            return _refused(operation, denial)
        exposed = True
    finally:
        if not exposed:
            _discard_private_directory(stage)
    readback = _read_candidate(operation, destination)
    if readback.state == "refused":
        return readback
    return CandidateResult(state="created", identity=readback.identity, receipt=readback.receipt)


def _build_empty_candidate(
    destination: AdmittedCandidateDestination, stage: Path
) -> CandidateResult | None:
    """Create the declared schema and the repository row in the private stage."""

    try:
        store = open_knowledge_store(
            candidate_database_path(stage), destination.repository.repository_id
        )
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _refused(
            "create_candidate", _stage_failure_refusal("create_candidate", stage, error)
        )
    try:
        created = store.create_repository(destination.repository)
    finally:
        _close_without_discarding_peers(store)
    if created.state == "refused":
        return _refused("create_candidate", created.refusal)
    return None


def _build_cloned_candidate(stage: Path, baseline: CandidateBaseline) -> CandidateResult | None:
    """Freeze the selected baseline into the private stage's candidate database.

    The admission's namespace is enforced where the receipt is sealed: a baseline bound to a
    different namespace than the destination declares is refused there with
    ``candidate_binding_changed`` rather than copied and relabelled.
    """
    try:
        source = open_existing_knowledge_store(
            baseline.database_path, baseline.expected_identity.repository_id
        )
    except KnowledgeStorageError as error:
        return _refused(
            "clone_candidate",
            refusal(
                "unsupported_schema",
                "clone_candidate",
                f"the selected baseline is not a database of this schema: {error}",
                facts=RefusalFacts(record_id=str(baseline.database_path)),
                next_action=(
                    "Select a baseline written by this code generation, or migrate it through a "
                    "separately authorized operation. No clone is created from an unreadable "
                    "baseline."
                ),
            ),
        )
    try:
        if source.get_repository() is None:
            return _refused(
                "clone_candidate",
                candidate_binding_changed_refusal(
                    "clone_candidate",
                    "the selected baseline is not bound to the repository namespace the admitted "
                    "identity names",
                    expected=baseline.expected_identity.repository_id,
                    observed="<unbound>",
                ),
            )
        freeze_closed_snapshot(source, baseline.expected_identity, candidate_database_path(stage))
    except KnowledgeRefused as refused:
        return _refused("clone_candidate", refused.refusal)
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _refused("clone_candidate", _stage_failure_refusal("clone_candidate", stage, error))
    finally:
        _close_without_discarding_peers(source)
    return None


def _stage_failure_refusal(
    operation: KnowledgeOperation, stage: Path, error: BaseException
) -> KnowledgeRefusal:
    """One typed refusal for a private stage that could not be produced or flushed."""

    return snapshot_incomplete_refusal(
        operation,
        f"the staged candidate could not be completed: {error}",
        stage_ref=str(candidate_database_path(stage)),
    )


def _expose_candidate(
    stage: Path, destination: AdmittedCandidateDestination
) -> KnowledgeRefusal | None:
    """Verify the staged database, seal its receipt, and expose the complete directory.

    The stage is verified before anything becomes visible: the schema it declares, the namespace
    it is bound to and the identity it holds are read from the database itself, and the receipt is
    derived from those observed facts rather than from the admission alone.

    Sealing the receipt and flushing the database are one step and the expose is the next. A
    failure in the first returns ``snapshot_incomplete`` with nothing exposed, so a candidate whose
    durability step failed can never be read back as created; the expose itself fails as
    ``destination_occupied`` when something is there after all, and as ``publication_failed`` when
    the completed directory could not be installed for any other reason.
    """

    observed = _read_staged_candidate(stage, destination)
    if isinstance(observed, KnowledgeRefusal):
        return observed
    store, identity = observed
    try:
        receipt = build_receipt_for_candidate(destination, store.schema)
    finally:
        _close_without_discarding_peers(store)
    if identity.repository_id != receipt.repository_id:
        return candidate_binding_changed_refusal(
            "create_candidate",
            "the staged database is bound to a namespace other than the admitted one",
            expected=receipt.repository_id,
            observed=identity.repository_id,
        )
    receipt_path = candidate_receipt_path(stage)
    try:
        write_candidate_receipt(receipt_path, receipt)
        if read_candidate_receipt(receipt_path) != receipt:
            raise KnowledgeStorageError("the written candidate receipt did not read back unchanged")
        fsync_file(candidate_database_path(stage))
    except (KnowledgeStorageError, apsw.Error, OSError) as error:
        return _stage_failure_refusal("create_candidate", stage, error)
    try:
        atomic_replace(stage, destination.directory)
    except OSError as error:
        occupied = _occupied_refusal("create_candidate", destination)
        if occupied is not None:
            return occupied
        return publication_failed_refusal(
            "create_candidate",
            f"the completed candidate could not be exposed: {error}",
            destination_ref=str(destination.directory),
            observed="<the destination directory was not installed>",
        )
    return None


def _read_staged_candidate(
    stage: Path, destination: AdmittedCandidateDestination
) -> tuple[OpenedKnowledgeStore, SnapshotIdentity] | KnowledgeRefusal:
    """Open the staged database and return it with the identity it holds, or the refusal."""

    database = candidate_database_path(stage)
    if not database.is_file():
        return selected_input_unavailable_refusal(
            "create_candidate",
            f"the staged candidate database was not created: {database}",
            record_id=str(database),
        )
    try:
        store = open_existing_knowledge_store(database, destination.repository.repository_id)
    except KnowledgeStorageError as error:
        return refusal(
            "unsupported_schema",
            "create_candidate",
            f"the staged candidate is not a database of this schema: {error}",
            facts=RefusalFacts(record_id=str(database)),
            next_action=(
                "Rebuild the candidate with the code generation that declares the intended "
                "schema. The staged directory is removed by the operation that made it."
            ),
        )
    if store.get_repository() is None:
        _close_without_discarding_peers(store)
        return candidate_binding_changed_refusal(
            "create_candidate",
            "the staged candidate holds no repository namespace row",
            observed="<unbound>",
        )
    return store, store.snapshot_identity()


# -- the read and disposal paths -------------------------------------------------------


def _read_candidate(
    operation: KnowledgeOperation, destination: AdmittedCandidateDestination
) -> CandidateResult:
    """Verify one candidate directory and return the identity it currently holds."""

    inputs = _candidate_inputs(operation, destination)
    if isinstance(inputs, KnowledgeRefusal):
        return _refused(operation, inputs)
    store = _open_for_verification(operation, destination.database_path, destination)
    if isinstance(store, KnowledgeRefusal):
        return _refused(operation, store)
    try:
        denial = _receipt_denial(operation, inputs, store, destination)
        if denial is not None:
            return _refused(operation, denial)
        identity = store.snapshot_identity()
    except apsw.BusyError as error:
        return _refused(operation, candidate_busy_refusal(operation, str(error)))
    except (apsw.Error, OSError) as error:
        return _refused(
            operation,
            selected_input_unavailable_refusal(
                operation,
                f"the candidate database could not be read: {error}",
                record_id=str(destination.database_path),
            ),
        )
    finally:
        _close_without_discarding_peers(store)
    return CandidateResult(state="resumed", identity=identity, receipt=inputs)


def _candidate_inputs(
    operation: KnowledgeOperation, destination: AdmittedCandidateDestination
) -> CandidateReceipt | KnowledgeRefusal:
    """Read one candidate's two required inputs, refusing when either is unavailable."""

    database = destination.database_path
    if not database.is_file():
        return selected_input_unavailable_refusal(
            operation,
            f"the candidate working database does not exist: {database}",
            record_id=str(database),
        )
    receipt_path = destination.receipt_path
    if not receipt_path.is_file():
        return selected_input_unavailable_refusal(
            operation,
            f"the candidate has no receipt beside its working database: {receipt_path}",
            record_id=str(receipt_path),
        )
    try:
        return read_candidate_receipt(receipt_path)
    except KnowledgeStorageError as error:
        return selected_input_unavailable_refusal(
            operation, str(error), record_id=str(receipt_path)
        )


def _receipt_denial(
    operation: KnowledgeOperation,
    receipt: CandidateReceipt,
    store: OpenedKnowledgeStore,
    destination: AdmittedCandidateDestination,
) -> KnowledgeRefusal | None:
    """Return the refusal for a receipt that does not bind this database to this admission."""

    bound = store.get_repository()
    if bound is None:
        return candidate_binding_changed_refusal(
            operation,
            "the candidate database holds no repository namespace row",
            observed="<unbound>",
        )
    return receipt_binding_refusal(
        receipt, destination, bound_repository=bound, schema=store.schema
    )


def _open_for_verification(
    operation: KnowledgeOperation, database: Path, destination: AdmittedCandidateDestination
) -> OpenedKnowledgeStore | KnowledgeRefusal:
    """Open one candidate database for verification, mapping its failures to refusals."""

    try:
        return open_existing_knowledge_store(database, destination.repository.repository_id)
    except apsw.BusyError as error:
        return candidate_busy_refusal(operation, str(error))
    except KnowledgeStorageError as error:
        return refusal(
            "unsupported_schema",
            operation,
            str(error),
            facts=RefusalFacts(record_id=str(database)),
            next_action=(
                "Open the candidate with the code generation that wrote it, or admit a new "
                "candidate. The existing database is not migrated, repaired or replaced here."
            ),
        )
    except (apsw.Error, OSError) as error:
        return selected_input_unavailable_refusal(
            operation,
            f"the candidate database could not be read: {error}",
            record_id=str(database),
        )


def _disposal_identity_refusal(
    observed: SnapshotIdentity, disposition: CandidateDisposition
) -> KnowledgeRefusal:
    """Refuse a disposal whose named identity is not the one the candidate now holds."""

    return refusal(
        "stale_precondition",
        "dispose_candidate",
        "the disposal disposition names a candidate identity the working database does not hold",
        facts=RefusalFacts(
            expected=disposition.candidate.logical_digest, observed=observed.logical_digest
        ),
        next_action=(
            "Reauthorize the disposal against the identity the candidate holds now. Unpublished "
            "work written after the disposition was issued is not covered by it."
        ),
    )


def _published_identity_refusal(
    published_path: Path, observation: SnapshotIdentity
) -> KnowledgeRefusal | None:
    """Return the refusal for a publication that does not retain this exact dataset."""

    if not published_path.is_file():
        return selected_input_unavailable_refusal(
            "dispose_candidate",
            f"the named publication does not exist: {published_path}",
            record_id=str(published_path),
        )
    try:
        connection = open_read_only_database(published_path)
    except (apsw.Error, OSError) as error:
        return selected_input_unavailable_refusal(
            "dispose_candidate",
            f"the named publication could not be opened: {error}",
            record_id=str(published_path),
        )
    try:
        try:
            schema = inspect_schema(connection)
            digest = logical_digest(connection, schema.schema_name)
        except (KnowledgeStorageError, apsw.Error, OSError) as error:
            return refusal(
                "unsupported_schema",
                "dispose_candidate",
                f"the named publication is not a database of this schema: {error}",
                facts=RefusalFacts(record_id=str(published_path)),
                next_action=(
                    "Name a publication this code can read and verify. An unreadable file does "
                    "not establish that the candidate's authored work is retained."
                ),
            )
    finally:
        connection.close()
    if digest != observation.logical_digest:
        return refusal(
            "stale_precondition",
            "dispose_candidate",
            "the named publication holds a different logical dataset than the candidate now holds",
            facts=RefusalFacts(
                record_id=str(published_path),
                expected=observation.logical_digest,
                observed=digest,
            ),
            next_action=(
                "Publish the candidate's current dataset first, then dispose of it against that "
                "exact publication."
            ),
        )
    return None


# -- local layout helpers --------------------------------------------------------------


def _occupied_refusal(
    operation: KnowledgeOperation, destination: AdmittedCandidateDestination
) -> KnowledgeRefusal | None:
    """Return the refusal for an occupied destination, or ``None`` when it is free."""

    if not destination.directory.exists():
        return None
    return refusal(
        "destination_occupied",
        operation,
        "the destination already exists, so it is a resume attempt rather than a creation target",
        facts=RefusalFacts(record_id=str(destination.directory)),
        next_action=(
            "Reopen the existing candidate through the candidate open operation, or admit a new "
            "absent destination for a new candidate. The existing directory is left untouched."
        ),
    )


def _stage_directory(destination: AdmittedCandidateDestination) -> Path:
    """Return a fresh private sibling directory for one two-phase candidate creation.

    The directory is created here rather than by whichever builder runs first: both builders
    write into it (a schema, or a frozen baseline copy) through a path whose parent has to exist,
    and creating it in one place is what keeps "the stage is this operation's own temporary
    output" true for both.
    """

    parent = destination.directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / f".{destination.directory.name}.{os.getpid()}.{uuid4().hex}.candidate"
    stage.mkdir()
    return stage


def _discard_private_directory(stage: Path) -> None:
    """Remove only the private staging directory this operation created."""

    shutil.rmtree(stage, ignore_errors=True)


def _close_without_discarding_peers(store: OpenedKnowledgeStore) -> None:
    """Close the connection and leave SQLite's own recovery files exactly where they are.

    A candidate's durability contract is that no journal or WAL file is ever deleted to make the
    database look clean, so the lifecycle paths here close the connection and let SQLite decide
    what survives beside it. ``OpenedKnowledgeStore.close`` is the same thing today; this helper
    exists so a later change to that method cannot quietly reintroduce an unlink underneath the
    lifecycle without a test noticing.
    """

    store.connection.close()


def _refused(operation: KnowledgeOperation, denial: KnowledgeRefusal | None) -> CandidateResult:
    """Return a refused candidate result, refusing a missing refusal value as a defect."""

    if denial is None:
        raise KnowledgeStorageError(f"{operation} refused without a refusal value")
    return CandidateResult(state="refused", refusal=denial)
