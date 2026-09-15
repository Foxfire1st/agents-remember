"""Concrete knowledge storage: one APSW-backed SQLite candidate with immutable revisions.

The package owns the schema, the row codecs and the insert-only revision operation. It does
not own approval, task status or Git attribution, and nothing ranked below it may import it:
lower worktree and memory-quality owners receive :mod:`agents_remember.models.knowledge`
values or prepared results from the application layer instead.
"""

from agents_remember.memory.knowledge.connection import (
    BUSY_TIMEOUT_MILLISECONDS,
    discard_closed_wal_peers,
    immediate_transaction,
)
from agents_remember.memory.knowledge.schema import (
    CANONICAL_COLUMNS,
    CANONICAL_TABLES,
    IMMUTABILITY_TRIGGERS,
    SCHEMA_USER_VERSION,
    create_schema_statements,
    schema_fingerprint,
    schema_manifest,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
    open_knowledge_store,
)

__all__ = [
    "BUSY_TIMEOUT_MILLISECONDS",
    "CANONICAL_COLUMNS",
    "CANONICAL_TABLES",
    "IMMUTABILITY_TRIGGERS",
    "SCHEMA_USER_VERSION",
    "OpenedKnowledgeStore",
    "create_schema_statements",
    "discard_closed_wal_peers",
    "immediate_transaction",
    "open_existing_knowledge_store",
    "open_knowledge_store",
    "schema_fingerprint",
    "schema_manifest",
]
