"""The recorded effect of a meaning change, read from the after tree (MIK-R43).

This module is the projection the packet's Scope names: it reads the history rows of the
owner under review from the after memory tree's index -- the same index the outline
already reads -- and answers each record with its steps in attempt order, or with
``none`` or ``unavailable``. It derives no label and states no net effect: a step's
label is the effect the curator recorded in that row, and more than one step is reported
as successive recorded judgments, never as one overall label.

A step is a history row of the owner under review about the exact record, in every
attempt file of that owner:

* an invariant row is a step when its disposition is ``changed``, or ``deleted`` with the
  effect ``retire``;
* a family row is a step when its disposition is ``changed``;
* rows of the other dispositions record no change of meaning and carry no effect in the
  row model, so they are not steps;
* a row of another owner about the same record is never a step of this review.

Steps are ordered by the attempt number the history path names (the plain file is attempt
1), never by row id, date or revision. A changed family row without effect and without
own revision is a step that reads ``effect not recorded``; a changed family row with only
one of the two is invalid (MIK-R48 rule 3) and makes the display unavailable, as does a
changed invariant row without its required effect.

One owner-scoped index read serves every subject of an answer: the owner's rows are read
once and grouped by subject in memory, so neither the review nor the outline pays one
query per record.

The review addresses records by projected UUID -- the selector seeds and the family
context entries and members all carry the index's projected identities, never the raw
record ids. Each addressed identity is resolved to its raw id through the after index's
own reverse map before history is read; an addressed identity that resolves to nothing
and is no raw identity itself names no recorded record. Records keep their addressed
identity as the answer's key, so the reader joins the answer without mapping anything.

A retired record keeps its raw record and its history rows, but the index no longer
projects it, so the after reverse map has no entry for its UUID. The same holds for a
record the after tree no longer holds at all. Callers that hold the comparison's
baseline index pass it as ``identity_index``: a UUID the after map misses is resolved
through the baseline map -- the record lived there -- and history is still read from
the after tree alone. No candidate is resolved here and no label is inferred; the
baseline only names the raw id.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, cast

import apsw

from agents_remember.memory.knowledge_index import (
    HistoryRow,
    IndexMismatchError,
    KnowledgeIndex,
)
from agents_remember.models.knowledge.effect import ADMITTED_EFFECT_LABELS, EffectLabel
from agents_remember.models.knowledge.review_meaning_effects import (
    MULTI_STEP_LINE,
    NONE_TEXT,
    MeaningRecordKind,
    RecordedMeaningStep,
    RecordMeaningEffects,
    ReviewMeaningEffects,
    ReviewOutlineMeaning,
)
from agents_remember.models.knowledge_files.documents import owner_history_attempt
from agents_remember.models.knowledge_files.history import row_kind_for_subject

__all__ = [
    "outline_meanings",
    "read_record_meaning",
    "read_subjects_meaning",
    "review_meaning_effects",
]


class _IdentityFallback(NamedTuple):
    """How to resolve a UUID the after map misses: the open baseline, if any.

    ``problem`` names an unreadable baseline for subjects that resolve through
    neither map; ``index`` may be ``None`` with ``problem`` set, or both unset.
    """

    index: KnowledgeIndex | None
    problem: str | None = None


def review_meaning_effects(
    after_database: Path | None,
    owner: str,
    subjects: Iterable[tuple[str, MeaningRecordKind]],
    *,
    legacy_detail: str | None = None,
    before_database: Path | None = None,
) -> ReviewMeaningEffects:
    """Answer every ``(record, kind)`` subject from the after tree's index.

    ``after_database`` is the resolved candidate (after) index file the review already
    holds; ``owner`` is the leaf under review. When the after side cannot be read --
    ``None`` or legacy-unavailable (``legacy_detail`` names it), or an index file that
    does not open -- every subject is ``unavailable`` with the reason, never ``none``.
    An incomplete index is ``unavailable`` for the same reason: history the index did
    not read cannot be reported as absent.

    ``before_database`` is the comparison's baseline index file, opened only to resolve
    identities the after map misses (retired or after-absent records); history is still
    read from the after tree alone. When it is ``None`` or cannot be opened, those
    subjects read as before, with the baseline named in the reason.
    """

    subjects = tuple(subjects)
    if after_database is None or legacy_detail is not None:
        return ReviewMeaningEffects(
            records=tuple(
                _unavailable(record, kind, legacy_detail or _NO_AFTER_TREE)
                for record, kind in subjects
            ),
        )
    try:
        with KnowledgeIndex(after_database) as index:
            if before_database is None:
                return read_subjects_meaning(index, owner, subjects)
            try:
                with KnowledgeIndex(before_database) as before:
                    return read_subjects_meaning(index, owner, subjects, identity_index=before)
            except (apsw.Error, IndexMismatchError, OSError) as error:
                return read_subjects_meaning(
                    index,
                    owner,
                    subjects,
                    identity_problem=f"the comparison baseline cannot be read: {error}",
                )
    except (apsw.Error, IndexMismatchError, OSError) as error:
        return ReviewMeaningEffects(
            records=tuple(
                _unavailable(record, kind, f"the after memory side cannot be read: {error}")
                for record, kind in subjects
            ),
        )


def read_subjects_meaning(
    index: KnowledgeIndex,
    owner: str,
    subjects: Iterable[tuple[str, MeaningRecordKind]],
    *,
    identity_index: KnowledgeIndex | None = None,
    identity_problem: str | None = None,
) -> ReviewMeaningEffects:
    """Answer every subject from one owner-scoped read of an open after-tree index.

    ``identity_index`` is the open comparison baseline, used only to resolve UUIDs
    the after map misses; ``identity_problem`` names an unreadable baseline in the
    reason of subjects that resolve through neither map.
    """

    subjects = tuple(subjects)
    rows, problem = _owner_rows(index, owner)
    if problem is not None:
        return ReviewMeaningEffects(
            records=tuple(_unavailable(record, kind, problem) for record, kind in subjects),
        )
    assert rows is not None
    by_subject: dict[str, list[HistoryRow]] = {}
    for row in rows:
        by_subject.setdefault(row.subject, []).append(row)
    fallback = _IdentityFallback(identity_index, identity_problem)
    return ReviewMeaningEffects(
        records=tuple(
            _answer(index, record, kind, by_subject, fallback=fallback) for record, kind in subjects
        ),
    )


def read_record_meaning(
    index: KnowledgeIndex, owner: str, record: str, kind: MeaningRecordKind
) -> RecordMeaningEffects:
    """Answer one record from an open after-tree index."""

    return read_subjects_meaning(index, owner, [(record, kind)]).records[0]


def outline_meanings(
    index: KnowledgeIndex,
    owner: str,
    records: Iterable[tuple[str, MeaningRecordKind]],
    *,
    identity_index: KnowledgeIndex | None = None,
) -> dict[str, ReviewOutlineMeaning]:
    """Every listed record's meaning, keyed by record id, from one owner-scoped read.

    The same projection produces the tree badges and the reading area's block from the
    after memory tree's index, so a row shows its effect whether or not its family has
    been read, and the two are the same values. ``identity_index`` resolves the rare
    UUID the after map misses, exactly as the selected answer does.
    """

    records = tuple(records)
    rows, problem = _owner_rows(index, owner)
    if problem is not None:
        return {
            record: ReviewOutlineMeaning(state="unavailable", detail=problem)
            for record, _ in records
        }
    assert rows is not None
    by_subject: dict[str, list[HistoryRow]] = {}
    for row in rows:
        by_subject.setdefault(row.subject, []).append(row)
    fallback = _IdentityFallback(identity_index)
    return {
        record: _outline_meaning(_answer(index, record, kind, by_subject, fallback=fallback))
        for record, kind in records
    }


def _raw_subject(
    index: KnowledgeIndex, addressed: str, *, identity_index: KnowledgeIndex | None = None
) -> str | None:
    """The raw record id ``addressed`` names, or ``None`` when it names no record.

    Projected UUIDs resolve through the after index's own reverse map; when the after
    map misses -- a retired record, or one the after tree no longer holds -- the open
    comparison baseline resolves the UUID it still maps, and history is still read
    from the after tree. A raw record id stands as itself, so callers that already
    hold raw ids -- the outline -- read unchanged. Anything else names no recorded
    invariant or family.
    """

    found = index.text_id(addressed)
    if found is not None:
        return found
    if identity_index is not None:
        found = identity_index.text_id(addressed)
        if found is not None:
            try:
                row_kind_for_subject(found)
            except ValueError:
                return None
            return found
    try:
        row_kind_for_subject(addressed)
    except ValueError:
        return None
    return addressed


def _owner_rows(
    index: KnowledgeIndex, owner: str
) -> tuple[tuple[HistoryRow, ...] | None, str | None]:
    """The owner's history rows across every attempt file, or the unavailable reason.

    Rows of another owner are left out before anything else: they are never steps of
    this review, and they cannot make it unavailable either. Only files the path names
    as this owner's count; anything else filed under the owner is not one of its
    attempt files.
    """

    answer = index.history_rows_of(owner)
    if not answer.index.complete:
        return None, _incomplete_detail(answer.index.problems)
    return (
        tuple(row for row in answer.value if owner_history_attempt(row.path, owner) is not None),
        None,
    )


def _answer(
    index: KnowledgeIndex,
    record: str,
    kind: MeaningRecordKind,
    by_subject: dict[str, list[HistoryRow]],
    *,
    fallback: _IdentityFallback | None = None,
) -> RecordMeaningEffects:
    identity = fallback.index if fallback is not None else None
    raw = _raw_subject(index, record, identity_index=identity)
    if raw is None:
        if fallback is not None and fallback.problem is not None:
            return _unavailable(record, kind, fallback.problem)
        return _unavailable(record, kind, f"{record!r} names no recorded invariant or family")
    claimed = row_kind_for_subject(raw).name
    if claimed != kind:
        return _unavailable(record, kind, f"{record!r} is recorded as {claimed}, not as {kind}")
    steps: list[RecordedMeaningStep] = []
    for row in by_subject.get(raw, []):
        classified = _classify(row, kind)
        if classified is None:
            continue
        if isinstance(classified, str):
            return _unavailable(record, kind, classified)
        steps.append(classified)
    steps.sort(key=lambda step: (step.attempt, step.row_id))
    if not steps:
        return RecordMeaningEffects(record=record, kind=kind, state="none", detail=NONE_TEXT)
    return RecordMeaningEffects(
        record=record,
        kind=kind,
        state="steps",
        detail=(
            MULTI_STEP_LINE
            if len(steps) > 1
            else "The leaf's one recorded judgment about this record."
        ),
        steps=tuple(steps),
    )


def _outline_meaning(answer: RecordMeaningEffects) -> ReviewOutlineMeaning:
    if answer.state != "steps":
        return ReviewOutlineMeaning(state=answer.state, detail=answer.detail)
    return ReviewOutlineMeaning(state="steps", labels=tuple(step.label for step in answer.steps))


def _classify(row: HistoryRow, kind: MeaningRecordKind) -> RecordedMeaningStep | str | None:
    """One own row as a step, ``None`` when it is no step, or the invalid reason.

    The attempt comes from the history path, never from the row id, date or revision.
    """

    facts = _checked_facts(row)
    if isinstance(facts, str):
        return facts
    if kind == "invariant":
        return _invariant_step(row, facts)
    return _family_step(row, facts)


@dataclass(frozen=True)
class _RowFacts:
    """One row's step facts after validation: effect, judged revision, attempt, reason."""

    effect: EffectLabel | None
    revision: int | None
    attempt: int
    reason: str


def _checked_facts(row: HistoryRow) -> _RowFacts | str:
    """The row's effect, revision, attempt and reason, or why the row is invalid."""

    document = row.document
    effect = document.get("effect")
    revision = document.get("revision")
    if effect is not None and effect not in ADMITTED_EFFECT_LABELS:
        return f"row {row.id} carries {effect!r}, which is no admitted effect label"
    if effect is not None and not isinstance(effect, str):
        return f"row {row.id} carries a non-text effect"
    if revision is not None and not (isinstance(revision, int) and revision >= 1):
        return f"row {row.id} carries no valid judged revision"
    reason = document.get("reason")
    if not isinstance(reason, str) or not reason:
        return f"row {row.id} carries no reason"
    return _RowFacts(
        effect=cast(EffectLabel, effect),
        revision=revision,
        attempt=owner_history_attempt(row.path, row.owner) or 1,
        reason=reason,
    )


def _invariant_step(row: HistoryRow, facts: _RowFacts) -> RecordedMeaningStep | str | None:
    if facts.effect == "retire" and row.disposition == "deleted":
        return _step(row, facts)
    if row.disposition != "changed":
        return None
    if facts.effect is None:
        return f"row {row.id} changes the meaning but records no effect"
    return _step(row, facts)


def _family_step(row: HistoryRow, facts: _RowFacts) -> RecordedMeaningStep | str | None:
    if row.disposition != "changed":
        return None
    if (facts.revision is None) != (facts.effect is None):
        return (
            f"row {row.id} records only one of the effect and the judged revision; "
            "a half-recorded family row is invalid"
        )
    return _step(row, facts)


def _step(row: HistoryRow, facts: _RowFacts) -> RecordedMeaningStep:
    return RecordedMeaningStep(
        attempt=facts.attempt,
        row_id=row.id,
        history_path=row.path,
        closed=row.closed,
        reason=facts.reason,
        effect=facts.effect,
        revision=facts.revision,
    )


def _unavailable(record: str, kind: MeaningRecordKind, detail: str) -> RecordMeaningEffects:
    return RecordMeaningEffects(
        record=record,
        kind=kind,
        state="unavailable",
        detail=detail[:20000],
    )


def _incomplete_detail(problems: tuple[tuple[str, str], ...]) -> str:
    named = ", ".join(path for path, _ in problems[:3])
    more = f", and {len(problems) - 3} more" if len(problems) > 3 else ""
    return (
        f"the after memory index is incomplete ({len(problems)} file(s) failed: "
        f"{named}{more}); history it did not read cannot be reported as absent"
    )


_NO_AFTER_TREE = (
    "the review has no converted after memory tree to read history from; "
    "a meaning effect cannot be reported as absent for a side that was never read"
)
