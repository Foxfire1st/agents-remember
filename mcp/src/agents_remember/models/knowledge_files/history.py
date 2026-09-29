"""Per-leaf history files: the curator's judgment rows, one file per leaf (MIK-R07).

``knowledge/history/<owner-id>.json`` (``ar-history/v1``) belongs to exactly one owner: a ``leaf``,
a migration ``wave``, or a master-line ``crossing`` sync (MIK-R24 rule 8). Owner IDs are
task-qualified (``260928-MIK-L07``, ``260928-MIK-crossing-1``) and name the file, so two leaves --
or two masters sharing one memory repository -- never write the same file, and a parallel merge of
two leaves' history never conflicts (rule 9).

**Two histories.** An invariant's *meaning* history is the ``git log`` of its record file; its
*judgment* history is the set of rows about it across all history files. Nothing here is written
into a record file, and no row is ever produced automatically.

**Rows.** Every row has ``id`` (``ROW-…``, minted by the writer), ``subject``, ``disposition``, a
nonblank ``reason`` and ``items[]`` (the worklist item IDs it answered when written; informational
only -- the gate finds a row by ``subject``, MIK-R09). A row's kind is decided by its subject, through
:data:`HISTORY_ROW_KINDS`. This packet registers two kinds:

* **invariant rows** (subject ``INV-…``): ``changed`` | ``moved`` | ``deleted`` | ``extended`` |
  ``no_impact`` (D28), with ``covers[]`` (entry ID with its ``before`` and ``after`` anchor, either
  ``"absent"``), the invariant's ``revision`` in K_C, ``effect`` (required for ``changed``, only
  ``retire`` on ``deleted``) and ``because[]`` (decision IDs and requirement references);
* **family rows** (subject ``FAM-…``): ``changed`` | ``rerouted`` | ``assigned`` | ``retired`` |
  ``no_impact``, with ``examined[]``: ``{ id, revision }`` of every member examined, at its K_C
  revision (D7).

The other registered item kinds (MIK-R06, R10, R11, R14, R30) add their row kinds to
:data:`HISTORY_ROW_KINDS` when they land; until then a row whose subject no kind claims is refused.

**Checks that need the trees.** The models check shape. The writer support below checks a row
against K_B/K_C facts the caller reads: :func:`reanchor_mismatches` (rule 4: each ``after`` equals
the entry's anchor in K_C), :func:`unknown_subjects`, :func:`invariant_revision_violation` and
:func:`stale_examined_members` (the revision binding of rules 2 and 5). Whether a row is *current*
for an item is the gate's rule (MIK-R09 rule 2), not this module's.

**Freezing (rule 7).** Rows are edited freely until closeout. The closeout route sets
``closed: true`` (:meth:`HistoryFile.closed_copy`, or :func:`empty_history` when the leaf has no
rows) in the memory commit that publishes the leaf. From then on the file is frozen:
:func:`frozen_history_violation` is the predicate "``closed`` in K_B (or in any parent of a merge)
implies byte-identical in K_C", which the validator (MIK-R22 rule 7) enforces.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar, Final, Literal

from pydantic import Field, SerializeAsAny, StrictBool, StrictInt, field_validator, model_validator

from agents_remember.models.knowledge.effect import EffectLabel
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError, parse_json
from agents_remember.models.knowledge_files.ids import (
    ENTRY_ID_PATTERN,
    RECORD_PREFIXES,
    ROW_ID_PATTERN,
    id_pattern,
)
from agents_remember.models.knowledge_files.shapes import (
    Anchor,
    DecisionId,
    FileModel,
    InvariantId,
    Label,
    RequirementReference,
    Text,
    require_unique,
)
from agents_remember.models.knowledge_files.sidecars import ProofEntry, RealizationEntry

HISTORY_SCHEMA: Final = "ar-history/v1"
ABSENT: Final = "absent"

INVARIANT_DISPOSITIONS: Final = ("changed", "moved", "deleted", "extended", "no_impact")
FAMILY_DISPOSITIONS: Final = ("changed", "rerouted", "assigned", "retired", "no_impact")
InvariantDisposition = Literal["changed", "moved", "deleted", "extended", "no_impact"]
FamilyDisposition = Literal["changed", "rerouted", "assigned", "retired", "no_impact"]

_OWNER_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_CROSSING_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]*-crossing-[1-9][0-9]*$"
OwnerId = Annotated[str, Field(min_length=1, max_length=128, pattern=_OWNER_ID)]
CrossingId = Annotated[str, Field(min_length=1, max_length=128, pattern=_CROSSING_ID)]
RowId = Annotated[str, Field(pattern=ROW_ID_PATTERN)]
EntryId = Annotated[str, Field(pattern=ENTRY_ID_PATTERN)]
Revision = Annotated[StrictInt, Field(ge=1)]
Absent = Literal["absent"]


# --------------------------------------------------------------------------------------------------
# Rows (rules 1, 2, 3 and 5)
# --------------------------------------------------------------------------------------------------


class HistoryRow(FileModel):
    """The fields every row kind shares. A concrete kind fixes its subject form and dispositions."""

    row_kind: ClassVar[str]
    subject_pattern: ClassVar[str]
    dispositions: ClassVar[tuple[str, ...]]

    id: RowId
    subject: Label
    disposition: Label
    reason: Text
    items: tuple[Label, ...]

    @model_validator(mode="after")
    def _require_kind_subject_and_disposition(self) -> HistoryRow:
        if type(self) is HistoryRow:
            raise ValueError("a history row is read through its registered kind")
        if re.match(self.subject_pattern, self.subject) is None:
            raise ValueError(f"a {self.row_kind} row's subject is not a {self.row_kind} ID")
        if self.disposition not in self.dispositions:
            raise ValueError(
                f"disposition {self.disposition!r} is not allowed on a {self.row_kind} row; "
                f"allowed: {list(self.dispositions)}"
            )
        require_unique(self.items, what="items")
        return self


class CoveredEntry(FileModel):
    """``{ id, before, after }``: one realization or proof entry the row examined.

    Both anchors name their path, because a row is read apart from the sidecar that holds the entry
    and an entry may move between sidecars. ``before`` is ``"absent"`` for an entry added in this
    leaf and ``after`` is ``"absent"`` for an entry removed; never both.
    """

    id: EntryId
    before: Anchor | Absent
    after: Anchor | Absent

    @model_validator(mode="after")
    def _require_named_anchors(self) -> CoveredEntry:
        if self.before == ABSENT and self.after == ABSENT:
            raise ValueError("a covered entry is absent on at most one side")
        for anchor in (self.before, self.after):
            if isinstance(anchor, Anchor) and anchor.path is None:
                raise ValueError("a covered entry's anchor names its path")
        return self

    @property
    def added(self) -> bool:
        return self.before == ABSENT

    @property
    def removed(self) -> bool:
        return self.after == ABSENT

    @property
    def reanchored(self) -> bool:
        return not self.added and not self.removed and self.before != self.after


Because = Annotated[DecisionId | RequirementReference, Field(union_mode="left_to_right")]


def _entry_set_changed(covers: tuple[CoveredEntry, ...]) -> bool:
    return any(entry.added or entry.removed for entry in covers)


# What each disposition's own covers must show (rule 3). ``moved`` and ``no_impact`` keep the set of
# entries: an added or removed entry needs the stronger ``extended``, ``deleted`` or ``changed``.
_COVER_REQUIREMENTS: Final[
    Mapping[str, tuple[tuple[str, Callable[[tuple[CoveredEntry, ...]], bool]], ...]]
] = {
    "moved": (
        (
            "a moved invariant row covers a re-anchored entry",
            lambda c: any(e.reanchored for e in c),
        ),
        (
            "a moved invariant row keeps the set of entries: none added or removed",
            lambda c: not _entry_set_changed(c),
        ),
    ),
    "extended": (
        ("an extended invariant row covers an added entry", lambda c: any(e.added for e in c)),
    ),
    "no_impact": (
        (
            "a no_impact invariant row keeps the set of entries: none added or removed",
            lambda c: not _entry_set_changed(c),
        ),
    ),
}


def _require_disposition_evidence(row: InvariantRow) -> None:
    """Refuse a disposition that its own covers or effect contradict (rule 3)."""

    if row.disposition == "changed" and row.effect is None:
        raise ValueError("a changed invariant row names its effect")
    if row.disposition not in {"changed", "deleted"} and row.effect is not None:
        raise ValueError(f"a {row.disposition} invariant row carries no effect")
    if row.disposition == "deleted":
        if row.effect not in {None, "retire"}:
            raise ValueError("a deleted invariant row's effect can only be retire")
        if row.effect is None and not any(entry.removed for entry in row.covers):
            raise ValueError(
                "a deleted invariant row covers a removed entry or retires the invariant"
            )
    for message, holds in _COVER_REQUIREMENTS.get(row.disposition, ()):
        if not holds(row.covers):
            raise ValueError(message)


class InvariantRow(HistoryRow):
    """A judgment about one invariant that existed in K_B."""

    row_kind: ClassVar[str] = "invariant"
    subject_pattern: ClassVar[str] = id_pattern(RECORD_PREFIXES["invariant"])
    dispositions: ClassVar[tuple[str, ...]] = INVARIANT_DISPOSITIONS

    covers: tuple[CoveredEntry, ...]
    revision: Revision
    effect: EffectLabel | None = None
    because: tuple[Because, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_consistent_invariant_row(self) -> InvariantRow:
        require_unique(tuple(entry.id for entry in self.covers), what="covered entry ids")
        _require_disposition_evidence(self)
        return self


class ExaminedMember(FileModel):
    """``{ id, revision }``: a member invariant and its K_C revision when the curator examined it."""

    id: InvariantId
    revision: Revision


class FamilyRow(HistoryRow):
    """A judgment about one family that existed in K_B, bound to every member revision examined."""

    row_kind: ClassVar[str] = "family"
    subject_pattern: ClassVar[str] = id_pattern(RECORD_PREFIXES["family"])
    dispositions: ClassVar[tuple[str, ...]] = FAMILY_DISPOSITIONS

    examined: tuple[ExaminedMember, ...]

    @model_validator(mode="after")
    def _require_distinct_members(self) -> FamilyRow:
        require_unique(tuple(member.id for member in self.examined), what="examined member ids")
        return self


class OnboardingTraceRow(HistoryRow):
    """A reviewed-no-impact attestation about one onboarding card or route overview.

    Subject ``onboarding:<source path>`` for a file card and ``onboarding:<route>/overview`` for a
    route overview (``onboarding:overview`` for the root route). MIK-R30 owns the kind: such a row,
    with disposition ``no_impact``, satisfies the onboarding gate's item for a changed source file's
    card or governing route overview that has no counted change
    (``worktrees/modules/onboarding_trace.py``); ``no_impact`` is the only disposition. The curator
    writes it through the writer's ``history`` section; MIK-R24 rule 8 step 1 also writes it (an
    open leaf's Update History ``No content impact:``/``No route impact:`` markers move here when
    its line is converted).

    ``markers`` holds the moved marker lines, one entry per line (a line longer than one text value
    is split deterministically into consecutive pieces); ``reason`` is then a short fixed summary.
    A card can gain many markers on one leaf (81 lines, 29,351 characters on a real card), which no
    single ``reason`` holds.
    """

    row_kind: ClassVar[str] = "onboarding_trace"
    subject_pattern: ClassVar[str] = r"^onboarding:\S(?:.*\S)?$"
    dispositions: ClassVar[tuple[str, ...]] = ("no_impact",)

    markers: tuple[Text, ...] | None = Field(default=None, min_length=1)


@dataclass(frozen=True)
class HistoryRowKind:
    """A registered row kind: the subject form it claims, its model and its owner packet."""

    name: str
    model: type[HistoryRow]
    owner: str


# The row-kind registry. A registered item kind of MIK-R08 rule 1 whose satisfying row lives in the
# history file adds its row kind here when it lands. Subject forms must not overlap.
HISTORY_ROW_KINDS: Final[tuple[HistoryRowKind, ...]] = (
    HistoryRowKind("invariant", InvariantRow, "MIK-R07"),
    HistoryRowKind("family", FamilyRow, "MIK-R07"),
    HistoryRowKind("onboarding_trace", OnboardingTraceRow, "MIK-R30"),
)


def row_kind_for_subject(subject: str) -> HistoryRowKind:
    """Return the one registered row kind whose subject form ``subject`` has."""

    for kind in HISTORY_ROW_KINDS:
        if re.match(kind.model.subject_pattern, subject):
            return kind
    raise ValueError(f"no registered history row kind has the subject {subject!r}")


def parse_row(row: Any) -> HistoryRow:
    """Validate one row against the model of the kind its ``subject`` names."""

    if isinstance(row, HistoryRow):
        return row
    if not isinstance(row, Mapping) or not isinstance(row.get("subject"), str):
        raise ValueError("a history row is an object with a string 'subject'")
    return row_kind_for_subject(row["subject"]).model.model_validate(dict(row))


# --------------------------------------------------------------------------------------------------
# The file (rules 1, 7, 8 and 9)
# --------------------------------------------------------------------------------------------------

OwnerKind = Literal["leaf", "wave", "crossing"]


class HistoryFile(FileModel):
    """``ar-history/v1``: one owner's rows, open until its closeout and frozen afterwards."""

    schema_: Literal["ar-history/v1"] = Field(default=HISTORY_SCHEMA, alias="schema")
    leaf: OwnerId | None = None
    wave: OwnerId | None = None
    crossing: CrossingId | None = None
    closed: StrictBool
    rows: tuple[SerializeAsAny[HistoryRow], ...]

    @field_validator("rows", mode="before")
    @classmethod
    def _dispatch_rows(cls, rows: Any) -> Any:
        if not isinstance(rows, list | tuple):
            return rows
        return tuple(parse_row(row) for row in rows)

    @model_validator(mode="after")
    def _require_one_owner_and_one_row_per_subject(self) -> HistoryFile:
        owners = [value for value in (self.leaf, self.wave, self.crossing) if value is not None]
        if len(owners) != 1:
            raise ValueError("a history file names exactly one of leaf, wave or crossing")
        require_unique(tuple(row.id for row in self.rows), what="row ids")
        require_unique(tuple(row.subject for row in self.rows), what="row subjects")
        return self

    @property
    def owner_kind(self) -> OwnerKind:
        if self.leaf is not None:
            return "leaf"
        return "wave" if self.wave is not None else "crossing"

    @property
    def owner_id(self) -> str:
        return self.leaf or self.wave or self.crossing or ""

    def row_about(self, subject: str) -> HistoryRow | None:
        """Return the row about ``subject``; a file holds at most one per subject."""

        return next((row for row in self.rows if row.subject == subject), None)

    def closed_copy(self) -> HistoryFile:
        """Return this file with ``closed: true``, as the closeout commit writes it."""

        return self if self.closed else self.model_copy(update={"closed": True})


def empty_history(owner_kind: OwnerKind, owner_id: str, *, closed: bool = False) -> HistoryFile:
    """Return a history file with no rows: the closeout creates one when the leaf has none."""

    return HistoryFile.model_validate({owner_kind: owner_id, "closed": closed, "rows": []})


# --------------------------------------------------------------------------------------------------
# Freezing (rule 7)
# --------------------------------------------------------------------------------------------------


def is_closed_history(data: bytes | None) -> bool:
    """Answer whether ``data`` is an ``ar-history/v1`` document whose ``closed`` is ``true``.

    Only ``schema`` and the flag are read, so a closed file written with a row kind registered
    later is still recognized as frozen. Callers apply it to the bytes at a
    ``knowledge/history/<owner-id>.json`` path; bytes that are not a JSON object of that schema are
    not a closed history file, and the validator reports them through its shape check.
    """

    if data is None:
        return False
    try:
        document = parse_json(data.decode("utf-8"))
    except (UnicodeDecodeError, CanonicalFormatError):
        return False
    return (
        isinstance(document, dict)
        and document.get("schema") == HISTORY_SCHEMA
        and document.get("closed") is True
    )


def frozen_history_violation(bases: Iterable[bytes | None], candidate: bytes | None) -> bool:
    """Answer whether a history file closed on some base side differs in the candidate.

    ``bases`` are the file's bytes in K_B and, for a merge, in every parent (``None`` where the
    file is absent); ``candidate`` is its bytes in K_C (``None`` if deleted). A file that is
    ``closed`` on any base side must be byte-identical in K_C.
    """

    return any(is_closed_history(base) and base != candidate for base in bases)


# --------------------------------------------------------------------------------------------------
# Writer support: checks against K_B / K_C facts the caller supplies (rules 2, 4 and 5)
# --------------------------------------------------------------------------------------------------


def reanchor_mismatches(row: InvariantRow, candidate_anchors: Mapping[str, Anchor]) -> list[str]:
    """Return the covered entry IDs whose ``after`` differs from the entry's anchor in K_C.

    ``candidate_anchors`` maps each entry ID present in K_C to its anchor with ``path`` filled in
    (:func:`sidecar_entry_anchors`). An ``after`` of ``"absent"`` requires the entry to be absent.
    """

    mismatches = []
    for entry in row.covers:
        expected: Anchor | str = candidate_anchors.get(entry.id, ABSENT)
        if entry.after != expected:
            mismatches.append(entry.id)
    return mismatches


def sidecar_entry_anchors(
    sidecar_path: str, entries: Iterable[RealizationEntry | ProofEntry]
) -> dict[str, Anchor]:
    """Map each realization or proof entry of one file sidecar to its anchor, path filled in."""

    return {entry.id: entry.anchor.model_copy(update={"path": sidecar_path}) for entry in entries}


def unknown_subjects(history: HistoryFile, known_record_ids: Collection[str]) -> list[str]:
    """Return the invariant and family row subjects that name no known record."""

    return [
        row.subject
        for row in history.rows
        if isinstance(row, InvariantRow | FamilyRow) and row.subject not in known_record_ids
    ]


def invariant_revision_violation(
    row: InvariantRow, *, base_revision: int | None, candidate_revision: int
) -> str | None:
    """Return why ``row.revision`` does not bind the invariant's revisions, or ``None``.

    The row's revision is the invariant's revision in K_C. A ``changed`` row's revision is the
    K_B revision plus one: a leaf changes an invariant's meaning once, so its revision increments
    exactly once (MIK-R21 rule 4). ``moved``, ``extended`` and ``no_impact`` leave meaning, and so the
    revision, unchanged. ``deleted`` is not constrained beyond K_C. ``base_revision`` is ``None``
    for an invariant absent from K_B, which needs no row (rule 6).
    """

    if row.revision != candidate_revision:
        return f"row revision {row.revision} is not the K_C revision {candidate_revision}"
    if base_revision is None or row.disposition == "deleted":
        return None
    if row.disposition == "changed" and candidate_revision != base_revision + 1:
        return f"a changed row's revision is the K_B revision {base_revision} plus one"
    if row.disposition != "changed" and candidate_revision != base_revision:
        return f"a {row.disposition} row keeps the K_B revision {base_revision}"
    return None


def stale_examined_members(row: FamilyRow, candidate_revisions: Mapping[str, int]) -> list[str]:
    """Return the examined members whose K_C revision differs from the one the row examined.

    A member absent from K_C (``candidate_revisions`` has no entry) is not stale. A non-empty
    result means the family must be examined again (rule 5).
    """

    return [
        member.id
        for member in row.examined
        if member.id in candidate_revisions and candidate_revisions[member.id] != member.revision
    ]
