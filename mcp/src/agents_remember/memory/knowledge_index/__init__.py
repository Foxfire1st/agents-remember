"""The derived knowledge index: every relationship of one memory tree, both directions (MIK-R23).

Knowledge is text in Git (D18). Each relationship is written once, from its owner's side (D19); the
reverse directions -- invariant to its code, test to invariant, route to families, record to what
links to it, subject to its history rows -- are answered here, from an SQLite file derived from one
memory tree and nothing else.

* :mod:`.tree` reads a memory tree from a working-tree directory or from Git objects and computes
  its key, the Git tree id (of the captured state, for a directory).
* :mod:`.build` parses the tree's files through the MIK-R21/R07 models and writes the index; a file
  that fails its schema marks the index ``partial`` and is named.
* :mod:`.projection` also writes the index as a dataset of the knowledge store's newest schema
  generation, so the existing read, view and traversal code runs over it unchanged.
* :mod:`.query` answers the lookups; every answer carries the index state.
* :mod:`.adapters` opens an index as a read-only store for the registered-scope construction.
* :mod:`.cache` keeps one file per tree key under the coordination runtime, never inside a Git
  working tree, rebuilt when absent and evicted by age and size.

No knowledge writer writes the index, and the index is never merged: it is rebuilt from the tree.
"""

from __future__ import annotations

from agents_remember.memory.knowledge_index.adapters import (
    index_store,
    scope_snapshot_declaration,
    scope_snapshot_source,
)
from agents_remember.memory.knowledge_index.build import BuildReport, build_index, parse_tree
from agents_remember.memory.knowledge_index.cache import (
    CacheOutcome,
    KnowledgeIndexCache,
    default_cache_directory,
)
from agents_remember.memory.knowledge_index.projection import (
    INDEX_AUTHORITY_HOME,
    INDEX_REPOSITORY_ID,
    text_uuid,
)
from agents_remember.memory.knowledge_index.query import (
    Answer,
    EntriesAtPath,
    Entry,
    FamilyKnowledge,
    HistoryRow,
    IndexMismatchError,
    IndexState,
    InvariantKnowledge,
    KnowledgeIndex,
    Link,
    Record,
)
from agents_remember.memory.knowledge_index.schema import INDEX_FORMAT
from agents_remember.memory.knowledge_index.tree import (
    MemoryTreeError,
    MemoryTreeSnapshot,
    directory_key,
    directory_snapshot,
    git_tree_snapshot,
    is_indexed_path,
)

__all__ = [
    "INDEX_AUTHORITY_HOME",
    "INDEX_FORMAT",
    "INDEX_REPOSITORY_ID",
    "Answer",
    "BuildReport",
    "CacheOutcome",
    "EntriesAtPath",
    "Entry",
    "FamilyKnowledge",
    "HistoryRow",
    "IndexMismatchError",
    "IndexState",
    "InvariantKnowledge",
    "KnowledgeIndex",
    "KnowledgeIndexCache",
    "Link",
    "MemoryTreeError",
    "MemoryTreeSnapshot",
    "Record",
    "build_index",
    "default_cache_directory",
    "directory_key",
    "directory_snapshot",
    "git_tree_snapshot",
    "index_store",
    "is_indexed_path",
    "parse_tree",
    "scope_snapshot_declaration",
    "scope_snapshot_source",
    "text_uuid",
]
