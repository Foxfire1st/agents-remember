"""Side currentness and governing history rows of an exact review comparison."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_currentness import CodeTree, invariant_currentness
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.memory.knowledge_index import HistoryRow, KnowledgeIndex
from agents_remember.models.knowledge_files.documents import owner_history_attempt


def side_currentness(trees: ReviewTrees) -> dict[str, dict[str, Any]]:
    """Each side's MIK-R03 currentness, at that side's own code tree; an unread side says why."""

    record = trees.record
    code = {
        "before": CodeTree(Path(record.code_base.repository), record.code_base.tree),
        "after": CodeTree(Path(record.code_candidate.repository), record.code_candidate.tree),
    }
    result: dict[str, dict[str, Any]] = {}
    for side in (trees.before, trees.after):
        name = side.wire.side
        if side.database is None:
            result[name] = {"unverifiableReason": side.wire.detail or side.wire.state}
            continue
        with KnowledgeIndex(side.database) as index:
            currentness = invariant_currentness(
                code[name],
                index,
                index.record_ids("invariant").value,
                index.record_ids("family").value,
            )
            document = currentness.to_document()
            # Review responses retain the MIK-R03 listing: all entries are checked,
            # but only entries that are not current are listed (MIK-R81 preservation).
            for invariant in document["invariants"]:
                invariant["entries"] = [
                    entry for entry in invariant["entries"] if entry["state"] != "current"
                ]
            result[name] = {**document, "indexState": index.state.state}
    return result


def history_rows(
    trees: ReviewTrees, items: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    """The after tree's history rows about the items' subjects, with no currency mark (rule 3).

    An item names the subject its answering row carries in ``facts.row`` when that is not its own
    subject (an unexplained hunk is answered by a ``hunk:…`` row, MIK-R10), so both are looked up.
    Of one owner's rows about a subject only its latest attempt's is shown: a leaf that continued
    after a closeout answers the subject again in its next attempt file, and that judgment governs
    (L37 ruling B). The superseded row stays in its frozen file, read on the change-set page.
    """

    database = trees.after.database
    subjects = sorted({subject for item in items for subject in _row_subjects(item)})
    if database is None or not subjects:
        return ()
    rows: list[dict[str, Any]] = []
    with KnowledgeIndex(database) as index:
        for subject in subjects:
            rows += [
                {
                    "id": row.id,
                    "owner": row.owner,
                    "ownerKind": row.owner_kind,
                    "closed": row.closed,
                    "path": row.path,
                    "subject": row.subject,
                    "disposition": row.disposition,
                    "row": row.document,
                }
                for row in _governing(index.history_rows_about(subject).value)
            ]
    return tuple(rows)


def _governing(rows: tuple[HistoryRow, ...]) -> Iterator[HistoryRow]:
    """Each owner's row about one subject from that owner's latest attempt file."""

    attempts = [owner_history_attempt(row.path, row.owner) or 1 for row in rows]
    latest: dict[str, int] = {}
    for row, attempt in zip(rows, attempts, strict=True):
        latest[row.owner] = max(latest.get(row.owner, 0), attempt)
    for row, attempt in zip(rows, attempts, strict=True):
        if attempt == latest[row.owner]:
            yield row


def _row_subjects(item: Mapping[str, Any]) -> Iterator[str]:
    if item.get("subject"):
        yield str(item["subject"])
    facts = item.get("facts")
    row = facts.get("row") if isinstance(facts, Mapping) else None
    if isinstance(row, str) and row:
        yield row
