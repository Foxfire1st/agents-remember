"""MIK-R128 write-time field homes, grandfathering, and exact waitsFor validation.

This owner is only called by task_doc's candidate validation, never a start guard.
Legacy fields remain readable; a dependency list is checked on its next explicit
write. Reserved prose homes are checked only for newly created entries.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.tasks import TaskDocument, read_task_doc
from agents_remember.tasks.master_order import dependency_cycles, load_master_children

from .task_doc_route_review import TaskDocError

# One case-insensitive list owns the fixed headings and their authoring remedies.
RESERVED_SECTION_HEADINGS: dict[str, str] = {
    "objective": "task_doc.set_field fields.objective",
    "objectives": "task_doc.set_field fields.objective",
    "requirements": "task_doc.set_field fields.requirements",
    "requirement": "task_doc.set_field fields.requirements",
    "design": "task_doc.set_field fields.design",
    "non-goals": "task_doc.set_field fields.nonGoals",
    "non goals": "task_doc.set_field fields.nonGoals",
    "nongoals": "task_doc.set_field fields.nonGoals",
    "acceptance": "task_doc.set_field fields.acceptance",
    "acceptance criteria": "task_doc.set_field fields.acceptance",
    "decisions": "task_doc.append_decision",
    "shared decisions": "task_doc.append_decision",
    "decision log": "task_doc.append_decision",
    "sub-tasks": "task_doc.set_subtask",
    "subtasks": "task_doc.set_subtask",
    "sub tasks": "task_doc.set_subtask",
    "tasks": "task_doc.set_subtask",
    "order": "task_doc.set_field fields.waitsFor",
    "execution order": "task_doc.set_field fields.waitsFor",
    "task order": "task_doc.set_field fields.waitsFor",
    "order of tasks": "task_doc.set_field fields.waitsFor",
    "execution graph": "task_doc.set_field fields.waitsFor",
    "dependency graph": "task_doc.set_field fields.waitsFor",
    "sprint dependency graph": "task_doc.set_field fields.waitsFor",
}

RESERVED_HEADER_LABELS: dict[str, str] = {
    "master": "master",
    "parent sprint": "master",
    "sprint": "master",
    "dependency": "waitsFor",
    "dependencies": "waitsFor",
    "depends on": "waitsFor",
    "requires": "waitsFor",
    "waits for": "waitsFor",
    "wave": "waitsFor",
    "execution nature": "executionNature",
    "executionnature": "executionNature",
    "integration branch": "integrationBranch",
    "integrationbranch": "integrationBranch",
}


def _name(value: str) -> str:
    return value.strip().casefold()


def enforce_prose_field_homes(original: TaskDocument | None, candidate: TaskDocument) -> None:
    """Existing headings/labels can be edited or removed, not duplicated as new ones."""
    previous_sections = (
        Counter(
            _name(section.heading) for section in original.sections if section.kind == "freeform"
        )
        if original
        else Counter()
    )
    for section in candidate.sections:
        if section.kind != "freeform":
            continue
        heading = _name(section.heading)
        if previous_sections[heading]:
            previous_sections[heading] -= 1
        elif remedy := RESERVED_SECTION_HEADINGS.get(heading):
            if candidate.is_sprint and "fields.waitsFor" in remedy:
                remedy = "task_doc.author_execution_graph fields.executionGraph"
            raise TaskDocError(
                f"new free-form section {section.heading!r} repeats a fixed heading; use {remedy}."
            )
    previous_notes = (
        Counter(_name(note.label) for note in original.headerNotes) if original else Counter()
    )
    for note in candidate.headerNotes:
        label = _name(note.label)
        if previous_notes[label]:
            previous_notes[label] -= 1
        elif field := RESERVED_HEADER_LABELS.get(label):
            raise TaskDocError(
                f"new header note {note.label!r} restates field {field}; use that field."
            )


def enforce_waits_for_write(
    task_root: Path,
    operation: str,
    fields: Mapping[str, Any],
    candidate: TaskDocument,
) -> None:
    """Check only an explicitly supplied field, leaving old gone refs untouched otherwise."""
    if operation not in {"create", "replace", "set_field"} or "waitsFor" not in fields:
        return
    if candidate.waitsFor is None or not candidate.waitsFor:
        return
    if candidate.kind != "subTask":
        raise TaskDocError(f"waitsFor belongs to a task of a master, not {candidate.id!r}.")
    if candidate.id in candidate.waitsFor:
        raise TaskDocError(f"waitsFor cannot name the task itself: {candidate.id!r}.")
    master_path = (task_root / Path(candidate.master or "task.md").with_suffix(".json")).resolve()
    try:
        master = read_task_doc(master_path)
    except (OSError, ValueError) as exc:
        raise TaskDocError(
            f"waitsFor cannot resolve the master for {candidate.id!r}: {master_path}."
        ) from exc
    if master.kind != "master" or master.is_sprint:
        raise TaskDocError(
            f"waitsFor requires the task's master: {candidate.id!r} at {master_path}."
        )
    children = load_master_children(master_path.parent, master)
    _validate_waits_for_targets(master_path, master, children, candidate.waitsFor)
    children[candidate.id] = candidate
    waits = {task_id: tuple(child.waitsFor or ()) for task_id, child in children.items()}
    for cycle in dependency_cycles(waits):
        if candidate.id in cycle:
            raise TaskDocError(f"waitsFor forms a cycle with offending task ids: {list(cycle)!r}.")


def _validate_waits_for_targets(
    master_path: Path,
    master: TaskDocument,
    children: Mapping[str, TaskDocument],
    task_ids: list[str],
) -> None:
    listed = {row.number: row for row in master.subTasks}
    for task_id in task_ids:
        child = children.get(task_id)
        row = listed.get(task_id)
        if row is None:
            raise TaskDocError(
                f"waitsFor names an unknown or gone task of this master: {task_id!r}; drop it."
            )
        if row.status == "abandoned":
            raise TaskDocError(f"waitsFor names an abandoned task: {task_id!r}; drop it.")
        if (
            child
            and child.master
            and (master_path.parent / Path(child.master).with_suffix(".json")).resolve()
            != master_path
        ):
            raise TaskDocError(f"waitsFor names a task of another master: {task_id!r}.")
