"""Index access bookkeeping preserves immutable dataset witnesses and eviction policy."""

import hashlib
import os
import time
from pathlib import Path
from typing import Literal

import apsw
import pytest
from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.dataset_identity_memo import DatasetIdentityMemo
from agents_remember.memory.knowledge.schema import SchemaGeneration
from agents_remember.memory.knowledge_index import cache as module
from agents_remember.memory.knowledge_index.build import build_index
from agents_remember.memory.knowledge_index.cache import KnowledgeIndexCache
from agents_remember.memory.knowledge_index.query import KnowledgeIndex
from agents_remember.memory.knowledge_index.tree import MemoryTreeSnapshot
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.repository import RepositoryIdentity


def marker(path: Path) -> Path:
    return path.with_name(f"{path.name}.used")


def native(cache: KnowledgeIndexCache, key: str) -> Path:
    with cache._build(MemoryTreeSnapshot(key, "git", "tiny-native-index", {})) as index:
        return index.database_path


def reopen(cache: KnowledgeIndexCache, path: Path) -> KnowledgeIndex:
    index = cache._reuse(path.stem)
    assert index is not None
    return index


def stamp(path: Path) -> tuple[int, ...]:
    value = path.stat()
    return value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def old(path: Path, seconds: float) -> None:
    value = time.time() - seconds
    os.utime(path, (value, value))


def test_access_changes_only_usage_and_preserves_identity_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes")
    path = cache.path_for("1" * 40)
    build_index(MemoryTreeSnapshot(path.stem, "git", "tiny-native-index", {}), path)
    original_stat = stamp(path)
    original_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    memo = DatasetIdentityMemo()
    monkeypatch.setattr(logical, "DATASET_IDENTITIES", memo)
    computes: list[int] = []
    real_identity = logical.snapshot_identity

    def counted(
        connection: apsw.Connection,
        repository: RepositoryIdentity,
        generation: SchemaGeneration | str,
    ) -> SnapshotIdentity:
        computes.append(1)
        return real_identity(connection, repository, generation)

    monkeypatch.setattr(logical, "snapshot_identity", counted)
    try:
        first = logical.dataset_identity(path)
        for _ in range(2):
            with reopen(cache, path) as index:
                assert index.database_path == path
            assert logical.dataset_identity(path) == first
    finally:
        memo.clear()
    assert computes == [1]
    assert stamp(path) == original_stat
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_bytes
    assert marker(path).is_file() and marker(path).stat().st_size == 0


def test_marker_recency_survives_new_owner_and_drives_age_eviction(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes", max_age_seconds=3600)
    path = native(cache, "2" * 40)
    old(path, 7200)  # Content age no longer measures use once a marker exists.
    reopened = KnowledgeIndexCache(cache.directory, max_age_seconds=3600)
    assert reopened.evict() == []
    old(marker(path), 7200)
    assert reopened.evict() == [path]
    assert not path.exists() and not marker(path).exists()


def test_byte_eviction_orders_by_usage_and_keeps_the_protected_pair(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes")
    first, second = native(cache, "3" * 40), native(cache, "4" * 40)
    old(marker(first), 200)
    old(marker(second), 100)
    old(second, 500)  # A newer use outranks an older physical content timestamp.
    cache.max_bytes = second.stat().st_size
    assert cache.evict() == [first]
    assert not marker(first).exists()
    assert second.exists() and marker(second).exists()
    cache.max_bytes = 1
    assert cache.evict(keep=second) == []
    assert second.exists() and marker(second).exists()


def test_legacy_markerless_index_preserves_recorded_age_until_first_reuse(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes", max_age_seconds=3600)
    path = cache.path_for("5" * 40)
    build_index(MemoryTreeSnapshot(path.stem, "git", "existing-runtime-index", {}), path)
    old(path, 100)
    legacy_stamp = stamp(path)
    assert not marker(path).exists()
    assert cache.evict() == []
    with reopen(cache, path):
        pass
    assert stamp(path) == legacy_stamp
    assert marker(path).exists()
    # Once created, the marker is authoritative; the legacy mtime is not consulted.
    old(marker(path), 7200)
    assert cache.evict() == [path]


def test_stale_legacy_index_and_orphan_markers_are_removed(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes", max_age_seconds=3600)
    stale = cache.path_for("6" * 40)
    build_index(MemoryTreeSnapshot(stale.stem, "git", "existing-runtime-index", {}), stale)
    old(stale, 7200)
    orphan = marker(cache.path_for("7" * 40))
    orphan.touch()
    unrelated = cache.directory / "not-an-index.used"
    unrelated.touch()
    assert cache.evict() == [stale]
    assert not orphan.exists() and unrelated.exists()


def test_rebuild_replaces_stale_access_hint(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes")
    path = native(cache, "8" * 40)
    old(marker(path), 7200)
    previous = marker(path).stat().st_mtime_ns
    path.write_bytes(b"invalid previous index")
    assert cache._reuse(path.stem) is None
    native(cache, path.stem)
    assert marker(path).stat().st_mtime_ns > previous
    with reopen(cache, path) as rebuilt:
        assert rebuilt.state.key == path.stem


@pytest.mark.parametrize("linked", ["symlink", "hardlink"])
def test_usage_link_never_touches_its_target(
    tmp_path: Path, linked: Literal["symlink", "hardlink"]
) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes")
    path = native(cache, "9" * 40)
    target = tmp_path / "unrelated-target"
    target.write_bytes(b"unchanged target")
    marker(path).unlink()
    if linked == "symlink":
        marker(path).symlink_to(target)
    else:
        os.link(target, marker(path))
    before = stamp(target)
    with reopen(cache, path) as index:
        assert index.state.key == path.stem
    assert stamp(target) == before and target.read_bytes() == b"unchanged target"


def test_usage_write_error_does_not_break_native_read_or_touch_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes")
    path = native(cache, "a" * 40)
    before = stamp(path)
    real_open = module.os.open

    def refuse_marker(target: Path, flags: int, mode: int = 0o777) -> int:
        if target == marker(path):
            raise PermissionError("readable index, unavailable access hint")
        return real_open(target, flags, mode)

    monkeypatch.setattr(module.os, "open", refuse_marker)
    with reopen(cache, path) as index:
        assert index.state.key == path.stem
    assert stamp(path) == before


def test_marker_cleanup_error_does_not_prevent_index_eviction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes", max_age_seconds=3600)
    path = native(cache, "b" * 40)
    old(marker(path), 7200)
    real_unlink = Path.unlink

    def refuse_marker(target: Path, missing_ok: bool = False) -> None:
        if target == marker(path):
            raise PermissionError("unavailable access hint")
        real_unlink(target, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse_marker)
    assert cache.evict() == [path]
    assert not path.exists() and marker(path).exists()
    # The next maintenance pass also treats orphan-marker cleanup as best effort.
    assert cache.evict() == []


def test_fifo_usage_marker_never_waits_for_a_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = KnowledgeIndexCache(tmp_path / "indexes")
    path = native(cache, "c" * 40)
    marker(path).unlink()
    os.mkfifo(marker(path))
    before = stamp(path)
    real_open = module.os.open
    opened: list[int] = []

    def nonblocking_open(target: Path, flags: int, mode: int = 0o777) -> int:
        if target == marker(path):
            # Fail before entering a blocking syscall if the flag regresses.
            assert flags & os.O_NONBLOCK
            opened.append(flags)
        return real_open(target, flags, mode)

    monkeypatch.setattr(module.os, "open", nonblocking_open)
    # O_WRONLY|O_NONBLOCK returns ENXIO with no FIFO reader. The access hint is
    # optional, so the real native index read must return normally.
    with reopen(cache, path) as index:
        assert index.state.key == path.stem
    assert len(opened) == 1 and stamp(path) == before
