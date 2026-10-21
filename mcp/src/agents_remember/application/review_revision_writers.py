"""Who wrote each selected revision of the reviewed subject (MIK-R89 v2 rules 1 and 6, 9dde2b09).

The review page names nothing for the normal case: the task in its title wrote the change. A change
the reviewed task did not write -- one a sync carried in -- gets one short chip that names its
source, so the read serves each selected revision's writer as its own tree's history files record it
(MIK-R07):

* a record file holds one revision; an owner's ``changed`` row about the record at that revision
  records writing it (an invariant's revision increments exactly once per changing leaf, MIK-R21
  rule 4; a family's changed row records its own revision, MIK-R48 rule 3). Rows of the other
  dispositions judged the revision and wrote nothing;
* revision 1 that no row claims is the record's introduction by its ``origin``;
* any other revision has no recorded writer, which is said as that: nothing is inferred from Git,
  dates or the reviewed leaf;
* a tree whose history cannot be read in full answers ``unavailable``, never "no writer".

Each side is read from its own tree's index, the one the comparison already opened.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import apsw

from agents_remember.application.review_revision_comparison import SubjectRevisionSelection
from agents_remember.application.review_statement_sides import read_side
from agents_remember.memory.knowledge_index import (
    HistoryRow,
    IndexMismatchError,
    KnowledgeIndex,
)
from agents_remember.models.knowledge.diff import KnowledgeDiffItem
from agents_remember.models.knowledge.revision_selection import ReviewRevisionWriter

__all__ = ["with_revision_writers"]


def with_revision_writers(
    selected: SubjectRevisionSelection,
    *,
    before_database: Path | None,
    after_database: Path | None,
    legacy_detail: str | None = None,
) -> SubjectRevisionSelection:
    """The selection with each selected revision's writer; ``legacy_detail`` names a tree with no
    history files (an unconverted comparison), whose writers are unavailable."""

    selection = selected.selection
    if selection is None:
        return selected
    writers = {
        "before_writer": _side_writer(
            before_database, selected.before_item, "before", legacy_detail
        ),
        "after_writer": _side_writer(after_database, selected.after_item, "after", legacy_detail),
    }
    return replace(selected, selection=selection.model_copy(update=writers))


def _side_writer(
    database: Path | None, item: KnowledgeDiffItem | None, side: str, legacy_detail: str | None
) -> ReviewRevisionWriter | None:
    read = read_side(item, side)
    if read is None or read.record_id is None:
        return None
    if legacy_detail is not None or database is None:
        return _unavailable(legacy_detail or f"the {side} tree's index is not open")
    try:
        with KnowledgeIndex(database) as index:
            return _read_writer(index, read.record_id, side)
    except (apsw.Error, IndexMismatchError, OSError) as error:
        return _unavailable(f"the {side} tree's index cannot be read: {error}")


def _read_writer(index: KnowledgeIndex, addressed: str, side: str) -> ReviewRevisionWriter:
    raw = index.text_id(addressed)
    if raw is None:
        return _unavailable(f"the {side} tree's index names no record for {addressed}")
    rows = index.history_rows_about(raw)
    if not rows.index.complete:
        return _unavailable(
            f"the {side} memory index is incomplete ({len(rows.index.problems)} file(s) failed); "
            "history it did not read cannot name a writer"
        )
    record = index.record(raw).value
    if record is None or record.revision is None:
        return _unavailable(f"the {side} tree holds no revision of {raw}")
    revision = record.revision
    owners = tuple(dict.fromkeys(row.owner for row in rows.value if _wrote(row, revision)))
    if owners:
        return ReviewRevisionWriter(
            state="recorded",
            owners=owners,
            detail=f"revision {revision} of {raw} is the changed row of {', '.join(owners)}",
        )
    introducer = _introducer(record.document.get("origin")) if revision == 1 else None
    if introducer is not None:
        return ReviewRevisionWriter(
            state="introduced",
            owners=(introducer,),
            detail=f"revision 1 of {raw} is its introduction by {introducer}",
        )
    return ReviewRevisionWriter(
        state="not_recorded",
        detail=f"no history file records writing revision {revision} of {raw}",
    )


def _wrote(row: HistoryRow, revision: int) -> bool:
    return row.disposition == "changed" and row.document.get("revision") == revision


def _introducer(origin: object) -> str | None:
    """The leaf or wave of a record's ``origin``, else its task."""

    if not isinstance(origin, dict):
        return None
    for key in ("leaf", "wave", "task"):
        value = origin.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _unavailable(detail: str) -> ReviewRevisionWriter:
    return ReviewRevisionWriter(state="unavailable", detail=detail)
