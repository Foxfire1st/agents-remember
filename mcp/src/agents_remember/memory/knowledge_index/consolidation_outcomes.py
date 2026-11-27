"""What a consolidation pass did with each revision it read, for the writer table.

A consolidation task's history file names the records its pass's list reached
(``consolidationSubjects``). Each of its rule and family rows names a revision: the one it read,
or for a ``changed`` row the one it wrote, whose predecessor is the one it read. The outcome is
counted for the revision read, and so for that revision's writer:

* **corrected** -- a ``changed`` row: the Consolidator wrote a successor revision;
* **merged** -- the row retired the record (``deleted`` with the effect ``retire``, or a family's
  ``retired``) and a successor record names it in ``supersedes``, which a split is too;
* **retired** -- the row retired the record and no record names it as its predecessor;
* **unresolved** -- an ``unresolved`` row;
* **kept** -- any other row, where the revision read reads consolidated (a ``stands`` row, or a
  row whose card entries alone changed); the caller checks that state.

Several passes can read one revision; the outcome that tells most about its fate wins: a successor
or a retirement over unresolved, and unresolved over kept. The work is one pass over the rows of the
consolidation tasks' files and one over the records.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

from agents_remember.models.knowledge_files.history import FamilyRow, HistoryFile, InvariantRow
from agents_remember.models.knowledge_files.history_consolidation import UNRESOLVED
from agents_remember.models.knowledge_files.records import InvariantRecord, KnowledgeRecord

__all__ = ["OUTCOME_COLUMNS", "Outcome", "consolidation_outcomes"]

Outcome = Literal["kept", "corrected", "merged", "retired", "unresolved"]
# Each outcome's column in the writer table; ``retired`` is taken by the table's own column.
OUTCOME_COLUMNS: Final[dict[str, str]] = {
    "kept": "consolidationKept",
    "corrected": "consolidationCorrected",
    "merged": "consolidationMerged",
    "retired": "consolidationRetired",
    "unresolved": "consolidationUnresolved",
}
_WEIGHT: Final[dict[str, int]] = {
    "kept": 0,
    "unresolved": 1,
    "corrected": 2,
    "merged": 2,
    "retired": 2,
}


def consolidation_outcomes(
    histories: Iterable[HistoryFile], records: Iterable[KnowledgeRecord]
) -> dict[tuple[str, int], Outcome]:
    """``(record, revision read) -> outcome`` over every consolidation task's history file."""

    succeeded = {
        predecessor
        for record in records
        if isinstance(record, InvariantRecord)
        for predecessor in record.supersedes
    }
    outcomes: dict[tuple[str, int], Outcome] = {}
    for history in histories:
        if not history.consolidation_subjects:
            continue
        for row in history.rows:
            if not isinstance(row, InvariantRow | FamilyRow) or row.revision is None:
                continue
            outcome = _outcome(row, succeeded)
            read = row.revision - 1 if outcome == "corrected" else row.revision
            named = (row.subject, read)
            if read >= 1 and _WEIGHT[outcome] >= _WEIGHT[outcomes.get(named, "kept")]:
                outcomes[named] = outcome
    return outcomes


def _outcome(row: InvariantRow | FamilyRow, succeeded: set[str]) -> Outcome:
    if row.disposition == UNRESOLVED:
        return "unresolved"
    if row.disposition == "changed":
        return "corrected"
    retiring = row.disposition == "retired" or (
        row.disposition == "deleted" and row.effect == "retire"
    )
    if retiring:
        return "merged" if row.subject in succeeded else "retired"
    return "kept"
