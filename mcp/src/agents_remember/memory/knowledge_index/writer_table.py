"""How each writer did: one table over the revisions of one memory tree.

A writer is counted as its stamp names it: role, harness and model
(:meth:`.WriterStamp.writer_key`). For every writer the table counts revisions:

* **written** -- the revisions whose stamp names this writer;
* **reviewed** -- of them, those whose state (:class:`.RevisionStates`) is reviewed or
  consolidated; the state of a revision of a retired record is read as if it were live, since the
  review is a fact about the revision;
* **later changed by another writer** -- of them, those of which a later revision of the same
  record carries the stamp of another seat;
* **later changed, writer unknown** -- of them, those that a later revision changed where a seat
  on either side is unknown, so whether another writer changed it cannot be said; such a revision
  is never counted as changed by another writer (nothing is guessed);
* **retired** -- of them, the revision that was live when its record was retired. Retiring a record
  changes only its ``status``, which is no meaning field, so the revision number does not rise: the
  current revision of a retired record is the one that was live at retirement.

The revisions of a record are its current revision (stamped by the record's ``writer``), every
revision a history file of the tree names in ``written`` with its stamp, every revision a history
row names as the one it read, and every revision below the current one that no file names. A
revision without a stamp existed, since a number rises by one at a time, but its writer is unknown
(it was written before stamps existed): it counts as written by the writer ``unknown``. Revisions no file names are taken as runs of consecutive numbers and counted,
not visited one by one, so the work is linear in the tree's files and never in a revision number.

The table is computed in two steps. :func:`named_revisions` runs when the index is built: it reads
every named revision's stamp and state off the history files in one pass over their ``written``
lists, and the index stores one row per named revision. :func:`writer_table` runs on a read: it
counts those rows with each record's current revision, so a read opens no history file.

A seat is unknown when the revision has no stamp or its stamp's ``seat`` reads ``unknown`` (the
stamps the conversion gave old records read ``unknown`` throughout).

Not counted yet: a Consolidator's columns (kept, corrected, merged, retired or left unresolved by
it). They come with the Consolidator role; until it exists no revision has them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final

from agents_remember.models.knowledge_files.record_state import RevisionStates
from agents_remember.models.knowledge_files.records import KnowledgeRecord
from agents_remember.models.knowledge_files.writer_stamp import UNKNOWN, WriterStamp

WriterKey = tuple[str, str, str]
UNKNOWN_WRITER: Final[WriterKey] = (UNKNOWN, UNKNOWN, UNKNOWN)
# The columns of one writer's row, in the order the answer and the records file give them.
COLUMNS: Final = (
    "written",
    "reviewed",
    "laterChangedByAnother",
    "laterChangedWriterUnknown",
    "retired",
)
_REVIEWED_STATES: Final = frozenset({"reviewed", "consolidated"})


@dataclass(frozen=True)
class RevisionSpan:
    """Consecutive revisions ``first``..``last`` of one record that share every count."""

    record: str
    first: int
    last: int

    @property
    def size(self) -> int:
        return self.last - self.first + 1

    def label(self) -> str:
        """``INV-x@3``, or ``INV-x@1..2`` for a run of revisions no file names."""

        if self.first == self.last:
            return f"{self.record}@{self.first}"
        return f"{self.record}@{self.first}..{self.last}"


@dataclass
class WriterTally:
    """One writer's counts, with the revisions behind each count."""

    writer: WriterKey
    spans: dict[str, list[RevisionSpan]] = field(
        default_factory=lambda: {column: [] for column in COLUMNS}
    )
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COLUMNS, 0))

    def add(self, column: str, span: RevisionSpan) -> None:
        self.spans[column].append(span)
        self.counts[column] += span.size

    def row(self) -> dict[str, object]:
        role, harness, model = self.writer
        return {"role": role, "harness": harness, "model": model, **self.counts}

    def records(self) -> dict[str, list[str]]:
        return {column: [span.label() for span in spans] for column, spans in self.spans.items()}


@dataclass(frozen=True)
class WriterTable:
    """Every writer's tally, most revisions written first, and the totals of the tree."""

    tallies: tuple[WriterTally, ...]
    revisions: int
    records: int


@dataclass(frozen=True)
class NamedRevision:
    """One revision a file of the tree names, as the index stores it.

    ``writer`` is None when the revision carries no stamp; ``seat`` is None when its seat is
    unknown; ``reviewed`` says whether its state is reviewed or consolidated.
    """

    record: str
    revision: int
    writer: WriterKey | None
    seat: str | None
    reviewed: bool


@dataclass(frozen=True)
class LiveRevision:
    """A record's current revision, and whether the record is retired at it."""

    revision: int
    retired: bool


@dataclass(frozen=True)
class _Revision:
    span: RevisionSpan
    writer: WriterKey | None
    seat: str | None
    reviewed: bool
    named: bool
    """Named by a file of the tree (the record, a ``written`` row or a history row)."""


def named_revisions(
    records: Iterable[KnowledgeRecord], states: RevisionStates
) -> list[NamedRevision]:
    """Every revision a file of the tree names, with its stamp and whether it is reviewed.

    A revision the ``written`` rows name with one stamp has that stamp. One that two different
    writes share has none. The current revision uses the same content attribution as the record
    view. The order of the history files never decides which content a revision held.
    """

    named: dict[str, dict[int, WriterStamp | None]] = {}
    for record_id, revision in states.written_by:
        named.setdefault(record_id, {})[revision] = states.written_stamp(record_id, revision)
    current: dict[str, int] = {}
    current_reviewed: dict[tuple[str, int], bool] = {}
    for record in records:
        revision = getattr(record, "revision", None)
        if not isinstance(revision, int):
            continue
        stamps = named.setdefault(record.id, {})
        state, stamps[revision] = states.current_standing(record, retired=False)
        current[record.id] = revision
        current_reviewed[(record.id, revision)] = state in _REVIEWED_STATES
    for record_id, revision in states.row_files:
        # A revision a history row read is named too, so its state is read; it has no stamp.
        if revision <= current.get(record_id, 0):
            named[record_id].setdefault(revision, None)
    return [
        NamedRevision(
            record=record_id,
            revision=revision,
            writer=None if stamp is None else stamp.writer_key(),
            seat=_seat(stamp),
            reviewed=(
                current_reviewed[(record_id, revision)]
                if (record_id, revision) in current_reviewed
                else states.state_of(record_id, revision, retired=False, writer=stamp)
                in _REVIEWED_STATES
            ),
        )
        for record_id, stamps in named.items()
        for revision, stamp in stamps.items()
    ]


def writer_table(
    revisions: Iterable[NamedRevision], live: Mapping[str, LiveRevision]
) -> WriterTable:
    """The table of one tree: its named revisions and each record's current revision."""

    named: dict[str, dict[int, NamedRevision]] = {}
    for one in revisions:
        named.setdefault(one.record, {})[one.revision] = one
    tallies: dict[WriterKey, WriterTally] = {}
    total = 0
    for record_id, by_number in named.items():
        now = live.get(record_id)
        current = None if now is None else now.revision
        ordered = _revisions_of(record_id, by_number, current)
        total += sum(one.span.size for one in ordered)
        _count_record(tallies, ordered, current if now is not None and now.retired else None)
    ordered_tallies = sorted(tallies.values(), key=lambda one: (-one.counts["written"], one.writer))
    return WriterTable(tallies=tuple(ordered_tallies), revisions=total, records=len(named))


def _revisions_of(
    record_id: str, by_number: dict[int, NamedRevision], current: int | None
) -> list[_Revision]:
    """The record's revisions in ascending order: the named ones and the unnamed runs below."""

    revisions: list[_Revision] = []
    expected = 1
    for number in sorted(by_number):
        if current is not None and expected < number <= current:
            run = RevisionSpan(record_id, expected, number - 1)
            revisions.append(_Revision(run, None, None, reviewed=False, named=False))
        one = by_number[number]
        span = RevisionSpan(record_id, number, number)
        revisions.append(_Revision(span, one.writer, one.seat, one.reviewed, named=True))
        expected = number + 1
    return revisions


def _count_record(
    tallies: dict[WriterKey, WriterTally],
    revisions: list[_Revision],
    retired_at: int | None,
) -> None:
    """Count one record's revisions, the latest first, so each sees what came after it once.

    A run of unnamed revisions always lies below a named one, so each of its revisions has a later
    revision; no file names it, so it reads proposed and its seat is unknown.
    """

    later = _LaterSeats()
    for one in reversed(revisions):
        tally = _tally(tallies, one.writer)
        span = one.span
        tally.add("written", span)
        if one.named and one.reviewed:
            tally.add("reviewed", span)
        change = later.change_of(one.seat)
        if change is not None:
            tally.add(change, span)
        if retired_at is not None and span.first == retired_at:
            tally.add("retired", span)
        later.see(one.seat)


def _tally(tallies: dict[WriterKey, WriterTally], writer: WriterKey | None) -> WriterTally:
    key = UNKNOWN_WRITER if writer is None else writer
    found = tallies.get(key)
    if found is None:
        found = tallies[key] = WriterTally(key)
    return found


def _seat(stamp: WriterStamp | None) -> str | None:
    """The seat that wrote a revision, or None where it is unknown."""

    if stamp is None or stamp.seat == UNKNOWN:
        return None
    return stamp.seat


@dataclass
class _LaterSeats:
    """What the later revisions of one record hold: up to two known seats, and any unknown one.

    Two distinct known seats suffice: any seat differs from at least one of them.
    """

    known: set[str] = field(default_factory=set)
    unknown: bool = False
    any: bool = False

    def change_of(self, seat: str | None) -> str | None:
        """The change column a revision with ``seat`` falls in, or None when no other changed it."""

        if seat is not None and self.known - {seat}:
            return "laterChangedByAnother"
        if self.any and (seat is None or self.unknown):
            return "laterChangedWriterUnknown"
        return None

    def see(self, seat: str | None) -> None:
        self.any = True
        if seat is None:
            self.unknown = True
        elif len(self.known) < 2:
            self.known.add(seat)
