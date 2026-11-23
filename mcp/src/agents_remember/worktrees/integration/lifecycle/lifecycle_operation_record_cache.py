"""Memoize parsed lifecycle journal records under their exact file observation."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from agents_remember.kernel.request_memo import request_owner
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord

type _RecordIdentity = tuple[int, int, int]

_RECORD_CACHE_MAX = 4096


@dataclass(frozen=True)
class _RecordCacheEntry:
    identity: _RecordIdentity
    record: LifecycleOperationRecord | None


@dataclass
class _OperationRecordCache:
    entries: dict[str, _RecordCacheEntry] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)
    drops: int = 0

    def get(
        self, key: str, identity: _RecordIdentity
    ) -> tuple[LifecycleOperationRecord | None] | None:
        with self.lock:
            entry = self.entries.get(key)
        if entry is None or entry.identity != identity:
            return None
        return (entry.record,)

    def put(
        self, key: str, identity: _RecordIdentity, record: LifecycleOperationRecord | None
    ) -> None:
        with self.lock:
            if len(self.entries) >= _RECORD_CACHE_MAX and key not in self.entries:
                # A new journal evicts the oldest insertion.
                del self.entries[next(iter(self.entries))]
                self.drops += 1
            self.entries[key] = _RecordCacheEntry(identity, record)


_process_operation_records: dict[str, _RecordCacheEntry] = {}
_process_record_cache = _OperationRecordCache(_process_operation_records)


def operation_record_cache() -> _OperationRecordCache:
    """Share one memo per native call and keep dashboard reuse across ticks."""
    memo = request_owner(_OperationRecordCache, _OperationRecordCache)
    return _process_record_cache if memo is None else memo
