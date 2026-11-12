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

**The memory values (MIK-R89 v2 rule 3).** Inside the one open of the after-side index that lists the
after entries, each entry gets four values at its after-side path ``p`` (the after entry's path, else
the before entry's, as the after side is located) and its invariant ``i``: the distinct invariants
over ``entries_at_path(p).realizations``, ``len(incoming_links(i))`` (family member links count),
whether ``i`` is among ``invariants_without_proof()`` (live invariants only), and the open weaknesses
anchored at ``p``. Each path and each invariant is asked once per read, and the unproved list once.
An after side with no index, or a read of it that fails, answers the entries without values and says
why in the result's ``entry_memory``; the card read itself never fails for them.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import apsw

from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.knowledge_currentness.observe import (
    ObservationKey,
    OpenedCodeTree,
    observation_key,
    observe_entry,
    open_code_tree,
)
from agents_remember.application.knowledge_reader.marks import open_weakness_counts_in
from agents_remember.application.knowledge_worklist.code import CodeReadError, CodeTrees, Resolved
from agents_remember.application.review_line_changes import line_changes
from agents_remember.application.review_tree_comparison import ReviewTrees, TreeKnowledge
from agents_remember.kernel.read_cancellation import raise_if_read_cancelled
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.memory.knowledge_index import (
    Entry,
    IndexMismatchError,
    KnowledgeIndex,
    text_uuid,
)
from agents_remember.memory_quality.style.citations import grammars
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.review_tree_entries import (
    EXCERPT_MAX_CHARACTERS,
    EXCERPT_MAX_LINES,
    ReviewEntryChange,
    ReviewEntryMemory,
    ReviewLineChanges,
    ReviewTreeEntry,
    ReviewTreeEntrySide,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewEntryInventory,
    ReviewEntryMemoryState,
)
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    blob_lines,
    range_bytes,
)

__all__ = ["PLACEMENTS", "CardEntries", "tree_entries", "tree_entry_cards", "tree_entry_inventory"]

PLACEMENTS: Final[BoundedMemo[ObservationKey, Resolved | None]] = BoundedMemo(8_192)


@dataclass(frozen=True)
class _Side:
    """One code side as the entries are read at it: its memory tree's entries and its code tree."""

    entries: Mapping[str, Entry] | None
    unreadable: str | None
    code: OpenedCodeTree
    complete: bool = False


@dataclass(frozen=True)
class CardEntries:
    """A card read's entries, and whether their memory values could be read (MIK-R89 v2 rule 3)."""

    entries: tuple[ReviewTreeEntry, ...]
    memory: ReviewEntryMemoryState


@dataclass(frozen=True)
class _Memory:
    """The memory values of one card read: the result-level state, and the values per entry ID."""

    state: ReviewEntryMemoryState
    values: Mapping[str, ReviewEntryMemory]


def tree_entries(trees: ReviewTrees, invariant_keys: Iterable[str]) -> CardEntries:
    """Each entry of the named invariants in K_B or K_C, on B and C, ordered by invariant, kind,
    path and ID, with its memory values at the after side.

    ``invariant_keys`` are the identities the landed review payload addresses invariants by; a key
    neither memory tree holds contributes nothing.
    """

    wanted = frozenset(invariant_keys)
    before, after, memory = _comparison_sides(trees, wanted, details=True)
    assert memory is not None
    return CardEntries(entries=_listed_entries(before, after, memory.values), memory=memory.state)


def tree_entry_inventory(trees: ReviewTrees) -> ReviewEntryInventory:
    """The complete comparison's exact locations, using the cards' placement and change rules."""

    try:
        before, after, _ = _comparison_sides(trees, None, details=False)
        failures = [
            side.unreadable or side.code.problem or "the knowledge index is partial"
            for side in (before, after)
            if side.unreadable or side.code.problem or not side.complete
        ]
        if failures:
            return ReviewEntryInventory(
                state="unavailable", reason="; ".join(failures)[:PROSE_MAX_LENGTH]
            )
        return ReviewEntryInventory(
            state="ready", entries=_listed_entries(before, after, {}, details=False)
        )
    except (apsw.Error, IndexMismatchError, OSError) as error:
        return ReviewEntryInventory(state="unavailable", reason=str(error)[:PROSE_MAX_LENGTH])


def _comparison_sides(
    trees: ReviewTrees, wanted: frozenset[str] | None, *, details: bool
) -> tuple[_Side, _Side, _Memory | None]:
    record = trees.record
    before = _side(
        trees.before, CodeTree(Path(record.code_base.repository), record.code_base.tree), wanted
    )
    after, memory = _after_side(
        trees.after,
        CodeTree(Path(record.code_candidate.repository), record.code_candidate.tree),
        wanted,
        before,
        details=details,
    )
    return before, after, memory


def _listed_entries(
    before: _Side,
    after: _Side,
    memory: Mapping[str, ReviewEntryMemory],
    *,
    details: bool = True,
) -> tuple[ReviewTreeEntry, ...]:
    paths = {entry.path for side in (before, after) for entry in (side.entries or {}).values()}
    recorded = _line_range_blobs(before, after)
    for side in (before, after):
        if side.code.trees is not None:
            side.code.trees.warm(paths)
            side.code.trees.objects.prefetch_presence(recorded)
    known = set(before.entries or {}) | set(after.entries or {})
    listed: list[ReviewTreeEntry] = []
    for entry_id in sorted(known):
        # One safe point per entry: a read whose askers left stops here (D569 mean 4).
        raise_if_read_cancelled()
        listed.append(_entry(entry_id, before, after, memory, details=details))
    return tuple(sorted(listed, key=lambda one: (one.invariant, one.kind, _path(one), one.id)))


def _line_range_blobs(*sides: _Side) -> set[str]:
    """The blobs the sides' line-range entries were recorded against, which each entry asks the
    store about (:func:`_unsupported`, the mapping and the currentness), read ahead in one batch."""

    blobs: set[str] = set()
    for side in sides:
        for entry in (side.entries or {}).values():
            anchor = entry.document.get("anchor") or {}
            if (anchor.get("locator") or {}).get("kind") == "line_range":
                blobs.add(str(anchor.get("blob", "")))
    return blobs


def tree_entry_cards(
    trees: ReviewTrees, entry_id: str, side: Literal["before", "after"] | None
) -> CardEntries | ReviewRefusal:
    """Resolve one entry's owner on its named side, then read that invariant's cards."""

    reason = "entry requires a before or after side"
    owner_key = None
    if side in ("before", "after"):
        knowledge = trees.before if side == "before" else trees.after
        reason = knowledge.wire.detail or f"the {side} knowledge side is unavailable"
        if knowledge.database is not None:
            try:
                with KnowledgeIndex(knowledge.database) as index:
                    entry = index.entry(entry_id).value
                    if entry is not None:
                        owner = index.record(entry.invariant).value
                        if owner is not None and owner.kind == "invariant":
                            owner_key = text_uuid("identity", entry.invariant)
                        else:
                            reason = f"entry {entry_id} has no readable invariant owner on the {side} side"
                    elif index.state.complete:
                        reason = f"entry {entry_id} is not recorded on the {side} side"
                    else:
                        reason = (
                            f"the partial {side} index cannot establish entry {entry_id}'s owner"
                        )
            except (apsw.Error, IndexMismatchError, OSError) as error:
                reason = f"entry {entry_id}'s {side} owner cannot be read: {error}"
    if owner_key is not None:
        return tree_entries(trees, (owner_key,))
    return ReviewRefusal(
        code="subject_unresolved",
        detail=reason[:PROSE_MAX_LENGTH],
        next_action="select an entry recorded on the named side of this comparison",
        offending_input=entry_id,
    )


def _path(entry: ReviewTreeEntry) -> str:
    return entry.after.path if entry.after.recorded else entry.before.path


def _side(knowledge: TreeKnowledge, code_tree: CodeTree, wanted: frozenset[str] | None) -> _Side:
    code = open_code_tree(code_tree)
    if knowledge.database is None:
        return _Side(entries=None, unreadable=_unreadable(knowledge), code=code)
    with KnowledgeIndex(knowledge.database) as index:
        entries = {entry.id: entry for entry in _entries_of(index, wanted)}
        complete = index.state.complete
    return _Side(entries=entries, unreadable=None, code=code, complete=complete)


def _after_side(
    knowledge: TreeKnowledge,
    code_tree: CodeTree,
    wanted: frozenset[str] | None,
    before: _Side,
    *,
    details: bool = True,
) -> tuple[_Side, _Memory | None]:
    """The after side as :func:`_side` reads it, and the memory values read in the same open."""

    code = open_code_tree(code_tree)
    tree = knowledge.wire.tree
    if knowledge.database is None:
        detail = _unreadable(knowledge)
        return _Side(entries=None, unreadable=detail, code=code), _unavailable(tree, detail)
    with KnowledgeIndex(knowledge.database) as index:
        entries = {entry.id: entry for entry in _entries_of(index, wanted)}
        located = {**(before.entries or {}), **entries}
        memory = _memory_values(index, located, tree) if details else None
        complete = index.state.complete
    return _Side(entries=entries, unreadable=None, code=code, complete=complete), memory


def _unreadable(knowledge: TreeKnowledge) -> str:
    return knowledge.wire.detail or f"the memory tree is {knowledge.wire.state}"


def _memory_values(index: KnowledgeIndex, located: Mapping[str, Entry], tree: str) -> _Memory:
    """The four values of each entry, by the entry the after side is located with (rule 3).

    ``located`` maps each entry ID to the after side's own entry, else the before side's, which is
    the entry whose path the after side of the card names.
    """

    try:
        unproved = {record.id for record in index.invariants_without_proof().value}
        weaknesses = open_weakness_counts_in(index)
        at_path: dict[str, int] = {}
        links: dict[str, int] = {}
        values: dict[str, ReviewEntryMemory] = {}
        for entry_id, entry in located.items():
            path, invariant = entry.path, entry.invariant
            if path not in at_path:
                realized = index.entries_at_path(path).value.realizations
                at_path[path] = len({one.invariant for one in realized})
            if invariant not in links:
                links[invariant] = len(index.incoming_links(invariant).value)
            values[entry_id] = ReviewEntryMemory(
                path=path,
                invariants_at_path=at_path[path],
                incoming_links=links[invariant],
                without_proof=invariant in unproved,
                open_weaknesses=weaknesses.get(path, 0),
            )
    except (apsw.Error, IndexMismatchError, OSError) as error:
        return _unavailable(
            tree, f"the memory values cannot be read at memory tree {tree}: {error}"
        )
    state = ReviewEntryMemoryState(state="available", tree=tree, index_state=index.state.state)
    return _Memory(state=state, values=values)


def _unavailable(tree: str, reason: str) -> _Memory:
    state = ReviewEntryMemoryState(state="unavailable", tree=tree, reason=reason[:PROSE_MAX_LENGTH])
    return _Memory(state=state, values={})


def _entries_of(index: KnowledgeIndex, wanted: frozenset[str] | None) -> Iterator[Entry]:
    identities = [
        invariant
        for invariant in index.record_ids("invariant").value
        if wanted is None or text_uuid("identity", invariant) in wanted
    ]
    for entries in index.currentness_inputs(identities).value.entries.values():
        yield from entries.realizations
        yield from entries.proofs


def _entry(
    entry_id: str,
    before: _Side,
    after: _Side,
    memory: Mapping[str, ReviewEntryMemory],
    *,
    details: bool = True,
) -> ReviewTreeEntry:
    own_before = None if before.entries is None else before.entries.get(entry_id)
    own_after = None if after.entries is None else after.entries.get(entry_id)
    carried = own_after or own_before
    assert carried is not None  # listed from one side's entries
    before_side = _located(own_before or carried, own_before, before, details=details)
    after_side = _located(own_after or carried, own_after, after, details=details)
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
        memory=memory.get(entry_id),
        line_changes=_line_changes(before_side, after_side, before.code, after.code),
    )


def _line_changes(
    before: ReviewTreeEntrySide,
    after: ReviewTreeEntrySide,
    before_code: OpenedCodeTree,
    after_code: OpenedCodeTree,
) -> ReviewLineChanges | None:
    if (
        before.state == after.state == "resolved"
        and before.content is not None
        and before.content == after.content
    ):
        return ReviewLineChanges(added=0, removed=0)
    try:
        old = _range_text(before, before_code)
        new = _range_text(after, after_code)
    except (CodeReadError, UnicodeDecodeError, RangeOutsideBlobError, OSError):
        return None
    return None if old is None or new is None else line_changes(old, new)


def _range_text(side: ReviewTreeEntrySide, code: OpenedCodeTree) -> str | None:
    if side.state == "absent":
        return ""
    if side.state != "resolved" or code.trees is None or side.blob is None:
        return None
    if side.start_line is None or side.end_line is None:
        return None
    data = code.trees._blob(side.blob)
    # The resolver represents an empty whole file as L1-1, although it holds no text lines.
    if not data and side.start_line == side.end_line == 1:
        return ""
    return range_bytes(data, side.start_line, side.end_line).decode("utf-8")


def _change(before: ReviewTreeEntrySide, after: ReviewTreeEntrySide) -> ReviewEntryChange:
    states = {before.state, after.state}
    if states == {"resolved"}:
        return "unchanged" if before.content == after.content else "changed"
    if states == {"resolved", "absent"}:
        return "changed"  # the region exists on one side only: an added or a deleted file
    return "undetermined"


def _located(
    entry: Entry, own: Entry | None, side: _Side, *, details: bool = True
) -> ReviewTreeEntrySide:
    """``entry`` (the side's own, or the other side's when this side records none) on this side."""

    recorded = None if side.entries is None else own is not None
    fields = _authored(own, details=details) if own is not None else {}
    symbol = _symbol(entry)
    if side.unreadable is not None and own is None:
        return ReviewTreeEntrySide(
            recorded=recorded,
            state="unavailable",
            path=entry.path,
            reason=side.unreadable,
            symbol=symbol,
        )
    placed = _placed(entry, side.code, details=details)
    currentness = _currentness(own, side.code)
    return ReviewTreeEntrySide(
        recorded=recorded, path=entry.path, symbol=symbol, **placed, **fields, **currentness
    )


def _symbol(entry: Entry) -> str | None:
    """The name the entry's locator names, when it is a ``symbol`` locator (rule 3's filter)."""

    anchor = entry.document.get("anchor") or {}
    locator: Mapping[str, Any] = anchor.get("locator") or {}
    if locator.get("kind") != "symbol":
        return None
    return _text(locator.get("name"))


def _authored(entry: Entry, *, details: bool = True) -> dict[str, Any]:
    document = entry.document
    if entry.kind == "proof":
        return {"facet": _text(document.get("facet"))} if details else {}
    return {
        "role": _text(document.get("role")),
        **({"rationale": _text(document.get("rationale"))} if details else {}),
    }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _currentness(own: Entry | None, code: OpenedCodeTree) -> dict[str, Any]:
    if own is None:
        return {}
    observed = observe_entry(own, code)
    return {"currentness": observed.state, "currentness_reason": observed.reason}


def _placed(entry: Entry, code: OpenedCodeTree, *, details: bool = True) -> dict[str, Any]:
    """Where the entry's locator lands in this side's blob of its path (MIK-R08 definition 3)."""

    if code.problem is not None or code.trees is None:
        return {"state": "unavailable", "reason": code.problem}
    blob = code.files.get(entry.path)
    if blob is None:
        return {"state": "absent", "reason": "the code tree holds no regular file at the path"}
    return {"blob": blob, **_in_blob(entry, code.trees, blob, details=details)}


def _in_blob(entry: Entry, trees: CodeTrees, blob: str, *, details: bool = True) -> dict[str, Any]:
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
        **(_excerpt(trees, blob, start, end) if details else {}),
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
