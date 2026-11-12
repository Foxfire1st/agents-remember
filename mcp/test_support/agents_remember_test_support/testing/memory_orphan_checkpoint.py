"""The memory-orphan crash checkpoint used by one parked-candidate regression."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import tempfile
import time
import traceback
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from unittest import TestCase

from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord, SyncOperationStore
from agents_remember.worktrees.worktree_contract import WorktreeContract


class _MemoryOrphanFixture(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def contract(self) -> WorktreeContract: ...

    def sync(self) -> WorktreeCommandResult: ...


class MemoryOrphanCheckpoint:
    """Retain the native crash evidence and all-file proof for the single caller."""

    fixture: _MemoryOrphanFixture
    worktree: Path
    store: SyncOperationStore

    def __init__(self, test: TestCase, git: Callable[..., str]) -> None:
        self.test = test
        self.git = git
        self.evidence = Path(tempfile.mkdtemp(prefix="sync-memory-orphan-evidence-"))
        print(f"memory orphan evidence: {self.evidence}", flush=True)
        self.expected = {f"mem-{i:05d}.txt": f"parked memory {i}\n".encode() for i in range(500)}
        self.retain(
            "expected.json",
            {name: hashlib.sha256(data).hexdigest() for name, data in self.expected.items()},
        )
        self.pid: int | None = None

    def retain(self, name: str, value: object) -> None:
        (self.evidence / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def run(self, fixture: _MemoryOrphanFixture) -> None:
        self.fixture = fixture
        worktree = fixture.contract.memory_worktree
        assert worktree is not None
        self.worktree = worktree
        self.store = SyncOperationStore(fixture.contract.worktree_group)
        self.retain(
            "fixture.json",
            {
                "root": str(fixture.root),
                "memoryWorktree": str(self.worktree),
                "journal": str(self.store.path),
            },
        )
        self.pid = os.fork()
        if self.pid == 0:
            self._child()
        try:
            self._wait_for_stop()
            checkpoint = self._assert_checkpoint()
            self._kill()
            self.test.assertIsNone(self.store.read())
            self._recover(checkpoint)
        except BaseException:
            self.retain("parent-exception.json", {"traceback": traceback.format_exc()})
            raise
        finally:
            self._cleanup()

    def _pause(self, event: str, args: tuple) -> None:
        if event != "os.rename" or Path(args[1]) != self.store.path:
            return
        pending = Path(args[0]).read_text(encoding="utf-8")
        (self.evidence / "pending-journal.json").write_text(pending, encoding="utf-8")
        record = SyncOperationRecord.model_validate_json(pending)
        stash_list = self.git(self.worktree, "stash", "list")
        status = self.git(self.worktree, "status", "--porcelain")
        checkpoint = {
            "event": event,
            "source": str(args[0]),
            "destination": str(args[1]),
            "phase": record.phase,
            "stashList": stash_list,
            "status": status,
            "journalAbsent": self.store.read() is None,
            "stash": self.git(self.worktree, "rev-parse", "refs/stash"),
        }
        self.retain("checkpoint.json", checkpoint)
        assert record.phase == "running-code", checkpoint
        assert "parked memory WIP" in stash_list, checkpoint
        assert status == "" and checkpoint["journalAbsent"], checkpoint
        assert record.memory is not None, checkpoint
        assert record.memory.wipStash == checkpoint["stash"], checkpoint
        os.kill(os.getpid(), signal.SIGSTOP)
        raise AssertionError("held journal publish must end through SIGKILL")

    def _child(self) -> None:
        try:
            os.setsid()
            with (
                (self.evidence / "child.stdout").open("wb") as out,
                (self.evidence / "child.stderr").open("wb") as err,
            ):
                os.dup2(out.fileno(), 1)
                os.dup2(err.fileno(), 2)
                sys.addaudithook(self._pause)
                result = self.fixture.sync()
                self.retain(
                    "child-outcome.json",
                    {"returncode": result.returncode, "payload": result.payload},
                )
        except BaseException:
            self.retain("child-exception.json", {"traceback": traceback.format_exc()})
        finally:
            os._exit(42)

    def _wait_for_stop(self) -> None:
        assert self.pid is not None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            waited, status = os.waitpid(self.pid, os.WNOHANG | os.WUNTRACED)
            if waited:
                self.retain("child-status.json", {"pid": self.pid, "waitStatus": status})
                if not os.WIFSTOPPED(status):
                    self.pid = None
                    self.test.fail(
                        f"native sync ended before the memory checkpoint; evidence: {self.evidence}"
                    )
                self.test.assertEqual(os.WSTOPSIG(status), signal.SIGSTOP)
                return
            time.sleep(0.001)
        self.test.fail(f"native memory checkpoint was not reached; evidence: {self.evidence}")

    def _assert_checkpoint(self) -> dict:
        checkpoint = json.loads((self.evidence / "checkpoint.json").read_text(encoding="utf-8"))
        self.test.assertTrue(checkpoint["journalAbsent"])
        self.test.assertIsNone(self.store.read())
        self.test.assertEqual(self.git(self.worktree, "status", "--porcelain"), "")
        self.test.assertIn("parked memory WIP", self.git(self.worktree, "stash", "list"))
        return checkpoint

    def _kill(self) -> None:
        assert self.pid is not None
        os.killpg(self.pid, signal.SIGKILL)
        _, status = os.waitpid(self.pid, 0)
        self.retain("killed-status.json", {"pid": self.pid, "waitStatus": status})
        self.pid = None
        self.test.assertTrue(os.WIFSIGNALED(status), status)
        self.test.assertEqual(os.WTERMSIG(status), signal.SIGKILL, status)

    def _recover(self, checkpoint: dict) -> None:
        rerun = self.fixture.sync()
        self.retain("recovery.json", {"returncode": rerun.returncode, "payload": rerun.payload})
        record = self.store.read()
        assert isinstance(record, SyncOperationRecord)
        (self.evidence / "recovery-journal.json").write_text(
            record.model_dump_json(indent=2), encoding="utf-8"
        )
        assert record.memory is not None
        self.test.assertEqual(record.memory.wipStash, checkpoint["stash"])
        self.test.assertIn(
            rerun.payload["state"], ("synced", "memory-resolution-required"), rerun.payload
        )
        if rerun.payload["state"] == "synced":
            self._assert_restored()
        else:
            self._assert_retained(record.memory.wipStash, checkpoint["stash"])

    def _assert_restored(self) -> None:
        self.test.assertEqual(self.git(self.worktree, "stash", "list"), "")
        self.test.assertEqual({p.name for p in self.worktree.glob("mem-*.txt")}, set(self.expected))
        actual = {name: (self.worktree / name).read_bytes() for name in self.expected}
        self.retain(
            "restored.json",
            {name: hashlib.sha256(data).hexdigest() for name, data in actual.items()},
        )
        self.test.assertEqual(actual, self.expected)

    def _assert_retained(self, stash: str, expected_stash: str) -> None:
        stashes = self.git(self.worktree, "stash", "list", "--format=%H %s")
        self.retain("retained-stashes.json", stashes)
        self.test.assertIn(expected_stash, stashes)
        self.test.assertIn("parked memory WIP", stashes)
        raw_tree = self.git(self.worktree, "ls-tree", "-r", "-z", f"{stash}^3")
        self.retain("retained-tree.json", raw_tree)
        blobs = {}
        for entry in raw_tree.split("\0"):
            if entry:
                metadata, name = entry.split("\t", 1)
                blobs[name] = metadata.split()[2]
        for name, data in self.expected.items():
            self.test.assertEqual(
                blobs.get(name), hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest(), name
            )

    def _cleanup(self) -> None:
        if self.pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(self.pid, signal.SIGKILL)
            with suppress(ChildProcessError):
                _, status = os.waitpid(self.pid, 0)
                self.retain("cleanup-status.json", {"pid": self.pid, "waitStatus": status})
