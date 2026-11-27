"""History files a test writes itself, so a record's derived state is what the test arranged.

A revision's state (proposed, reviewed, consolidated) is never stored in a record; the index derives
it from the history files of the same tree. These helpers build the two parts the product writes
into a history file -- ``written`` (the revisions an owner wrote, with the writer's stamp) and
``review`` (the fact of the memory review that passed) -- as plain documents, so a test can place a
reviewed or consolidated revision without running a closeout.
"""

from __future__ import annotations

from typing import Any

from agents_remember.models.knowledge_files.documents import history_path

__all__ = [
    "consolidating",
    "history_document",
    "review_fact",
    "stamp",
    "written",
]

STAMP_AT = "2026-11-19T10:00:00+01:00"


def stamp(
    leaf: str | None, *, role: str = "curator", model: str = "fixture-model"
) -> dict[str, Any]:
    """One agent's writer stamp; ``leaf`` None stamps a write outside any owner."""

    document: dict[str, Any] = {
        "by": "agent",
        "role": role,
        "harness": "fixture-harness",
        "model": model,
        "effort": "high",
        "seat": f"seat-{role}",
        "task": "261112-FIX",
        "at": STAMP_AT,
    }
    if leaf is not None:
        document["leaf"] = leaf
    return document


def written(record: str, revision: int, leaf: str, **writer: Any) -> dict[str, Any]:
    """One ``written`` row: ``leaf`` wrote ``record`` at ``revision``."""

    return {"record": record, "revision": revision, "writer": stamp(leaf, **writer)}


def review_fact(leaf: str, *, handed_over_by: str = "curator") -> dict[str, Any]:
    """A passed memory review of ``leaf``'s change, handed over by ``handed_over_by``."""

    return {
        "round": 1,
        "reviewer": stamp(leaf, role="reviewer"),
        "handedOverBy": stamp(leaf, role=handed_over_by),
    }


def history_document(
    leaf: str,
    *,
    written_rows: tuple[dict[str, Any], ...] = (),
    review: dict[str, Any] | None = None,
    rows: tuple[dict[str, Any], ...] = (),
    attempt: int = 1,
) -> tuple[str, dict[str, Any]]:
    """The path and document of ``leaf``'s history file; a file with a review fact is closed."""

    document: dict[str, Any] = {
        "schema": "ar-history/v1",
        "leaf": leaf,
        "closed": review is not None,
        "rows": list(rows),
    }
    if attempt > 1:
        document["attempt"] = attempt
    if written_rows:
        document["written"] = list(written_rows)
    if review is not None:
        document["review"] = review
    return history_path(leaf, attempt), document


def consolidating(
    history: tuple[str, dict[str, Any]], *subjects: str
) -> tuple[str, dict[str, Any]]:
    """A consolidation task's history file: it names the records its pass's list reached."""

    path, document = history
    return path, {**document, "consolidationSubjects": sorted(subjects)}
