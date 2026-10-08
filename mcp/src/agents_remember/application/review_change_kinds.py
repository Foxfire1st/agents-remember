"""The change-kind facts of a tree comparison's family context, for the members a page returned.

MIK-R33 (adopting ICR-R32) badges every family occurrence and member occurrence of the reviewer's
tree with the kind of recorded change that brings it into review. The facts are computed here, on
the server, for the members the roster page returned and for nothing else, and they travel on each
family context entry (``change_kinds``); the browser orders and traverses by them and never
recomputes one. Each fact is read from the comparison itself:

* **intent** -- the invariant record's ``revision`` in the memory base tree against the memory
  candidate tree, through each side's derived index; a record only one side holds live (added,
  removed, or ``retired``) establishes it. One revision whose authored text (statement,
  applicability, conditions, exclusions) differs byte for byte between the sides establishes it too,
  marked ``text_differs``: changed text is never called unchanged. A family whose ``guarantee`` text
  differs, or that one side does not hold live, shows ``intent`` on its own row.
* **implementation** -- (a) an entry of the member added, retired or re-anchored (ICR-R32's "moved")
  between the sides. An entry whose anchor differs is re-anchored unless it is the writer's
  mechanical carry-forward (:func:`carried_mechanically`, definition 7's exception for a ``carried``
  base entry, decided by the worklist classifier); definition 7's other exceptions exist for the
  worklist's own covering items, which the reviewer does not have. Or (b) a hunk the lane links to
  an entry of the member (:meth:`TreeLane.classify`, the one classification owner). A proof entry
  counts, and marks the occurrence ``test``. A changed non-text file (binary, symlink, submodule or
  mode) has no hunk: there a ``file`` entry of the member establishes the fact, the gate's own
  predicate (MIK-R08 definition 8, :func:`non_text_linked`). Only a changed path where the member
  records an entry is classified; a linked file in the change inventory establishes nothing by
  itself. An entry of the member that supplies no range on a side where the file changes lines (or
  whose side is unread) adds the ``unknown`` mark unconditionally, even beside an established
  ``implementation`` (ICR-R32 rule 1); a non-file entry at a non-text content change leaves the fact
  ``unknown`` unless it is established. Every unknown says why, in the lane's own per-entry terms.
* **membership** -- whether this family's record lists the invariant on each side.

Nothing is inferred: a side that cannot be read makes the facts it would decide ``unknown``, never
``not_established``, so no occurrence reads ``unchanged`` on knowledge nobody read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

import apsw
from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.classify import (
    Classifier,
    EntryClass,
    carried_mechanically,
)
from agents_remember.application.knowledge_worklist.code import CodeReadError, Hunk
from agents_remember.application.knowledge_worklist.compute import non_text_linked
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.review_lane_classification import (
    FileResult,
    LaneReadError,
    LaneSide,
    Placed,
    SideReading,
    TreeLane,
    bounded_text,
    changes_lines,
    open_tree_lane,
)
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.memory.knowledge.tree_observation import TreeChange
from agents_remember.memory.knowledge_index import Entry, Record
from agents_remember.memory.knowledge_index.build import IndexedEntry, ParsedTree
from agents_remember.models.knowledge.review_change_kinds import (
    UNKNOWN_FACTS,
    ChangeEvidence,
    ChangeFact,
    ChangeFacts,
    GuaranteeChange,
    ReviewFamilyChanges,
    ReviewMemberChange,
)
from agents_remember.models.knowledge.review_family_context import (
    ReviewFamilyContext,
    ReviewFamilyContextEntry,
    ReviewFamilyMember,
)
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    ONBOARDING_ROOT,
    RECORD_DIRECTORIES,
)
from agents_remember.models.knowledge_files.ids import RecordKind
from agents_remember.models.knowledge_files.sidecars import ProofEntry, RealizationEntry

__all__ = ["with_change_kinds"]

_RETIRED: Final = "retired"
_UNREADABLE: Final = "the comparison's trees cannot be read or compared: {error}"
# The authored text of an invariant record: a byte difference in any of these is a text change.
_WORDING: Final = ("statement", "applicability", "conditions", "exclusions")
# The lane's per-entry reasons (MIK-R32), in the words a reviewer reads beside an unknown badge.
_RANGE_REASONS: Final = {
    "path_absent": "this side holds no file at the path",
    "unresolved": "its locator does not resolve in this side's blob",
    "unsupported_locator": "its locator kind is not resolved in this file",
    "unreadable": "a Git read failed",
}


def with_change_kinds(
    context: ReviewFamilyContext, trees: ReviewTrees | None
) -> ReviewFamilyContext:
    """``context`` with each family entry carrying the change facts of its returned members.

    A review with no tree comparison gets its context back exactly as composed.
    """

    if trees is None or not context.entries:
        return context
    try:
        with open_tree_lane(trees) as lane:
            reader = _ChangeKinds(lane)
            entries = tuple(_with(entry, reader.family(entry)) for entry in context.entries)
    except (LaneReadError, apsw.Error) as error:
        detail = bounded_text(_UNREADABLE.format(error=error))
        entries = tuple(_with(entry, _unread(entry, detail)) for entry in context.entries)
    return ReviewFamilyContext(**{**dict(context), "entries": entries})


def _with(entry: ReviewFamilyContextEntry, kinds: ReviewFamilyChanges) -> ReviewFamilyContextEntry:
    return ReviewFamilyContextEntry(**{**dict(entry), "change_kinds": kinds})


def _returned(entry: ReviewFamilyContextEntry) -> dict[str, ReviewFamilyMember]:
    """Each returned member occurrence once, by its membership identity (the same on both sides)."""

    returned: dict[str, ReviewFamilyMember] = {}
    for side in (entry.before, entry.after):
        for member in side.members:
            returned.setdefault(member.member_id, member)
    return returned


def _unread(entry: ReviewFamilyContextEntry, detail: str) -> ReviewFamilyChanges:
    return ReviewFamilyChanges(
        guarantee="unknown",
        guarantee_detail=detail,
        members=tuple(
            ReviewMemberChange.of(
                member_id,
                None,
                UNKNOWN_FACTS,
                ChangeEvidence(unknown=(detail,), membership=(detail,)),
            )
            for member_id in _returned(entry)
        ),
        detail=detail,
    )


@dataclass(frozen=True)
class _Known:
    """One side's answer about one record: live, absent (``record`` is ``None``), or unknown."""

    record: Record | None = None
    retired: bool = False
    unknown: str | None = None


@dataclass
class _Fact:
    """One fact as it is decided: established (with why), unknown (with why), or neither."""

    established: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)
    # Entries in a changed file whose range cannot be resolved there: unknown whatever else holds.
    # Each reason keeps the entry it is about (``None`` for an unread side).
    unresolved: dict[str, str | None] = field(default_factory=dict)
    # The entries that established the fact: their unresolved reasons are listed last.
    by: set[str] = field(default_factory=set)
    proof: bool = False
    text_differs: bool = False

    def establish(self, why: str, *, proof: bool = False, entry: str | None = None) -> None:
        if why not in self.established:
            self.established.append(why)
        if entry is not None:
            self.by.add(entry)
        self.proof = self.proof or proof

    def unknown(self, why: str) -> None:
        if why not in self.open:
            self.open.append(why)

    def unresolvable(self, why: str, *, entry: str | None = None) -> None:
        self.unresolved.setdefault(why, entry)

    @property
    def state(self) -> ChangeFact:
        if self.established:
            return "established"
        return "unknown" if self.open or self.unresolved else "not_established"

    @property
    def reasons(self) -> list[str]:
        """Why the fact is unknown, or why an established one still carries an unresolved range."""

        # An entry that established nothing is the more telling reason, so it comes first.
        unresolved = sorted(self.unresolved, key=lambda why: self.unresolved[why] in self.by)
        if self.state == "unknown":
            return [*unresolved, *self.open]
        return unresolved if self.state == "established" else []


@dataclass
class _ChangeKinds:
    """The facts of one comparison, over the lane's code trees and both memory indexes."""

    lane: TreeLane
    _changes: dict[str, TreeChange] | None = None
    _inventory_problem: str | None = None
    _files: dict[str, FileResult | str] = field(default_factory=dict)

    @property
    def sides(self) -> tuple[LaneSide, LaneSide]:
        return (self.lane.before, self.lane.after)

    # -- one family ----------------------------------------------------------------------------

    def family(self, entry: ReviewFamilyContextEntry) -> ReviewFamilyChanges:
        returned = _returned(entry)
        family = self._text_id(entry.family_id)
        if family is None:
            return _unread(entry, "neither memory tree's index names this family identity")
        guarantee, detail = self._guarantee(family)
        return ReviewFamilyChanges(
            family=family,
            guarantee=guarantee,
            guarantee_detail=detail,
            members_total=self._members_total(family),
            members=tuple(
                self._member(family, member_id, member) for member_id, member in returned.items()
            ),
        )

    def _guarantee(self, family: str) -> tuple[GuaranteeChange, str]:
        before, after = (self._record(side, family, "family") for side in self.sides)
        unread = before.unknown or after.unknown
        if unread is not None:
            return "unknown", unread
        if before.record is None and after.record is None:
            return "unknown", f"neither memory tree holds a live record of {family}"
        if before.record is None or after.record is None:
            held = "after" if before.record is None else "before"
            return "intent", f"{family} is live on the {held} side only"
        if before.record.document.get("guarantee") != after.record.document.get("guarantee"):
            return "intent", (
                f"the guarantee text of {family} differs (revision {before.record.revision} → "
                f"{after.record.revision})"
            )
        return "unchanged", f"the guarantee text of {family} is the same on both sides"

    def _members_total(self, family: str) -> int | None:
        """The deduplicated union of the family's live members on either side, or ``None``."""

        members: set[str] = set()
        for side in self.sides:
            if side.index is None or _own_problem(side, "family", family):
                return None
            record = side.index.record(family).value
            if record is None or record.status == _RETIRED:
                continue
            for invariant in side.index.family(family).value.members:
                if _own_problem(side, "invariant", invariant):
                    return None
                listed = side.index.record(invariant).value
                if listed is not None and listed.status != _RETIRED:
                    members.add(invariant)
        return len(members)

    # -- one member occurrence -----------------------------------------------------------------

    def _member(
        self, family: str, member_id: str, member: ReviewFamilyMember
    ) -> ReviewMemberChange:
        invariant = self._invariant_of(member)
        if invariant is None:
            detail = "neither memory tree's index names this member's invariant"
            return ReviewMemberChange.of(
                member_id,
                None,
                UNKNOWN_FACTS,
                ChangeEvidence(unknown=(detail,), membership=(detail,)),
            )
        intent = self._intent(invariant)
        implementation = self._implementation(invariant)
        membership = self._membership(family, invariant)
        facts = ChangeFacts(
            intent.state,
            implementation.state,
            membership.state,
            proof=implementation.proof,
            text_differs=intent.text_differs,
            range_unresolved=bool(implementation.unresolved),
        )
        evidence = ChangeEvidence(
            established=(
                *intent.established[:2],
                *implementation.established[:4],
                *membership.established[:2],
            ),
            unknown=(*intent.reasons[:1], *implementation.reasons[:5]),
            membership=tuple(membership.reasons[:1]),
        )
        return ReviewMemberChange.of(
            member_id,
            invariant,
            facts,
            evidence,
            authored_position=self._position(family, invariant),
        )

    def _position(self, family: str, invariant: str) -> int | None:
        """The invariant's index in the family record's authored ``members`` (after, else before)."""

        for side in (self.lane.after, self.lane.before):
            record = self._record(side, family, "family").record
            members = [] if record is None else list(record.document.get("members") or [])
            if invariant in members:
                return members.index(invariant)
        return None

    def _invariant_of(self, member: ReviewFamilyMember) -> str | None:
        if member.invariant_id is not None:
            found = self._text_id(member.invariant_id)
            if found is not None:
                return found
        membership = self._text_id(member.member_id)
        return None if membership is None else membership.split("/", 1)[-1]

    def _intent(self, invariant: str) -> _Fact:
        fact = _Fact()
        before, after = (self._record(side, invariant, "invariant") for side in self.sides)
        unread = before.unknown or after.unknown
        if unread is not None:
            fact.unknown(unread)
            return fact
        if before.record is None or after.record is None:
            _presence(fact, invariant, before, after)
        elif before.record.revision != after.record.revision:
            fact.establish(
                f"{invariant} revision {before.record.revision} → {after.record.revision}"
            )
        elif _wording(before.record) != _wording(after.record):
            fact.establish(
                f"{invariant} keeps revision {after.record.revision}, but its text differs"
            )
            fact.text_differs = True
        return fact

    def _membership(self, family: str, invariant: str) -> _Fact:
        fact = _Fact()
        listed: list[bool] = []
        for side in self.sides:
            known = self._record(side, family, "family")
            if known.unknown is not None or side.index is None:
                fact.unknown(known.unknown or f"the {side.name} memory tree could not be read")
                return fact
            members = () if known.record is None else side.index.family(family).value.members
            listed.append(invariant in members)
        if listed[0] != listed[1]:
            joined = "joined" if listed[1] else "left"
            fact.establish(f"{invariant} {joined} {family}")
        return fact

    def _implementation(self, invariant: str) -> _Fact:
        fact = _Fact()
        entries = [self._entries(side, invariant) for side in self.sides]
        before, after = entries
        if isinstance(before, str) or isinstance(after, str):
            fact.unknown(before if isinstance(before, str) else str(after))
        else:
            self._entry_changes(fact, before, after)
        paths = sorted(
            {entry.path for side in entries if not isinstance(side, str) for entry in side.values()}
        )
        for path in paths:
            self._hunks(fact, invariant, path)
        return fact

    def _entry_changes(
        self, fact: _Fact, before: Mapping[str, Entry], after: Mapping[str, Entry]
    ) -> None:
        """(a): entries added, retired or re-anchored between the sides (definition 7)."""

        for entry_id in sorted(after.keys() - before.keys()):
            entry = after[entry_id]
            fact.establish(
                f"{entry.kind} {entry_id} added", proof=entry.kind == "proof", entry=entry_id
            )
        for entry_id in sorted(before.keys() - after.keys()):
            entry = before[entry_id]
            fact.establish(
                f"{entry.kind} {entry_id} retired", proof=entry.kind == "proof", entry=entry_id
            )
        for entry_id in sorted(before.keys() & after.keys()):
            old, new = before[entry_id].document["anchor"], after[entry_id].document["anchor"]
            if old == new:
                continue
            entry = before[entry_id]
            entry_class = self._base_class(entry)
            if entry_class is None:
                fact.unknown(f"whether {entry_id} was re-anchored could not be decided")
            elif not carried_mechanically(old, new, entry_class):
                # A repair of an entry already stale at the base reads apart from a move this
                # leaf's code caused.
                stale = " (stale at base)" if entry_class == "stale_at_base" else ""
                fact.establish(
                    f"{entry.kind} {entry_id} re-anchored{stale}",
                    proof=entry.kind == "proof",
                    entry=entry_id,
                )

    def _hunks(self, fact: _Fact, invariant: str, path: str) -> None:
        """(b): a hunk the lane links to an entry of ``invariant`` at the changed ``path``."""

        change = self._change(path)
        if change is None:
            return
        if isinstance(change, str):
            fact.unknown(change)
            return
        result = self._file(change)
        if isinstance(result, str):
            fact.unknown(result)
            return
        linked = _hunk_linked(fact, invariant, path, result)
        if result.non_text is not None:
            linked = _file_covered(fact, invariant, path, result) or linked
        for reading in (result.before, result.after):
            _unresolved(fact, invariant, path, result, reading)
        if not linked:
            _unintersectable(fact, invariant, path, result)

    # -- reads -----------------------------------------------------------------------------------

    def _text_id(self, projected: str) -> str | None:
        for side in self.sides:
            if side.index is not None:
                found = side.index.text_id(projected)
                if found is not None:
                    return found
        return None

    def _record(self, side: LaneSide, record_id: str, kind: RecordKind) -> _Known:
        if side.index is None:
            return _Known(unknown=side.detail or f"the {side.name} memory tree could not be read")
        record = side.index.record(record_id).value
        if record is not None:
            retired = record.status == _RETIRED
            return _Known(record=None if retired else record, retired=retired)
        failed = _problems(side, kind)
        if failed:
            return _Known(
                unknown=bounded_text(
                    f"the {side.name} memory tree has {kind} records that do not parse: "
                    f"{', '.join(failed[:3])}"
                )
            )
        return _Known()

    def _entries(self, side: LaneSide, invariant: str) -> dict[str, Entry] | str:
        """The side's entries of ``invariant`` by ID, or why they are not all known."""

        if side.index is None:
            return side.detail or f"the {side.name} memory tree could not be read"
        onboarding = sorted(
            path for path in side.problems if path.startswith(f"{ONBOARDING_ROOT}/")
        )
        if onboarding:
            return bounded_text(
                f"the {side.name} memory tree has sidecars that do not parse, so the entries of "
                f"{invariant} there are not all known: {', '.join(onboarding[:3])}"
            )
        found = side.index.invariant(invariant).value
        return {entry.id: entry for entry in (*found.realizations, *found.proofs)}

    def _change(self, path: str) -> TreeChange | str | None:
        """The changed path's inventory entry; ``None`` when unchanged; why, when unmeasured."""

        if self._changes is None:
            observed = self.lane.observe()
            if not observed.available:
                self._inventory_problem = bounded_text(
                    f"the change inventory could not be read: {observed.detail}"
                )
            self._changes = {change.path: change for change in observed.entries}
        if self._inventory_problem is not None:
            return self._inventory_problem
        return self._changes.get(path)

    def _file(self, change: TreeChange) -> FileResult | str:
        if change.path not in self._files:
            try:
                self._files[change.path] = self.lane.classify(change)
            except LaneReadError as error:
                self._files[change.path] = bounded_text(
                    f"{change.path} could not be classified: {error}"
                )
        return self._files[change.path]

    def _base_class(self, entry: Entry) -> EntryClass | None:
        """The worklist classifier's class of one base entry (definition 4), for definition 7.

        The classifier reads the base entry through a knowledge side that holds that entry alone;
        rename inference is not needed, because a renamed path is absent at C and so already
        ``moved_or_absent``.
        """

        index = self.lane.before.index
        try:
            indexed = _indexed(entry)
        except ValidationError:
            return None
        if index is None:
            return None
        side = KnowledgeSide(
            label="K_B",
            key=index.state.key,
            converted=True,
            parsed=ParsedTree(entries={entry.id: indexed}),
            files={},
        )
        try:
            return Classifier(self.lane.code, side, {}).classify(entry.id).entry_class
        except CodeReadError:
            return None


def _presence(fact: _Fact, invariant: str, before: _Known, after: _Known) -> None:
    """Intent of an invariant at most one side holds live: added, removed (or retired), or unknown."""

    if before.record is None and after.record is None:
        fact.unknown(f"neither memory tree holds a live record of {invariant}")
        return
    change = "added" if before.record is None else "removed"
    retired = " (retired)" if before.retired or after.retired else ""
    fact.establish(f"{invariant} is {change}{retired}")


def _wording(record: Record) -> tuple[object, ...]:
    return tuple(record.document.get(key) for key in _WORDING)


def _locator_kind(placed: Placed) -> str:
    anchor = placed.entry.document.get("anchor") or {}
    return str((anchor.get("locator") or {}).get("kind", ""))


def _hunk_linked(fact: _Fact, invariant: str, path: str, result: FileResult) -> bool:
    """(b) for one member: a hunk the lane links to one of its entries at ``path``."""

    linked = False
    for classified in result.hunks:
        for side, placed in classified.links:
            if placed.entry.invariant == invariant:
                linked = True
                fact.establish(
                    f"{_hunk_text(path, classified.hunk)} intersects {placed.entry.kind} "
                    f"{placed.entry.id} on the {side} side",
                    proof=placed.entry.kind == "proof",
                    entry=placed.entry.id,
                )
    return linked


def _file_covered(fact: _Fact, invariant: str, path: str, result: FileResult) -> bool:
    """Definition 8 for one member: a ``file`` entry of it covers the changed non-text ``path``."""

    covered = False
    for reading in (result.before, result.after):
        for placed in reading.placed:
            if placed.entry.invariant == invariant and non_text_linked([_locator_kind(placed)]):
                covered = True
                fact.establish(
                    f"the non-text change of {path} is covered by {placed.entry.kind} "
                    f"{placed.entry.id}, a file entry on the {reading.side.name} side",
                    proof=placed.entry.kind == "proof",
                    entry=placed.entry.id,
                )
    return covered


def _withheld(placed: Placed, reading: SideReading, path: str) -> str:
    """The lane's reason an entry supplies no range on one side, in a reviewer's words."""

    name = reading.side.name
    head = f"{placed.entry.id} supplies no range on the {name} side of {path}"
    if placed.reason == "recorded_blob_mismatch":
        recorded = str((placed.entry.document.get("anchor") or {}).get("blob", ""))
        return (
            f"{head}: it is recorded at blob {recorded[:10]}, but the {name} blob is "
            f"{(reading.blob or '')[:10]} (recorded_blob_mismatch: until the entry is re-recorded "
            "at this blob, its range here is not known)"
        )
    words = _RANGE_REASONS.get(str(placed.reason), placed.detail or "no range is supplied")
    return f"{head}: {words} ({placed.reason})"


def _non_text_content(result: FileResult) -> bool:
    return not result.hunks and result.before.blob != result.after.blob


def _unresolved(
    fact: _Fact, invariant: str, path: str, result: FileResult, reading: SideReading
) -> None:
    """ICR-R32 rule 1: an entry of the member on a side where the changed file changes lines, whose
    range cannot be resolved there (or whose side is unread), is unknown -- unconditionally."""

    name = reading.side.name
    if not (
        _non_text_content(result) or any(changes_lines(one.hunk, name) for one in result.hunks)
    ):
        return
    if reading.unavailable is not None:
        fact.unresolvable(f"the {name} knowledge of {path} is unavailable: {reading.unavailable}")
        return
    for placed in reading.placed:
        if placed.entry.invariant == invariant and placed.span is None:
            fact.unresolvable(_withheld(placed, reading, path), entry=placed.entry.id)


def _unintersectable(fact: _Fact, invariant: str, path: str, result: FileResult) -> None:
    """A resolved non-file entry at a non-text content change: no hunk to meet, so unknown."""

    if not _non_text_content(result):
        return
    for reading in (result.before, result.after):
        for placed in reading.placed:
            if placed.entry.invariant == invariant and placed.span is not None:
                fact.unknown(
                    f"{path} changed as a non-text file, which has no hunk; {placed.entry.id} is "
                    "not a file entry, so whether the change meets it is unknown"
                )


def _own_problem(side: LaneSide, kind: RecordKind, record_id: str) -> bool:
    """Whether the file that fails to parse is this record's own (``<ID>-<slug>.json``)."""

    return any(
        path.rsplit("/", 1)[-1].startswith((f"{record_id}-", f"{record_id}."))
        for path in _problems(side, kind)
    )


def _problems(side: LaneSide, kind: RecordKind) -> list[str]:
    prefix = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES[kind]}/"
    return sorted(path for path in side.problems if path.startswith(prefix))


def _indexed(entry: Entry) -> IndexedEntry:
    """The index's entry as the parsed model (its anchor omits the path, as in its sidecar)."""

    document = dict(entry.document)
    anchor = document.get("anchor") or {}
    document["anchor"] = {key: value for key, value in anchor.items() if key != "path"}
    model = ProofEntry if entry.kind == "proof" else RealizationEntry
    return IndexedEntry(
        path=entry.path, sidecar=entry.sidecar, entry=model.model_validate(document)
    )


def _hunk_text(path: str, hunk: Hunk) -> str:
    return (
        f"the hunk of {path} at before {hunk.old_start},{hunk.old_count} "
        f"after {hunk.new_start},{hunk.new_count}"
    )
