"""Retired rows: what they are, and the one refusal for every route that would change one.

A sprint row that carries a retirement proof is the record of a master's retirement. Only
``task_doc.retire_master`` writes it; every other writer of rows refuses with the text below, which
names the row by its number.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .document import TaskDocument


def retired_row_message(document_id: str, numbers: list[str], action: str) -> str:
    rows = ", ".join(repr(number) for number in numbers)
    return (
        f"{action} would change or remove row {rows} of {document_id!r}, which records a master "
        "retirement; a retirement is recorded only by task_doc.retire_master and cannot be "
        "edited or removed by a generic task-document operation. Leave that row as it is"
    )


def records_retirements(document: TaskDocument) -> bool:
    """Whether the document holds a retired row; a sprint that lost its last master still does."""

    return any(row.retirement is not None for row in document.subTasks)


def payload_is_sprint(payload: Mapping[str, Any]) -> bool:
    """Whether a task document that is still raw JSON is a sprint.

    The same answer as ``TaskDocument.is_sprint``, for a reader that decides before it validates:
    a master that commands masters, or one that holds a retired row and commands none any more.
    """

    if payload.get("kind") != "master":
        return False
    if payload.get("orchestrates"):
        return True
    rows = payload.get("subTasks")
    return isinstance(rows, list) and any(
        isinstance(row, Mapping) and row.get("retirement") is not None for row in rows
    )


def refuse_retired_row(
    document: TaskDocument, number: str, action: str, error: Callable[[str], Exception]
) -> None:
    """Refuse, in the caller's own error family, an operation that targets a retired row."""

    if any(row.number == number and row.retirement is not None for row in document.subTasks):
        raise error(retired_row_message(document.id, [number], action))
