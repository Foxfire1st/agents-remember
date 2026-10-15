"""L81 F07/F08: real owned commits, late notes, outside effects and failures."""

from __future__ import annotations

import builtins
import os
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from agents_remember.controlplane import durable_store
from agents_remember.kernel import atomic_write, own_writes
from agents_remember.serving import change_watcher
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from watchfiles import Change


class WatchedWriterTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = own_writes.OwnWriteRegistry(max_entries=4)
        for module in (atomic_write, durable_store, change_watcher):
            patch = mock.patch.object(module, "OWN_WRITES", self.registry)
            patch.start()
            self.addCleanup(patch.stop)

    @contextmanager
    def _held_commit(self, path: Path) -> Iterator[None]:
        committed, release = threading.Event(), threading.Event()
        errors: list[BaseException] = []
        real_flush = atomic_write._fsync_directory

        def pause(directory: Path) -> None:
            real_flush(directory)
            committed.set()
            if not release.wait(HANG_GUARD_SECONDS):
                raise RuntimeError("test did not release the committed writer")

        def write() -> None:
            try:
                atomic_write.atomic_write_bytes(path, b"owned\n", note_own=True)
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(atomic_write, "_fsync_directory", pause):
            worker = threading.Thread(target=write)
            worker.start()
            try:
                self.assertTrue(committed.wait(HANG_GUARD_SECONDS), errors)
                yield
            finally:
                release.set()
                worker.join(HANG_GUARD_SECONDS)
                self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])

    def _judge(self, path: Path):
        return change_watcher._judge_input_changes({(Change.modified, path.as_posix())})

    def test_own_commit_before_note_is_deferred_then_suppressed(self) -> None:
        path = self.root / "current.json"
        with self._held_commit(path):
            self.assertEqual(path.read_bytes(), b"owned\n")
            paths, deferred, overflow = self._judge(path)
            self.assertEqual(paths, set())
            self.assertEqual(deferred, {(Change.modified, path.as_posix())})
            self.assertFalse(overflow)
        self.assertEqual(change_watcher._judge_input_changes(deferred), (set(), set(), False))

    def test_outside_mutation_during_the_late_note_still_wakes(self) -> None:
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                path = self.root / f"current-{replacement}.json"
                with self._held_commit(path):
                    if replacement:
                        # Same bytes, size and restored mtime still cannot adopt a foreign inode.
                        original = path.stat()
                        foreign = path.with_suffix(".foreign")
                        foreign.write_bytes(b"owned\n")
                        os.utime(foreign, ns=(original.st_atime_ns, original.st_mtime_ns))
                        foreign.replace(path)
                    else:
                        path.write_bytes(b"other\n")
                    _, deferred, _ = self._judge(path)
                paths, pending, overflow = change_watcher._judge_input_changes(deferred)
                self.assertEqual(paths, {path.as_posix()})
                self.assertEqual(pending, set())
                self.assertFalse(overflow)

    def test_failed_writer_leaves_no_suppression(self) -> None:
        path = self.root / "current.json"
        with (
            mock.patch.object(atomic_write, "_fsync_directory", side_effect=OSError("refused")),
            self.assertRaisesRegex(OSError, "refused"),
        ):
            atomic_write.atomic_write_bytes(path, b"committed\n", note_own=True)
        self.assertFalse(self.registry.is_pending(path.as_posix()))
        self.assertEqual(self._judge(path), ({path.as_posix()}, set(), False))
        log = self.root / "gates.jsonl"
        with (
            mock.patch.object(durable_store.os, "fsync", side_effect=OSError("append refused")),
            self.assertRaisesRegex(OSError, "append refused"),
        ):
            durable_store.append_line(log, "appended", note_own=True)
        self.assertEqual(self._judge(log), ({log.as_posix()}, set(), False))

    def test_uncertain_identity_and_expired_announcement_wake(self) -> None:
        path = self.root / "current.json"
        atomic_write.atomic_write_bytes(path, b"owned\n", note_own=True)
        with mock.patch.object(own_writes.os, "stat", side_effect=PermissionError("unreadable")):
            self.assertEqual(self._judge(path), ({path.as_posix()}, set(), False))
        with self.registry.announcing(path.as_posix()):
            with mock.patch.object(own_writes.time, "monotonic", return_value=10**10):
                _, pending, _ = self._judge(path)
                paths, pending, _ = change_watcher._judge_input_changes(pending)
            self.assertEqual(paths, {path.as_posix()})
            self.assertEqual(pending, set())

    def test_file_operations_never_hold_the_registry_lock(self) -> None:
        operations = (
            (os, "stat"),
            (os, "fstat"),
            (os, "replace"),
            (os, "fsync"),
            (builtins, "open"),
            (Path, "open"),
            (Path, "mkdir"),
            (Path, "unlink"),
        )
        seen: set[str] = set()

        def checked(name, real):
            def call(*args, **kwargs):
                self.assertFalse(self.registry._lock.locked(), name)
                seen.add(name)
                return real(*args, **kwargs)

            return call

        with ExitStack() as stack:
            for owner, name in operations:
                stack.enter_context(
                    mock.patch.object(owner, name, checked(name, getattr(owner, name)))
                )
            path = self.root / "current.json"
            atomic_write.atomic_write_bytes(path, b"owned\n", note_own=True)
            self.assertTrue(self.registry.is_own_echo(path.as_posix()))
            log = self.root / "gates.jsonl"
            durable_store.append_line(log, "one", note_own=True)
            self.assertTrue(self.registry.is_own_echo(log.as_posix()))
            with self.registry.announcing(path.as_posix()) as announcement:
                path.unlink()
                self.registry.note_removed(path.as_posix(), announcement=announcement)
            self.assertTrue(self.registry.is_own_echo(path.as_posix(), "deleted"))
        self.assertTrue({"stat", "fstat", "replace", "fsync", "open", "mkdir", "unlink"} <= seen)

    def test_evicted_proof_rejects_a_same_byte_foreign_replacement(self) -> None:
        first = self.root / "first.json"
        atomic_write.atomic_write_bytes(first, b"owned\n", note_own=True)
        for index in range(5):
            atomic_write.atomic_write_bytes(
                self.root / f"other-{index}.json", b"owned\n", note_own=True
            )
        self.assertGreater(self.registry.dropped_entries, 0)
        self.assertTrue(self.registry.is_own_echo(first.as_posix()))
        original = first.stat()
        foreign = first.with_suffix(".foreign")
        foreign.write_bytes(b"owned\n")
        os.utime(foreign, ns=(original.st_atime_ns, original.st_mtime_ns))
        foreign.replace(first)
        self.assertFalse(self.registry.is_own_echo(first.as_posix()))
