"""Compact projection of dependent rows for bounded stale-dependent verification."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def compact_dependent_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project one dependent row into a short summary without blob hashes or repeated prose."""

    confirmed = row.get("confirmed")
    confirmed_map = confirmed if isinstance(confirmed, Mapping) else {}
    raw_commit = confirmed_map.get("commit")
    short_commit = raw_commit[:10] if isinstance(raw_commit, str) and raw_commit else None
    confirmed_task = confirmed_map.get("task")
    confirmed_uncommitted = bool(confirmed_map.get("uncommitted", False))

    condition_tasks, condition_files = _aggregate_conditions(row.get("deferrals"))
    moved_links = row.get("movedLinks")
    moved_count = (
        len(moved_links)
        if isinstance(moved_links, Sequence) and not isinstance(moved_links, (str, bytes))
        else 0
    )

    compact: dict[str, Any] = {
        "id": row.get("id"),
        "document": row.get("document"),
        "state": row.get("state"),
        "deferred": bool(row.get("deferred", False)),
    }
    if condition_tasks:
        compact["conditionTask"] = (
            condition_tasks[0] if len(condition_tasks) == 1 else condition_tasks
        )
    if condition_files:
        compact["conditionFile"] = (
            condition_files[0] if len(condition_files) == 1 else condition_files
        )
    if isinstance(confirmed_task, str) and confirmed_task:
        compact["confirmedTask"] = confirmed_task
    if short_commit is not None:
        compact["confirmedCommit"] = short_commit
    compact["confirmedUncommitted"] = confirmed_uncommitted
    compact["movedLinks"] = moved_count
    return compact


def compact_dependent_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project every dependent row into its compact verification shape."""

    return [compact_dependent_row(row) for row in rows]


def _aggregate_conditions(deferrals: Any) -> tuple[list[str], list[str]]:
    if not isinstance(deferrals, Sequence) or isinstance(deferrals, (str, bytes)):
        return [], []
    tasks: set[str] = set()
    files: set[str] = set()
    for item in deferrals:
        if not isinstance(item, Mapping):
            continue
        condition = item.get("condition")
        if not isinstance(condition, Mapping):
            continue
        task = condition.get("task")
        if isinstance(task, str) and task.strip():
            tasks.add(task.strip())
        file_path = condition.get("file")
        if isinstance(file_path, str) and file_path.strip():
            files.add(file_path.strip())
    return sorted(tasks), sorted(files)


__all__ = ["compact_dependent_row", "compact_dependent_rows"]
