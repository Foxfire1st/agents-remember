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
        return {
            "state": "skipped",
            "reason": "root-series-still-active",
            "taskRoot": task_root.as_posix(),
        }
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
