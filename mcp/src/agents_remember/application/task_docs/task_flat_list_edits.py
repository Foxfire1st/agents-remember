"""Flat task-document lists, edited inside the task owner's read/publication (MIK-R54)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .task_doc_route_review import TaskDocError


@dataclass(frozen=True)
class TaskMutationEdit:
    """The one edit payload bundle an ``_apply`` operation may draw from."""

    kind: str
    fields: dict[str, Any]
    step: dict[str, Any] | None
    decision: dict[str, Any] | None
    subtask: dict[str, Any] | None
    section: dict[str, Any] | None
    lifecycle_id: str | None


def append_decision(data: dict[str, Any], edit: TaskMutationEdit) -> None:
    if not edit.decision:
        raise TaskDocError("append_decision requires a decision object")
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(edit.decision)


def put_open_question(data: dict[str, Any], edit: TaskMutationEdit) -> None:
    supplied = edit.fields.get("questions", [edit.fields])
    if (
        not isinstance(supplied, list)
        or not supplied
        or not all(isinstance(one, dict) for one in supplied)
    ):
        raise TaskDocError("put_open_question questions must be a nonempty list of {key, text}")
    validated = [_question_fields(one) for one in supplied]
    questions = data.setdefault("openQuestions", [])
    for key, text in validated:
        for index, question in enumerate(questions):
            if isinstance(question, str) and question.startswith(key):
                questions[index] = text
                break
        else:
            questions.append(text)


def _question_fields(fields: dict[str, Any]) -> tuple[str, str]:
    key, text = fields.get("key"), fields.get("text")
    if (
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(text, str)
        or not text.startswith(key)
    ):
        raise TaskDocError(
            "put_open_question requires fields {key, text}; text starts with the nonempty key"
        )
    return key, text
