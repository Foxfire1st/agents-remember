"""Keep a leaf task's tool-call lines before cleanup or abandon removes them.

The enclosure's ``logs/`` folder holds one line per tool call of the agents
bound to the leaf, and cleanup and abandon delete that folder.
Before they do, the lines of the leaf's own task are copied into
``<master folder>/notes/reports/<leaf id>-tool-calls.jsonl``, which survives
cleanup and archiving.

Each kept line drops ``detail``: it names a file inside the folder being
removed. For the same reason every path the line names inside the removed
enclosure or its outside scratch (a worktree root, a hand-over file in role
scratch) is replaced by the leaf id, so no kept line names a file cleanup
removes. A path outside them, such as the leaf's ``series-contract.md`` in the
master folder, stays as it was called. A second keep (a retried cleanup) replaces the file by the union of
its lines and the log's lines, so no line is kept twice.

Not covered: the calls about the task the central log holds (the cleanup
itself, a Manager's calls) are not copied, and a series enclosure keeps
nothing, because a series is not a leaf task. The file's size is bounded by
the enclosure log budget it is copied from.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.tool_answer_log import TOOL_LOG_BASENAME_PREFIX
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees import outside_scratch
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    normalize_contract_leaf_id,
)

KEPT_TOOL_CALLS_SUFFIX = "-tool-calls.jsonl"
_DROPPED_FIELDS = ("detail",)


@dataclass(frozen=True)
class KeptToolCalls:
    """What one keep wrote: where, how many lines kept, how many skipped."""

    path: Path | None
    kept: int
    skipped: int
    error: str | None = None

    def entry(self) -> dict[str, object]:
        """The ``keptToolCalls`` report a terminal answer carries."""

        report: dict[str, object] = {
            "path": self.path.as_posix() if self.path is not None else None,
            "kept": self.kept,
            "skipped": self.skipped,
        }
        if self.error is not None:
            report["error"] = self.error
        return report


def kept_tool_calls_entry(contract: WorktreeContract, *, dry_run: bool) -> dict[str, object]:
    """The ``keptToolCalls`` field for the entry that removes the log folder.

    A preview and a series enclosure keep nothing and add no field. A failure
    to keep is reported in the field; :func:`removal_after_keep` then leaves the
    log in place.
    """

    if dry_run or contract.kind != "leaf":
        return {}
    try:
        kept = keep_tool_calls(contract)
    except (OSError, ValueError) as error:
        kept = KeptToolCalls(path=None, kept=0, skipped=0, error=str(error))
    return {"keptToolCalls": kept.entry()}


KEEP_FAILED_REASON = "tool-calls-not-kept"


def removal_after_keep(
    contract: WorktreeContract,
    folder: Path,
    *,
    dry_run: bool,
    remove_tree: Callable[..., dict[str, object]],
) -> dict[str, object]:
    """Keep the leaf's lines, then remove ``folder``, which holds its log; never the log alone.

    When the keep failed, ``folder`` stays with the log in it and its entry names the failure as
    the reason it was not removed, so the terminal operation reports itself blocked and keeps its
    contract live. A retry keeps the lines (merged with any kept before) and then removes it.
    """

    kept = kept_tool_calls_entry(contract, dry_run=dry_run)
    report = kept.get("keptToolCalls")
    if isinstance(report, dict) and report.get("error") is not None:
        return {
            "path": folder.as_posix(),
            "removed": False,
            "reason": KEEP_FAILED_REASON,
            **kept,
        }
    return {**remove_tree(folder, dry_run=dry_run), **kept}


def keep_tool_calls_for_group(
    contract: WorktreeContract, *, dry_run: bool
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    """Run or preview a leaf's tool-call move-out before whole-folder removal.

    Returns ``(kept_field, blocker_directories)``. Series enclosures keep nothing.
    A failure in either dry-run or execution blocks before any deletion occurs.
    """

    if contract.kind != "leaf":
        return {}, {}
    try:
        kept = keep_tool_calls(contract, dry_run=dry_run)
    except (OSError, ValueError) as error:
        report = KeptToolCalls(path=None, kept=0, skipped=0, error=str(error)).entry()
        return {}, {
            "logs": {
                "path": (contract.worktree_group / "logs").as_posix(),
                "removed": False,
                "reason": KEEP_FAILED_REASON,
                "keptToolCalls": report,
            }
        }
    return ({}, {}) if dry_run else ({"keptToolCalls": kept.entry()}, {})


def keep_tool_calls(contract: WorktreeContract, *, dry_run: bool = False) -> KeptToolCalls:
    """Copy this leaf's lines from the enclosure log into its master's notes."""

    log_dir = contract.worktree_group / "logs"
    if log_dir.is_symlink() or not log_dir.is_dir():
        return KeptToolCalls(path=None, kept=0, skipped=0)
    day_files = _day_files(log_dir)
    if not day_files:
        return KeptToolCalls(path=None, kept=0, skipped=0)
    leaf = normalize_contract_leaf_id(contract, keep_unresolved=True)
    task = _leaf_task_path(leaf)
    lines, skipped = _leaf_lines(day_files, task)
    removed = _removed_paths(contract)
    lines = [_without_removed_paths(line, removed, leaf.leaf_id) for line in lines]
    target = contract.task_root / "notes" / "reports" / f"{leaf.leaf_id}{KEPT_TOOL_CALLS_SUFFIX}"
    if lines or target.is_symlink() or target.exists():
        _check_keep_target(contract.task_root, target)
    previous, malformed = _read_lines(target) if target.is_file() else ([], 0)
    skipped += malformed
    merged = _union(previous, lines)
    if not merged:
        return KeptToolCalls(path=None, kept=0, skipped=skipped)
    if dry_run:
        return KeptToolCalls(path=target, kept=len(merged), skipped=skipped)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, "".join(f"{_canonical(line)}\n" for line in merged))
    return KeptToolCalls(path=target, kept=len(merged), skipped=skipped)


def _day_files(log_dir: Path) -> list[Path]:
    with os.scandir(log_dir) as entries:
        return sorted(
            Path(entry.path)
            for entry in entries
            if entry.name.startswith(TOOL_LOG_BASENAME_PREFIX)
            and entry.name.endswith(".jsonl")
            and not entry.is_symlink()
        )


def _check_keep_target(task_root: Path, target: Path) -> None:
    notes = task_root / "notes"
    reports = notes / "reports"
    for folder in (notes, reports):
        if folder.is_symlink():
            raise ValueError(f"Canonical report folder {folder} is a symlink")
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Canonical report path {folder} is not a directory")
    if target.is_symlink():
        raise ValueError(f"Canonical tool-calls file {target} is a symlink")
    if target.exists() and not target.is_file():
        raise ValueError(f"Canonical tool-calls path {target} is not a regular file")
    existing = reports if reports.exists() else (notes if notes.exists() else task_root)
    if not os.access(existing, os.W_OK | os.X_OK):
        raise PermissionError(f"Canonical report directory {existing} cannot be written")


def _leaf_task_path(contract: WorktreeContract) -> str:
    """The leaf's task document as a call's ``caller.task`` names it."""

    ref = TaskDocumentTopology(contract.coordination_root).ref_for_id(
        contract.repo_name, contract.task_root, contract.leaf_id
    )
    return ref.path


def _leaf_lines(day_files: list[Path], task: str) -> tuple[list[dict[str, Any]], int]:
    """Every line of ``task`` in the day files, oldest day first, without ``detail``."""

    kept: list[dict[str, Any]] = []
    skipped = 0
    for day_file in day_files:
        lines, malformed = _read_lines(day_file)
        skipped += malformed
        for line in lines:
            caller = line.get("caller")
            if not isinstance(caller, dict) or caller.get("task") != task:
                skipped += 1
                continue
            kept.append({key: value for key, value in line.items() if key not in _DROPPED_FIELDS})
    return kept, skipped


def _removed_paths(contract: WorktreeContract) -> re.Pattern[str]:
    """A pattern for any path inside the folders cleanup and abandon remove.

    The enclosure under ``worktrees/`` and the leaf's outside scratch root, each
    as recorded and as resolved, followed by the rest of the path.
    """

    roots = [contract.worktree_group]
    outside = outside_scratch.recorded_root(contract)
    if outside is not None:
        roots.append(outside.root)
    names = {
        name for root in roots for name in (root.as_posix(), root.resolve(strict=False).as_posix())
    }
    alternatives = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?:{alternatives})(?=/|$|[\s\"'`,;)])(?:/[^\s\"'`,;)]*)?")


def _without_removed_paths(value: Any, removed: re.Pattern[str], leaf_id: str) -> Any:
    """``value`` with every removed path in its strings replaced by ``leaf_id``."""

    if isinstance(value, str):
        return removed.sub(leaf_id, value)
    if isinstance(value, dict):
        return {key: _without_removed_paths(item, removed, leaf_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_removed_paths(item, removed, leaf_id) for item in value]
    return value


def _read_lines(path: Path) -> tuple[list[dict[str, Any]], int]:
    """The JSON object lines of one file and the count of lines that are not."""

    lines: list[dict[str, Any]] = []
    malformed = 0
    # A log line ends at LF alone: the logger writes text outside ASCII as it is, so a value may
    # hold a character ``str.splitlines`` would also break at (U+2028 and its kin).
    for raw in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            lines.append(value)
        else:
            malformed += 1
    return lines, malformed


def _union(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Both lists without a line twice, sorted by time (stable)."""

    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for line in [*previous, *current]:
        text = _canonical(line)
        if text not in seen:
            seen.add(text)
            merged.append(line)
    return sorted(merged, key=lambda line: str(line.get("time", "")))


def _canonical(line: dict[str, Any]) -> str:
    return json.dumps(line, ensure_ascii=False, sort_keys=True, default=str)
