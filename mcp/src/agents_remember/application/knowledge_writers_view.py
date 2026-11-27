"""The ``writers`` view of ``knowledge_read``: one table says how each writer did.

The table is read from the index of the tree the read's selection names: the index stores every
revision a file of the tree names with its writer and whether it is reviewed
(:meth:`.KnowledgeIndex.writer_revisions`), and the counting is
:mod:`agents_remember.memory.knowledge_index.writer_table`. A read opens no history file.

The answer stays inside the tool answer bound: it holds at most the list bound of rows, most
revisions written first, each naming a writer (role, harness, model) and its counts of revisions,
the totals of the tree with their units, and the report. A label longer than
:data:`ANSWER_LABEL_MAX_CHARS` is shortened in the answer, and when the rows still do not fit the
last rows are left out and counted as remaining; the totals and the report's path always stay. The
report is a caller-readable file (:func:`.knowledge_reports.write_report`) that holds every
writer's row with its whole labels and, for every number, the revisions behind it as
``<record>@<revision>`` (``<record>@<first>..<last>`` for a run of revisions no file names). It is
reclaimed with the tool log folder it lies in. A report that cannot be written refuses the read
with the reason; nothing partial is left.

Beside each writer's written, reviewed, changed and retired counts, the row carries the
Consolidator's columns: of that writer's revisions a consolidation pass read, how many it kept,
corrected, merged, retired or left unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import apsw

from agents_remember.application.knowledge_reports import ReportWriteError, write_report
from agents_remember.application.published_intent import SelectedKnowledgeDataset
from agents_remember.memory.knowledge_index.query import IndexMismatchError, KnowledgeIndex
from agents_remember.memory.knowledge_index.writer_table import (
    COLUMNS,
    WriterTable,
    writer_table,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    bound_for,
)

WRITERS_VIEW: Final = "writers"
REPORT_STEM: Final = "knowledge-writers"
ENTRY_FORMAT: Final = (
    "<record>@<revision>; <record>@<first>..<last> is a run of revisions no file of the tree "
    "names, each written by the writer unknown"
)
_READ_ON: Final = (
    "each number counts revisions; the report holds every writer's row and the revisions behind "
    "each number"
)
# A writer label may hold 1,024 characters; twenty rows of three such labels exceed any answer
# bound, so the answer carries a prefix and the report the whole label.
ANSWER_LABEL_MAX_CHARS: Final = 64
_SHORTENED_MARK: Final = "…"
_READ_ON_SHORTENED: Final = (
    f"; a label longer than {ANSWER_LABEL_MAX_CHARS} characters ends in {_SHORTENED_MARK} here "
    "and stands whole in the report"
)
# The tool boundary adds the log location and the next step after this answer is built; this many
# tokens of the bound stay free for them.
_BOUNDARY_RESERVE: Final = TOOL_ANSWER_OVERFLOW_MAX_TOKENS


class WriterTableRefusal(Exception):
    """The table could not be answered for this selection: a refusal code and its sentence."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class WritersRequest:
    """What one ``writers`` read names: the memory root as given, its namespace, the row limit."""

    memory_root: str
    repository_id: str
    limit: int
    coordination_root: str | None


def writers_view(selected: SelectedKnowledgeDataset, request: WritersRequest) -> dict[str, Any]:
    """The writer table of the selected tree, bounded, beside the report of its records.

    Raises :class:`WriterTableRefusal` when the selection names no tree, the index of its tree
    cannot be read, or the report cannot be written.
    """

    tree = selected.memory_tree
    if tree is None:
        raise WriterTableRefusal(
            "snapshot_unavailable",
            "the selection names no converted memory tree, so no revision can be counted",
        )
    table = _table_of(selected.database_path, tree.tree_key)
    report = _write_table_report(table, request, tree.tree_key)
    shown = min(max(1, request.limit), TOOL_ANSWER_LIST_MAX)
    whole = [tally.row() for tally in table.tallies[:shown]]
    rows = [_answer_row(row) for row in whole]
    shortened = [row != one for row, one in zip(rows, whole, strict=True)]
    answer: dict[str, Any] = {
        "ok": True,
        "operation": "knowledge_read",
        "view": WRITERS_VIEW,
        "state": "view",
        "repositoryId": request.repository_id,
        "memoryRoot": Path(request.memory_root).as_posix(),
        "memoryTree": {"treeKey": tree.tree_key, "indexState": tree.index_state},
        "rows": rows,
        "counts": {
            "writers": len(table.tallies),
            "rowsReturned": len(rows),
            "revisions": table.revisions,
            "records": table.records,
        },
        "report": report.as_posix(),
        "readOn": {
            "how": _READ_ON + (_READ_ON_SHORTENED if any(shortened) else ""),
            "writersRemaining": len(table.tallies) - len(rows),
            "labelsShortened": sum(shortened),
        },
    }
    return _fitted(answer, shortened, len(table.tallies))


def _answer_row(row: dict[str, object]) -> dict[str, object]:
    """A writer's row as the answer carries it: each long label cut to its first characters."""

    return {
        name: _shortened(value) if name in ("role", "harness", "model") else value
        for name, value in row.items()
    }


def _shortened(value: object) -> object:
    if isinstance(value, str) and len(value) > ANSWER_LABEL_MAX_CHARS:
        return value[: ANSWER_LABEL_MAX_CHARS - 1] + _SHORTENED_MARK
    return value


def _fitted(answer: dict[str, Any], shortened: list[bool], writers: int) -> dict[str, Any]:
    """Leave out the last rows until the answer fits its bound; the rest are counted remaining.

    There are at most the list bound of rows, so the answer is measured at most that many times.
    """

    budget = bound_for("knowledge_read") - _BOUNDARY_RESERVE
    rows: list[dict[str, Any]] = answer["rows"]
    while rows and count_response_tokens(answer) > budget:
        rows.pop()
        shortened.pop()
        answer["counts"]["rowsReturned"] = len(rows)
        answer["readOn"]["writersRemaining"] = writers - len(rows)
        answer["readOn"]["labelsShortened"] = sum(shortened)
    return answer


def _table_of(index_path: Path, tree_key: str) -> WriterTable:
    """Count the tree the selection keyed, from its index: no history file is opened."""

    try:
        with KnowledgeIndex(index_path, expected_key=tree_key) as index:
            revisions, live = index.writer_revisions().value
    except (IndexMismatchError, apsw.Error) as error:
        raise WriterTableRefusal(
            "snapshot_unavailable",
            f"the index of the selected tree {tree_key} cannot be read ({error}); read the view "
            "again",
        ) from error
    return writer_table(revisions, live)


def _write_table_report(table: WriterTable, request: WritersRequest, tree_key: str) -> Path:
    payload = {
        "operation": "knowledge_read",
        "view": WRITERS_VIEW,
        "memoryRoot": request.memory_root,
        "treeKey": tree_key,
        "columns": list(COLUMNS),
        "entryFormat": ENTRY_FORMAT,
        "totals": {
            "writers": len(table.tallies),
            "revisions": table.revisions,
            "records": table.records,
        },
        "writers": [{**tally.row(), "revisions": tally.records()} for tally in table.tallies],
    }
    try:
        return write_report(REPORT_STEM, payload, coordination_root=request.coordination_root)
    except ReportWriteError as error:
        detail = error.reason
        if error.residual is None:
            detail += "; nothing partial was left behind"
        raise WriterTableRefusal("report-unwritable", detail) from error
