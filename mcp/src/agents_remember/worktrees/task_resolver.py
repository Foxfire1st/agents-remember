"""Resolve active task roots and leaf enclosure contract paths.

The path rules themselves -- the series contract filename, the enclosure
directory, a leaf's enclosure contract, the archive segment -- are defined in
``agents_remember.tasks.task_paths``, the task package that owns them, and
re-exported here. This module stays the published import site for every existing
caller, while the definition lives below ``worktrees`` where the layering
contract requires it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.sprint_rows import sprint_census
from agents_remember.tasks.store import read_task_doc
from agents_remember.tasks.task_paths import (
    ARCHIVE_DIR,
    ENCLOSURES_DIR,
    SERIES_CONTRACT_FILENAME,
    is_archived_path,
    is_enclosure_contract,
    iter_leaf_enclosure_contracts,
    leaf_enclosure_dir,
    leaf_enclosure_path,
    series_contract_path,
    slugify,
)

__all__ = [
    "ARCHIVE_DIR",
    "ENCLOSURES_DIR",
    "SERIES_CONTRACT_FILENAME",
    "TaskResolutionError",
    "is_archived_path",
    "is_enclosure_contract",
    "iter_active_series_contracts",
    "iter_leaf_enclosure_contracts",
    "leaf_enclosure_dir",
    "leaf_enclosure_path",
    "legacy_task_folder_name",
    "legacy_task_root_for",
    "resolve_active_task_root",
    "resolve_leaf_enclosure_contract",
    "series_contract_path",
    "slugify",
    "task_folder_name",
    "task_root_candidates",
    "task_root_for",
]


class TaskResolutionError(ValueError):
    """Raised when an active task name cannot resolve to one task root."""


def task_folder_name(task_name: str) -> str:
    return slugify(task_name)


def legacy_task_folder_name(task_name: str) -> str:
    slug = slugify(task_name)
    return slug if slug.endswith("-ar") else f"{slug}-ar"


def task_root_for(coordination_root: Path, repo_name: str, task_name: str) -> Path:
    return coordination_root / "tasks" / repo_name / task_folder_name(task_name)


def legacy_task_root_for(coordination_root: Path, repo_name: str, task_name: str) -> Path:
    return coordination_root / "tasks" / repo_name / legacy_task_folder_name(task_name)


def task_root_candidates(coordination_root: Path, repo_name: str, task_name: str) -> list[Path]:
    current = task_root_for(coordination_root, repo_name, task_name)
    legacy = legacy_task_root_for(coordination_root, repo_name, task_name)
    return [current] if current == legacy else [current, legacy]


def iter_active_series_contracts(repo_task_root: Path) -> Iterator[Path]:
    if not repo_task_root.is_dir():
        return
    for path in sorted(repo_task_root.rglob(SERIES_CONTRACT_FILENAME)):
        if is_archived_path(path) or ENCLOSURES_DIR in path.parts:
            continue
        yield path


def resolve_active_task_root(
    coordination_root: Path,
    repo_name: str,
    task_name: str,
    *,
    parent_task: str | None = None,
    fallback: bool = True,
) -> Path:
    repo_task_root = coordination_root / "tasks" / repo_name
    wanted = task_folder_name(task_name)
    parent = task_folder_name(parent_task) if parent_task else None
    matches = [
        contract.parent
        for contract in iter_active_series_contracts(repo_task_root)
        if contract.parent.name == wanted and (parent is None or parent in contract.parent.parts)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(path.as_posix() for path in matches)
        raise TaskResolutionError(f"multiple active tasks named {task_name!r}: {names}")
    if fallback:
        for candidate in task_root_candidates(coordination_root, repo_name, task_name):
            if candidate.exists() and not is_archived_path(candidate):
                return candidate
        return task_root_candidates(coordination_root, repo_name, task_name)[0]
    raise TaskResolutionError(f"active task not found: {task_name}")


def resolve_leaf_enclosure_contract(
    coordination_root: Path,
    repo_name: str,
    task_name: str,
    *,
    leaf_id: str | None = None,
    parent_task: str | None = None,
) -> Path | None:
    task_root = resolve_active_task_root(
        coordination_root,
        repo_name,
        task_name,
        parent_task=parent_task,
    )
    if leaf_id:
        path = leaf_enclosure_path(task_root, leaf_id)
        return path if path.exists() else None
    matches = [
        path for path in sorted((task_root / ENCLOSURES_DIR).glob(f"*/{SERIES_CONTRACT_FILENAME}"))
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(path.parent.name for path in matches)
        raise TaskResolutionError(
            f"task {task_name!r} has multiple leaf enclosures; pass leaf_id ({names})"
        )
    return None


def archive_completed_root_task(
    coordination_root: Path,
    repo_name: str,
    task_root: Path,
    *,
    dry_run: bool,
) -> dict[str, object]:
    repo_task_root = coordination_root / "tasks" / repo_name
    if task_root.parent != repo_task_root:
        return {"state": "skipped", "reason": "not-root-task", "taskRoot": task_root.as_posix()}
    if series_contract_path(task_root).exists():
        return _series_archive_skip(coordination_root, repo_name, task_root)
    archive_root = repo_task_root / ARCHIVE_DIR
    target = archive_root / task_root.name
    if target.exists():
        return {
            "state": "blocked",
            "reason": "archive-target-exists",
            "taskRoot": task_root.as_posix(),
            "archivePath": target.as_posix(),
        }
    if dry_run:
        return {
            "state": "would-archive",
            "taskRoot": task_root.as_posix(),
            "archivePath": target.as_posix(),
        }
    archive_root.mkdir(parents=True, exist_ok=True)
    task_root.rename(target)
    return {
        "state": "archived",
        "taskRoot": task_root.as_posix(),
        "archivePath": target.as_posix(),
    }


_RETIRE_ROUTE = (
    "a master is archived only by task_doc.retire_master (a dry run first), never by finalization"
)


def _series_archive_skip(
    coordination_root: Path, repo_name: str, task_root: Path
) -> dict[str, object]:
    """Why finalizing a task that holds a series contract never archives it.

    A task whose document is a master is skipped with the route that does archive it: a sprint that
    commands it is named, a master no sprint commands is pointed at the retire operation. Any other
    task keeps the skip it has always had.
    """

    skipped: dict[str, object] = {"state": "skipped", "taskRoot": task_root.as_posix()}
    repository_tasks = coordination_root / "tasks" / repo_name
    try:
        document = read_task_doc(task_root / "task.json")
    except (OSError, ValueError):
        return {**skipped, "reason": "root-series-still-active"}
    if document.kind != "master":
        return {**skipped, "reason": "root-series-still-active"}
    own = TaskDocumentRef(repository=repo_name, path=f"{task_root.name}/task.json")
    census = sprint_census(repository_tasks, task_root / "task.json", own, document)
    sprints = [
        TaskDocumentRef(repository=repo_name, path=path.relative_to(repository_tasks).as_posix())
        for path in census.commanding
    ]
    if sprints:
        names = ", ".join(sprint.key for sprint in sprints)
        return {
            **skipped,
            "reason": "sprint-commands-master",
            "sprintTaskDocumentRef": sprints[0].model_dump(mode="json"),
            "detail": f"The archive was skipped because sprint {names} commands this master, "
            f"so its folder must stay resolvable; {_RETIRE_ROUTE}.",
        }
    return {
        **skipped,
        "reason": "master-archived-only-by-retire",
        "detail": f"The archive was skipped: {_RETIRE_ROUTE}.",
    }
