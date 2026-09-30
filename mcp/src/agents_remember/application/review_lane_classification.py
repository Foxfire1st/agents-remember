"""One classification of a tree comparison's changed paths: file buckets and hunks (MIK-R32).

The reviewer's unexplained-changes lane, its entry count, and the triage badges and hunk markers
built on it all read this module; nothing else classifies a hunk for the reviewer. It owns no hunk
arithmetic of its own: a path's hunks are the worklist's (:func:`change_hunks`, MIK-R08 definition
2), an entry's range is the worklist's resolution (:meth:`CodeTrees.resolve`, definition 3) and a
non-text change's gate linkage is the worklist's predicate (:func:`non_text_linked`, definition 8).
What differs from the gate is stated once, here:

* **An entry supplies a range on a side only at its own recorded blob.** The side's blob of the path
  must be exactly the entry's ``blob``, and the locator must then bind there (a ``symbol`` the
  extractor binds uniquely, a ``line_range`` inside the blob, or a ``file``). A range mapped
  through a diff, or an entry MIK-R03 would still call current at another blob, supplies nothing:
  it is ``recorded_blob_mismatch``. The gate maps line ranges; the reviewer does not.
* **Only changed lines are intersected.** A hunk links through a side only where it changes lines
  on that side, so an insertion is matched against after-side ranges only and a deletion against
  before-side ranges only. Until the curator records the new blob, no after-side entry of an edited
  file supplies a range, so an insertion there is attribution unknown -- while the gate, which maps
  ranges and also counts an insertion strictly inside a before-side range, may call it linked or
  raise it as ``unexplained_hunk``. The two views are allowed to differ; the gate decides gate items.

**Buckets.** A path is *attributed* when an entry supplies a range on a side whose knowledge was
read; *unexplained* when neither side records any entry at the path and both sides were read (a side
where the file is absent is read all the same); every other path is of *attribution unknown* -- a
side's knowledge is unavailable, or entries are recorded that supply no range. A partial index makes
one path's side unavailable when that path's own sidecar is among the files that failed to parse.

**Hunks.** A hunk is *linked* when its changed lines on a side intersect a range supplied there;
otherwise *attribution unknown* when, on a side where it has changed lines, the knowledge is
unavailable or some entry at the path supplies no range; otherwise *unexplained*.

The code trees and both memory indexes are opened once per :class:`TreeLane` and closed with it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.application.knowledge_currentness.observe import observation_key
from agents_remember.application.knowledge_worklist.code import (
    CodeReadError,
    CodeTrees,
    Hunk,
    LineRange,
    Resolved,
    change_hunks,
    hits_new,
    hits_old,
)
from agents_remember.application.knowledge_worklist.compute import non_text_linked
from agents_remember.application.review_source_inventory import tree_difference_observation
from agents_remember.application.review_tree_comparison import ReviewTrees, TreeKnowledge
from agents_remember.application.review_tree_entries import PLACEMENTS
from agents_remember.memory.knowledge.tree_observation import TreeChange, TreePaths, TreeSide
from agents_remember.memory.knowledge_index import Entry, KnowledgeIndex
from agents_remember.memory.knowledge_index.query import IndexMismatchError
from agents_remember.memory_quality.style.citations import grammars
from agents_remember.models.knowledge.review_lane import (
    LaneBucket,
    LaneGateLinkage,
    LaneHunkClass,
    LaneRangeReason,
    LaneSideName,
    ReviewLaneCounts,
    ReviewLaneNonText,
    ReviewLaneUnknown,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    ONBOARDING_ROOT,
    RECORD_DIRECTORIES,
)

__all__ = [
    "DETAIL_LIMIT",
    "NAMED_ENTRIES",
    "FileResult",
    "HunkResult",
    "LaneReadError",
    "LaneSide",
    "Placed",
    "SideReading",
    "TreeLane",
    "bounded_text",
    "open_tree_lane",
]

_LOCATOR_KINDS: Final = frozenset({"symbol", "line_range", "file"})
# A reason names at most this many entries and then says how many more; the per-hunk and per-side
# facts carry every entry ID. Quoted details are clipped too, so no reason outgrows its model's cap
# however many entries a path records or however long an owner's message is.
NAMED_ENTRIES: Final = 10
DETAIL_LIMIT: Final = 2_000
_FAMILY_RECORDS: Final = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES['family']}/"
_UNRESOLVED: Final[Mapping[str, str]] = {
    "symbol": "the symbol does not resolve uniquely in this blob",
    "line_range": "the recorded line range lies outside this blob",
}


class LaneReadError(ValueError):
    """The comparison's code trees cannot be read or compared, so nothing can be classified."""


@dataclass
class LaneSide:
    """One side as the lane reads it: its code tree's files and its memory tree's index.

    ``index`` is ``None`` when the side's knowledge could not be read at all (``detail`` says why).
    ``problems`` are the knowledge files a partial index could not parse.
    """

    name: LaneSideName
    files: Mapping[str, str]
    index: KnowledgeIndex | None
    detail: str | None
    problems: Mapping[str, str] = field(default_factory=dict)
    _revisions: dict[str, int | None] = field(default_factory=dict)
    _families: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _members: dict[str, tuple[str, ...] | None] = field(default_factory=dict)

    def unavailable(self, path: str) -> str | None:
        """Why this side's knowledge of ``path`` is unknown, or ``None`` when it was read."""

        if self.index is None:
            return bounded_text(self.detail or f"the {self.name} memory tree could not be read")
        sidecar = f"{ONBOARDING_ROOT}/{path}.json"
        problem = self.problems.get(sidecar)
        if problem is not None:
            return f"the {self.name} sidecar {sidecar} does not parse: {bounded_text(problem)}"
        return None

    @property
    def complete(self) -> bool:
        """Whether this side's knowledge was read whole: an index with no file it failed to parse."""

        return self.index is not None and not self.problems

    def entries_at(self, path: str) -> tuple[Entry, ...]:
        if self.index is None:
            return ()
        found = self.index.entries_at_path(path).value
        return (*found.realizations, *found.proofs)

    @property
    def families_complete(self) -> bool:
        """Whether every family record of this side was read (a partial index may have lost one)."""

        return self.index is not None and not any(
            path.startswith(_FAMILY_RECORDS) for path in self.problems
        )

    def revision(self, record_id: str) -> int | None:
        if self.index is None:
            return None
        if record_id not in self._revisions:
            record = self.index.record(record_id).value
            self._revisions[record_id] = None if record is None else record.revision
        return self._revisions[record_id]

    def families_of(self, invariant: str) -> tuple[str, ...]:
        """The families whose record on this side lists ``invariant`` as a member."""

        if self.index is None:
            return ()
        if invariant not in self._families:
            self._families[invariant] = self.index.invariant(invariant).value.families
        return self._families[invariant]

    def family_members(self, family: str) -> tuple[str, ...] | None:
        """The members this side's record of ``family`` lists, or ``None`` when it holds no record."""

        if self.index is None:
            return None
        if family not in self._members:
            known = self.index.family(family).value
            self._members[family] = None if known.record is None else known.members
        return self._members[family]


@dataclass(frozen=True)
class Placed:
    """One entry recorded at the path on one side: the range it supplies there, or why none."""

    entry: Entry
    span: LineRange | None
    reason: LaneRangeReason | None = None
    detail: str | None = None


@dataclass(frozen=True)
class SideReading:
    """One side of one path: its blob, whether its knowledge was read, and every entry placed."""

    side: LaneSide
    blob: str | None
    unavailable: str | None
    placed: tuple[Placed, ...]

    @property
    def supplying(self) -> tuple[Placed, ...]:
        return tuple(one for one in self.placed if one.span is not None)

    @property
    def withholding(self) -> tuple[Placed, ...]:
        return tuple(one for one in self.placed if one.span is None)


@dataclass(frozen=True)
class HunkResult:
    """One hunk and its class, with the entries that link it or the reasons it is unknown."""

    hunk: Hunk
    classification: LaneHunkClass
    links: tuple[tuple[LaneSideName, Placed], ...] = ()
    unknown: tuple[ReviewLaneUnknown, ...] = ()


@dataclass(frozen=True)
class FileResult:
    """One changed path classified: bucket and reason, both sides, hunks and any non-text fact."""

    change: TreeChange
    bucket: LaneBucket
    reason: str
    before: SideReading
    after: SideReading
    hunks: tuple[HunkResult, ...]
    non_text: ReviewLaneNonText | None

    def counts(self) -> ReviewLaneCounts:
        classes = [one.classification for one in self.hunks]
        return ReviewLaneCounts(
            hunks=len(classes),
            linked=classes.count("linked"),
            unexplained=classes.count("unexplained"),
            attribution_unknown=classes.count("attribution_unknown"),
        )


@dataclass
class TreeLane:
    """The code trees B and C and both knowledge sides of one comparison, opened once."""

    code: CodeTrees
    before: LaneSide
    after: LaneSide

    def observe(self) -> TreePaths:
        """The comparison's changed paths, from the landed change inventory (the denominator)."""

        root = str(self.code.repository)
        return tree_difference_observation(
            TreeSide(tree_id=self.code.base_tree, root=root),
            TreeSide(tree_id=self.code.candidate_tree, root=root),
        )

    def side(self, name: LaneSideName) -> LaneSide:
        return self.before if name == "before" else self.after

    def other(self, name: LaneSideName) -> LaneSide:
        return self.after if name == "before" else self.before

    # -- file buckets (no hunk is read) ------------------------------------------------------------

    def bucket(self, path: str) -> tuple[LaneBucket, str, SideReading, SideReading]:
        before, after = self._reading(self.before, path), self._reading(self.after, path)
        bucket, reason = _bucket(before, after)
        return bucket, reason, before, after

    def _reading(self, side: LaneSide, path: str) -> SideReading:
        unavailable = side.unavailable(path)
        blob = side.files.get(path)
        if unavailable is not None:
            return SideReading(side=side, blob=blob, unavailable=unavailable, placed=())
        placed = tuple(self._place(side, entry) for entry in side.entries_at(path))
        return SideReading(side=side, blob=blob, unavailable=None, placed=placed)

    def _place(self, side: LaneSide, entry: Entry) -> Placed:
        anchor: Mapping[str, Any] = entry.document.get("anchor") or {}
        locator: Mapping[str, Any] = anchor.get("locator") or {}
        recorded = str(anchor.get("blob", ""))
        blob = side.files.get(entry.path)
        if blob is None:
            detail = f"the {side.name} code tree holds no regular file at {entry.path}"
            return Placed(entry, None, "path_absent", detail)
        if recorded != blob:
            detail = f"recorded against blob {recorded}; the {side.name} blob is {blob}"
            return Placed(entry, None, "recorded_blob_mismatch", detail)
        kind = locator.get("kind")
        if kind not in _LOCATOR_KINDS or (
            kind == "symbol" and grammars.grammar_of(entry.path) is None
        ):
            detail = f"the locator kind {kind!r} is not resolved in this file"
            return Placed(entry, None, "unsupported_locator", detail)
        try:
            resolved = _placement(self.code, entry.path, locator, blob)
        except (CodeReadError, subprocess.SubprocessError, OSError) as error:
            return Placed(
                entry, None, "unreadable", f"a Git read failed: {bounded_text(str(error))}"
            )
        if resolved is None:
            detail = _UNRESOLVED.get(str(kind), "the locator does not resolve in this blob")
            return Placed(entry, None, "unresolved", detail)
        return Placed(entry, resolved.span)

    # -- one path, whole ---------------------------------------------------------------------------

    def classify(self, change: TreeChange) -> FileResult:
        """Bucket, hunks and non-text fact of one changed path; raises :class:`LaneReadError`."""

        bucket, reason, before, after = self.bucket(change.path)
        try:
            hunks = change_hunks(self.code, change, before.blob, after.blob)
        except CodeReadError as error:
            raise LaneReadError(str(error)) from error
        non_text = None
        if hunks is None or change.mode_change:
            non_text = ReviewLaneNonText(
                content=change.content,
                mode_change=change.mode_change,
                gate=_gate(before, after),
            )
        return FileResult(
            change=change,
            bucket=bucket,
            reason=reason,
            before=before,
            after=after,
            hunks=tuple(_classified(hunk, before, after) for hunk in hunks or ()),
            non_text=non_text,
        )


@contextmanager
def open_tree_lane(trees: ReviewTrees) -> Iterator[TreeLane]:
    """Open the comparison's code trees and both memory indexes; close the indexes on exit.

    Raises :class:`LaneReadError` when a code tree cannot be listed.
    """

    record = trees.record
    code = CodeTrees.open(
        Path(record.code_candidate.repository), record.code_base.tree, record.code_candidate.tree
    )
    try:
        base, candidate = code.base(), code.candidate()
    except CodeReadError as error:
        raise LaneReadError(str(error)) from error
    with ExitStack() as stack:
        before = _side(stack, "before", trees.before, base)
        after = _side(stack, "after", trees.after, candidate)
        yield TreeLane(code=code, before=before, after=after)


def _side(
    stack: ExitStack, name: LaneSideName, knowledge: TreeKnowledge, files: Mapping[str, str]
) -> LaneSide:
    if knowledge.database is None:
        detail = knowledge.wire.detail or f"the {name} memory tree is {knowledge.wire.state}"
        return LaneSide(name=name, files=files, index=None, detail=detail)
    try:
        index = stack.enter_context(KnowledgeIndex(knowledge.database))
    except (IndexMismatchError, apsw.Error, OSError) as error:
        detail = f"the {name} memory tree's index cannot be opened: {error}"
        return LaneSide(name=name, files=files, index=None, detail=detail)
    return LaneSide(
        name=name,
        files=files,
        index=index,
        detail=None,
        problems=dict(index.state.problems),
    )


def _placement(
    code: CodeTrees, path: str, locator: Mapping[str, Any], blob: str
) -> Resolved | None:
    """The locator's range in ``blob``, recorded at that very blob, through the shared placements.

    The answer is a function of the blob, the locator and the extractor -- the placement cache's own
    key, with the recorded blob equal to the blob -- so the cards' reads and this one share answers.
    """

    key = observation_key(blob, path, locator, blob)
    remembered = PLACEMENTS.get(key)
    if remembered is not None:
        return remembered[0]
    resolved = code.resolve(path, locator, blob, blob)
    PLACEMENTS.put(key, resolved)
    return resolved


# -- buckets ---------------------------------------------------------------------------------------


def _bucket(before: SideReading, after: SideReading) -> tuple[LaneBucket, str]:
    sides = (before, after)
    supplying = [one for one in sides if one.supplying]
    if supplying:
        return "attributed", _attributed_reason(supplying, sides)
    if all(one.unavailable is None and not one.placed for one in sides):
        return "unexplained", (
            "no realization or proof entry is recorded for this path in either memory tree, and "
            "both memory trees were read"
        )
    return "attribution_unknown", "; ".join(_unknown_facts(sides))


def bounded_text(text: str, limit: int = DETAIL_LIMIT) -> str:
    """``text``, or its first ``limit`` characters marked as clipped."""

    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _named(items: list[str]) -> str:
    """At most :data:`NAMED_ENTRIES` items, then how many more there are."""

    shown = ", ".join(items[:NAMED_ENTRIES])
    more = len(items) - NAMED_ENTRIES
    return shown if more <= 0 else f"{shown} and {more} more"


def _attributed_reason(supplying: list[SideReading], sides: tuple[SideReading, ...]) -> str:
    named = "; ".join(
        f"on the {one.side.name} side {_named([placed.entry.id for placed in one.supplying])}"
        for one in supplying
    )
    reason = f"a recorded entry supplies a range at its recorded blob ({named})"
    unread = [one.side.name for one in sides if one.unavailable is not None]
    if unread:
        reason += (
            f"; the {' and '.join(unread)} knowledge was not read, so its mappings are unknown"
        )
    return reason


def _unknown_facts(sides: tuple[SideReading, ...]) -> list[str]:
    facts: list[str] = []
    for one in sides:
        if one.unavailable is not None:
            facts.append(f"the {one.side.name} knowledge is unavailable: {one.unavailable}")
        elif one.withholding:
            facts.append(f"on the {one.side.name} side {_withheld(one)}")
    return facts


def _withheld(reading: SideReading) -> str:
    withheld = reading.withholding
    listed = _named([f"{one.entry.id} ({one.reason})" for one in withheld])
    counted = (
        "1 entry recorded here supplies"
        if len(withheld) == 1
        else (f"{len(withheld)} entries recorded here supply")
    )
    return f"{counted} no range: {listed}"


# -- hunks -----------------------------------------------------------------------------------------


def _changes_lines(hunk: Hunk, side: LaneSideName) -> bool:
    return (hunk.old_count if side == "before" else hunk.new_count) > 0


def _intersects(hunk: Hunk, side: LaneSideName, span: LineRange) -> bool:
    """Whether the hunk's changed lines on ``side`` meet ``span``; ``side`` is one it changes lines on,
    where the worklist's hit test is exactly the intersection of those lines with the range."""

    return hits_old(hunk, span) if side == "before" else hits_new(hunk, span)


def _links(hunk: Hunk, reading: SideReading) -> list[tuple[LaneSideName, Placed]]:
    """The entries of one side (where the hunk changes lines) whose supplied range it intersects."""

    name = reading.side.name
    return [
        (name, placed)
        for placed in reading.supplying
        if placed.span is not None and _intersects(hunk, name, placed.span)
    ]


def _classified(hunk: Hunk, before: SideReading, after: SideReading) -> HunkResult:
    changed = [one for one in (before, after) if _changes_lines(hunk, one.side.name)]
    links = tuple(link for one in changed for link in _links(hunk, one))
    if links:
        return HunkResult(hunk=hunk, classification="linked", links=links)
    unknown = tuple(reason for one in changed if (reason := _unknown(one)) is not None)
    if unknown:
        return HunkResult(hunk=hunk, classification="attribution_unknown", unknown=unknown)
    return HunkResult(hunk=hunk, classification="unexplained")


def _unknown(reading: SideReading) -> ReviewLaneUnknown | None:
    name = reading.side.name
    if reading.unavailable is not None:
        return ReviewLaneUnknown(
            side=name, reason="knowledge_unavailable", detail=reading.unavailable
        )
    withheld = reading.withholding
    if not withheld:
        return None
    return ReviewLaneUnknown(
        side=name,
        reason="range_not_supplied",
        detail=_withheld(reading),
        entries=tuple(one.entry.id for one in withheld),
    )


def _gate(before: SideReading, after: SideReading) -> LaneGateLinkage:
    """The gate's linkage of a non-text change: a ``file``-locator entry on either side links it.

    The gate computes no linkage at all over a knowledge side it cannot read whole -- its run is then
    ``incomplete`` and raises no item -- so a side that is unread, or read with any file it failed to
    parse, makes the linkage ``unknown``: never a gate result the gate could not have produced.
    """

    if not (before.side.complete and after.side.complete):
        return "unknown"
    kinds = (
        str((one.entry.document.get("anchor") or {}).get("locator", {}).get("kind", ""))
        for reading in (before, after)
        for one in reading.placed
    )
    return "linked" if non_text_linked(kinds) else "unexplained"
