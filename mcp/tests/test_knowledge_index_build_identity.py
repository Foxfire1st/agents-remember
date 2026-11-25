"""A derived index belongs to the package source that built it."""

import json
import os
import select
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Semaphore, local
from time import monotonic_ns

import pytest
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.errors import LockCapabilityError
from agents_remember.kernel.file_lock import LockAcquisitionTimeout, lock_path_for
from agents_remember.memory.knowledge_index import build as builder
from agents_remember.memory.knowledge_index import cache as module
from agents_remember.memory.knowledge_index.build import BuildReport, build_index
from agents_remember.memory.knowledge_index.cache import KnowledgeIndexCache
from agents_remember.memory.knowledge_index.query import KnowledgeIndex
from agents_remember.memory.knowledge_index.tree import MemoryTreeSnapshot
from agents_remember.models.knowledge.read import (
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    PathSeed,
)
from agents_remember.serving.build_info import process_serving_build, runtime_source_digest
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from knowledge_index_test_support import (
    REVIEW_INVARIANT,
    REVIEW_PATH,
    init_repository,
    write_review_tree,
)


def loaded(monkeypatch: pytest.MonkeyPatch, digest: str | None) -> None:
    stamp = replace(process_serving_build(), source_digest=digest)
    monkeypatch.setattr(module, "process_serving_build", lambda: stamp)


def test_another_build_rebuilds_a_partial_index_and_the_same_build_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    original = builder._parse_file

    def older_reader(
        parsed: builder.ParsedTree,
        path: str,
        text: str,
        read: Callable[[str], builder.FileModel],
    ) -> None:
        if REVIEW_INVARIANT in path:
            raise ValueError("record unsupported by the older build")
        original(parsed, path, text, read)

    loaded(monkeypatch, "sha256:" + "1" * 64)
    first = KnowledgeIndexCache(tmp_path / "cache")
    with monkeypatch.context() as patch:
        patch.setattr(builder, "_parse_file", older_reader)
        with first.for_directory(memory) as partial:
            assert not partial.state.complete
            assert partial.record(REVIEW_INVARIANT).value is None
            first_path = partial.database_path
    loaded(monkeypatch, "sha256:" + "2" * 64)
    second = KnowledgeIndexCache(first.directory)
    with second.for_directory(memory) as complete:
        assert complete.state.complete
        assert complete.record(REVIEW_INVARIANT).value is not None
        assert complete.database_path != first_path
    with second.for_directory(memory):
        assert second.last_outcome is not None and second.last_outcome.reused


def test_known_build_reuses_after_a_process_restart(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    command = """
import json, sys
from pathlib import Path
from agents_remember.memory.knowledge_index.cache import KnowledgeIndexCache
cache = KnowledgeIndexCache(Path(sys.argv[1]))
with cache.for_directory(Path(sys.argv[2])) as index:
    print(json.dumps([str(index.database_path), cache.last_outcome.reused]))
"""
    arguments = [sys.executable, "-c", command, str(tmp_path / "cache"), str(memory)]
    first = json.loads(subprocess.check_output(arguments, text=True))
    second = json.loads(subprocess.check_output(arguments, text=True))
    assert first[0] == second[0]
    assert first[1] is False and second[1] is True


def _memory_fixture(tmp_path: Path) -> Path:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    return memory


def _align_cold_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    first_miss = Barrier(8, timeout=HANG_GUARD_SECONDS)
    seen = local()
    reuse = KnowledgeIndexCache._reuse

    def observe_miss(cache: KnowledgeIndexCache, key: str) -> KnowledgeIndex | None:
        index = reuse(cache, key)
        if index is None and not getattr(seen, "missed", False):
            seen.missed = True
            first_miss.wait()
        return index

    monkeypatch.setattr(KnowledgeIndexCache, "_reuse", observe_miss)


def _read_index(directory: Path, memory: Path) -> Path:
    cache = KnowledgeIndexCache(directory)
    with cache.for_directory(memory) as index:
        assert index.invariant(REVIEW_INVARIANT).value is not None
        return index.database_path


def _assert_build_quiescent(directory: Path) -> None:
    directory = directory.resolve()
    with module._builds_guard:
        assert not any(path.parent == directory for path in module._builds)
    with module._active_temps_guard:
        assert not any(path.parent == directory for path in module._active_temps)


@pytest.mark.parametrize(
    "lock_failure",
    [
        pytest.param(None, id="real-lock"),
        pytest.param(LockCapabilityError, id="lock-does-not-exclude"),
        pytest.param(LockAcquisitionTimeout, id="lock-wait-expires"),
    ],
)
def test_eight_cold_callers_share_one_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_failure: type[LockCapabilityError] | None
) -> None:
    memory = _memory_fixture(tmp_path)
    directory = tmp_path / "cache"
    build_started = Event()
    release_build = Event()
    builds: list[Path] = []
    lock_attempts: list[Path] = []

    def refuse_lock(resource_path: Path, _resource: str, *, deadline: float) -> None:
        assert lock_failure is not None and deadline > 0
        lock_attempts.append(resource_path)
        raise lock_failure("the cache lock could not be acquired")

    def slow_build(snapshot: MemoryTreeSnapshot, path: Path) -> BuildReport:
        builds.append(path)
        build_started.set()
        assert release_build.wait(HANG_GUARD_SECONDS)
        return build_index(snapshot, path)

    if lock_failure is not None:
        monkeypatch.setattr(module, "exclusive_file_lock", refuse_lock)
    _align_cold_readers(monkeypatch)
    monkeypatch.setattr(module, "build_index", slow_build)
    with ThreadPoolExecutor(max_workers=8) as pool:
        reads = [pool.submit(_read_index, directory, memory) for _ in range(8)]
        try:
            assert build_started.wait(HANG_GUARD_SECONDS)
        finally:
            release_build.set()
        assert len({read.result(timeout=HANG_GUARD_SECONDS) for read in reads}) == 1
    _assert_build_quiescent(directory)
    assert len(builds) == 1
    if lock_failure is not None:
        assert len(lock_attempts) == 1


def test_failed_build_reaches_all_waiters_and_a_later_lookup_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = _memory_fixture(tmp_path)
    directory = tmp_path / "cache"
    followers = Semaphore(0)
    release_build = Event()
    builds: list[Path] = []

    class ObservedFuture[T](Future[T]):
        def result(self, timeout: float | None = HANG_GUARD_SECONDS) -> T:
            followers.release()
            return super().result(timeout)

    def fail_build(_snapshot: MemoryTreeSnapshot, path: Path) -> BuildReport:
        builds.append(path)
        assert release_build.wait(HANG_GUARD_SECONDS)
        raise LockCapabilityError("failure inside the index build")

    with monkeypatch.context() as patch:
        patch.setattr(module, "Future", ObservedFuture)
        patch.setattr(module, "build_index", fail_build)
        _align_cold_readers(patch)
        with ThreadPoolExecutor(max_workers=8) as pool:
            reads = [pool.submit(_read_index, directory, memory) for _ in range(8)]
            try:
                assert all(followers.acquire(timeout=HANG_GUARD_SECONDS) for _ in range(7))
            finally:
                release_build.set()
            for read in reads:
                with pytest.raises(LockCapabilityError, match="failure inside the index build"):
                    read.result(timeout=HANG_GUARD_SECONDS)
    _assert_build_quiescent(directory)
    assert len(builds) == 1
    assert not list(directory.glob("*.sqlite"))
    assert _read_index(directory, memory).is_file()


def test_ordinary_lock_error_reaches_caller_without_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = _memory_fixture(tmp_path)
    directory = tmp_path / "cache"

    def fail_lock(_path: Path, _resource: str, *, deadline: float) -> None:
        assert deadline > 0
        raise PermissionError("cannot open the cache lock")

    monkeypatch.setattr(module, "exclusive_file_lock", fail_lock)
    with pytest.raises(PermissionError, match="cannot open the cache lock"):
        _read_index(directory, memory)
    assert not list(directory.glob("*.sqlite"))


def test_lookup_reuses_the_captured_key_after_a_working_tree_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = _memory_fixture(tmp_path)
    prior_key = module.directory_key(memory)
    (memory / "working.txt").write_text("New working tree content.\n", encoding="utf-8")
    cache = KnowledgeIndexCache(tmp_path / "cache")
    with cache.for_directory(memory) as index:
        captured_path = index.database_path
        assert index.state.key != prior_key

    monkeypatch.setattr(module, "directory_key", lambda _memory: prior_key)
    with cache.for_directory(memory) as index:
        assert index.database_path == captured_path
        assert cache.last_outcome is not None and cache.last_outcome.reused
    assert not cache.path_for(prior_key).exists()


_CONCURRENT_INDEX_PROCESS = """
import fcntl
import json
import sys
from pathlib import Path

from agents_remember.memory.knowledge_index import cache as module

mode, directory, memory = sys.argv[1:]
if mode == "builder":
    build = module.build_index

    def slow_build(snapshot, path):
        path.touch()
        print(json.dumps(["builder entered", str(path)]), flush=True)
        sys.stdin.readline()
        return build(snapshot, path)

    module.build_index = slow_build
else:
    flock = fcntl.flock
    announced = False

    def observe_flock(descriptor, operation):
        global announced
        try:
            return flock(descriptor, operation)
        except BlockingIOError:
            if not announced:
                print("waiter blocked", flush=True)
                announced = True
            raise

    fcntl.flock = observe_flock

cache = module.KnowledgeIndexCache(Path(directory))
with cache.for_directory(Path(memory)) as index:
    print(json.dumps([str(index.database_path), cache.last_outcome.reused, index.state.complete]))
"""


@contextmanager
def _index_process(mode: str, directory: Path, memory: Path) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [sys.executable, "-c", _CONCURRENT_INDEX_PROCESS, mode, str(directory), str(memory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=HANG_GUARD_SECONDS)


def _process_signal(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], HANG_GUARD_SECONDS)
    assert ready, f"index process {process.pid} did not reach its synchronization point"
    return process.stdout.readline().strip()


def test_two_processes_share_one_cold_index(tmp_path: Path) -> None:
    memory = _memory_fixture(tmp_path)
    directory = tmp_path / "cache"
    with _index_process("builder", directory, memory) as first:
        signal, temporary = json.loads(_process_signal(first))
        assert signal == "builder entered"
        with _index_process("waiter", directory, memory) as second:
            assert _process_signal(second) == "waiter blocked"
            KnowledgeIndexCache(directory).evict()
            assert Path(temporary).is_file()
            built, _ = first.communicate(input="\n", timeout=HANG_GUARD_SECONDS)
            opened, _ = second.communicate(timeout=HANG_GUARD_SECONDS)
            assert first.returncode == second.returncode == 0
    built_path, built_reused, built_complete = json.loads(built)
    opened_path, opened_reused, opened_complete = json.loads(opened)
    assert built_path == opened_path
    assert built_reused is False and opened_reused is True
    assert built_complete is True and opened_complete is True
    assert not Path(temporary).exists()
    assert list(directory.glob("*.sqlite")) == [Path(built_path)]


def _temp_path(directory: Path, pid: int) -> Path:
    return directory / f".{'a' * 40}.{pid}.{monotonic_ns()}.tmp"


def test_eviction_removes_dead_builder_temp_and_keeps_live_builder_temp(tmp_path: Path) -> None:
    memory = _memory_fixture(tmp_path)
    cache = KnowledgeIndexCache(tmp_path / "cache")
    command = [sys.executable, "-c", "import sys; sys.stdin.read()"]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as dead:
        abandoned = _temp_path(cache.directory, dead.pid)
        abandoned.touch()
        dead.communicate(timeout=HANG_GUARD_SECONDS)
        assert dead.returncode == 0
    with subprocess.Popen(command, stdin=subprocess.PIPE) as live:
        active = _temp_path(cache.directory, live.pid)
        active.touch()
        try:
            with cache.for_directory(memory) as index:
                assert index.state.complete
            assert not abandoned.exists()
            assert active.is_file()
        finally:
            live.communicate(timeout=HANG_GUARD_SECONDS)


def test_eviction_keeps_registered_and_ambiguous_current_pid_temps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = _memory_fixture(tmp_path)
    cache = KnowledgeIndexCache(tmp_path / "cache")
    ambiguous = _temp_path(cache.directory, os.getpid())
    ambiguous.touch()

    def evict_during_build(snapshot: MemoryTreeSnapshot, path: Path) -> BuildReport:
        report = build_index(snapshot, path)
        cache.evict()
        assert path.is_file()
        assert ambiguous.is_file()
        return report

    monkeypatch.setattr(module, "build_index", evict_during_build)
    assert _read_index(cache.directory, memory).is_file()
    assert ambiguous.is_file()


def test_identity_uses_loaded_source_and_package_changes_but_not_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = package / "builder.py"
    source.write_text("VALUE = 1\n")
    first = runtime_source_digest(package)
    loaded(monkeypatch, first)
    before = KnowledgeIndexCache(tmp_path / "cache")
    source.write_text("VALUE = 2\n")
    second = runtime_source_digest(package)
    assert first != second
    # The already-loaded process keeps its identity after disk bytes move.
    assert KnowledgeIndexCache(before.directory).path_for("tree") == before.path_for("tree")
    loaded(monkeypatch, second)
    changed = KnowledgeIndexCache(before.directory)
    assert changed.path_for("tree") != before.path_for("tree")
    (package / "README.md").write_text("A document edit\n")
    assert runtime_source_digest(package) == second


def test_legacy_and_new_builds_never_find_each_others_file(tmp_path: Path) -> None:
    cache = KnowledgeIndexCache(tmp_path / "cache")
    key = "a" * 40
    snapshot = MemoryTreeSnapshot(key, "git", "native", {})
    legacy = cache.directory / f"{key}.sqlite"
    build_index(snapshot, legacy)
    assert cache._reuse(key) is None
    with cache._build(snapshot) as index:
        assert index.database_path != legacy
    legacy.unlink()
    assert not legacy.exists()
    assert cache.path_for(key).is_file()


def test_unknown_build_private_files_support_real_reads_and_are_not_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = tmp_path / "memory"
    init_repository(memory)
    write_review_tree(memory)
    known = KnowledgeIndexCache(tmp_path / "cache")
    with known.for_directory(memory) as retained:
        retained_path = retained.database_path
        key = retained.state.key
    loaded(monkeypatch, None)
    first = KnowledgeIndexCache(known.directory)
    second = KnowledgeIndexCache(first.directory)
    assert first.path_for(key) != second.path_for(key)
    assert first._reuse(key) is None
    with first.for_directory(memory) as index:
        assert index.database_path.is_file()
        assert index.database_path != retained_path
        context = open_read_context(index.database_path, index.repository_id)
        page = read_knowledge_scope(
            index.database_path,
            context,
            KnowledgeReadRequest(
                seed=PathSeed(path=REVIEW_PATH),
                budget=KnowledgeReadBudget(max_items=64),
            ),
        ).page
        assert page is not None and page.items
        assert second._reuse(key) is None
    with second.for_directory(memory) as other:
        assert other.database_path != first.path_for(key)
    assert len(list(first.directory.glob("*.sqlite"))) == 3
    first.max_bytes = 0
    first.evict()
    assert list(first.directory.iterdir()) == [
        lock_path_for(first.directory / module._BUILD_LOCK_RESOURCE)
    ]
