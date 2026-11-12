"""Bounded full-body preparation, revalidated before parsing a repeated request."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from fastapi.responses import JSONResponse

from agents_remember.observer.projection import TaskSummary
from agents_remember.serving.projections.snapshots import read_task_document_body
from agents_remember.serving.projections.task_body_generation import (
    BodyGeneration,
    capture_body_generation,
)


@dataclass(frozen=True)
class TaskBody:
    body: bytes
    revision: str
    generation: BodyGeneration


class TaskDocumentBodyCache:
    """One serving app's LRU; an unreadable or changing input is served uncached."""

    def __init__(self, *, max_entries: int = 1024, max_bytes: int = 16 * 1024 * 1024) -> None:
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("task body cache bounds must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._entries: OrderedDict[tuple[Path, str], TaskBody] = OrderedDict()
        self._lock = RLock()

    def read(self, root: Path, path: str, *, now: datetime) -> TaskBody | None:
        with self._lock:
            key = (root, path)
            cached = self._entries.get(key)
            if cached is not None:
                if cached.generation.current():
                    self._entries.move_to_end(key)
                    return cached
                self._drop(key)
            with capture_body_generation() as generation:
                document = read_task_document_body(root, doc_path=path, now=now)
            if document is None:
                return None
            content = TaskSummary(
                **{name: getattr(document, name) for name in TaskSummary.model_fields}
            )
            body = bytes(
                JSONResponse(content=content.model_dump(by_alias=True, exclude_none=True)).body
            )
            prepared = TaskBody(body, hashlib.sha256(body).hexdigest(), generation)
            if generation.current() and len(body) <= self._max_bytes:
                self._entries[key] = prepared
                self._bytes += len(body)
                while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
                    self._drop(next(iter(self._entries)))
            return prepared

    def _drop(self, key: tuple[Path, str]) -> None:
        self._bytes -= len(self._entries.pop(key).body)

    @property
    def retained(self) -> tuple[int, int]:
        with self._lock:
            return len(self._entries), self._bytes
