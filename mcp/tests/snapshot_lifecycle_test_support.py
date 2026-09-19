"""The case harness for candidate lifecycle and snapshot publication.

Every case in the two snapshot modules needs the same four things, and this module owns them so
neither module re-derives them:

* **One admitted candidate built through the real seam.** The harness creates a candidate with
  the lifecycle operation itself -- receipt, schema and all -- rather than by writing a file, so
  a case measures the operation's own objects.
* **Writes through the real write boundary.** A record is authored with the candidate-change
  batch against the candidate's own database, so a published snapshot is compared with knowledge
  the store actually wrote.
* **The measurements a claim needs.** File digests, journal peers, table row counts and logical
  identities are read through separate read-only connections, so "the bytes did not move" and
  "the dataset is the same" are measured rather than asserted.
* **A real process crash.** The restart case runs the write, the abandonment and the exit in a
  child interpreter, because an in-process "crash" is a clean close with extra steps.

It is test support, not production code: it decides nothing, and the only writes it performs are
the ones a case explicitly asks for.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import apsw
from agents_remember.application.knowledge import (
    change_knowledge_candidate,
    resolve_candidate_context,
    write_authorship,
)
from agents_remember.application.knowledge_snapshot import (
    admitted_candidate_destination,
    candidate_write_destination,
    clone_knowledge_candidate,
    create_knowledge_candidate,
    knowledge_publication_state,
    open_knowledge_candidate,
    publish_knowledge_snapshot,
)
from agents_remember.memory.knowledge.connection import journal_mode, open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity, logical_digest
from agents_remember.memory.knowledge.schema import CANONICAL_TABLES
from agents_remember.memory.knowledge.schema_generations import generation_of_database
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    AddInvariant,
    AddInvariantRevision,
    CandidateResolution,
    ChangeBatch,
    MutationResult,
    SnapshotIdentity,
)
from agents_remember.models.knowledge.context import AdmittedKnowledgeDestination
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import InvariantRequest, RevisionDraft
from agents_remember.models.knowledge.snapshot import (
    AdmittedCandidateDestination,
    CandidateBaseline,
    CandidateReceipt,
    CandidateResult,
    PublicationState,
    PublishSnapshotRequest,
    SnapshotDestinationRequest,
    SnapshotPublicationResult,
)

DEFAULT_AUTHORITY_HOME = "agents-remember"
CODE_TREE_ID = "c" * 40
MEMORY_TREE_ID = "d" * 40

PUBLICATION_NAME = "knowledge-snapshot.sqlite"
BASELINE_NAME = "baseline.sqlite"

_CRASH_SCRIPT = """
import os
import sys
from pathlib import Path
from uuid import uuid4

import apsw

from agents_remember.application.knowledge import write_authorship
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.result import InvariantRequest

database = Path(sys.argv[1])
repository_id = sys.argv[2]
marker = sys.argv[3]
expected_path = Path(sys.argv[4])

store = open_existing_knowledge_store(database, repository_id)
store.connection.execute("PRAGMA journal_mode=WAL")
created = store.create_invariant(
    InvariantRequest(
        repository_id=repository_id,
        invariant_id=str(uuid4()),
        display_label=marker,
        provenance=write_authorship(actor_ref="agent:crash-case", authorization_ref="case"),
    )
)
if created.state != "created":
    raise SystemExit(f"the crash case could not commit its batch: {created.refusal!r}")
expected_path.write_text(store.snapshot_identity().logical_digest, encoding="utf-8")

# A second connection abandons an open write transaction: the process exits without committing
# and without closing either connection, which is what a killed runtime looks like on disk.
abandoned = apsw.Connection(str(database))
abandoned.execute("BEGIN IMMEDIATE")
abandoned.execute(
    "INSERT INTO invariant (repository_id, invariant_id, display_label, label_provenance) "
    "VALUES (?, ?, ?, ?)",
    (repository_id, str(uuid4()), marker + "-uncommitted", "{}"),
)
os._exit(1)
"""


@dataclass(frozen=True)
class SnapshotCase:
    """One case's world: its root, its admission, its provenance and its candidate name."""

    root: Path
    repository: RepositoryIdentity
    resolution: CandidateResolution
    authorship: Authorship
    name: str

    def directory(self) -> Path:
        """Return this case's candidate directory."""

        return self.root / self.name

    def destination_path(self) -> Path:
        """Return the publication destination this case publishes to."""

        return self.root / "memory" / PUBLICATION_NAME

    def baseline_path(self) -> Path:
        """Return the closed baseline snapshot this case clones from."""

        return self.root / "baseline" / BASELINE_NAME

    def candidate(self) -> AdmittedCandidateDestination:
        """Return the admitted candidate destination for this case."""

        return admitted_candidate_destination(self.directory(), self.repository, self.resolution)

    @property
    def database_path(self) -> Path:
        """Return this case's candidate database path."""

        return self.candidate().database_path

    @property
    def receipt_path(self) -> Path:
        """Return this case's candidate receipt path."""

        return self.candidate().receipt_path

    def write_destination(self) -> AdmittedKnowledgeDestination:
        """Return the write boundary addressed at this case's candidate database."""

        return candidate_write_destination(self.candidate(), self.authorship)


def build_case(
    root: Path,
    *,
    name: str = "candidate",
    memory_tree_id: str = MEMORY_TREE_ID,
    candidate_ref: str = "draft:snapshot-case",
) -> SnapshotCase:
    """Build one case world without creating its candidate."""

    return SnapshotCase(
        root=root,
        repository=RepositoryIdentity(
            repository_id=str(uuid4()), authority_home=DEFAULT_AUTHORITY_HOME
        ),
        resolution=CandidateResolution(
            lane="draft-candidate",
            code_tree_id=CODE_TREE_ID,
            memory_tree_id=memory_tree_id,
            snapshot_ref="candidate:snapshot-case",
            candidate_ref=candidate_ref,
        ),
        authorship=write_authorship(
            actor_ref="agent:snapshot-case",
            authorization_ref="260915-KS developer kickoff ruling",
            origin_refs=("requirement:KS-R04@v1",),
        ),
        name=name,
    )


def create(case: SnapshotCase) -> CandidateResult:
    """Create this case's candidate through the lifecycle operation."""

    return create_knowledge_candidate(case.candidate())


def clone(case: SnapshotCase, name: str, baseline: CandidateBaseline) -> SnapshotCase:
    """Create a second case world in one namespace, cloned from an explicit baseline."""

    source = derive_case(case, name)
    created = clone_knowledge_candidate(source.candidate(), baseline)
    if created.state != "created":
        raise AssertionError(f"the clone case could not create its candidate: {created!r}")
    return source


def derive_case(case: SnapshotCase, name: str) -> SnapshotCase:
    """Return a second candidate world in the same repository namespace and admission."""

    return SnapshotCase(
        root=case.root,
        repository=case.repository,
        resolution=case.resolution,
        authorship=case.authorship,
        name=name,
    )


def clone_from(
    case: SnapshotCase, database_path: Path, expected: SnapshotIdentity
) -> CandidateResult:
    """Clone one closed baseline into this case's candidate through the lifecycle operation."""

    return clone_knowledge_candidate(
        case.candidate(),
        CandidateBaseline(database_path=database_path, expected_identity=expected),
    )


def open_candidate(case: SnapshotCase) -> CandidateResult:
    """Reopen this case's candidate through the lifecycle operation."""

    return open_knowledge_candidate(case.candidate())


def candidate_receipt(case: SnapshotCase) -> CandidateReceipt:
    """Return the receipt the reopen path verified for this case's candidate."""

    opened = open_candidate(case)
    if opened.receipt is None:
        raise AssertionError(f"the candidate did not reopen: {opened.refusal!r}")
    return opened.receipt


def write_record(case: SnapshotCase, statement: str) -> tuple[str, MutationResult]:
    """Author one invariant and its first revision through the candidate-change batch."""

    destination = case.write_destination()
    context = resolve_candidate_context(destination, case.resolution)
    invariant_id, revision_id = str(uuid4()), str(uuid4())
    result = change_knowledge_candidate(
        destination,
        ChangeBatch(
            expected=context,
            commands=(
                AddInvariant(
                    invariant_id=invariant_id, display_label=f"obligation {statement[:24]}"
                ),
                AddInvariantRevision(
                    revision=RevisionDraft(
                        revision_id=revision_id,
                        invariant_id=invariant_id,
                        display_version="v1",
                        statement=statement,
                        applicability="Every admitted candidate write in this namespace.",
                        provenance=case.authorship,
                    )
                ),
            ),
        ),
    )
    if result.state != "changed":
        raise AssertionError(f"the case's authored batch was not applied: {result.refusal!r}")
    return invariant_id, result


def live_store(case: SnapshotCase) -> OpenedKnowledgeStore:
    """Open this case's candidate database; the caller closes the connection."""

    return open_existing_knowledge_store(
        case.candidate().database_path, case.repository.repository_id
    )


def write_label_on_live_store(store: OpenedKnowledgeStore, case: SnapshotCase, label: str) -> str:
    """Author one invariant identity on a connection the caller keeps open.

    The single-record operation leaves whatever transaction it ran committed on the caller's
    connection, so a case that holds that connection open leaves the committed frames in the
    journal instead of letting a closing connection checkpoint them away.
    """

    invariant_id = str(uuid4())
    created = store.create_invariant(
        InvariantRequest(
            repository_id=case.repository.repository_id,
            invariant_id=invariant_id,
            display_label=label,
            provenance=case.authorship,
        )
    )
    if created.state != "created":
        raise AssertionError(f"the live write was not applied: {created.refusal!r}")
    return invariant_id


def vacuum(database_path: Path) -> None:
    """Rewrite one database's physical layout without changing its logical records."""

    connection = apsw.Connection(str(database_path))
    try:
        connection.execute("VACUUM")
    finally:
        connection.close()


def add_raw_invariant(database_path: Path, repository_id: str, label: str) -> None:
    """Write one invariant row through a plain connection, to prove a file's journal mode."""

    connection = apsw.Connection(str(database_path))
    try:
        connection.execute(
            "INSERT INTO invariant (repository_id, invariant_id, display_label, label_provenance) "
            "VALUES (?, ?, ?, ?)",
            (repository_id, str(uuid4()), label, "{}"),
        )
    finally:
        connection.close()


def live_identity(case: SnapshotCase) -> SnapshotIdentity:
    """Return the logical identity this case's candidate holds right now."""

    store = live_store(case)
    try:
        return store.snapshot_identity()
    finally:
        store.connection.close()


def publish(
    case: SnapshotCase,
    *,
    expected_candidate: SnapshotIdentity | None = None,
    destination_path: Path | None = None,
    expected_destination: SnapshotIdentity | None = None,
    admitted_absent: bool = True,
) -> SnapshotPublicationResult:
    """Publish this case's candidate to one destination through the publication operation."""

    resolved = destination_path or case.destination_path()
    if expected_candidate is None:
        expected_candidate = live_identity(case)
    return publish_knowledge_snapshot(
        case.candidate(),
        PublishSnapshotRequest(
            expected_candidate=expected_candidate,
            destination=SnapshotDestinationRequest(
                destination_path=resolved,
                expected_destination=None if admitted_absent else expected_destination,
            ),
        ),
    )


def publication_state(case: SnapshotCase, published_path: Path | None = None) -> PublicationState:
    """Compare this case's live candidate with one closed snapshot."""

    store = live_store(case)
    try:
        return knowledge_publication_state(store, published_path or case.destination_path())
    finally:
        store.connection.close()


def read_identity(database_path: Path) -> SnapshotIdentity:
    """Return one database file's logical identity through a read-only connection."""

    return dataset_identity(database_path)


def read_schema_name(database_path: Path) -> str:
    """Return one database file's declared schema name."""

    return dataset_identity(database_path).schema_version


def file_digest(path: Path) -> str:
    """Return the sha256 of one file's exact bytes."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def journal_peer_names(database_path: Path) -> list[str]:
    """Return the journal/WAL peer file names beside one database."""

    return sorted(
        peer.name
        for suffix in ("-wal", "-shm", "-journal")
        if (peer := database_path.with_name(database_path.name + suffix)).exists()
    )


def row_counts(database_path: Path) -> dict[str, int]:
    """Return every canonical table's row count, read from a separate read-only connection."""

    connection = open_read_only_database(database_path)
    try:
        return {
            table: int(next(iter(connection.execute(f"SELECT count(*) FROM {table}")))[0])
            for table in CANONICAL_TABLES
        }
    finally:
        connection.close()


def read_journal_mode(database_path: Path) -> str:
    """Return the journal mode one database file carries in its header."""

    connection = open_read_only_database(database_path)
    try:
        return journal_mode(connection)
    finally:
        connection.close()


def logical_identity_of(database_path: Path) -> str:
    """Return one database file's logical digest."""

    connection = open_read_only_database(database_path)
    try:
        return logical_digest(connection, generation_of_database(connection))
    finally:
        connection.close()


def byte_copy(source: Path, target: Path) -> Path:
    """Copy one file's bytes only, as a reader with no journal knowledge would."""

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def statement_present(database_path: Path, statement: str) -> bool:
    """Whether one authored statement's text is inside one database file's bytes."""

    return statement.encode("utf-8") in database_path.read_bytes()


@dataclass(frozen=True)
class CrashOutcome:
    """What one crashed child left on disk, measured before anything reopens the database."""

    identity: SnapshotIdentity
    wal_bytes: int
    peer_names: list[str]
    stderr: str


def crash_and_abandon(case: SnapshotCase, marker: str) -> CrashOutcome:
    """Run one real write-then-crash child and report what it left behind.

    The child opens the candidate, switches it to the WAL journal, commits one authored batch,
    opens a second connection with an unfinished write transaction, and exits through ``os._exit``
    without closing either connection. What is on disk afterwards -- a WAL holding the committed
    batch and no committed trace of the abandoned transaction -- is what a killed runtime leaves.

    The on-disk facts are measured here, before any reopen: opening and closing the last
    connection checkpoints the WAL away, so a case that measured it afterwards would be measuring
    its own reader rather than the crash.
    """

    expected_path = case.root / f"{marker}-expected.txt"
    database = case.candidate().database_path
    source_root = Path(__file__).resolve().parents[2] / "mcp" / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _CRASH_SCRIPT,
            str(database),
            case.repository.repository_id,
            marker,
            str(expected_path),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if "Traceback" in completed.stderr or not expected_path.is_file():
        raise AssertionError(
            "the crash case's child did not commit its batch and abandon a transaction: "
            f"exit {completed.returncode}, stdout {completed.stdout!r}, stderr {completed.stderr!r}"
        )
    wal = database.with_name(database.name + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    peer_names = [
        peer.name
        for suffix in ("-wal", "-shm")
        if (peer := database.with_name(database.name + suffix)).exists()
    ]
    identity = live_identity(case)
    outcome = CrashOutcome(
        identity=identity,
        wal_bytes=wal_bytes,
        peer_names=peer_names,
        stderr=completed.stderr,
    )
    if outcome.identity.logical_digest != expected_path.read_text(encoding="utf-8"):
        raise AssertionError("the crash case's committed batch did not survive the restart")
    return outcome
