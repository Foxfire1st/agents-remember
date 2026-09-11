"""Raw task-section shape boundary and canonical register scaffolding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agents_remember.worktrees.queue.closeout_queue_evidence import (
    register_scaffold_sections,
)

from .task_doc_route_review import TaskDocError

_MISSING = object()


def scaffold_register_sections(data: dict[str, Any]) -> None:
    """Validate raw section shape, then append missing planning registers.

    This boundary proves only the list/mapping operations scaffolding needs.
    The task-document model and register validator remain the semantic owners.
    """

    raw_sections = data.get("sections", _MISSING)
    if raw_sections is _MISSING:
        if not _requires_register_scaffolding(data):
            return
        sections: list[Mapping[str, Any]] = []
    else:
        sections = _validated_section_list(raw_sections)
    data["sections"] = sections
    if not _requires_register_scaffolding(data):
        return
    present = {str(section.get("heading", "")).strip().casefold() for section in sections}
    for scaffold in register_scaffold_sections():
        if scaffold["heading"].strip().casefold() not in present:
            sections.append(dict(scaffold))


def _validated_section_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise TaskDocError(
            "invalid task document: sections must be a list before register scaffolding"
        )
    for index, section in enumerate(value):
        if not isinstance(section, Mapping):
            raise TaskDocError(
                "invalid task document: "
                f"sections[{index}] must be an object before register scaffolding"
            )
    return list(value)


def _requires_register_scaffolding(data: Mapping[str, Any]) -> bool:
    return data.get("kind") == "master" and bool(data.get("orchestrates"))
