"""The text knowledge format: Markdown for prose, canonical JSON for structured facts (MIK-R21).

Text files in the memory repository are the source of truth for knowledge (D18, Doc14). This
package is the one declaration of their formats; the validator (MIK-R22), the derived index
(MIK-R23), the writer (MIK-R12) and the conversion (MIK-R24) all read and write through it.

* :mod:`.ids` -- stable, branch-safe IDs: random ``<KIND>-<6 Crockford base32>`` minted by the
  writer, and 8-character IDs derived from legacy IDs for exported records and entries.
* :mod:`.shapes` -- anchors ``{ path?, locator, blob, content }``, numbered references with typed
  targets, requirement references, links with relation kinds, admission and origin.
* :mod:`.records` -- the global records under ``knowledge/``: invariant, family, decision,
  incident and the six other facet kinds, each ``ar-<kind>/v1``.
* :mod:`.sidecars` -- the local sidecars under ``onboarding/`` (file and route) with their
  realization and proof entries, and the ``knowledge/layout.json`` marker.
* :mod:`.history` -- the per-leaf history files (``ar-history/v1``, MIK-R07): judgment rows,
  the row-kind registry, and the freeze predicate.
* :mod:`.documents` -- locations and the schema-to-model dispatch.
* :mod:`.canonical` -- the canonical JSON formatting, applied by ``agents-remember
  knowledge-format``.

Markdown files carry ``[n]`` markers whose numbers are local to the file and resolve through the
paired sidecar's ``references``. Text that looks like a marker but is not one (``signals[0]``) sits
in a code span or is written ``\\[0]``; only an unescaped ``[n]`` outside code is a marker.

The models check shape and formatting only. Integrity (IDs resolve, markers match references, one
owner per relationship, family route coverage) is the validator's (MIK-R22).
"""

from __future__ import annotations

from agents_remember.models.knowledge_files.canonical import (
    FORMATTER_COMMAND,
    CanonicalFormatError,
    canonical_text,
    format_bytes,
    format_text,
    is_canonical,
)
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    RECORD_DIRECTORIES,
    SCHEMA_MODELS,
    KnowledgeDocument,
    file_sidecar_path,
    history_path,
    parse_document,
    parse_document_text,
    parse_history_document,
    record_path,
    route_sidecar_path,
)
from agents_remember.models.knowledge_files.history import (
    HISTORY_ROW_KINDS,
    FamilyRow,
    HistoryFile,
    HistoryRow,
    InvariantRow,
    frozen_history_violation,
)
from agents_remember.models.knowledge_files.ids import (
    ENTRY_PREFIXES,
    RECORD_PREFIXES,
    derived_realization_id,
    derived_record_id,
    mint_id,
)
from agents_remember.models.knowledge_files.records import (
    RECORD_MODELS,
    RELATIONS_BY_KIND,
    KnowledgeRecord,
)
from agents_remember.models.knowledge_files.shapes import Anchor, Link, Origin, Reference
from agents_remember.models.knowledge_files.sidecars import (
    FileSidecar,
    LayoutMarker,
    ProofEntry,
    RealizationEntry,
    RouteSidecar,
)

__all__ = [
    "ENTRY_PREFIXES",
    "FORMATTER_COMMAND",
    "HISTORY_ROW_KINDS",
    "LAYOUT_MARKER_PATH",
    "RECORD_DIRECTORIES",
    "RECORD_MODELS",
    "RECORD_PREFIXES",
    "RELATIONS_BY_KIND",
    "SCHEMA_MODELS",
    "Anchor",
    "CanonicalFormatError",
    "FamilyRow",
    "FileSidecar",
    "HistoryFile",
    "HistoryRow",
    "InvariantRow",
    "KnowledgeDocument",
    "KnowledgeRecord",
    "LayoutMarker",
    "Link",
    "Origin",
    "ProofEntry",
    "RealizationEntry",
    "Reference",
    "RouteSidecar",
    "canonical_text",
    "derived_realization_id",
    "derived_record_id",
    "file_sidecar_path",
    "format_bytes",
    "format_text",
    "frozen_history_violation",
    "history_path",
    "is_canonical",
    "mint_id",
    "parse_document",
    "parse_document_text",
    "parse_history_document",
    "record_path",
    "route_sidecar_path",
]
