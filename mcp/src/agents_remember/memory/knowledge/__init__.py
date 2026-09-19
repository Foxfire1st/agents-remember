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
    schema_manifest,
)
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_1_FINGERPRINT,
    GENERATION_2,
    GENERATIONS,
    SchemaGeneration,
    generation_of_database,
    generation_of_new_store,
    require_pinned_generation_1_unchanged,
    structure_fingerprint,
    structure_manifest,
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
    "CURRENT_GENERATION",
    "GENERATIONS",
    "GENERATION_1",
    "GENERATION_1_FINGERPRINT",
    "GENERATION_2",
    "IMMUTABILITY_TRIGGERS",
    "SCHEMA_USER_VERSION",
    "OpenedKnowledgeStore",
    "SchemaGeneration",
    "discard_closed_wal_peers",
    "generation_of_database",
    "generation_of_new_store",
    "immediate_transaction",
    "open_existing_knowledge_store",
    "open_knowledge_store",
    "require_pinned_generation_1_unchanged",
    "schema_manifest",
    "structure_fingerprint",
    "structure_manifest",
]
