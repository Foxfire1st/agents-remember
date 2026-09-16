"""Shared file-surface helpers for the observer snapshot readers.

The readers split by responsibility (providers, runtime enclosures, analytical
surfaces, task documents) share the task-document payload cache, the status
payload TTL cache, and the small JSON/stat helpers collected here. No reader
logic lives in this module; it is the common leaf the split modules import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.observer.task_document_cache import TaskDocumentPayloadCache
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA
from agents_remember.worktrees.task_resolver import ARCHIVE_DIR, ENCLOSURES_DIR

# Task and series readers share one stat-identity parse cache. Runtime-only
# watcher changes still trigger a projection, but unchanged task JSON is never
# reparsed merely because wall-clock time passed.
_task_doc_cache = TaskDocumentPayloadCache()

# 260707-HFX2-L12 F11: status_payload() shells out to git per leaf; without a cache the projection
# fires O(active-leaf) git subprocesses every tick. Git state changes slowly, so this TTL cache lets
# each leaf's git probe run at most once per interval; the cache is pruned to the live leaf set each
# tick so it cannot grow unbounded. Keyed by enclosure-contract path.
STATUS_PAYLOAD_TTL_SECONDS = 8.0
_status_payload_cache: dict[str, tuple[datetime, dict[str, Any] | None]] = {}


@dataclass(frozen=True)
class _TaskDocumentLifecycleMaps:
    lifecycle_by_enclosure: dict[str, str]
    lifecycle_by_dir: dict[Path, str]
    lifecycle_by_root_doc: dict[Path, str]
    lifecycle_by_leaf_doc: dict[tuple[Path, str], str]


def _iter_task_document_payloads(
    tasks_root: Path, *, now: datetime | None
) -> list[tuple[Path, dict[str, object]]]:
    """Shared ``(path, payload)`` list with per-file invalidation.

    ``now=None`` preserves the standalone fresh-read contract. Projection
    callers enumerate the live set each tick, reuse only unchanged stat
    identities, parse changed/new files, and prune deleted paths.
    """

    paths = _iter_task_json(tasks_root)
    docs = (
        [(path, payload) for path in paths if (payload := _read_json(path)) is not None]
        if now is None
        else _task_doc_cache.payloads(tasks_root, paths, read_payload=_read_json)
    )
    return [
        (path, payload) for path, payload in docs if payload.get("schema") == TASK_DOCUMENT_SCHEMA
    ]


# A task-document summary limit (250 roots-plus-newest-leaves) used to bound the readers
# below. It was removed deliberately. The eviction was silent and untested, so an operator
# saw a master whose sub-task rows were simply not clickable, with no diagnostic anywhere,
# and no way to tell a missing document from an unreadable one. Every canonical task
# document under ``tasks/<repo>/<task>/`` is projected now. If a bound is ever needed
# again it must be larger, must ANNOUNCE its own truncation to the operator, and must offer
# a way to reach what it hid.


def _iter_task_json(tasks_root: Path) -> list[Path]:
    """Enumerate only canonical task documents directly inside one task root.

    A task root is exactly ``tasks/<repository>/<task>/``. Recursive discovery used
    to admit valid-schema historical copies under ``notes/`` as live masters, which
    duplicated and flattened the dashboard hierarchy.
    """
    return [
        path
        for path in sorted(tasks_root.rglob("*.json"))
        if ARCHIVE_DIR not in path.parts
        and ENCLOSURES_DIR not in path.parts
        and len(path.relative_to(tasks_root).parts) == 3
    ]


def _read_json(path: Path) -> dict[str, object] | None:  # pragma: no cover
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_float(value: Any) -> float | None:  # pragma: no cover
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _report_label(name: str) -> str:
    stem = name[:-5] if name.endswith(".json") else name
    parts = stem.split("-", 1)
    return parts[1] if len(parts) == 2 else stem


def _file_age_seconds(path: Path, now: datetime) -> float | None:  # pragma: no cover
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return now.timestamp() - mtime


def _current_phase_text(current: Any) -> str | None:  # pragma: no cover
    if not isinstance(current, dict):
        return None
    provider = current.get("provider")
    action = current.get("action")
    if provider and action:
        return f"{provider} {action}"
    return str(provider or action) if (provider or action) else None
