"""Read-only task-fact index over canonical coordination task documents.

Shared by the knowledge writer (validating deferral targets), closeout gate,
memory-quality checks, and knowledge reader (deriving orphaned deferrals).
Scans the coordination repository's ``tasks/`` tree once per call in O(N) time
so callers never perform per-deferral filesystem walks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.models.task_document import RESOLVED_MASTER_ROW_STATUSES
from agents_remember.tasks.document import TASK_DOCUMENT_SCHEMA
from agents_remember.tasks.store import read_task_doc
from agents_remember.tasks.task_paths import ARCHIVE_DIR, TASK_DOCUMENT_FILENAME, iter_task_folders

_TERMINAL_STATUSES_LOWER = frozenset(status.lower() for status in RESOLVED_MASTER_ROW_STATUSES)


@dataclass(frozen=True)
class TaskFact:
    """Minimal task identity and lifecycle state read from coordination task documents."""

    task_id: str
    kind: str
    status: str
    master_id: str | None = None
    archived: bool = False

    @property
    def is_master(self) -> bool:
        return self.kind == "master"

    @property
    def is_terminal(self) -> bool:
        return self.status in RESOLVED_MASTER_ROW_STATUSES or (
            self.status.lower() in _TERMINAL_STATUSES_LOWER
        )


def _iter_repo_roots(coordination_root: Path, repo_name: str | None) -> Iterator[Path]:
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return
    if repo_name:
        candidate = tasks_root / repo_name
        if candidate.is_dir():
            yield candidate
        return
    try:
        entries = sorted(tasks_root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir() and not entry.name.startswith("."):
            yield entry


def _iter_archive_folders(archive_root: Path) -> Iterator[Path]:
    """Yield archived task folders holding JSON task documents under ``0_archive/``."""

    if not archive_root.is_dir():
        return
    stack = [archive_root]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir())
        except OSError:
            continue
        has_task_json = False
        subdirs: list[Path] = []
        for child in children:
            if child.name in {"notes", "enclosures", "requirements"} or child.name.startswith("."):
                continue
            if child.is_file() and child.name == TASK_DOCUMENT_FILENAME:
                has_task_json = True
            elif child.is_dir():
                subdirs.append(child)
        if has_task_json:
            yield current
        # Sprints in archive can contain nested master folders; walk child folders too.
        stack.extend(reversed(subdirs))


def _read_raw_task_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != TASK_DOCUMENT_SCHEMA:
        return None
    return raw


def _parse_task_file(path: Path) -> tuple[str, str, str, list[tuple[str, str]]] | None:
    """Return ``(id, kind, status, [(subtask_id, subtask_status), ...])`` or ``None``."""

    try:
        doc = read_task_doc(path)
    except (OSError, ValueError, ValidationError):
        raw = _read_raw_task_json(path)
        if raw is None:
            return None
        task_id = raw.get("id")
        kind = raw.get("kind")
        status = raw.get("status")
        if not (isinstance(task_id, str) and isinstance(kind, str) and isinstance(status, str)):
            return None
        subtasks: list[tuple[str, str]] = []
        for row in raw.get("subTasks") or ():
            if isinstance(row, dict):
                number = row.get("number")
                row_status = row.get("status")
                if isinstance(number, str) and isinstance(row_status, str):
                    subtasks.append((number, row_status))
        return task_id, kind, status, subtasks
    subtasks = [(row.number, row.status) for row in doc.subTasks]
    return doc.id, doc.kind, doc.status, subtasks


def _index_master_file(
    folder: Path,
    facts: dict[str, TaskFact],
    *,
    archived: bool,
    explicit_ids: set[str],
) -> str | None:
    master_path = folder / TASK_DOCUMENT_FILENAME
    if not master_path.is_file():
        return None
    parsed = _parse_task_file(master_path)
    if parsed is None:
        return None
    task_id, kind, status, subtasks = parsed
    master_id = task_id if kind == "master" else None
    if not archived or task_id not in explicit_ids:
        facts[task_id] = TaskFact(
            task_id=task_id,
            kind=kind,
            status=status,
            master_id=None,
            archived=archived,
        )
        explicit_ids.add(task_id)
    for sub_id, sub_status in subtasks:
        if sub_id not in explicit_ids and (not archived or sub_id not in facts):
            facts[sub_id] = TaskFact(
                task_id=sub_id,
                kind="subTask",
                status=sub_status,
                master_id=master_id,
                archived=archived,
            )
    return master_id


def _index_folder(
    folder: Path,
    facts: dict[str, TaskFact],
    *,
    archived: bool,
    explicit_ids: set[str],
) -> None:
    master_id = _index_master_file(folder, facts, archived=archived, explicit_ids=explicit_ids)
    try:
        siblings = sorted(folder.glob("*.json"))
    except OSError:
        return
    for sibling in siblings:
        if sibling.name == TASK_DOCUMENT_FILENAME:
            continue
        parsed = _parse_task_file(sibling)
        if parsed is None:
            continue
        task_id, kind, status, _ = parsed
        if archived and task_id in explicit_ids:
            continue
        facts[task_id] = TaskFact(
            task_id=task_id,
            kind=kind,
            status=status,
            master_id=master_id if kind == "subTask" else None,
            archived=archived,
        )
        explicit_ids.add(task_id)


def load_task_facts(
    coordination_root: Path | None,
    repo_name: str | None = None,
) -> dict[str, TaskFact]:
    """Load all canonical task facts from ``coordination_root/tasks[/repo_name]``.

    Live task folders take precedence over archived copies if an ID ever appears
    in both; standalone sub-task JSON files take precedence over summary rows in
    a master's ``subTasks[]``.
    """

    if coordination_root is None:
        return {}
    facts: dict[str, TaskFact] = {}
    explicit_ids: set[str] = set()
    for repo_root in _iter_repo_roots(coordination_root, repo_name):
        for folder in iter_task_folders(repo_root):
            _index_folder(folder, facts, archived=False, explicit_ids=explicit_ids)
        _archive = repo_root / ARCHIVE_DIR
        for folder in _iter_archive_folders(_archive):
            _index_folder(folder, facts, archived=True, explicit_ids=explicit_ids)
    return facts
