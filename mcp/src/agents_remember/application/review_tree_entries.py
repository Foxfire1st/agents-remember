"""Every entry of a tree comparison, located on both code sides for the reviewer's cards (MIK-R31).

For one comparison of four Git trees (MIK-R25), this module lists each realization and proof entry
of the two memory trees K_B and K_C, and places it on the code base B and the code candidate C with
the worklist's own resolution (:meth:`CodeTrees.resolve`, MIK-R08 definition 3). Each side also
carries the side's own authored fields -- role and rationale for a realization, facet for a proof --
and its MIK-R03 entry state through the one observation function
(:func:`~agents_remember.application.knowledge_currentness.observe_entry`, cached as that module
caches it).

Nothing is written, re-anchored or judged, and no text is supplied that the entry does not carry: a
realization read without a rationale has none here, and the renderer names the gap.

**The placement cache.** Where a locator lands in a blob is a function of the blob, the locator and
the extractor, so a placement -- the range and its content identity, or "does not resolve" -- is
remembered per process in a bounded LRU table (:data:`PLACEMENTS`, 8,192 entries, the observation
cache's bound and key, :func:`observation_key`). Only answers are remembered; a read that failed is
asked again.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_currentness.observe import (
    ObservationKey,
    OpenedCodeTree,
    observation_key,
    observe_entry,
    open_code_tree,
)
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees, Resolved
from agents_remember.application.review_tree_comparison import ReviewTrees, TreeKnowledge
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex, text_uuid
from agents_remember.memory_quality.style.citations import grammars
from agents_remember.models.knowledge.review_tree_entries import (
    EXCERPT_MAX_CHARACTERS,
    EXCERPT_MAX_LINES,
    ReviewEntryChange,
    ReviewTreeEntry,
    ReviewTreeEntrySide,
)
from agents_remember.models.knowledge_files.anchor_content import blob_lines

__all__ = ["PLACEMENTS", "tree_entries"]

PLACEMENTS: Final[BoundedMemo[ObservationKey, Resolved | None]] = BoundedMemo(8_192)


@dataclass(frozen=True)
class _Side:
    """One code side as the entries are read at it: its memory tree's entries and its code tree."""

    entries: Mapping[str, Entry] | None
    unreadable: str | None
    code: OpenedCodeTree


def tree_entries(trees: ReviewTrees, invariant_keys: Iterable[str]) -> tuple[ReviewTreeEntry, ...]:
    """Each entry of the named invariants in K_B or K_C, on B and C, ordered by invariant, kind,
    path and ID.

    ``invariant_keys`` are the identities the landed review payload addresses invariants by; a key
    neither memory tree holds contributes nothing.
    """

    wanted = frozenset(invariant_keys)
    record = trees.record
    before = _side(
        trees.before, CodeTree(Path(record.code_base.repository), record.code_base.tree), wanted
    )
    after = _side(
        trees.after,
        CodeTree(Path(record.code_candidate.repository), record.code_candidate.tree),
        wanted,
    )
    known = set(before.entries or {}) | set(after.entries or {})
    listed = [_entry(entry_id, before, after) for entry_id in sorted(known)]
    return tuple(sorted(listed, key=lambda one: (one.invariant, one.kind, _path(one), one.id)))


def _path(entry: ReviewTreeEntry) -> str:
    return entry.after.path if entry.after.recorded else entry.before.path


def _side(knowledge: TreeKnowledge, code_tree: CodeTree, wanted: frozenset[str]) -> _Side:
    code = open_code_tree(code_tree)
    if knowledge.database is None:
        detail = knowledge.wire.detail or f"the memory tree is {knowledge.wire.state}"
        return _Side(entries=None, unreadable=detail, code=code)
    with KnowledgeIndex(knowledge.database) as index:
        entries = {entry.id: entry for entry in _entries_of(index, wanted)}
    return _Side(entries=entries, unreadable=None, code=code)


def _entries_of(index: KnowledgeIndex, wanted: frozenset[str]) -> Iterator[Entry]:
    for invariant in index.record_ids("invariant").value:
        if text_uuid("identity", invariant) not in wanted:
            continue
        knowledge = index.invariant(invariant).value
        yield from knowledge.realizations
        yield from knowledge.proofs


def _entry(entry_id: str, before: _Side, after: _Side) -> ReviewTreeEntry:
    own_before = None if before.entries is None else before.entries.get(entry_id)
    own_after = None if after.entries is None else after.entries.get(entry_id)
    carried = own_after or own_before
    assert carried is not None  # listed from one side's entries
    before_side = _located(own_before or carried, own_before, before)
    after_side = _located(own_after or carried, own_after, after)
    change = _change(before_side, after_side)
    if change == "unchanged":  # one content identity: the same bytes, carried once
        before_side = before_side.model_copy(update={"excerpt": None, "excerpt_truncated": False})
    return ReviewTreeEntry(
        id=entry_id,
        kind=carried.kind,
        invariant=carried.invariant,
        invariant_key=text_uuid("identity", carried.invariant),
        before=before_side,
        after=after_side,
        change=change,
    )


def _change(before: ReviewTreeEntrySide, after: ReviewTreeEntrySide) -> ReviewEntryChange:
    states = {before.state, after.state}
    if states == {"resolved"}:
        return "unchanged" if before.content == after.content else "changed"
    if states == {"resolved", "absent"}:
        return "changed"  # the region exists on one side only: an added or a deleted file
    return "undetermined"


def _located(entry: Entry, own: Entry | None, side: _Side) -> ReviewTreeEntrySide:
    """``entry`` (the side's own, or the other side's when this side records none) on this side."""

    recorded = None if side.entries is None else own is not None
    fields = _authored(own) if own is not None else {}
    if side.unreadable is not None and own is None:
        return ReviewTreeEntrySide(
            recorded=recorded, state="unavailable", path=entry.path, reason=side.unreadable
        )
    placed = _placed(entry, side.code)
    currentness = _currentness(own, side.code)
    return ReviewTreeEntrySide(
        recorded=recorded, path=entry.path, **placed, **fields, **currentness
    )


def _authored(entry: Entry) -> dict[str, Any]:
    document = entry.document
    if entry.kind == "proof":
        return {"facet": _text(document.get("facet"))}
    return {"role": _text(document.get("role")), "rationale": _text(document.get("rationale"))}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _currentness(own: Entry | None, code: OpenedCodeTree) -> dict[str, Any]:
    if own is None:
        return {}
    observed = observe_entry(own, code)
    return {"currentness": observed.state, "currentness_reason": observed.reason}


def _placed(entry: Entry, code: OpenedCodeTree) -> dict[str, Any]:
    """Where the entry's locator lands in this side's blob of its path (MIK-R08 definition 3)."""

    if code.problem is not None or code.trees is None:
        return {"state": "unavailable", "reason": code.problem}
    blob = code.files.get(entry.path)
    if blob is None:
        return {"state": "absent", "reason": "the code tree holds no regular file at the path"}
    return {"blob": blob, **_in_blob(entry, code.trees, blob)}


def _in_blob(entry: Entry, trees: CodeTrees, blob: str) -> dict[str, Any]:
    anchor = entry.document.get("anchor") or {}
    locator: Mapping[str, Any] = anchor.get("locator") or {}
    recorded_blob = str(anchor.get("blob", ""))
    try:
        unsupported = _unsupported(entry.path, locator, recorded_blob, blob, trees)
        if unsupported is not None:
            return unsupported
        resolved = _resolve(trees, entry.path, locator, recorded_blob, blob)
    except CodeReadError as error:
        return {"state": "unavailable", "reason": str(error)}
    except (subprocess.SubprocessError, OSError) as error:
        return {"state": "unavailable", "reason": f"a Git read failed: {error}"}
    if resolved is None:
        return {"state": "unresolved", "reason": _unresolved(locator.get("kind"))}
    start, end = resolved.span
    return {
        "state": "resolved",
        "start_line": start,
        "end_line": end,
        "content": resolved.content,
        **_excerpt(trees, blob, start, end),
    }


def _excerpt(trees: CodeTrees, blob: str, start: int, end: int) -> dict[str, Any]:
    """Lines ``start``..``end`` of the exact blob as text, bounded; never re-encoded."""

    try:
        data = trees.objects.blob(blob)
    except Exception as error:  # a blob the store cannot give: named, never an empty excerpt
        return {"reason": f"the code blob {blob} cannot be read: {error}"}
    lines = blob_lines(data)[start - 1 : end]
    kept = lines[:EXCERPT_MAX_LINES]
    try:
        text = b"".join(kept).decode("utf-8")
    except UnicodeDecodeError:
        return {"reason": "the range is not UTF-8 text, so no excerpt is shown"}
    truncated = len(kept) < len(lines) or len(text) > EXCERPT_MAX_CHARACTERS
    return {"excerpt": text[:EXCERPT_MAX_CHARACTERS], "excerpt_truncated": truncated}


def _resolve(
    trees: CodeTrees, path: str, locator: Mapping[str, Any], recorded_blob: str, blob: str
) -> Resolved | None:
    key = observation_key(blob, path, locator, recorded_blob)
    remembered = PLACEMENTS.get(key)
    if remembered is not None:
        return remembered[0]
    resolved = trees.resolve(path, locator, recorded_blob, blob)
    PLACEMENTS.put(key, resolved)
    return resolved


def _unsupported(
    path: str,
    locator: Mapping[str, Any],
    recorded_blob: str,
    blob: str,
    trees: CodeTrees,
) -> dict[str, str] | None:
    kind = locator.get("kind")
    if kind not in ("symbol", "line_range", "file"):
        return {"state": "unresolved", "reason": f"the locator kind {kind!r} is unsupported"}
    if kind == "symbol" and grammars.grammar_of(path) is None:
        return {"state": "unresolved", "reason": "no shipped grammar reads this file's symbols"}
    if kind == "line_range" and blob != recorded_blob and not trees.has_blob(recorded_blob):
        return {
            "state": "unavailable",
            "reason": f"the blob {recorded_blob} the line range was recorded against is unavailable",
        }
    return None


def _unresolved(kind: object) -> str:
    if kind == "symbol":
        return "the symbol does not resolve uniquely in this blob"
    if kind == "line_range":
        return "the line range has no mapping to this blob"
    return "the locator does not resolve in this blob"
