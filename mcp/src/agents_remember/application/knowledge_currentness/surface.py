"""The ``currentness`` block the read surfaces attach for a converted memory tree (MIK-R03 rule 4).

``knowledge_read`` and the published-intent block of ``read_ar_files`` both read a converted memory
tree through its derived index, whose projection names records by UUID (MIK-R23 rule 6). The
invariants and families a read *returns* are the records its answer names: every UUID in the answer
that the index's reverse map translates to an invariant or family ID. :func:`read_currentness`
collects them, computes their state at the caller's resolved code tree with
:func:`invariant_currentness`, and returns the block. The answer itself is never edited, so every
invariant stays visible with its statement and relationships whatever its state (D9).
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
    invariant_currentness,
)
from agents_remember.memory.knowledge_index import KnowledgeIndex

__all__ = ["read_currentness", "requested_code_tree", "returned_records"]

# Every way the currentness step can fail on its own inputs: the index (``IndexMismatchError`` and
# ``MemoryTreeError`` are ``ValueError``s; SQLite errors), the file system, and Git calls.
_STEP_FAILURES: Final = (apsw.Error, OSError, ValueError, subprocess.SubprocessError)
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


def returned_records(index: KnowledgeIndex, answer: Any) -> tuple[list[str], list[str]]:
    """The invariant and family IDs ``answer`` names through the index's projected UUIDs."""

    invariants: set[str] = set()
    families: set[str] = set()
    for value in {one for one in _strings(answer) if _UUID.match(one)}:
        text_id = index.text_id(value)
        if text_id is None or "/" in text_id:
            continue  # not a record (a family-membership row names two)
        record = index.record(text_id.split("@", 1)[0]).value
        if record is None:
            continue
        if record.kind == "invariant":
            invariants.add(record.id)
        elif record.kind == "family":
            families.add(record.id)
    return sorted(invariants), sorted(families)


def requested_code_tree(
    code_tree_id: str | None, repository_root: str | None, default_repository: str | None
) -> CodeTree | None:
    """The code tree a caller requested, or ``None``: only a named tree ID counts as a request.

    The tree is read from ``repository_root``'s object store, or from ``default_repository`` (the
    mount's workspace) when the caller named a tree without a repository. A repository named
    without a tree requests nothing: its ``HEAD`` is never substituted (MIK-R03, Preservation).
    """

    if code_tree_id is None:
        return None
    repository = repository_root if repository_root is not None else default_repository
    return None if repository is None else CodeTree(Path(repository), code_tree_id)


def read_currentness(
    index_path: Path, tree_key: str, code_tree: CodeTree | None, answer: Any
) -> dict[str, Any]:
    """The ``currentness`` block for the records ``answer`` returns, at ``code_tree``; never raises.

    ``index_path`` is the index of the memory tree the read selected, opened for ``tree_key`` only.
    ``code_tree`` is the tree the caller resolved; ``None`` makes every entry ``unverifiable``
    ("no code tree was requested"), and there is no fallback to a working tree or ``HEAD``.
    Currentness is advisory beside the answer (L03 ruling N2): if the step itself fails -- the index
    cannot be opened or read, or a Git call fails -- the block states that reason as
    ``unverifiableReason`` and evaluates nothing, and the read it accompanies is still answered.
    """

    try:
        with KnowledgeIndex(index_path, expected_key=tree_key) as index:
            invariants, families = returned_records(index, answer)
            return invariant_currentness(code_tree, index, invariants, families).to_document()
    except _STEP_FAILURES as error:
        return {
            "codeTree": None if code_tree is None else code_tree.to_document(),
            "counts": dict.fromkeys(INVARIANT_STATES, 0),
            "invariants": [],
            "families": [],
            "unverifiableReason": (
                f"currentness could not be computed ({type(error).__name__}: {error})"
            ),
        }
