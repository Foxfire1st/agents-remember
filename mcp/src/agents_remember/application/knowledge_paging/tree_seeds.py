"""Record seeds of a converted memory tree's reads: a seed the tree does not hold is refused.

A read of a converted tree (MIK-R23 rule 6) addresses invariant and family revisions by the UUIDs the
index projects for ``<ID>@<revision>``. At the cutover every remembered database-era revision ID
names nothing, and so does a bare ``INV-…``; answering such a seed with an empty, complete view looks
like a real answer that the tree holds nothing about it. So a seed the tree does not hold is refused
as ``selector_absent``, naming where current seeds come from (L37 ruling, P2 task 4). A database
read is not touched: an unconverted dataset keeps its own answers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from agents_remember.memory.knowledge_index import KnowledgeIndex
from agents_remember.memory.knowledge_index.projection import text_uuid

__all__ = ["SEED_SOURCES", "absent_seed", "tree_seed_refusal"]

SEED_SOURCES: Final = (
    "a converted memory tree's seeds are the revision UUIDs its own reads return -- take one from "
    "the knowledge section of read_ar_files or from a source_context row (the projected UUID of "
    "'<ID>@<revision>'); a database-era revision ID names nothing in a converted tree"
)
_PREFIXES: Final = {
    "invariantRevisionId": ("INV-", "invariant"),
    "familyRevisionId": ("FAM-", "family"),
}


def absent_seed(index: KnowledgeIndex, name: str, named: str) -> str | None:
    """Why the seed ``named`` (the parameter ``name``) names no revision the tree holds, or ``None``."""

    prefix, kind = _PREFIXES[name]
    text = index.text_id(named)
    if text is not None and text.startswith(prefix) and "@" in text:
        return None
    reason = f"{name} {named!r} names no {kind} revision this memory tree holds: {SEED_SOURCES}"
    record = index.record(named).value if named.startswith(prefix) else None
    if record is not None and record.status != "retired" and record.revision is not None:
        seed = text_uuid("revision", f"{record.id}@{record.revision}")
        reason += f"; {record.id} is held here at revision {record.revision}, whose seed is {seed}"
    return reason


def tree_seed_refusal(
    database_path: Path,
    tree_key: str,
    *,
    invariant_revision_id: str | None,
    family_revision_id: str | None,
) -> str | None:
    """The ``selector_absent`` reason for the first seed the tree does not hold, or ``None``."""

    seeds = (
        ("invariantRevisionId", invariant_revision_id),
        ("familyRevisionId", family_revision_id),
    )
    named = [(name, value) for name, value in seeds if value is not None]
    if not named:
        return None
    with KnowledgeIndex(database_path, expected_key=tree_key) as index:
        for name, value in named:
            absent = absent_seed(index, name, value)
            if absent is not None:
                return absent
    return None
