"""Stat-identity parse cache for the task-document corpus."""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

Payload = dict[str, object]
PayloadReader = Callable[[Path], Payload | None]


@dataclass(frozen=True)
class _PayloadEntry:
    mtime_ns: int
    size: int
    ctime_ns: int
    payload: Payload


class TaskDocumentPayloadCache:
    """Reuse unchanged JSON parses and retain only the live path set.

    The projection worker serializes ticks, so this cache has one mutation
    owner and needs no lock. Roots are LRU-bounded because standalone callers
    can address temporary coordination trees during tests and diagnostics.
    """

    def __init__(self, *, max_roots: int = 8) -> None:
        if max_roots < 1:
            raise ValueError("task-document cache root bound must be positive")
        self._max_roots = max_roots
        self._roots: OrderedDict[str, dict[Path, _PayloadEntry]] = OrderedDict()

    def clear(self) -> None:
        self._roots.clear()

    def payloads(
        self,
        tasks_root: Path,
        paths: Iterable[Path],
        *,
        read_payload: PayloadReader,
    ) -> list[tuple[Path, Payload]]:
        key = str(tasks_root)
        entries = self._roots.setdefault(key, {})
        self._roots.move_to_end(key)
        while len(self._roots) > self._max_roots:
            self._roots.popitem(last=False)

        docs: list[tuple[Path, Payload]] = []
        seen: set[Path] = set()
        for path in paths:
            seen.add(path)
            stat = _safe_stat(path)
            cached = _cached(entries.get(path), stat)
            if cached is not None:
                docs.append((path, cached))
                continue
            payload = read_payload(path)
            if payload is None:
                entries.pop(path, None)
                continue
            if stat is not None:
                entries[path] = _PayloadEntry(
                    mtime_ns=stat.st_mtime_ns,
                    size=stat.st_size,
                    ctime_ns=stat.st_ctime_ns,
                    payload=payload,
                )
            else:
                entries.pop(path, None)
            docs.append((path, payload))
        for stale in [path for path in entries if path not in seen]:
            del entries[stale]
        return docs

    def entry_count(self, tasks_root: Path) -> int:
        return len(self._roots.get(str(tasks_root), ()))


def _cached(entry: _PayloadEntry | None, stat: os.stat_result | None) -> Payload | None:
    if (
        entry is None
        or stat is None
        or entry.mtime_ns != stat.st_mtime_ns
        or entry.size != stat.st_size
        or entry.ctime_ns != stat.st_ctime_ns
    ):
        return None
    return entry.payload


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None
