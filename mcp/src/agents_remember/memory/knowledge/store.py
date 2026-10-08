"""One opened knowledge dataset: its namespace, its validated schema and its read methods.

The dataset a reader opens is the derived index of a memory tree (MIK-R23): an SQLite file with the
store's schema, built from the tree's text files and never written by this module. The store reads
a repository's identities and revisions and re-derives each stored revision's payload seal on the
way out. Family and membership reads live in :mod:`families` and :mod:`memberships`; they take
this store as their first argument.

**No writer of knowledge rows is here.** The insert operations of the canonical database -- the
invariant, revision, family, membership, anchor and realization writers -- were retired with it
(MIK-R26 rule 4). :func:`open_existing_knowledge_store` and :func:`open_read_only_store` open
through a connection that cannot write.

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import apsw

from agents_remember.memory.knowledge import logical, records
from agents_remember.memory.knowledge.connection import (
    inspect_schema,
    open_read_only_database,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
)
from agents_remember.memory.knowledge.schema_generations import (
    SchemaGeneration,
    generation_for_name,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity
from agents_remember.models.knowledge.family import FamilyIdentity
from agents_remember.models.knowledge.invariant import (
    InvariantIdentity,
    StoredInvariantRevision,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity

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

    @property
    def generation(self) -> SchemaGeneration:
        """Resolve the pinned index schema the validated file declares.

        Row readers use its table and column manifest; this store never creates or migrates a schema.
        """

        resolved = generation_for_name(self.schema.schema_name)
        if resolved is None:
            raise KnowledgeStorageError(
                f"the store is open under schema {self.schema.schema_name!r}, which is not a "
                "generation this build supports"
            )
        return resolved

    def __enter__(self) -> OpenedKnowledgeStore:
        return self

    def __exit__(self, *exception: object) -> Literal[False]:
        """Close on every exit, including a failure raised inside the block."""

        del exception
        self.close()
        return False

    # -- lifecycle -----------------------------------------------------------------

    def close(self) -> None:
        """Close this owned read handle; cache files remain with the index cache's cleanup owner."""

        self.connection.close()

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

    def list_invariants(self) -> tuple[InvariantIdentity, ...]:
        """Return every invariant identity this namespace records, in stable order.

        The Intent Reviewer's subject is one of these identities, and a reader that must be handed
        a subject before it can list the candidates cannot enumerate a candidate it was not pointed
        at. The order is the identity's own column, so two runs over one snapshot agree without a
        tiebreak this reader chose.
        """

        return tuple(
            records.decode_invariant_row(row)
            for row in self.connection.execute(
                "SELECT repository_id, invariant_id, display_label, label_provenance "
                "FROM invariant WHERE repository_id = ? ORDER BY invariant_id",
                (self.repository_id,),
            )
        )

    def list_families(self) -> tuple[FamilyIdentity, ...]:
        """Return every family identity this namespace records, in stable order.

        The same enumeration as :meth:`list_invariants`, for the surface's other admitted subject
        kind. A family identity is the second thing the reviewer can be opened on, so both lists
        are read the same way rather than one being derived from the other.
        """

        return tuple(
            records.decode_family_row(row)
            for row in self.connection.execute(
                "SELECT repository_id, family_id, display_label, label_provenance "
                "FROM family WHERE repository_id = ? ORDER BY family_id",
                (self.repository_id,),
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

    # -- index identity -------------------------------------------------------------

    def snapshot_identity(self) -> SnapshotIdentity:
        """Return this index's logical identity, as stored right now.

        The caller resolves a context from this value and later compares the two inside one
        transaction, which is what makes "the dataset I read" a checked precondition rather than a
        remembered one.
        """

        repository = self.get_repository()
        if repository is None:
            raise KnowledgeStorageError(
                f"the candidate database is not bound to repository namespace {self.repository_id}"
            )
        return logical.snapshot_identity(self.connection, repository, self.generation)

    # -- internals ------------------------------------------------------------------

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


def open_existing_knowledge_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Open an existing dataset for reading, without creating, repairing or writing it.

    The same open as :func:`open_read_only_store`, under the name the reviewer's readers call: the
    datasets they open are index files, which nothing but the index builder writes.
    """

    return open_read_only_store(database_path, repository_id)


def open_read_only_store(database_path: Path, repository_id: str) -> OpenedKnowledgeStore:
    """Open one existing dataset through a connection that cannot write it.

    The connection is :func:`…connection.open_read_only_database`, whose strongest available
    statement is a ``SELECT``, and the schema is validated against the generation the *file*
    declares. A caller therefore cannot reach a writable handle by accident. The returned store is
    closed by the caller.
    """

    path = Path(database_path)
    if not path.exists():
        raise KnowledgeStorageError(f"the knowledge index does not exist: {path}")
    connection = open_read_only_database(path)
    return OpenedKnowledgeStore(
        database_path=path,
        repository_id=repository_id,
        schema=inspect_schema(connection),
        connection=connection,
    )
