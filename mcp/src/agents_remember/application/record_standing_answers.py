"""How a record's standing travels in the reader's answers: its state and its writer.

Where a record is shown, its state (proposed, reviewed or consolidated) and the
writer of its current revision (role and model) are shown with it. Both come from the knowledge
index of the tree that was read (:meth:`KnowledgeIndex.standing`), which derived them when it was
built, so no answer searches the history files.

The keys are ``recordState`` and ``writer``. ``state`` stays the name of a record's currentness
(current, stale, unverifiable) in the tree views' rows, so the derived state never overloads it.
A retired record has no derived state. An indexed revision without an attributable stamp shows
an explicit unknown writer. A result without projected standing has no writer attribution.

* the record view carries the whole writer: role, harness and model; so does each record summary
  of the dashboard reader's lists (a path's records, the record list, a family's members);
* a tree view's row carries ``role/model`` as one short string, since a page holds many rows and
  the answer is bounded (:mod:`agents_remember.models.tools.tool_answer`);
* the record answer of ``knowledge_read`` repeats both at its top as short scalars, so the overflow
  short form, which keeps only short top-level scalars, still names them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import apsw

from agents_remember.memory.knowledge_index import (
    IndexMismatchError,
    KnowledgeIndex,
    RecordStanding,
    RecordWriter,
)

__all__ = [
    "RowStandings",
    "compact_writer",
    "record_answer_standing",
    "stamp_summaries",
    "standing_fields",
]

RECORD_STATE_KEY = "recordState"
WRITER_KEY = "writer"


def standing_fields(standing: RecordStanding | None) -> dict[str, Any]:
    """The record view's state and whole writer; ``None`` for each the record has not."""

    writer = None if standing is None else standing.writer
    return {
        RECORD_STATE_KEY: None if standing is None else standing.state,
        WRITER_KEY: None
        if writer is None
        else {"role": writer.role, "harness": writer.harness, "model": writer.model},
    }


def compact_writer(writer: RecordWriter) -> str:
    """The writer as a row shows it: ``role/model``."""

    return f"{writer.role}/{writer.model}"


def record_answer_standing(found: Mapping[str, Any]) -> dict[str, Any]:
    """The record view's state and ``role/model`` writer as top-level scalars of its answer."""

    record = found.get("record")
    if not isinstance(record, Mapping):
        return {}
    writer = record.get(WRITER_KEY)
    return {
        RECORD_STATE_KEY: record.get(RECORD_STATE_KEY),
        WRITER_KEY: f"{writer['role']}/{writer['model']}" if isinstance(writer, Mapping) else None,
    }


_SUMMARY_KEYS = frozenset({"id", "kind", "revision", "title"})


def stamp_summaries[D: (dict[str, Any], list[Any])](index: KnowledgeIndex, document: D) -> D:
    """Put ``recordState`` and the whole ``writer`` on every record summary inside ``document``.

    A summary is the reader's link to a record (``id``, ``kind``, ``revision``, ``title``); its
    standing is read from the same index the answer was built from, by one query. A summary of a
    record the tree does not hold, and a row that is no record, stays as it is.
    """

    summaries = list(_summaries_in(document))
    if not summaries:
        return document
    found = index.standings([one["id"] for one in summaries]).value
    for summary in summaries:
        standing = found.get(summary["id"])
        if standing is not None:
            summary.update(standing_fields(standing))
    return document


def _summaries_in(node: Any) -> Iterable[dict[str, Any]]:
    if isinstance(node, dict):
        if node.keys() >= _SUMMARY_KEYS and isinstance(node["id"], str) and not node.get("missing"):
            yield node
        for inner in node.values():
            yield from _summaries_in(inner)
    elif isinstance(node, list):
        for inner in node:
            yield from _summaries_in(inner)


@dataclass
class RowStandings:
    """The standings one projected answer's rows need, read from its index once per record.

    The tree views size a page by projecting it more than once; the standings already read are
    kept, so a repeated projection asks the index only for records it has not seen.
    """

    index_path: Path
    tree_key: str
    _known: dict[str, RecordStanding | None] = field(default_factory=dict, repr=False)

    def stamp(self, projected: Mapping[str, Any]) -> None:
        """Put ``recordState`` and ``writer`` on every projected row that names a record."""

        rows = list(_rows_of(projected))
        self._read(row["id"] for row in rows if isinstance(row.get("id"), str))
        for row in rows:
            standing = self._known.get(row["id"]) if isinstance(row.get("id"), str) else None
            if standing is None:
                continue
            if standing.state is not None:
                row[RECORD_STATE_KEY] = standing.state
            row[WRITER_KEY] = compact_writer(standing.writer)

    def _read(self, ids: Iterable[str]) -> None:
        missing = sorted({one for one in ids if one not in self._known})
        if not missing:
            return
        try:
            with KnowledgeIndex(self.index_path, expected_key=self.tree_key) as index:
                found = index.standings(missing).value
        except (apsw.Error, IndexMismatchError, OSError):
            # The rows keep their names and currentness; an index that cannot be read here names
            # no state rather than a guessed one.
            found = {}
        for one in missing:
            self._known[one] = found.get(one)


def _rows_of(projected: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    payload = projected.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        yield from (row for row in payload["rows"] if isinstance(row, dict))
    families = projected.get("families")
    if isinstance(families, list):
        yield from (row for row in families if isinstance(row, dict))
