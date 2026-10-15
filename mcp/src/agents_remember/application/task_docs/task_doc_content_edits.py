"""Scalar and prose mutations of a task document's prepared content."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .task_doc_phrases import _MUTABLE_FIELDS
from .task_doc_route_review import TaskDocError

if TYPE_CHECKING:
    from .task_doc_tools import _Edit


def _apply_set_status(data: dict[str, Any], edit: _Edit) -> None:
    status = edit.fields.get("status")
    if status is None:
        raise TaskDocError("set_status requires fields.status")
    data["status"] = status


def _apply_set_field(data: dict[str, Any], edit: _Edit) -> None:
    updates = {key: value for key, value in edit.fields.items() if key in _MUTABLE_FIELDS}
    if not updates:
        raise TaskDocError(
            f"set_field requires at least one of: {', '.join(sorted(_MUTABLE_FIELDS))}"
        )
    data.update(updates)


def _apply_append_decision(data: dict[str, Any], edit: _Edit) -> None:
    if not edit.decision:
        raise TaskDocError("append_decision requires a decision object")
    decisions: list[dict[str, Any]] = data.setdefault("decisions", [])
    decisions.append(edit.decision)


def _upsert_subtask(data: dict[str, Any], subtask: dict[str, Any]) -> None:
    number = subtask.get("number")
    if not number:
        raise TaskDocError("set_subtask requires subtask.number")
    refs: list[dict[str, Any]] = data.setdefault("subTasks", [])
    updates = {key: subtask[key] for key in ("name", "file", "status", "scope") if key in subtask}
    existing = next((ref for ref in refs if ref.get("number") == number), None)
    if existing is None:
        new_ref: dict[str, Any] = {"number": str(number), "name": subtask.get("name", str(number))}
        new_ref.update(updates)
        refs.append(new_ref)
    else:
        existing.update(updates)


def _upsert_section(data: dict[str, Any], section: dict[str, Any]) -> None:
    heading = section.get("heading")
    if not heading:
        raise TaskDocError("set_section requires section.heading")
    sections: list[dict[str, Any]] = data.setdefault("sections", [])
    updates = {key: section[key] for key in ("kind", "body") if key in section}
    existing = next((sec for sec in sections if sec.get("heading") == heading), None)
    if existing is None:
        new_section: dict[str, Any] = {"heading": heading}
        new_section.update(updates)
        sections.append(new_section)
    else:
        existing.update(updates)
