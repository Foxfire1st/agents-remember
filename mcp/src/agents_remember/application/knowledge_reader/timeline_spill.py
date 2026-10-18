"""Immutable oversized timeline bytes on anonymous process-private temporary disk.

Offsets live on disk too: retaining a history never retains an event-sized tuple or an
O(history) in-memory index. No names, persisted store, worker or per-history byte cap.
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import threading
import weakref
from collections.abc import Iterable
from contextlib import ExitStack
from typing import BinaryIO, overload

_OFFSET = struct.Struct(">Q")


def _close_files(data: BinaryIO, offsets: BinaryIO) -> None:
    try:
        data.close()
    finally:
        offsets.close()


def _write(file: BinaryIO, data: bytes) -> None:
    if file.write(data) != len(data):
        raise OSError("short write to anonymous timeline backing")


class SpilledEvents:
    """A detached immutable sequence; only the requested event range is read into memory.

    The final owner releases both anonymous files. An in-flight page can finish after cache
    eviction without a close/read race; no cache reference or background work survives clear.
    The OS also reclaims these unlinked files on abrupt process termination.
    """

    __slots__ = (
        "__weakref__",
        "_count",
        "_data",
        "_finalizer",
        "_lock",
        "_offsets",
        "storage_bytes",
    )

    def __init__(self, events: Iterable[bytes]) -> None:
        with ExitStack() as owner:
            data = owner.enter_context(tempfile.TemporaryFile(mode="w+b", buffering=0))
            offsets = owner.enter_context(tempfile.TemporaryFile(mode="w+b", buffering=0))
            # POSIX TemporaryFile is unlinked before return, including its O_TMPFILE fallback.
            # Refuse a non-anonymous backing rather than invent persisted or repository storage.
            if any(os.fstat(file.fileno()).st_nlink for file in (data, offsets)):
                raise OSError("timeline backing must be anonymous temporary files")
            count = 0
            for event in events:
                _write(offsets, _OFFSET.pack(data.tell()))
                _write(data, event)
                count += 1
            _write(offsets, _OFFSET.pack(data.tell()))
            self.storage_bytes = data.tell() + offsets.tell()
            self._data = data
            self._offsets = offsets
            self._count = count
            self._lock = threading.Lock()
            self._finalizer = weakref.finalize(self, _close_files, data, offsets)
            owner.pop_all()

    @property
    def retained_bytes(self) -> int:
        """Resident objects only; complete disk cost is separately named storage_bytes."""
        return (
            sys.getsizeof(self)
            + sys.getsizeof(self._data)
            + sys.getsizeof(self._offsets)
            + sys.getsizeof(self._count)
            + sys.getsizeof(self.storage_bytes)
            + sys.getsizeof(self._lock)
            + 1024  # Finalizer, weakref and allocator allowance.
        )

    def __len__(self) -> int:
        return self._count

    @overload
    def __getitem__(self, position: int) -> bytes: ...

    @overload
    def __getitem__(self, position: slice) -> tuple[bytes, ...]: ...

    def __getitem__(self, position: int | slice) -> bytes | tuple[bytes, ...]:
        if isinstance(position, slice):
            return tuple(self[n] for n in range(*position.indices(self._count)))
        n = position + self._count if position < 0 else position
        if not 0 <= n < self._count:
            raise IndexError("timeline event index out of range")
        with self._lock:
            self._offsets.seek(n * _OFFSET.size)
            bounds = self._offsets.read(2 * _OFFSET.size)
            if len(bounds) != 2 * _OFFSET.size:
                raise OSError("unreadable anonymous timeline offsets")
            start, end = struct.unpack(">QQ", bounds)
            self._data.seek(start)
            data = self._data.read(end - start)
            if len(data) != end - start:
                raise OSError("unreadable anonymous timeline event")
            return data
