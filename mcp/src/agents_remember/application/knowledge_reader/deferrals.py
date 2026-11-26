"""Derive runtime deferral states and ages for dependent records.

Each deferral stored on a ``DependentRecord`` is projected at read time against the selected
code tree and canonical coordination task facts:

* ``orphaned`` when ``condition.task`` names a task whose canonical status is terminal;
* ``open`` when ``current_leaf`` is the deferral's creating leaf (never due in its creator);
* ``due`` when ``current_leaf`` matches ``condition.task`` or when ``condition.file`` changed
  blob relative to the recorded ``fileBlob``;
* ``open`` otherwise.

Nothing here mutates records or task documents (L52-R8).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.models.knowledge_files.documents import KNOWLEDGE_ROOT
from agents_remember.models.knowledge_files.records import DependentDeferral, DependentRecord
from agents_remember.tasks.task_facts import TaskFact

if TYPE_CHECKING:
    from agents_remember.application.knowledge_reader.selection import ReaderSelection

__all__ = ["derive_deferrals", "infer_reading_leaf", "opened_code_files"]

_HISTORY_PREFIX = f"{KNOWLEDGE_ROOT}/history"
_GIT_OPTIONS = GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS)


def opened_code_files(opened: Any) -> Mapping[str, str] | None:
    """Extract the path-to-blob map from an opened code tree when it resolved cleanly."""

    if opened is None or getattr(opened, "problem", None) is not None:
        return None
    files = getattr(opened, "files", None)
    return files if isinstance(files, Mapping) else None


def _open_leaf_from_bytes(raw: bytes) -> str | None:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, Mapping) or doc.get("closed") is not False:
        return None
    leaf = doc.get("leaf")
    return leaf.strip() if isinstance(leaf, str) and leaf.strip() else None


def _history_blobs_from_directory(memory_root: Path) -> list[bytes]:
    history_dir = memory_root / _HISTORY_PREFIX
    if not history_dir.is_dir():
        return []
    blobs: list[bytes] = []
    for path in sorted(history_dir.glob("*.json")):
        if not path.is_file():
            continue
        try:
            blobs.append(path.read_bytes())
        except OSError:
            continue
    return blobs


def _history_blobs_from_git(repository: Path, revision: str) -> list[bytes]:
    try:
        listed = run_git(
            repository,
            ["ls-tree", "-r", "-z", revision, "--", f"{_HISTORY_PREFIX}/"],
            _GIT_OPTIONS,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if listed.returncode != 0:
        return []
    object_ids: list[str] = []
    for entry in listed.stdout.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        if not path.endswith(".json"):
            continue
        parts = meta.split(" ", 2)
        if len(parts) == 3 and parts[1] == "blob":
            object_ids.append(parts[2])
    if not object_ids:
        return []
    try:
        loaded = read_git_blobs_bytes(repository, object_ids)
    except (OSError, subprocess.SubprocessError):
        return []
    return [loaded[obj] for obj in object_ids if obj in loaded]


def infer_reading_leaf(selection: ReaderSelection) -> str | None:
    """Identify the leaf being read from ``selection`` or its single open history file."""

    if selection.leaf_id:
        return selection.leaf_id
    if selection.memory_root is not None:
        raw_files = _history_blobs_from_directory(selection.memory_root)
    elif selection.revision is not None:
        raw_files = _history_blobs_from_git(selection.memory_repository, selection.revision)
    else:
        return None
    open_leaves = {leaf for raw in raw_files if (leaf := _open_leaf_from_bytes(raw)) is not None}
    return next(iter(open_leaves)) if len(open_leaves) == 1 else None


def _format_age(created_at: str, reference: datetime) -> str:
    """Format elapsed time from an ISO-8601 timestamp to ``reference`` concisely."""

    normalized = created_at.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    elapsed = max(0, int((reference - parsed.astimezone(UTC)).total_seconds()))
    if elapsed < 3600:
        return "<1h"
    if elapsed < 86400:
        return f"{elapsed // 3600}h"
    return f"{elapsed // 86400}d"


def _deferral_state(
    deferral: DependentDeferral,
    *,
    code_files: Mapping[str, str] | None,
    task_facts: Mapping[str, TaskFact],
    current_leaf: str | None,
) -> str:
    """Derive ``orphaned``, ``due``, or ``open`` for one deferral."""

    target_task = deferral.condition.task
    if target_task is not None:
        fact = task_facts.get(target_task)
        if fact is not None and fact.is_terminal:
            return "orphaned"
    if current_leaf is not None and deferral.createdByLeaf == current_leaf:
        return "open"
    if current_leaf is not None and target_task == current_leaf:
        return "due"
    watched_file = deferral.condition.file
    if (
        watched_file is not None
        and code_files is not None
        and code_files.get(watched_file) != deferral.fileBlob
    ):
        return "due"
    return "open"


def derive_deferrals(
    record: DependentRecord,
    *,
    code_files: Mapping[str, str] | None,
    task_facts: Mapping[str, TaskFact],
    now: datetime | None = None,
    current_leaf: str | None = None,
) -> list[dict[str, Any]]:
    """Project every stored deferral of ``record`` with derived ``age`` and ``state``."""

    if not record.deferrals:
        return []
    reference = datetime.now(UTC) if now is None else now
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    else:
        reference = reference.astimezone(UTC)

    rows: list[dict[str, Any]] = []
    for deferral in record.deferrals:
        entry: dict[str, Any] = {
            "id": deferral.id,
            "passage": deferral.passage,
            "reason": deferral.reason,
            "condition": deferral.condition.to_document(),
            "owner": deferral.owner,
            "createdAt": deferral.createdAt,
            "createdByLeaf": deferral.createdByLeaf,
            "age": _format_age(deferral.createdAt, reference),
            "state": _deferral_state(
                deferral,
                code_files=code_files,
                task_facts=task_facts,
                current_leaf=current_leaf,
            ),
        }
        if deferral.fileBlob is not None:
            entry["fileBlob"] = deferral.fileBlob
        rows.append(entry)
    return rows
