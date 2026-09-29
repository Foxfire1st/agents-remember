"""Adapters that hand the existing store-shaped code an index as its data source (MIK-R23 rule 6).

The read, view and comparison seams take a dataset *path*, and :attr:`KnowledgeIndex.database_path`
is that path. The registered-scope construction (:mod:`agents_remember.memory.knowledge.registered_scope`,
the worklist's scope) takes an opened store per declared side instead; this module opens one over an
index, through a connection that cannot write, so the construction runs unchanged and reads nothing
but the index.
"""

from __future__ import annotations

from agents_remember.memory.knowledge.connection import inspect_schema, open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.registered_scope import ScopeSnapshotSource
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory.knowledge_index.query import KnowledgeIndex
from agents_remember.models.knowledge.registered_scope import (
    ScopeSnapshotDeclaration,
    ScopeSnapshotSide,
)

# The selection policy the declaration names; the index serves the same recorded-family frontier.
SCOPE_SELECTOR_POLICY_VERSION = "recorded-family-frontier/v1"


def index_store(index: KnowledgeIndex) -> OpenedKnowledgeStore:
    """Open the index as a read-only store. The caller closes it (``store.connection.close()``)."""

    connection = open_read_only_database(index.database_path)
    return OpenedKnowledgeStore(
        database_path=index.database_path,
        repository_id=index.repository_id,
        schema=inspect_schema(connection),
        connection=connection,
        resource_lock_path=index.database_path,
    )


def scope_snapshot_source(side: ScopeSnapshotSide, index: KnowledgeIndex) -> ScopeSnapshotSource:
    """One declared side of a registered scope, served by ``index``."""

    return ScopeSnapshotSource(
        side=side, snapshot=dataset_identity(index.database_path), store=index_store(index)
    )


def scope_snapshot_declaration(
    side: ScopeSnapshotSide, index: KnowledgeIndex
) -> ScopeSnapshotDeclaration:
    """The declaration that names ``index``'s exact snapshot for ``side``."""

    return ScopeSnapshotDeclaration(
        side=side,
        snapshot=dataset_identity(index.database_path),
        selector_policy_version=SCOPE_SELECTOR_POLICY_VERSION,
    )
