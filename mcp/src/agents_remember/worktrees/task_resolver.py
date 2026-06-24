"""Resolve active task roots and leaf enclosure contract paths."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

SERIES_CONTRACT_FILENAME = "series-contract.md"
ARCHIVE_DIR = "0_archive"
ENCLOSURES_DIR = "enclosures"


class TaskResolutionError(ValueError):
    """Raised when an active task name cannot resolve to one task root."""


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")
    return slug or "task"


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


def series_contract_path(task_root: Path) -> Path:
    return task_root / SERIES_CONTRACT_FILENAME


def leaf_enclosure_dir(task_root: Path, leaf_id: str) -> Path:
    return task_root / ENCLOSURES_DIR / slugify(leaf_id)


def leaf_enclosure_path(task_root: Path, leaf_id: str) -> Path:
    return leaf_enclosure_dir(task_root, leaf_id) / SERIES_CONTRACT_FILENAME


def is_archived_path(path: Path) -> bool:
    return ARCHIVE_DIR in path.parts


def is_enclosure_contract(path: Path) -> bool:
    return (
        path.name == SERIES_CONTRACT_FILENAME
        and len(path.parts) >= 3
        and path.parent.parent.name == ENCLOSURES_DIR
    )


def iter_active_series_contracts(repo_task_root: Path) -> Iterator[Path]:
    if not repo_task_root.is_dir():
        return
    for path in sorted(repo_task_root.rglob(SERIES_CONTRACT_FILENAME)):
        if is_archived_path(path) or ENCLOSURES_DIR in path.parts:
            continue
        yield path


def iter_leaf_enclosure_contracts(tasks_root: Path) -> Iterator[Path]:
    if not tasks_root.is_dir():
        return
    for path in sorted(tasks_root.rglob(f"{ENCLOSURES_DIR}/*/{SERIES_CONTRACT_FILENAME}")):
        if not is_archived_path(path):
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
