"""Index-only recorded meaning facts shared by the review outline and detail badges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from agents_remember.application.review_lane_classification import bounded_text
from agents_remember.memory.knowledge_index import KnowledgeIndex, Record
from agents_remember.models.knowledge.review_change_kinds import ChangeFact
from agents_remember.models.knowledge.review_outline import OutlineChange
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT, RECORD_DIRECTORIES
from agents_remember.models.knowledge_files.ids import RecordKind

_RETIRED = "retired"
_WORDING = ("statement", "applicability", "conditions", "exclusions")


class RecordSide(Protocol):
    """The index and read state needed to decide recorded meaning; no source read."""

    @property
    def name(self) -> str: ...

    @property
    def index(self) -> KnowledgeIndex | None: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def problems(self) -> Mapping[str, str]: ...


@dataclass(frozen=True)
class Known:
    """One side's answer about one record: live, absent (``record`` is ``None``), or unknown."""

    record: Record | None = None
    retired: bool = False
    unknown: str | None = None


@dataclass
class Fact:
    """One fact as it is decided: established (with why), unknown (with why), or neither."""

    change: OutlineChange = "unchanged"
    membership: Literal["both", "joined", "left"] = "both"
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
class RecordChanges:
    """One read's recorded facts, with each record and membership list read once."""

    records: dict[tuple[str, str], Record | None] = field(default_factory=dict)
    failed: dict[tuple[str, RecordKind], list[str]] = field(default_factory=dict)
    members: dict[tuple[str, str], frozenset[str]] = field(default_factory=dict)

    def record(self, side: RecordSide, record_id: str, kind: RecordKind) -> Known:
        if side.index is None:
            return Known(unknown=side.detail or f"the {side.name} memory tree could not be read")
        key = (side.name, record_id)
        if key not in self.records:
            self.records[key] = side.index.record(record_id).value
        record = self.records[key]
        if record is not None:
            retired = record.status == _RETIRED
            return Known(record=None if retired else record, retired=retired)
        problem_key = (side.name, kind)
        if problem_key not in self.failed:
            self.failed[problem_key] = problems(side, kind)
        failed = self.failed[problem_key]
        if failed:
            return Known(
                unknown=bounded_text(
                    f"the {side.name} memory tree has {kind} records that do not parse: "
                    f"{', '.join(failed[:3])}"
                )
            )
        return Known()

    def guarantee(
        self, sides: tuple[RecordSide, RecordSide], family: str
    ) -> tuple[OutlineChange, str]:
        before, after = (self.record(side, family, "family") for side in sides)
        unread = before.unknown or after.unknown
        if unread is not None:
            return "unknown", unread
        if before.record is None and after.record is None:
            return "unknown", f"neither memory tree holds a live record of {family}"
        if before.record is None or after.record is None:
            held = "after" if before.record is None else "before"
            return (
                "added" if before.record is None else "removed",
                f"{family} is live on the {held} side only",
            )
        if before.record.document.get("guarantee") != after.record.document.get("guarantee"):
            return "changed", (
                f"the guarantee text of {family} differs (revision {before.record.revision} → "
                f"{after.record.revision})"
            )
        return "unchanged", f"the guarantee text of {family} is the same on both sides"

    def intent(self, sides: tuple[RecordSide, RecordSide], invariant: str) -> Fact:
        fact = Fact()
        before, after = (self.record(side, invariant, "invariant") for side in sides)
        unread = before.unknown or after.unknown
        if unread is not None:
            fact.change = "unknown"
            fact.unknown(unread)
            return fact
        if before.record is None or after.record is None:
            presence(fact, invariant, before, after)
        elif before.record.revision != after.record.revision:
            fact.change = "changed"
            fact.establish(
                f"{invariant} revision {before.record.revision} → {after.record.revision}"
            )
        elif wording(before.record) != wording(after.record):
            fact.change = "changed"
            fact.establish(
                f"{invariant} keeps revision {after.record.revision}, but its text differs"
            )
            fact.text_differs = True
        return fact

    def membership(self, sides: tuple[RecordSide, RecordSide], family: str, invariant: str) -> Fact:
        fact = Fact()
        listed: list[bool] = []
        for side in sides:
            known = self.record(side, family, "family")
            if known.unknown is not None or side.index is None:
                fact.unknown(known.unknown or f"the {side.name} memory tree could not be read")
                return fact
            key = (side.name, family)
            if key not in self.members:
                self.members[key] = frozenset(
                    () if known.record is None else known.record.document.get("members", ())
                )
            members = self.members[key]
            listed.append(invariant in members)
        if listed[0] != listed[1]:
            joined = "joined" if listed[1] else "left"
            fact.membership = joined
            fact.establish(f"{invariant} {joined} {family}")
        return fact


def presence(fact: Fact, invariant: str, before: Known, after: Known) -> None:
    """Intent of an invariant at most one side holds live: added, removed (or retired), or unknown."""

    if before.record is None and after.record is None:
        fact.change = "unknown"
        fact.unknown(f"neither memory tree holds a live record of {invariant}")
        return
    change = "added" if before.record is None else "removed"
    fact.change = change
    retired = " (retired)" if before.retired or after.retired else ""
    fact.establish(f"{invariant} is {change}{retired}")


def wording(record: Record) -> tuple[object, ...]:
    return tuple(record.document.get(key) for key in _WORDING)


def problems(side: RecordSide, kind: RecordKind) -> list[str]:
    prefix = f"{KNOWLEDGE_ROOT}/{RECORD_DIRECTORIES[kind]}/"
    return sorted(path for path in side.problems if path.startswith(prefix))
