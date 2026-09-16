"""The concrete APSW-backed knowledge store and its one atomic insert-only mutation.

The store owns a connection contract and a candidate mutation; it owns no transport, no
approval decision and no Git resolution. Two properties are load-bearing:

* **One resource lock, one transaction.** Every mutation holds the candidate database's
  exclusive file lock and runs inside one ``BEGIN IMMEDIATE`` transaction. A refusal rolls the
  transaction back, so an expected failure can never leave a partial aggregate behind.
* **Insert-only.** There is no update or delete path for a revision, no upsert, and no
  standalone predecessor-append operation. A successor is a new revision naming its exact
  predecessors, and the database refuses the alternative even if a future caller forgets.

The family, anchor, membership and realization half of the graph lives in the sibling modules
:mod:`families`, :mod:`anchors`, :mod:`memberships` and :mod:`realizations` -- one module per
authored concept, each owning its own reads -- and the acyclic lineage rule both lineage graphs
apply lives in :mod:`lineage`. Those modules take this store as their first argument instead of
adding two dozen more methods to one class: the connection, the resource lock and the transaction
boundary stay owned here, and a caller reaches the graph through the module functions. The lock and
transaction helpers below are this package's shared plumbing, not a private detail of the
invariant operations.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import apsw

from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.memory.knowledge import labels, lineage, logical, records
from agents_remember.memory.knowledge.connection import (
    _ImmediateTransaction,
    create_or_validate_schema,
    discard_closed_wal_peers,
    fetch_one,
    immediate_transaction,
    inspect_schema,
    open_database,
)
from agents_remember.memory.knowledge.lineage import LineageEdge
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    SqliteFailureContext,
    candidate_busy_refusal,
    cross_invariant_predecessor_refusal,
    dangling_predecessor_refusal,
    duplicate_invariant_refusal,
    duplicate_revision_refusal,
    invalid_payload_refusal,
    lineage_cycle_refusal,
    lock_capability_refusal,
    map_sqlite_error,
    repository_rebind_refusal,
    scope_refusal,
    unknown_invariant_refusal,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity
from agents_remember.models.knowledge.invariant import (
    InvariantIdentity,
    InvariantRevision,
    StoredInvariantRevision,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    CreateInvariantResult,
    CreateRevisionResult,
    InvariantRequest,
    KnowledgeOperation,
    KnowledgeRefusal,
    RepositoryCreationResult,
    RevisionRequest,
    SetInvariantLabelRequest,
    SetInvariantLabelResult,
)

ResultT = TypeVar("ResultT")

_REVISION_COLUMNS = (
    "repository_id, invariant_id, revision_id, display_version, statement, applicability, "
    "conditions, exclusions, state_at_origin, acceptance_ref, provenance, payload_digest"
)


@dataclass(frozen=True)
class OpenedKnowledgeStore:
    """One opened store: its bound namespace, its validated schema and its connection."""

    database_path: Path
    repository_id: str
    schema: KnowledgeSchemaIdentity
    connection: apsw.Connection
    resource_lock_path: Path

    def __enter__(self) -> OpenedKnowledgeStore:
        return self

    def __exit__(self, *exception: object) -> Literal[False]:
        """Close on every exit, including a failure raised inside the block."""

        del exception
        self.close()
        return False

    # -- lifecycle -----------------------------------------------------------------

    def close(self) -> None:
        """Close the connection and discard a WAL peer left by an uncheckpointed close.

        A durable candidate never deletes a journal to look clean while a connection is open;
        this runs only after the last connection to this file is closed, where the peer files
        are scratch that SQLite itself removes on a clean shutdown.
        """

        self.connection.close()
        discard_closed_wal_peers(self.database_path)

    # -- identity ------------------------------------------------------------------

    def get_repository(self) -> RepositoryIdentity | None:
        """Return the bound namespace, or ``None`` when the store has no repository row."""

        row = next(
            iter(
                self.connection.execute(
                    "SELECT repository_id, authority_home FROM repository WHERE repository_id = ?",
                    (self.repository_id,),
                )
            ),
            None,
        )
        return None if row is None else records.decode_repository_row(row)

    def get_invariant(self, invariant_id: str) -> InvariantIdentity | None:
        """Return one invariant identity, or ``None`` when it is not in this namespace."""

        row = next(
            iter(
                self.connection.execute(
                    "SELECT repository_id, invariant_id, display_label, label_provenance "
                    "FROM invariant WHERE repository_id = ? AND invariant_id = ?",
                    (self.repository_id, invariant_id),
                )
            ),
            None,
        )
        return None if row is None else records.decode_invariant_row(row)

    def list_revision_ids(self, invariant_id: str) -> tuple[str, ...]:
        """Return every revision identity of one invariant, in stable order."""

        return tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT revision_id FROM invariant_revision "
                "WHERE repository_id = ? AND invariant_id = ? ORDER BY revision_id",
                (self.repository_id, invariant_id),
            )
        )

    def get_revision(self, revision_id: str) -> StoredInvariantRevision | None:
        """Return one revision aggregate with its decoded predecessor set, or ``None``."""

        row = next(
            iter(
                self.connection.execute(
                    f"SELECT {_REVISION_COLUMNS} FROM invariant_revision "
                    "WHERE repository_id = ? AND revision_id = ?",
                    (self.repository_id, revision_id),
                )
            ),
            None,
        )
        if row is None:
            return None
        return records.decode_revision_row(row, self._predecessors_of(str(row[1]), revision_id))

    # -- mutation -------------------------------------------------------------------

    def create_repository(self, identity: RepositoryIdentity) -> RepositoryCreationResult:
        """Initialize an empty store for exactly one repository namespace.

        Rebinding a populated store to a different namespace is refused: the stored revisions
        are addressed within the namespace that authored them, so a rebind would re-scope every
        one of them at once.
        """

        with self._exclusive_candidate_lock("create_repository") as denied:
            if denied is not None:
                return RepositoryCreationResult(
                    state="refused", repository=identity, refusal=denied
                )
            return self._within_immediate(
                lambda: self._insert_repository(identity),
                on_refusal=lambda refused: RepositoryCreationResult(
                    state="refused", repository=identity, refusal=refused
                ),
                failure=SqliteFailureContext(
                    operation="create_repository",
                    table="repository",
                    record_id=identity.repository_id,
                ),
            )

    def create_invariant(self, request: InvariantRequest) -> CreateInvariantResult:
        """Insert one invariant identity into the bound namespace."""

        denied = scope_refusal("create_invariant", self.repository_id, request.repository_id)
        if denied is not None:
            return self._invariant_refusal(request, denied)
        with self.exclusive_candidate_lock("create_invariant") as lock_refusal:
            if lock_refusal is not None:
                return self._invariant_refusal(request, lock_refusal)
            return self.within_immediate(
                lambda: self._insert_invariant(request),
                on_refusal=lambda refused: self._invariant_refusal(request, refused),
                failure=SqliteFailureContext(
                    operation="create_invariant",
                    table="invariant",
                    record_id=request.invariant_id,
                ),
            )

    def create_revision(self, request: RevisionRequest) -> CreateRevisionResult:
        """Insert one whole revision aggregate atomically, or refuse without any row.

        The complete aggregate -- statement, applicability, conditions, exclusions, origin
        state, provenance and every declared predecessor -- becomes one immutable, separately
        addressable object under one transaction.
        """

        denied = scope_refusal(
            "create_invariant_revision", self.repository_id, request.repository_id
        )
        if denied is not None:
            return self._revision_result(request, "refused", refusal=denied)
        try:
            revision = records.sealed_revision_from_draft(request.repository_id, request.revision)
        except ValueError as error:
            return self._revision_result(
                request,
                "refused",
                refusal=invalid_payload_refusal(request.revision.invariant_id, str(error)),
            )
        with self.exclusive_candidate_lock("create_invariant_revision") as lock_refusal:
            if lock_refusal is not None:
                return self._revision_result(request, "refused", refusal=lock_refusal)
            return self.within_immediate(
                lambda: self._insert_revision(request, revision),
                on_refusal=lambda refused: self._revision_result(
                    request, "refused", refusal=refused
                ),
                failure=SqliteFailureContext(
                    operation="create_invariant_revision",
                    table="invariant_revision",
                    record_id=revision.revision_id,
                ),
            )

    def set_invariant_label(self, request: SetInvariantLabelRequest) -> SetInvariantLabelResult:
        """Change one invariant's display label, naming the row the caller read."""

        return labels.set_invariant_label(self, request)

    def insert_invariant_identity(
        self, *, invariant_id: str, display_label: str, provenance: Authorship
    ) -> None:
        """Insert one invariant identity inside the caller's open transaction.

        The batch calls this rather than :meth:`create_invariant` because the batch already owns
        the lock and the transaction: nesting a second ``BEGIN IMMEDIATE`` inside the first is a
        SQLite error, and re-taking the resource lock would break the one-lock rule. It is also why
        the batch path cannot confirm an identical repeat: a batch authors new identities, and a
        stored identity it believed it was creating means its read was stale.
        """

        insert_invariant(
            self,
            invariant_id=invariant_id,
            display_label=display_label,
            provenance=provenance,
        )

    def insert_revision_aggregate(
        self, revision: InvariantRevision, *, pending: Iterable[tuple[str, str]] = ()
    ) -> None:
        """Insert one whole sealed revision aggregate inside the caller's open transaction.

        ``pending`` names the identities the batch's commands declare. They are legitimately absent
        from the table -- the batch has not committed yet -- and the lineage passes are what prove
        the completed graph those identities take part in is acyclic.
        """

        insert_revision(self, revision, pending=frozenset(pending))

    def snapshot_identity(self) -> SnapshotIdentity:
        """Return this candidate's logical dataset identity, as stored right now.

        The caller resolves a context from this value and later compares the two inside one
        transaction, which is what makes "the dataset I read" a checked precondition rather than a
        remembered one.
        """

        repository = self.get_repository()
        if repository is None:
            raise KnowledgeStorageError(
                f"the candidate database is not bound to repository namespace {self.repository_id}"
            )
        return logical.snapshot_identity(self.connection, repository, self.schema.schema_name)

    # -- internals ------------------------------------------------------------------

    def _insert_repository(self, identity: RepositoryIdentity) -> RepositoryCreationResult:
        existing = self.get_repository()
        if existing is None:
            self.write(
                "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
                records.repository_row(identity),
            )
            return RepositoryCreationResult(state="created", repository=identity)
        if existing == identity:
            return RepositoryCreationResult(state="no_change", repository=existing)
        raise KnowledgeRefused(
            repository_rebind_refusal(
                identity.repository_id, existing.authority_home, identity.authority_home
            )
        )

    def _insert_invariant(self, request: InvariantRequest) -> CreateInvariantResult:
        wrote = insert_invariant(
            self,
            invariant_id=request.invariant_id,
            display_label=request.display_label,
            provenance=request.provenance,
            confirm_repeat=True,
        )
        return self._invariant_result(request, "created" if wrote else "no_change")

    def _insert_revision(
        self, request: RevisionRequest, revision: InvariantRevision
    ) -> CreateRevisionResult:
        existing = self.get_revision(revision.revision_id)
        if existing is not None:
            if existing.revision.payload_digest == revision.payload_digest:
                return self._revision_result(
                    request, "no_change", payload_digest=revision.payload_digest
                )
            raise KnowledgeRefused(
                duplicate_revision_refusal(
                    revision.revision_id,
                    existing.revision.payload_digest,
                    revision.payload_digest,
                )
            )
        insert_revision(self, revision)
        return self._revision_result(request, "created", payload_digest=revision.payload_digest)

    def _invariant_result(
        self, request: InvariantRequest, state: Literal["created", "no_change"]
    ) -> CreateInvariantResult:
        return CreateInvariantResult(
            state=state,
            repository_id=request.repository_id,
            invariant_id=request.invariant_id,
            stored=state == "created",
        )

    def lineage_cycle_members(self, revision: InvariantRevision) -> tuple[str, ...]:
        """Return the edges on a cycle through ``revision``, as their child endpoints.

        This is the membership query, not the write-path rule. Two facts about its authority:

        * It reads the *declared* edge set -- the stored edges plus the revision's own -- so it can
          see a cycle the surrounding transaction has not created yet.
        * Only edges *on* a cycle through the revision are members. A revision that descends from a
          stored cycle without being on it is refused by the write path (see
          :func:`find_lineage_cycle`) but has no members here, because no cycle passes
          through it.
        """

        return lineage.edges_on_cycle(
            candidate_id=revision.revision_id,
            predecessors=revision.predecessors,
            edges=lineage.invariant_edges(
                self.connection, revision.repository_id, revision.invariant_id
            ),
        )

    def require_referential_integrity(self) -> None:
        violations = list(self.connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise KnowledgeStorageError(
                f"deferred foreign-key violations survived the transaction: {violations!r}"
            )

    def _predecessors_of(self, invariant_id: str, revision_id: str) -> tuple[str, ...]:
        return records.decode_predecessor_rows(
            list(
                self.connection.execute(
                    "SELECT parent_revision_id FROM invariant_predecessor "
                    "WHERE repository_id = ? AND invariant_id = ? AND child_revision_id = ?",
                    (self.repository_id, invariant_id, revision_id),
                )
            )
        )

    def _invariant_refusal(
        self, request: InvariantRequest, denied: KnowledgeRefusal
    ) -> CreateInvariantResult:
        return CreateInvariantResult(
            state="refused",
            repository_id=request.repository_id,
            invariant_id=request.invariant_id,
            stored=False,
            refusal=denied,
        )

    def _revision_result(
        self,
        request: RevisionRequest,
        state: Literal["created", "no_change", "refused"],
        *,
        payload_digest: str | None = None,
        refusal: KnowledgeRefusal | None = None,
    ) -> CreateRevisionResult:
        return CreateRevisionResult(
            state=state,
            repository_id=request.repository_id,
            revision_id=request.revision.revision_id,
            invariant_id=request.revision.invariant_id,
            payload_digest=payload_digest,
            stored=state == "created",
            refusal=refusal,
        )

    def within_immediate(
        self,
        action: Callable[[], ResultT],
        *,
        on_refusal: Callable[[KnowledgeRefusal], ResultT],
        failure: SqliteFailureContext,
    ) -> ResultT:
        """Run one action inside a single immediate transaction over this store.

        A refusal rolls the whole transaction back, which is what makes a late lineage or
        referential failure leave no row behind. A storage defect is not converted: it
        propagates, because a defect the caller could "handle" as a refusal would be reported
        as an expected outcome. ``failure`` says which operation and table a *surviving* SQLite
        failure belongs to, so the mapped refusal names the right row.
        """

        try:
            with immediate_transaction(self.connection):
                return action()
        except KnowledgeRefused as refused:
            return on_refusal(refused.refusal)
        except apsw.Error as error:
            return on_refusal(map_sqlite_error(error, failure))

    def immediate_transaction(self) -> _ImmediateTransaction:
        """Open this store's one immediate transaction: the write lock precedes the first read.

        The candidate-change batch spans several commands, so it takes the transaction directly
        instead of through :meth:`within_immediate`, which owns a transaction per action.
        """

        return immediate_transaction(self.connection)

    def write(self, statement: str, parameters: Sequence[Any]) -> None:
        """Run one parameterized write inside the caller's open immediate transaction."""

        cursor = self.connection.cursor()
        try:
            cursor.execute(statement, tuple(parameters))
        finally:
            cursor.close()

    @contextmanager
    def exclusive_candidate_lock(
        self, operation: KnowledgeOperation
    ) -> Iterator[KnowledgeRefusal | None]:
        """Hold the candidate's resource lock, or yield the refusal that replaced it.

        Exactly one lock is held at a time. The lock is what makes the whole read-check-write
        sequence of one operation exclusive of every other writer on this candidate, which is why
        an operation acquires it once, outside its transaction, and never acquires a second one.
        """

        try:
            with exclusive_file_lock(self.resource_lock_path, "knowledge candidate database"):
                yield None
        except LockCapabilityError as error:
            yield lock_capability_refusal(operation, str(error))
        except apsw.BusyError as error:
            yield candidate_busy_refusal(operation, str(error))

    # The per-concept modules and the candidate-change batch call the public names above. These
    # aliases keep the original spelling working for the calls written before the names were
    # shared, so no module has to be rewritten merely to rename a helper it already uses.
    _within_immediate = within_immediate
    _write = write
    _exclusive_candidate_lock = exclusive_candidate_lock


def insert_invariant(
    store: OpenedKnowledgeStore,
    *,
    invariant_id: str,
    display_label: str,
    provenance: Authorship,
    confirm_repeat: bool = False,
) -> bool:
    """Insert one invariant identity inside the caller's open transaction.

    Returns whether a row was written. Two callers want two different answers for the same stored
    row, and the difference is the point:

    * the single-record operation (``confirm_repeat=True``) treats an identical repeat as a
      confirmation -- ``no_change`` -- and refuses only a repeat that carries a different label,
      because re-stating what is already there is not an attempt to change it;
    * a batch command (``confirm_repeat=False``) is an insertion of a newly authored identity, so
      any stored row under that identity refuses: the caller's read is stale, and the alternative
      would be the operation deciding two aggregates are "the same" on the caller's behalf.

    The row digest, not the label text, is what a later edit names.
    """

    existing = store.get_invariant(invariant_id)
    label = display_label.strip()
    if existing is not None:
        if confirm_repeat and existing.display_label == label:
            return False
        raise KnowledgeRefused(
            duplicate_invariant_refusal(invariant_id, existing.display_label, label)
        )
    store.write(
        "INSERT INTO invariant "
        "(repository_id, invariant_id, display_label, label_provenance) VALUES (?, ?, ?, ?)",
        records.invariant_row(store.repository_id, invariant_id, label, provenance),
    )
    return True


def insert_revision(
    store: OpenedKnowledgeStore,
    revision: InvariantRevision,
    *,
    pending: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Insert one whole sealed revision aggregate inside the caller's open transaction.

    The aggregate is the revision row plus every declared predecessor edge, so a successor is
    authored with its exact ancestors and there is no separate append operation to forget. The
    lineage rule is stated once in :func:`find_lineage_cycle`:

        the candidate is refused with ``lineage_cycle`` when inserting it would leave ANY revision
        in this invariant's lineage graph on a cycle -- equivalently, when the candidate itself
        would be on a cycle, or when a retained revision reachable from the candidate through
        predecessors is already on one.

    It is evaluated over the post-insert graph (the stored edges plus the candidate's own) and
    before any row is written, so a refusal leaves the tables exactly as they were. A batch judges
    the wider graph -- the batch's own declarations included -- through
    :func:`agents_remember.memory.knowledge.lineage.declared_cycle` before it writes anything; this
    call is the single-record rule. Raw cyclic state can only arise outside the operation --
    admission requires every declared predecessor to exist already and refuses a self-referencing
    payload -- which is why the refusal is demonstrated against a graph written by hand.
    """

    if not _identity_available(store, pending, "invariant", revision.invariant_id):
        raise KnowledgeRefused(unknown_invariant_refusal(revision.invariant_id))
    existing = store.get_revision(revision.revision_id)
    if existing is not None:
        raise KnowledgeRefused(
            duplicate_revision_refusal(
                revision.revision_id,
                existing.revision.payload_digest,
                revision.payload_digest,
            )
        )
    require_same_invariant_predecessors(store, revision, pending=pending)
    require_acyclic_lineage(store, revision)
    store.write(
        "INSERT INTO invariant_revision "
        "(repository_id, invariant_id, revision_id, display_version, statement, applicability, "
        "conditions, exclusions, state_at_origin, acceptance_ref, provenance, payload_digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        records.revision_row(revision),
    )
    for edge in records.predecessor_rows(revision):
        store.write(
            "INSERT INTO invariant_predecessor "
            "(repository_id, invariant_id, child_revision_id, parent_revision_id) "
            "VALUES (?, ?, ?, ?)",
            edge,
        )
    # A predecessor this batch has not written yet is a deferred-foreign-key violation that the
    # batch's own final integrity pass resolves. Checking here would refuse a batch whose command
    # order is perfectly legal simply because another command's row is not visible yet.
    if not pending:
        store.require_referential_integrity()


def require_same_invariant_predecessors(
    store: OpenedKnowledgeStore,
    revision: InvariantRevision,
    *,
    pending: frozenset[tuple[str, str]] = frozenset(),
) -> None:
    """Every predecessor must be a stored revision of the *same* invariant, or one of the batch's.

    A predecessor a command in the same batch declares is not in the table yet, so its ownership is
    read from the aggregate the batch declared rather than from a row that does not exist. The
    position of that command in the batch does not matter: validation is over the completed graph.
    """

    for parent_id in sorted(revision.predecessors):
        if ("invariant_revision", parent_id) in pending:
            continue
        row = fetch_one(
            store.connection,
            "SELECT invariant_id FROM invariant_revision "
            "WHERE repository_id = ? AND revision_id = ?",
            (revision.repository_id, parent_id),
        )
        if row is None:
            raise KnowledgeRefused(dangling_predecessor_refusal(parent_id, revision.invariant_id))
        if str(row[0]) != revision.invariant_id:
            raise KnowledgeRefused(
                cross_invariant_predecessor_refusal(parent_id, revision.invariant_id, str(row[0]))
            )


def find_lineage_cycle(
    store: OpenedKnowledgeStore,
    revision: InvariantRevision,
    *,
    extra_predecessors: Iterable[LineageEdge] = (),
) -> lineage.CycleFinding | None:
    """Return the cycle this revision would leave in the post-insert invariant lineage graph."""

    return lineage.find_cycle(
        candidate_id=revision.revision_id,
        predecessors=revision.predecessors,
        edges=lineage.invariant_edges(
            store.connection, revision.repository_id, revision.invariant_id
        ),
        extra_predecessors=extra_predecessors,
    )


def _identity_available(
    store: OpenedKnowledgeStore,
    pending: frozenset[tuple[str, str]],
    table: str,
    record_id: str,
) -> bool:
    """Whether an identity exists now, or is declared by a command in the same batch."""

    if (table, record_id) in pending:
        return True
    if table == "invariant":
        return store.get_invariant(record_id) is not None
    return False


def require_acyclic_lineage(
    store: OpenedKnowledgeStore,
    revision: InvariantRevision,
    *,
    extra_predecessors: Iterable[LineageEdge] = (),
) -> None:
    """Refuse when the post-insert lineage graph leaves the candidate on, or above, a cycle.

    The rule itself, its two branches and the reason it is evaluated over the post-insert graph
    live in :mod:`agents_remember.memory.knowledge.lineage`, which the family lineage applies
    unchanged. Two branches reach the refusal and the message names the one that applied: the
    candidate is itself on a cycle, or a retained revision reachable from the candidate through
    predecessors is on one. The second branch is the wider reading of the rule and is why a
    revision whose own lineage is acyclic can still be refused.

    ``extra_predecessors`` carries the declared edges of *other* revisions authored in the same
    batch, so a caller judging a completed batch can hand this function the whole graph. The batch
    path does exactly that through :func:`agents_remember.memory.knowledge.lineage.declared_cycle`;
    this function on its own judges one revision against the stored edges and whatever the caller
    declares, and it is called with the default by the single-record insert.
    """

    finding = find_lineage_cycle(store, revision, extra_predecessors=extra_predecessors)
    if finding is not None:
        raise KnowledgeRefused(
            lineage_cycle_refusal(
                revision.revision_id,
                finding.members,
                candidate_on_cycle=finding.candidate_on_cycle,
            )
        )


def open_knowledge_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Create or reopen one candidate knowledge database for a bound namespace.

    On an empty path the declared schema is created. On an existing path the schema is
    validated against this generation instead of being silently accepted: a database with a
    missing table, a renamed column or a dropped trigger refuses rather than being used for
    writes whose constraints are not the ones this code assumes.
    """

    path = Path(database_path)
    connection = open_database(path)
    return OpenedKnowledgeStore(
        database_path=path,
        repository_id=repository_id,
        schema=create_or_validate_schema(connection),
        connection=connection,
        resource_lock_path=path,
    )


def open_existing_knowledge_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Reopen an existing candidate database without creating or repairing it."""

    path = Path(database_path)
    if not path.exists():
        raise KnowledgeStorageError(f"candidate database does not exist: {path}")
    connection = open_database(path)
    return OpenedKnowledgeStore(
        database_path=path,
        repository_id=repository_id,
        schema=inspect_schema(connection),
        connection=connection,
        resource_lock_path=path,
    )
