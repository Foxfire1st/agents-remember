"""One missing-master diagnostic shared by every exact sprint topology reader."""

from __future__ import annotations

from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document import TaskDocument


def archived_master_paths(
    coordination_root: Path, sprint_ref: TaskDocumentRef, entry: str
) -> list[Path]:
    """Where under ``0_archive/`` the master an ``orchestrates`` entry names was found, if at all."""

    archive = coordination_root / "tasks" / sprint_ref.repository / "0_archive"
    direct = archive / entry / "task.json"
    if direct.is_file():
        return [direct]
    found: list[Path] = []
    for path in sorted(archive.glob("*/task.json")):
        try:
            document = TaskDocument.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            continue
        if entry in {path.parent.name, document.id, document.title}:
            found.append(path)
    return found


def missing_master_detail(coordination_root: Path, sprint_ref: TaskDocumentRef, entry: str) -> str:
    found = archived_master_paths(coordination_root, sprint_ref, entry)
    location = (
        f"; found under 0_archive at {[path.as_posix() for path in found]!r}"
        if found
        else "; its live task folder is missing"
    )
    return (
        f"sprint {sprint_ref.key} commands master {entry!r}, which resolves to no live master{location}. "
        "Restore the master's folder to its canonical live location, then use task_doc.retire_master "
        "on this sprint with fields={masterRef:<canonical master reference>, reason:<reason>, "
        "removeEdges:<affirmed unfinished successor edges>} to remove its membership and graph before archival."
    )
