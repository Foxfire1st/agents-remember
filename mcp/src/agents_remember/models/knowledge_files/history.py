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

The other registered item kinds add their row kinds to :data:`HISTORY_ROW_KINDS` when they land
(MIK-R30 ``onboarding:…``, MIK-R11 ``planned:…``, MIK-R10 ``hunk:…``/``file:…``, MIK-R14
``reconsider:…``; MIK-R06 later); until then a row whose subject no kind claims is refused.

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

**Reopen after a converted closeout (L37 ruling, 2026-10-01T01:57:55).** A leaf reopened after its
closeout keeps its closed file frozen and writes a new, *attempt-qualified* file for the same leaf:
``knowledge/history/<leaf-id>-attempt-<n>.json`` with ``leaf`` and ``attempt: n`` (``n`` >= 2; the
first attempt is the plain ``<leaf-id>.json`` and carries no ``attempt``, so every earlier file keeps
its bytes). All of a leaf's files are its history: :func:`merged_leaf_history` reads them as one,
where a row in a later attempt about the same subject supersedes the earlier one, and a row of a
closed file still counts while it is current. :func:`writable_attempt` names the file a write or a
closeout goes to: the latest attempt while it is open, else the next one.
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
from agents_remember.models.knowledge_files.planned import (
    PLANNED_DISPOSITIONS,
    PLANNED_SUBJECT_PATTERN,
    REFS_BY_DISPOSITION,
)
from agents_remember.models.knowledge_files.reconsideration import (
    RECONSIDER_DISPOSITIONS,
    RECONSIDER_SUBJECT_PATTERN,
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
from agents_remember.models.knowledge_files.unexplained import NO_INVARIANT, ROW_SUBJECT_PATTERN

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
Attempt = Annotated[StrictInt, Field(ge=2)]
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


class PlannedRef(FileModel):
    """What a planned row names (MIK-R11 rule 5); exactly one key, the one its disposition takes.

    * ``realized_elsewhere``: ``row`` (the ``ROW-…`` that delivered the effect) or ``invariant``;
    * ``deferred``: ``requirement`` (the follow-up requirement) or ``leaf`` (the follow-up leaf);
    * ``dropped``: ``decision`` -- the ``at`` of one decision entry in the leaf's task document,
      resolved through the task owner when the writer writes the row.
    """

    row: RowId | None = None
    invariant: InvariantId | None = None
    requirement: RequirementReference | None = None
    leaf: OwnerId | None = None
    decision: Label | None = None

    @model_validator(mode="after")
    def _require_exactly_one(self) -> PlannedRef:
        if len(self.named) != 1:
            raise ValueError(
                "a planned row's ref names exactly one of row, invariant, requirement, leaf or "
                "decision"
            )
        return self

    @property
    def named(self) -> tuple[str, ...]:
        return tuple(key for key in type(self).model_fields if getattr(self, key) is not None)


class PlannedEffectRow(HistoryRow):
    """The authored disposition of a declared effect no row delivered (MIK-R11 rule 5).

    Subject ``planned:<declared subject>#<effect>`` -- built from the declaration, never from its
    list position -- and disposition ``realized_elsewhere``, ``deferred`` or ``dropped``, with the
    ``ref`` that disposition names. MIK-R11 owns the kind; the row answers a ``planned_untouched``
    worklist item.
    """

    row_kind: ClassVar[str] = "planned"
    subject_pattern: ClassVar[str] = PLANNED_SUBJECT_PATTERN
    dispositions: ClassVar[tuple[str, ...]] = PLANNED_DISPOSITIONS

    ref: PlannedRef

    @model_validator(mode="after")
    def _require_ref_of_disposition(self) -> PlannedEffectRow:
        allowed = REFS_BY_DISPOSITION.get(self.disposition, frozenset())
        if not set(self.ref.named) <= allowed:
            raise ValueError(
                f"a {self.disposition} planned row's ref names one of {sorted(allowed)}, "
                f"not {list(self.ref.named)}"
            )
        return self


class UnexplainedChangeRow(HistoryRow):
    """The ``no_invariant`` disposition of an unexplained change in a covered file (MIK-R10).

    Subject ``hunk:<item id>`` for an ``unexplained_hunk`` item and ``file:<path>@<blob>`` for an
    ``unexplained_file`` item; ``no_invariant`` is the only disposition, and its ``reason`` says why
    the change carries no invariant. It adds nothing to the common row fields. MIK-R10 owns the
    kind. Attaching or authoring an entry over the change is the other answer, and needs no row of
    this kind: the change is then linked, and the invariant's own row follows (MIK-R08).
    """

    row_kind: ClassVar[str] = "unexplained"
    subject_pattern: ClassVar[str] = ROW_SUBJECT_PATTERN
    dispositions: ClassVar[tuple[str, ...]] = (NO_INVARIANT,)


class ReconsiderationRow(HistoryRow):
    """The curator's answer to a reconsideration candidate (MIK-R14 rule 4).

    Subject ``reconsider:<DEC-ID>#<alternative index>``; disposition ``still_rejected`` (the
    rejection holds, and ``reason`` says why) or ``raise`` (the alternative goes to the developer:
    the writer sets the decision's ``status`` to ``under_reconsideration`` and appends a question to
    the leaf's task-document ``openQuestions``). It carries nothing beyond the common fields. MIK-R14
    owns the kind; the row answers a ``reconsideration_candidate`` worklist item.
    """

    row_kind: ClassVar[str] = "reconsideration"
    subject_pattern: ClassVar[str] = RECONSIDER_SUBJECT_PATTERN
    dispositions: ClassVar[tuple[str, ...]] = RECONSIDER_DISPOSITIONS


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
    HistoryRowKind("planned", PlannedEffectRow, "MIK-R11"),
    HistoryRowKind("unexplained", UnexplainedChangeRow, "MIK-R10"),
    HistoryRowKind("reconsideration", ReconsiderationRow, "MIK-R14"),
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
    attempt: Attempt | None = None
    """A reopened leaf's later attempt (2, 3, ...); absent on a leaf's first file and on every
    wave or crossing file."""
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
        if self.attempt is not None and self.leaf is None:
            raise ValueError("only a leaf's history is attempt-qualified (a reopened leaf)")
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

    @property
    def attempt_number(self) -> int:
        """The attempt this file records: ``attempt``, or 1 for a leaf's first file."""

        return self.attempt or 1

    def closed_copy(self) -> HistoryFile:
        """Return this file with ``closed: true``, as the closeout commit writes it."""

        return self if self.closed else self.model_copy(update={"closed": True})


def empty_history(
    owner_kind: OwnerKind, owner_id: str, *, closed: bool = False, attempt: int = 1
) -> HistoryFile:
    """Return a history file with no rows: the closeout creates one when the leaf has none."""

    document: dict[str, Any] = {owner_kind: owner_id, "closed": closed, "rows": []}
    if attempt > 1:
        document["attempt"] = attempt
    return HistoryFile.model_validate(document)


def writable_attempt(closed_by_attempt: Mapping[int, bool]) -> int:
    """The attempt a write or a closeout goes to, from each existing attempt's ``closed`` flag.

    The latest attempt while it is open; the next one once it is closed (the leaf was reopened);
    1 when the leaf has no file yet.
    """

    if not closed_by_attempt:
        return 1
    latest = max(closed_by_attempt)
    return latest + 1 if closed_by_attempt[latest] else latest


def merged_leaf_history(files: Iterable[HistoryFile]) -> HistoryFile | None:
    """All of one owner's history files read as its history (L37 ruling on reopen).

    The files are taken in attempt order; a row in a later attempt about a subject supersedes the
    earlier one, and every other row -- a closed file's included -- still counts. The result carries
    the latest file's ``closed`` and ``attempt``; it is a reading, never written back.
    """

    ordered = sorted(files, key=lambda one: one.attempt_number)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rows: dict[str, HistoryRow] = {}
    for history in ordered:
        for row in history.rows:
            rows.pop(row.subject, None)
            rows[row.subject] = row
    return ordered[-1].model_copy(update={"rows": tuple(rows.values())})


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
    row: InvariantRow,
    *,
    base_revision: int | None,
    candidate_revision: int,
    restated_revision: int | None = None,
) -> str | None:
    """Return why ``row.revision`` does not bind the invariant's revisions, or ``None``.

    The row's revision is the invariant's revision in K_C. A ``changed`` row's revision is the
    K_B revision plus one: a leaf changes an invariant's meaning once, so its revision increments
    exactly once (MIK-R21 rule 4). ``moved``, ``extended`` and ``no_impact`` leave meaning, and so the
    revision, unchanged. ``deleted`` is not constrained beyond K_C. ``base_revision`` is ``None``
    for an invariant absent from K_B, which needs no row (rule 6).

    ``restated_revision`` is the revision of the owner's governing ``changed`` row in an earlier,
    frozen attempt of the same leaf (``None`` when it has none). A ``changed`` row at an unchanged
    revision is accepted only as a restatement of that revision step: the record has not changed
    since that row (K_C's revision is K_B's) and both rows are at that revision. It corrects the
    step's effect, because and reason; it is no second change (L37 ruling of 2026-10-01T22:49:36,
    Q2 a).
    """

    if row.revision != candidate_revision:
        return f"row revision {row.revision} is not the K_C revision {candidate_revision}"
    if base_revision is None or row.disposition == "deleted":
        return None
    if row.disposition == "changed" and candidate_revision != base_revision + 1:
        if candidate_revision == base_revision == restated_revision:
            return None
        return f"a changed row's revision is the K_B revision {base_revision} plus one"
    if row.disposition != "changed" and candidate_revision != base_revision:
        return f"a {row.disposition} row keeps the K_B revision {base_revision}"
    return None


def keeps_change_visible(row: InvariantRow | FamilyRow) -> bool:
    """Whether ``row`` may govern a record its leaf changed: the ``changed`` row of that change, or
    the row that retires the record (``deleted`` for an invariant, ``retired`` for a family)."""

    return row.disposition in {"changed", "deleted", "retired"}


def changed_record_row_violation(
    row: InvariantRow, *, base_revision: int | None, candidate_revision: int | None
) -> str | None:
    """Return why ``row`` cannot govern an invariant whose revision the leaf changed, or ``None``.

    ``base_revision`` is the invariant's revision on the parent line (the leaf's K_B) and
    ``candidate_revision`` its revision in K_C. While the two differ, the leaf changed the
    invariant's meaning, and the leaf's governing row about it -- the latest across its attempt
    files -- is the ``changed`` row of that change (or the ``deleted`` row that retires it). A
    later ``no_impact``, ``moved`` or ``extended`` row would replace that judgment and hide the
    change, so it is refused at the routes (L37 ruling of 2026-10-01T22:49:36, Q2 b). The writer's
    own form of the rule is :func:`invariant_revision_violation`, against its base.
    """

    if base_revision is None or candidate_revision is None or base_revision == candidate_revision:
        return None
    if keeps_change_visible(row):
        return None
    return (
        f"{_a_row(row)} governs an invariant this leaf changed (revision "
        f"{base_revision} on the parent line, {candidate_revision} in K_C); the governing row of "
        "a changed record is its changed row"
    )


def changed_family_row_violation(row: FamilyRow, *, guarantee_changed: bool) -> str | None:
    """Return why ``row`` cannot govern a family whose guarantee the leaf changed, or ``None``.

    A family has one governing row, the leaf's latest about it (rule 5; MIK-R06 rule 3 reads "the
    latest family row"). ``guarantee_changed`` says that K_C states another guarantee than the
    parent line (the leaf's K_B). While it does, the governing row is the ``changed`` row of that
    change (or the ``retired`` row that retires the family): a ``no_impact``, ``assigned`` or
    ``rerouted`` row would replace that judgment. Route work is recorded under the ``changed``
    row, which answers the family's route conditions too (L37 ruling of 2026-10-02T01:04:49).
    """

    if not guarantee_changed or keeps_change_visible(row):
        return None
    return (
        f"{_a_row(row)} governs a family whose guarantee this leaf changed; the governing row of "
        "such a family is its changed row, which answers its route conditions too (MIK-R06 rule 3)"
    )


def _a_row(row: HistoryRow) -> str:
    return f"{'an' if row.disposition[:1] in 'aeiou' else 'a'} {row.disposition} row"


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
