"""MIK-R09's rule on history rows, in the validator's registry (MIK-R22 rule 9).

The writer (MIK-R12) checks every row of the owner's history file it writes against the tree it
produces. A row edited by hand, or left behind by a direct sidecar edit, is never seen by the writer,
so the same two MIK-R07 writer-support checks also run as validator rules, and therefore at every
commit route (carried from the L12 review):

* every invariant and family row's subject names a record of the candidate
  (:func:`unknown_subjects`; a retired record still resolves), and
* every covered entry's ``after`` anchor equals that entry's anchor in the candidate, its path
  filled in, and ``absent`` means the entry is gone (:func:`reanchor_mismatches` over
  :func:`sidecar_entry_anchors`, MIK-R07 rule 4).

**Which files (L09 review R1, finding 1; review R2-1).**

* Every row of **every** history file, open or closed, at every route, must name a record of the
  candidate: a retired record still resolves, so no legitimate row ever names an unknown subject,
  and a closed file committed outside the AR routes cannot carry a ghost subject past a master or
  checkpoint landing.
* The re-anchor check reads every **open** history file.
* At a commit that **publishes a leaf** (``leaf_publication``: closeout, direct landing, the recorded
  landing of a leaf), the re-anchor check also reads every history file that is not closed in a
  comparison base, whatever its own ``closed`` flag says. The leaf's own file stays editable until its
  closeout commit writes ``closed: true`` (MIK-R07 rule 7), so a flag set earlier -- by hand, or
  left by a refused attempt -- is never a waiver.
* A file closed in a base is frozen (MIK-R22 rule 7 keeps it byte-identical) and historical: its rows
  describe the tree it closed on. The same holds for a file closed in the commit the candidate sits
  on (``frozen``): an earlier closeout of the same leaf that was not integrated closed it, and the
  leaf's later rows are in its next attempt file (L37 ruling B). At a master or checkpoint landing,
  or a sync, the master's leaves' closed files are such records, re-anchor-checked by their own
  closing commits, and are not re-anchor-checked again (a later leaf may re-anchor an entry an
  earlier row covered).
* All of a leaf's attempt files are its history, and the latest judgment governs (L37 ruling B): a
  row about a subject that a later attempt of the same owner answers again is superseded and is not
  re-anchor-checked, whether or not its file is frozen. The later row is.

**A change the leaf made stays visible (L37 ruling of 2026-10-01T22:49:36, Q2 b; review R5-2).** At a
commit that publishes a leaf, every history file not closed in a comparison base holds work that is
not on the leaf's parent line yet. Among those files, a ``changed`` row about an invariant is the
judgment of a revision step, with its effect and its because. A later attempt's row about the same
invariant governs, so it must be a ``changed`` row again (the step restated, or the next step) or
the ``deleted`` row that retires the record: a ``no_impact``, ``moved`` or ``extended`` row there
would replace the judgment and is refused (:func:`_hidden_changes`). The gate holds the same rule
against the record's revision on the parent line (``invariant_row_open``); this one needs no
converted base, and runs at a leaf's recorded landing, which the worklist does not reach.

A family has one governing row as well (MIK-R07 rule 5; MIK-R06 rule 3 reads "the latest family
row"), so the same holds for a ``changed`` family row: a later ``no_impact``, ``assigned`` or
``rerouted`` row would replace it and is refused; the ``changed`` row named again, or the
``retired`` row, is not. A ``changed`` row answers the family's route conditions too, so route
work a later attempt does is recorded under it (L37 ruling of 2026-10-02T01:04:49). The gate holds
the same rule against the guarantee on the parent line (``family_row_open``).

**Rows the merge moved (MIK-R09 Failure and Recovery).** "After a sync, items are recomputed from the
new base … stale or new items reopen." At a merge, a row that agreed with the entries of a parent
holding the same file and the same row, and that disagrees only because the other side moved an
entry, is reported by ``R09-history-rows-merged`` and does not refuse the merge: the leaf's own
next closeout checks it again, against its new base, and refuses there until the curator re-records
the row. A row that already disagreed on the leaf's own side still refuses the merge.

The revision and examined-member bindings are *currentness*, not validity: a row whose invariant
changed revision afterwards reopens its item at the gate (MIK-R09 rule 2) and is not invalid here.

``writer_reports``: inside the writer these rules only report, because the writer refuses its own
owner's rows through its whole-file check and a leaf may repair another file before closeout; every
commit route refuses on the refusing rule.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from agents_remember.memory_quality.knowledge_validator.parsed import parse_sidecars_leniently
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
)
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.canonical import CanonicalFormatError
from agents_remember.models.knowledge_files.documents import parse_history_document
from agents_remember.models.knowledge_files.history import (
    FamilyRow,
    HistoryFile,
    InvariantRow,
    is_closed_history,
    keeps_change_visible,
    reanchor_mismatches,
    sidecar_entry_anchors,
    unknown_subjects,
)
from agents_remember.models.knowledge_files.shapes import Anchor
from agents_remember.models.knowledge_files.sidecars import FileSidecar

__all__ = [
    "HISTORY_ROWS_MERGED_RULE",
    "HISTORY_ROWS_RULE",
    "check_history_rows",
    "checked_history_files",
]


def checked_history_files(context: ValidationContext) -> list[tuple[str, HistoryFile]]:
    """The candidate's history files the re-anchor check reads (see the module docstring)."""

    checked = []
    for path, history in context.parsed.histories:
        if not history.closed:
            checked.append((path, history))
        elif context.leaf_publication and not any(
            is_closed_history(base.get(path)) for base in context.closed_before
        ):
            checked.append((path, history))  # the leaf's own file: closed here, not before
    return checked


def _governing_attempts(context: ValidationContext) -> dict[tuple[str, str], int]:
    """``(owner, subject) -> attempt`` of the owner's latest row about the subject in the candidate."""

    latest: dict[tuple[str, str], int] = {}
    for _path, history in context.parsed.histories:
        for row in history.rows:
            key = (history.owner_id, row.subject)
            latest[key] = max(latest.get(key, 0), history.attempt_number)
    return latest


def _anchors(sidecars: Iterator[FileSidecar] | tuple[FileSidecar, ...]) -> dict[str, Anchor]:
    anchors: dict[str, Anchor] = {}
    for sidecar in sidecars:
        anchors.update(
            sidecar_entry_anchors(sidecar.path, (*sidecar.realizes, *(sidecar.proves or ())))
        )
    return anchors


def _candidate_anchors(context: ValidationContext) -> dict[str, Anchor]:
    return _anchors(
        tuple(
            one.sidecar for one in context.parsed.sidecars if isinstance(one.sidecar, FileSidecar)
        )
    )


@dataclass
class _BaseRows:
    """A merge parent's rows and entry anchors, read only when a row disagrees."""

    tree: KnowledgeTree
    _anchors: dict[str, Anchor] | None = None

    def anchors(self) -> dict[str, Anchor]:
        if self._anchors is None:
            self._anchors = _anchors(
                tuple(
                    one.sidecar
                    for one in parse_sidecars_leniently(self.tree)
                    if isinstance(one.sidecar, FileSidecar)
                )
            )
        return self._anchors

    def agreed(self, path: str, row: InvariantRow) -> bool:
        """Whether this parent holds the same row, and it agreed with this parent's entries."""

        data = self.tree.get(path)
        if data is None:
            return False
        try:
            history = parse_history_document(path, data.decode("utf-8"))
        except (UnicodeDecodeError, CanonicalFormatError, ValueError):
            return False
        same = history.row_about(row.subject)
        return same == row and not reanchor_mismatches(row, self.anchors())


def _mismatches(context: ValidationContext) -> Iterator[tuple[str, InvariantRow, list[str], bool]]:
    """``(path, row, mismatched entries, moved by the merge)`` for every disagreeing row."""

    histories = checked_history_files(context)
    if not histories:
        return
    anchors = _candidate_anchors(context)
    parents = [_BaseRows(base) for base in context.bases] if len(context.bases) > 1 else []
    governing = _governing_attempts(context)
    for path, history in histories:
        for row in history.rows:
            if not isinstance(row, InvariantRow):
                continue
            if governing[history.owner_id, row.subject] != history.attempt_number:
                continue  # a later attempt of the same owner answers the subject again
            mismatched = reanchor_mismatches(row, anchors)
            if mismatched:
                moved = any(parent.agreed(path, row) for parent in parents)
                yield path, row, mismatched, moved


Judgment = InvariantRow | FamilyRow


def _unlanded_judgments(
    context: ValidationContext,
) -> Iterator[tuple[tuple[str, str], str, Judgment]]:
    """``((owner, subject), path, row)`` for every invariant and family row of a history file that
    no comparison base holds closed, each owner's attempts in order."""

    unlanded = sorted(
        (
            (history.owner_id, history.attempt_number, path, history)
            for path, history in context.parsed.histories
            if not any(is_closed_history(base.get(path)) for base in context.bases)
        ),
        key=lambda one: one[:2],
    )
    for owner, _attempt, path, history in unlanded:
        for row in history.rows:
            if isinstance(row, InvariantRow | FamilyRow):
                yield (owner, row.subject), path, row


def _hidden_changes(context: ValidationContext) -> Iterator[tuple[str, Judgment, Judgment]]:
    """``(path, governing row, the changed row it replaces)`` for every invariant and family the
    publishing leaf changed and then answered again with a row of another disposition (module
    docstring)."""

    if not context.leaf_publication:
        return
    answers: dict[tuple[str, str], list[tuple[str, Judgment]]] = {}
    for key, path, row in _unlanded_judgments(context):
        answers.setdefault(key, []).append((path, row))
    for *earlier, (path, row) in answers.values():
        hidden = [one for _path, one in earlier if one.disposition == "changed"]
        if hidden and not keeps_change_visible(row):
            yield path, row, hidden[-1]


def _hidden_change_message(row: Judgment, hidden: Judgment) -> str:
    if isinstance(hidden, InvariantRow):
        what = f"(revision {hidden.revision}, effect {hidden.effect})"
        again = "with the entries this row covers"
    else:
        what = "(the family's own change)"
        again = (
            "with the members this row examined; a changed row answers the family's route "
            "conditions too (MIK-R06 rule 3)"
        )
    return (
        f"the {row.disposition} row about {row.subject} replaces this leaf's changed row "
        f"{hidden.id} {what}; the governing row of a record the leaf changed is its changed row: "
        f"name that changed row again through the writer, {again}"
    )


def check_history_rows(context: ValidationContext) -> Iterator[Finding]:
    for path, history in context.parsed.histories:  # every file: a subject never disappears
        for subject in unknown_subjects(history, context.record_ids):
            row = history.row_about(subject)
            yield Finding(
                path,
                f"rows.{row.id if row is not None else subject}.subject",
                f"the row's subject {subject} names no record of this tree; a row is about an "
                "existing invariant or family (MIK-R07 failure rule)",
            )
    for path, row, mismatched, moved in _mismatches(context):
        if not moved:
            yield Finding(
                path,
                f"rows.{row.id}.covers",
                f"covered entries {mismatched} of the row about {row.subject} no longer carry "
                "its 'after' anchor in this tree (MIK-R07 rule 4); name the row again through "
                "the writer so it records the entries' anchors",
            )
    for path, row, hidden in _hidden_changes(context):
        yield Finding(path, f"rows.{row.id}.disposition", _hidden_change_message(row, hidden))


def check_merged_history_rows(context: ValidationContext) -> Iterator[Finding]:
    for path, row, mismatched, moved in _mismatches(context):
        if moved:
            yield Finding(
                path,
                f"rows.{row.id}.covers",
                f"the merge moved covered entries {mismatched} of the row about {row.subject}; "
                "the leaf's next gate checks the row against its new base (MIK-R09)",
            )


HISTORY_ROWS_RULE = register_rule(
    ValidationRule(
        "R09-history-rows",
        "MIK-R09 (carried from MIK-R12)",
        "history rows being published name existing records and their entries' anchors",
        check_history_rows,
        writer_reports=True,
    )
)
HISTORY_ROWS_MERGED_RULE = register_rule(
    ValidationRule(
        "R09-history-rows-merged",
        "MIK-R09 (sync)",
        "a row the merge itself moved an entry under is reported, and re-checked at the gate",
        check_merged_history_rows,
        report_only=True,
    )
)
