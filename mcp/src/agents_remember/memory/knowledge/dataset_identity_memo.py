"""Bounded identity reads over retained read-only SQLite handles.

data_version is meaningful only on the same connection. Each retained handle therefore owns its
version and its result. File identities additionally catch replacement, deletion and raw DB/WAL
rewrites, including restored mtimes; a changed file closes the handle to discard SQLite's pages.
No transaction remains open between calls. The caller still owns schema, namespace and digest
validation, and failures or inputs moving during that validation are never retained.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

import apsw

from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.request_digests import _generation_key
from agents_remember.memory.knowledge.schema import SchemaGeneration, generation_of_database
from agents_remember.models.knowledge.candidate import SnapshotIdentity


def _identity(path: Path) -> tuple[int, ...]:
    try:
        link, target = path.lstat(), path.stat()
    except FileNotFoundError:
        return ()
    return tuple(
        value
        for info in (link, target)
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    )


def _files(path: Path) -> tuple[tuple[int, ...], ...]:
    return tuple(_identity(Path(f"{path}{suffix}")) for suffix in ("", "-wal", "-shm", "-journal"))


def _version(connection: apsw.Connection) -> int:
    ((version,),) = connection.execute("PRAGMA data_version")
    return int(version)


@dataclass
class _Reader:
    lock: RLock = field(default_factory=RLock)
    retired: bool = False
    connection: apsw.Connection | None = None
    files: tuple[tuple[int, ...], ...] = ()
    version: int = 0
    generation: tuple[object, ...] = ()
    result: SnapshotIdentity | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.result = None


class DatasetIdentityMemo:
    """At most ``capacity`` retained handles; unrelated reads have separate locks.

    An evicted in-flight reader closes when its borrower exits, so active operations can briefly
    hold additional handles without delaying another database's read behind their locks.
    """

    def __init__(self, capacity: int = 16) -> None:
        self.capacity = capacity
        self._readers: OrderedDict[Path, _Reader] = OrderedDict()
        self._lock = RLock()

    @staticmethod
    def _retire(reader: _Reader) -> None:
        reader.retired = True
        # An in-flight borrower closes its own retired handle on exit. Never wait here while
        # admitting an unrelated path, or close SQLite out from under that borrower.
        if reader.lock.acquire(blocking=False):
            try:
                reader.close()
            finally:
                reader.lock.release()

    def clear(self) -> None:
        with self._lock:
            readers = tuple(self._readers.values())
            self._readers.clear()
        for reader in readers:
            self._retire(reader)

    def _reader(self, path: Path) -> _Reader:
        evicted = None
        with self._lock:
            reader = self._readers.get(path)
            if reader is None:
                reader = _Reader()
                self._readers[path] = reader
            self._readers.move_to_end(path)
            if len(self._readers) > self.capacity:
                _, evicted = self._readers.popitem(last=False)
        if evicted is not None:
            self._retire(evicted)
        return reader

    def read(
        self,
        path: Path,
        compute: Callable[[apsw.Connection, SchemaGeneration], SnapshotIdentity],
    ) -> SnapshotIdentity:
        path = path.absolute()
        reader = self._reader(path)
        with reader.lock:
            try:
                before = _files(path)
                if reader.connection is not None and before != reader.files:
                    reader.close()
                if reader.connection is None:
                    reader.connection = open_read_only_database(path)
                connection = reader.connection
                version = _version(connection)
                generation = generation_of_database(connection)
                key = _generation_key(generation)
                unchanged = before == _files(path)
                if (
                    reader.result is not None
                    and unchanged
                    and before == reader.files
                    and version == reader.version
                    and key == reader.generation
                ):
                    return reader.result.model_copy(deep=True)
                reader.result = None
                result = compute(connection, generation)
                if version == _version(connection) and before == _files(path):
                    reader.files, reader.version, reader.generation = before, version, key
                    reader.result = result.model_copy(deep=True)
                return result
            except Exception:
                reader.close()
                raise
            finally:
                if reader.retired:
                    reader.close()


DATASET_IDENTITIES = DatasetIdentityMemo()
