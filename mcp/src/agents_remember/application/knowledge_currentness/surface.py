"""The ``currentness`` block the read surfaces attach for a converted memory tree (MIK-R03 rule 4).

``knowledge_read`` and the published-intent block of ``read_ar_files`` both read a converted memory
tree through its derived index, whose projection names records by UUID (MIK-R23 rule 6). The
invariants and families a read *returns* are the records its answer names: every UUID in the answer
that the index's reverse map translates to an invariant or family ID. :func:`evaluate_answer`
collects them and computes their state at the caller's resolved code tree with
:func:`invariant_currentness`; the surfaces that attach the block cut it from that one
evaluation. The answer itself is never edited, so every invariant stays visible with its
statement and relationships whatever its state (D9).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.application.knowledge_currentness.observe import CodeTree
from agents_remember.application.knowledge_currentness.state import (
    INVARIANT_STATES,
    Currentness,
    invariant_currentness,
)
from agents_remember.memory.knowledge_index import KnowledgeIndex

__all__ = [
    "CURRENTNESS_FAILURES",
    "evaluate_answer",
    "failure_document",
    "named_uuids",
    "record_of",
    "returned_records",
]

# Every way the currentness step can fail on its own inputs: the index (``IndexMismatchError`` and
# ``MemoryTreeError`` are ``ValueError``s; SQLite errors), the file system, and Git calls.
CURRENTNESS_FAILURES: Final = (apsw.Error, OSError, ValueError, subprocess.SubprocessError)
_UUID: Final = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)


def named_uuids(answer: Any) -> set[str]:
    """Every projected UUID ``answer`` carries anywhere in its structure."""

    return {one for one in _strings(answer) if _UUID.match(one)}


def record_of(index: KnowledgeIndex, value: str) -> tuple[str, str] | None:
    """The ``(kind, ID)`` of the invariant or family one projected UUID names, or ``None``."""

    text_id = index.text_id(value)
    if text_id is None or "/" in text_id:
        return None  # not a record (a family-membership row names two)
    record = index.record(text_id.split("@", 1)[0]).value
    if record is None or record.kind not in ("invariant", "family"):
        return None
    return record.kind, record.id


def returned_records(index: KnowledgeIndex, answer: Any) -> tuple[list[str], list[str]]:
    """The invariant and family IDs ``answer`` names through the index's projected UUIDs."""

    invariants: set[str] = set()
    families: set[str] = set()
    for value in named_uuids(answer):
        named = record_of(index, value)
        if named is not None:
            (invariants if named[0] == "invariant" else families).add(named[1])
    return sorted(invariants), sorted(families)


def evaluate_answer(
    index_path: Path, tree_key: str, code_tree: CodeTree | None, answer: Any
) -> tuple[dict[str, tuple[str, str]], Currentness]:
    """Every record ``answer`` names, by UUID, and their currentness at ``code_tree``.

    The one evaluation a caller cuts several candidate answers from; it raises the
    :data:`CURRENTNESS_FAILURES` that each surface reports as its block's reason.
    """

    with KnowledgeIndex(index_path, expected_key=tree_key) as index:
        records: dict[str, tuple[str, str]] = {}
        for value in named_uuids(answer):
            named = record_of(index, value)
            if named is not None:
                records[value] = named
        kinds = {
            kind: sorted({i for k, i in records.values() if k == kind})
            for kind in ("invariant", "family")
        }
        return records, invariant_currentness(code_tree, index, kinds["invariant"], kinds["family"])


def failure_document(code_tree: CodeTree | None, error: BaseException) -> dict[str, Any]:
    """The block when the currentness step itself failed: its reason, and nothing evaluated."""

    return {
        "codeTree": None if code_tree is None else code_tree.to_document(),
        "counts": dict.fromkeys(INVARIANT_STATES, 0),
        "invariants": [],
        "families": [],
        "unverifiableReason": f"currentness could not be computed ({type(error).__name__}: {error})",
    }
