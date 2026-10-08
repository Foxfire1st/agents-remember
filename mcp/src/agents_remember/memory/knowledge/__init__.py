"""The knowledge dataset readers: the schema of a memory tree's derived index and the reads over it.

Knowledge is text in Git; the only SQLite dataset this build creates is the derived index of a
memory tree (:mod:`agents_remember.memory.knowledge_index`), built with the schema this package
declares. The package owns that schema, the row codecs and the read operations. It owns no writer
of knowledge rows -- the canonical database was retired (MIK-R26) -- and it does not own approval,
task status or Git attribution. Nothing ranked below it may import it: lower worktree and
memory-quality owners receive :mod:`agents_remember.models.knowledge` values or prepared results
from the application layer instead.
"""

from agents_remember.memory.knowledge.connection import BUSY_TIMEOUT_MILLISECONDS
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    SchemaGeneration,
    generation_of_database,
    generation_of_new_store,
    require_pinned_schema_unchanged,
    structure_fingerprint,
    structure_manifest,
)
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
    open_read_only_store,
)

__all__ = [
    "BUSY_TIMEOUT_MILLISECONDS",
    "CURRENT_GENERATION",
    "OpenedKnowledgeStore",
    "SchemaGeneration",
    "generation_of_database",
    "generation_of_new_store",
    "open_existing_knowledge_store",
    "open_read_only_store",
    "require_pinned_schema_unchanged",
    "structure_fingerprint",
    "structure_manifest",
]
