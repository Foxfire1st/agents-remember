"""D569: one process-wide inotify watch over the leaves' git-eligible worktree directories.

Every case runs against real temporary Git repositories and the real kernel instance. A case that
shows an event does NOT arrive writes a sentinel into another watched repository afterwards: the
instance has one ordered queue, so once the sentinel's event is read, any earlier event was too.
"""

from __future__ import annotations

import errno
import os
import struct
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from agents_remember.serving import leaf_worktree_watch as module
from agents_remember.serving.leaf_worktree_watch import (
    IN_Q_OVERFLOW,
    STREAM_REASON,
    LeafWorktreeWatch,
    eligible_directories,
)
from agents_remember_test_support.testing.waits import wait_until

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="inotify is Linux's")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={"PATH": os.environ["PATH"], "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )


def _repository(root: Path) -> Path:
    for relative, text in {
        ".gitignore": "node_modules/\n",
        "pkg/a.py": "a = 1\n",
        "docs/guide/b.md": "b\n",
        "node_modules/dep/index.js": "x\n",
    }.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    return root


@dataclass
class Recorder:
    """What the watch called, guarded for the reader thread."""

    changes: list[str] = field(default_factory=list)
    overflows: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def change(self, key: str) -> None:
        with self.lock:
            self.changes.append(key)

    def overflow(self) -> None:
        with self.lock:
            self.overflows += 1

    def seen(self, key: str) -> bool:
        with self.lock:
            return key in self.changes

    def clear(self) -> None:
        with self.lock:
            self.changes.clear()


@dataclass
class Fixture:
    roots: dict[str, tuple[Path, ...]]
    recorder: Recorder
    watch: LeafWorktreeWatch[str]


@pytest.fixture
def fixture(tmp_path: Path) -> Iterator[Fixture]:
    one = _repository(tmp_path / "one")
    two = _repository(tmp_path / "two")
    roots = {"A": (one,), "B": (two,), "S": (one,)}
    recorder = Recorder()
    watch: LeafWorktreeWatch[str] = LeafWorktreeWatch(
        lambda key: roots[key], recorder.change, recorder.overflow
    )
    watch.start()
    try:
        yield Fixture(roots, recorder, watch)
    finally:
        watch.shutdown()


def _watched(watch: LeafWorktreeWatch[str]) -> set[Path]:
    with watch._lock:
        return set(watch._maps.wd_of)


def _sentinel(fixture: Fixture, key: str = "B") -> None:
    """Write into ``key``'s repository and wait for its event: the queue is read up to here."""

    fixture.recorder.clear()
    (fixture.roots[key][0] / "pkg/a.py").write_text("sentinel\n", encoding="utf-8")
    wait_until(lambda: fixture.recorder.seen(key), f"the sentinel event of {key}")


def test_the_directories_are_the_git_eligible_ones(tmp_path: Path) -> None:
    root = _repository(tmp_path / "repo")
    assert eligible_directories(root) == (root, root / "docs", root / "docs/guide", root / "pkg")


def test_an_edit_fires_its_key(fixture: Fixture) -> None:
    fixture.watch.cover("A", "seat")
    assert fixture.watch.covered("A")
    (fixture.roots["A"][0] / "docs/guide/b.md").write_text("edited\n", encoding="utf-8")
    wait_until(lambda: fixture.recorder.seen("A"), "the edit's change")


def test_an_ignored_directory_is_not_watched_and_its_edits_do_not_fire(fixture: Fixture) -> None:
    fixture.watch.cover("A", "seat")
    fixture.watch.cover("B", "seat")
    one = fixture.roots["A"][0]
    assert not {path for path in _watched(fixture.watch) if "node_modules" in path.parts}
    assert not {path for path in _watched(fixture.watch) if ".git" in path.parts}
    fixture.recorder.clear()
    (one / "node_modules/dep/index.js").write_text("changed\n", encoding="utf-8")
    (one / "pkg/scratch.py.lock").write_text("lock\n", encoding="utf-8")
    _sentinel(fixture)
    assert not fixture.recorder.seen("A")


def test_a_new_eligible_directory_is_watched_after_it_is_created(fixture: Fixture) -> None:
    fixture.watch.cover("A", "seat")
    one = fixture.roots["A"][0]
    (one / "fresh").mkdir()
    (one / "fresh/new.py").write_text("n = 1\n", encoding="utf-8")
    wait_until(lambda: one / "fresh" in _watched(fixture.watch), "the new directory's watch")
    fixture.recorder.clear()
    (one / "fresh/new.py").write_text("n = 2\n", encoding="utf-8")
    wait_until(lambda: fixture.recorder.seen("A"), "an edit inside the new directory")


def test_dropping_a_key_removes_its_watches_and_its_events_stop(fixture: Fixture) -> None:
    fixture.watch.cover("A", "seat")
    fixture.watch.cover("B", "seat")
    one = fixture.roots["A"][0]
    fixture.watch.uncover("A", "seat")
    assert not fixture.watch.covered("A")
    assert not {path for path in _watched(fixture.watch) if one in path.parents or path == one}
    (one / "pkg/a.py").write_text("after the drop\n", encoding="utf-8")
    _sentinel(fixture)
    assert not fixture.recorder.seen("A")


def test_two_keys_sharing_a_root_both_fire_and_share_its_watches(fixture: Fixture) -> None:
    fixture.watch.cover("A", "seat")
    alone = fixture.watch.total_watches()
    fixture.watch.cover("S", "environment")
    assert fixture.watch.total_watches() == alone  # one watch per directory, whoever needs it
    (fixture.roots["A"][0] / "pkg/a.py").write_text("both\n", encoding="utf-8")
    wait_until(
        lambda: fixture.recorder.seen("A") and fixture.recorder.seen("S"), "both keys' changes"
    )
    fixture.watch.uncover("A", "seat")
    assert fixture.watch.total_watches() == alone  # S still holds every one of them


def test_an_overflow_calls_on_overflow_once_each_and_notifies_every_stream(
    fixture: Fixture,
) -> None:
    notified: list[Exception | None] = []
    unsubscribe = fixture.watch.subscribe("A", fixture.roots["A"][0], notified.append)
    overflow = struct.pack("iIII", -1, IN_Q_OVERFLOW, 0, 0)
    fixture.watch.handle(overflow + overflow)
    assert fixture.recorder.overflows == 2
    assert notified == [None]  # one batch, one notification
    unsubscribe()


def test_an_add_failure_rolls_the_key_back_and_marks_it_uncovered(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture.watch.cover("B", "seat")
    before = fixture.watch.total_watches()
    calls = fixture.watch._calls
    assert calls is not None
    real = calls.add
    added: list[int] = []

    def spent(fd: int, path: Path, mask: int) -> int:
        if len(added) == 2:
            raise OSError(errno.ENOSPC, "inotify_add_watch: No space left on device")
        added.append(real(fd, path, mask))
        return added[-1]

    monkeypatch.setattr(calls, "add", spent)
    fixture.watch.cover("A", "seat")
    assert fixture.watch.uncovered() == frozenset({"A"}) and not fixture.watch.covered("A")
    assert fixture.watch.total_watches() == before  # the two it had added are gone again
    monkeypatch.setattr(calls, "add", real)
    fixture.watch.sync({"A": frozenset({"seat"}), "B": frozenset({"seat"})})
    assert fixture.watch.covered("A") and not fixture.watch.uncovered()


def test_one_inotify_instance_serves_every_key_and_stream(
    fixture: Fixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def instances() -> int:
        return sum(
            os.readlink(f"/proc/self/fd/{one}") == "anon_inode:inotify"
            for one in os.listdir("/proc/self/fd")
            if os.path.exists(f"/proc/self/fd/{one}")
        )

    opened: list[int] = []
    real = module._Inotify.init
    monkeypatch.setattr(module._Inotify, "init", lambda self: opened.append(1) or real(self))
    before = instances()
    for number in range(5):
        fixture.roots[f"K{number}"] = (_repository(tmp_path / f"k{number}"),)
        fixture.watch.cover(f"K{number}", "seat")
    unsubscribe = fixture.watch.subscribe("A", fixture.roots["A"][0], lambda _error: None)
    assert opened == [] and instances() == before  # the instance start() opened serves them all
    unsubscribe()


def test_a_key_is_covered_while_any_reason_holds_it(fixture: Fixture) -> None:
    fixture.watch.cover("A", "seat")
    fixture.watch.cover("A", "environment")
    fixture.watch.uncover("A", "seat")
    assert fixture.watch.covered("A") and fixture.watch.reasons("A") == {"environment"}
    fixture.watch.sync({"A": frozenset({"seat", "environment"})})
    assert fixture.watch.reasons("A") == {"seat", "environment"}
    fixture.watch.sync({})
    assert not fixture.watch.covered("A") and fixture.watch.total_watches() == 0


def test_a_subscription_is_armed_before_it_returns(fixture: Fixture) -> None:
    one = fixture.roots["A"][0]
    notified = threading.Event()
    unsubscribe = fixture.watch.subscribe("A", one, lambda _error: notified.set())
    assert fixture.watch.covered("A")
    # The baseline is read now, after arming: an edit made next is the stream's next event.
    (one / "pkg/a.py").write_text("after arming\n", encoding="utf-8")
    wait_until(notified.is_set, "the subscriber's notification")
    unsubscribe()


def test_a_subscription_hears_only_its_listen_root(fixture: Fixture) -> None:
    one = fixture.roots["A"][0]
    docs: list[Exception | None] = []
    pkg = threading.Event()
    stop_docs = fixture.watch.subscribe("A", one / "docs", docs.append)
    stop_pkg = fixture.watch.subscribe("A", one / "pkg", lambda _error: pkg.set())
    (one / "pkg/a.py").write_text("only pkg\n", encoding="utf-8")
    wait_until(pkg.is_set, "the pkg subscriber's notification")
    assert docs == []
    stop_docs()
    stop_pkg()


def test_unsubscribing_frees_the_watches_no_other_reason_keeps(fixture: Fixture) -> None:
    unsubscribe = fixture.watch.subscribe("A", fixture.roots["A"][0], lambda _error: None)
    assert fixture.watch.total_watches() > 0
    unsubscribe()
    unsubscribe()  # once only
    assert not fixture.watch.covered("A") and fixture.watch.total_watches() == 0
    fixture.watch.cover("B", "seat")
    stop = fixture.watch.subscribe("B", fixture.roots["B"][0], lambda _error: None)
    stop()
    assert fixture.watch.covered("B") and fixture.watch.reasons("B") == {"seat"}


def test_a_stream_only_key_is_covered_and_sync_leaves_its_stream(fixture: Fixture) -> None:
    unsubscribe = fixture.watch.subscribe("A", fixture.roots["A"][0], lambda _error: None)
    assert fixture.watch.covered("A") and fixture.watch.reasons("A") == {STREAM_REASON}
    fixture.watch.sync({"B": frozenset({"seat"})})
    assert fixture.watch.covered("A") and fixture.watch.reasons("A") == {STREAM_REASON}
    unsubscribe()
    assert not fixture.watch.covered("A")


def test_a_subscription_that_cannot_be_covered_raises_its_reason(
    fixture: Fixture, tmp_path: Path
) -> None:
    fixture.roots["gone"] = (tmp_path / "missing",)
    with pytest.raises(OSError, match="missing"):
        fixture.watch.subscribe("gone", tmp_path / "missing", lambda _error: None)
    assert fixture.watch.reasons("gone") == frozenset() and not fixture.watch.covered("gone")


def test_a_sync_logs_the_plan_when_it_changed_it_and_not_on_every_tick(
    fixture: Fixture, caplog: pytest.LogCaptureFixture
) -> None:
    def synced() -> int:
        return sum("leaf worktree watch synced" in record.getMessage() for record in caplog.records)

    caplog.set_level("INFO", logger=module.__name__)
    fixture.watch.sync({"A": frozenset({"seat"})})
    assert synced() == 1
    fixture.watch.sync({"A": frozenset({"seat"})})
    fixture.watch.sync({"A": frozenset({"seat"})})
    assert synced() == 1
    fixture.watch.sync({"A": frozenset({"seat", "environment"})})
    assert synced() == 2
    fixture.watch.sync({})
    assert synced() == 3
