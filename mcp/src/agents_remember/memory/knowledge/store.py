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

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import apsw

from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.memory.knowledge import lineage, records
from agents_remember.memory.knowledge.connection import (
    create_or_validate_schema,
    discard_closed_wal_peers,
    immediate_transaction,
    inspect_schema,
    open_database,
)
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
        with self._exclusive_candidate_lock("create_invariant") as lock_refusal:
            if lock_refusal is not None:
                return self._invariant_refusal(request, lock_refusal)
            return self._within_immediate(
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
        with self._exclusive_candidate_lock("create_invariant_revision") as lock_refusal:
            if lock_refusal is not None:
                return self._revision_result(request, "refused", refusal=lock_refusal)
            return self._within_immediate(
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

    # -- internals ------------------------------------------------------------------

    def _insert_repository(self, identity: RepositoryIdentity) -> RepositoryCreationResult:
        existing = self.get_repository()
        if existing is None:
            self._write(
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
        existing = self.get_invariant(request.invariant_id)
        label = request.display_label.strip()
        if existing is not None:
            if existing.display_label == label:
                return self._invariant_result(request, "no_change")
            raise KnowledgeRefused(
                duplicate_invariant_refusal(request.invariant_id, existing.display_label, label)
            )
        self._write(
            "INSERT INTO invariant "
            "(repository_id, invariant_id, display_label, label_provenance) VALUES (?, ?, ?, ?)",
            records.invariant_row(
                request.repository_id, request.invariant_id, label, request.provenance
            ),
        )
        return self._invariant_result(request, "created")

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
        if self.get_invariant(revision.invariant_id) is None:
            raise KnowledgeRefused(unknown_invariant_refusal(revision.invariant_id))
        self._require_same_invariant_predecessors(revision)
        # The lineage rule, stated once:
        #
        #   ``create_revision`` refuses with ``lineage_cycle`` when inserting the candidate would
        #   leave ANY revision in this invariant's lineage graph on a cycle -- equivalently, when
        #   the candidate itself would be on a cycle, or when a retained revision reachable from
        #   the candidate through predecessors is already on one.
        #
        # It is evaluated over the POST-INSERT graph (the stored edges plus the candidate's own)
        # and before any row is written, so a refusal leaves the tables exactly as they were. Raw
        # cyclic state can only arise outside the operation -- the admission rule admits only
        # existing predecessors and refuses a self-referencing payload -- which is why the
        # refusal is demonstrated against a graph written by hand.
        self._require_acyclic_lineage(revision)
        self._write(
            "INSERT INTO invariant_revision "
            "(repository_id, invariant_id, revision_id, display_version, statement, applicability, "
            "conditions, exclusions, state_at_origin, acceptance_ref, provenance, payload_digest) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            records.revision_row(revision),
        )
        for edge in records.predecessor_rows(revision):
            self._write(
                "INSERT INTO invariant_predecessor "
                "(repository_id, invariant_id, child_revision_id, parent_revision_id) "
                "VALUES (?, ?, ?, ?)",
                edge,
            )
        self._require_referential_integrity()
        return self._revision_result(request, "created", payload_digest=revision.payload_digest)

    def _require_same_invariant_predecessors(self, revision: InvariantRevision) -> None:
        """Every predecessor must be an existing revision of the *same* invariant."""

        for parent_id in sorted(revision.predecessors):
            row = next(
                iter(
                    self.connection.execute(
                        "SELECT invariant_id FROM invariant_revision "
                        "WHERE repository_id = ? AND revision_id = ?",
                        (revision.repository_id, parent_id),
                    )
                ),
                None,
            )
            if row is None:
                raise KnowledgeRefused(
                    dangling_predecessor_refusal(parent_id, revision.invariant_id)
                )
            if str(row[0]) != revision.invariant_id:
                raise KnowledgeRefused(
                    cross_invariant_predecessor_refusal(
                        parent_id, revision.invariant_id, str(row[0])
                    )
                )

    def lineage_cycle_members(self, revision: InvariantRevision) -> tuple[str, ...]:
        """Return the edges on a cycle through ``revision``, as their child endpoints.

        This is the membership query, not the write-path rule. Two facts about its authority:

        * It reads the *declared* edge set -- the stored edges plus the revision's own -- so it can
          see a cycle the surrounding transaction has not created yet.
        * Only edges *on* a cycle through the revision are members. A revision that descends from a
          stored cycle without being on it is refused by the write path (see
          :meth:`_require_acyclic_lineage`) but has no members here, because no cycle passes
          through it.
        """

        return lineage.edges_on_cycle(
            candidate_id=revision.revision_id,
            predecessors=revision.predecessors,
            edges=lineage.invariant_edges(
                self.connection, revision.repository_id, revision.invariant_id
            ),
        )

    def _require_acyclic_lineage(self, revision: InvariantRevision) -> None:
        """Refuse when the post-insert lineage graph leaves the candidate on, or above, a cycle.

        The rule itself, its two branches and the reason it is evaluated over the post-insert graph
        live in :mod:`agents_remember.memory.knowledge.lineage`, which the family lineage applies
        unchanged. Two branches reach the refusal and the message names the one that applied: the
        candidate is itself on a cycle, or a retained revision reachable from the candidate through
        predecessors is on one. The second branch is the wider reading of the rule and is why a
        revision whose own lineage is acyclic can still be refused.
        """

        finding = lineage.find_cycle(
            candidate_id=revision.revision_id,
            predecessors=revision.predecessors,
            edges=lineage.invariant_edges(
                self.connection, revision.repository_id, revision.invariant_id
            ),
        )
        if finding is not None:
            raise KnowledgeRefused(
                lineage_cycle_refusal(
                    revision.revision_id,
                    finding.members,
                    candidate_on_cycle=finding.candidate_on_cycle,
                )
            )

    def _require_referential_integrity(self) -> None:
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

    def _invariant_result(
        self, request: InvariantRequest, state: Literal["created", "no_change"]
    ) -> CreateInvariantResult:
        return CreateInvariantResult(
            state=state,
            repository_id=request.repository_id,
            invariant_id=request.invariant_id,
            stored=state == "created",
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

    def _within_immediate(
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

    def _write(self, statement: str, parameters: Sequence[Any]) -> None:
        """Run one parameterized write inside the open immediate transaction."""

        cursor = self.connection.cursor()
        try:
            cursor.execute(statement, tuple(parameters))
        finally:
            cursor.close()

    @contextmanager
    def _exclusive_candidate_lock(
        self, operation: KnowledgeOperation
    ) -> Iterator[KnowledgeRefusal | None]:
        """Hold the candidate's resource lock, or yield the refusal that replaced it."""

        try:
            with exclusive_file_lock(self.resource_lock_path, "knowledge candidate database"):
                yield None
        except LockCapabilityError as error:
            yield lock_capability_refusal(operation, str(error))
        except apsw.BusyError as error:
            yield candidate_busy_refusal(operation, str(error))


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
