"""The dashboard's own-write registry (MIK-R81 rule 3): its own files never wake it."""

from __future__ import annotations

import hashlib
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.own_writes import OWN_WRITES, OwnWriteRegistry


class OwnWriteEchoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_noted_write_reads_as_own_echo(self) -> None:
        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        self.assertFalse(registry.is_own_echo(path.as_posix()))
        registry.note_written(path.as_posix())
        self.assertTrue(registry.is_own_echo(path.as_posix()))

    def test_outside_write_after_the_note_breaks_the_echo(self) -> None:
        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        registry.note_written(path.as_posix())
        self.assertTrue(registry.is_own_echo(path.as_posix()))
        # An outside append changes the stat identity: the file is no longer ours.
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.assertFalse(registry.is_own_echo(path.as_posix()))

    def test_missing_file_is_never_an_echo(self) -> None:
        registry = OwnWriteRegistry()
        missing = self.tmp / "absent.json"
        registry.note_written(missing.as_posix())
        self.assertFalse(registry.is_own_echo(missing.as_posix()))

    def test_echo_expires_after_the_window(self) -> None:
        registry = OwnWriteRegistry(suppress_seconds=0.01)
        path = self.tmp / "current.json"
        path.write_text("{}\n", encoding="utf-8")
        with mock.patch(
            "agents_remember.kernel.own_writes.time.monotonic", return_value=100.0
        ) as clock:
            registry.note_written(path.as_posix())
            self.assertTrue(registry.is_own_echo(path.as_posix()))
            clock.return_value = 100.02
            self.assertFalse(registry.is_own_echo(path.as_posix()))


class OwnRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_noted_removal_suppresses_the_delete_event(self) -> None:
        registry = OwnWriteRegistry()
        gone = self.tmp / "snapshot.json"
        gone.write_text("{}\n", encoding="utf-8")
        gone.unlink()
        registry.note_removed(gone.as_posix())
        self.assertTrue(registry.is_own_echo(gone.as_posix()))

    def test_pruned_directory_covers_its_sidecars(self) -> None:
        registry = OwnWriteRegistry()
        lifecycle = self.tmp / "lifecycles" / "L1"
        lifecycle.mkdir(parents=True)
        registry.note_removed(lifecycle.as_posix())
        self.assertTrue(registry.is_own_echo((lifecycle / "events.jsonl").as_posix()))
        self.assertTrue(registry.is_own_echo((lifecycle / "heartbeat.json").as_posix()))
        self.assertFalse(
            registry.is_own_echo((self.tmp / "lifecycles" / "L2" / "events.jsonl").as_posix())
        )

    def test_removal_expires_after_the_window(self) -> None:
        registry = OwnWriteRegistry(suppress_seconds=0.01)
        gone = self.tmp / "snapshot.json"
        with mock.patch(
            "agents_remember.kernel.own_writes.time.monotonic", return_value=100.0
        ) as clock:
            registry.note_removed(gone.as_posix())
            self.assertTrue(registry.is_own_echo(gone.as_posix()))
            clock.return_value = 100.02
            self.assertFalse(registry.is_own_echo(gone.as_posix()))


class RegistryBoundTests(unittest.TestCase):
    def test_entries_are_capped_and_count_their_drops(self) -> None:
        registry = OwnWriteRegistry(max_entries=4)
        for index in range(10):
            registry.note_removed(f"/nonexistent/{index}.json")
        self.assertEqual(registry.dropped_entries, 6)
        self.assertTrue(registry.is_own_echo("/nonexistent/9.json"))
        self.assertFalse(registry.is_own_echo("/nonexistent/0.json"))


class OwnIdentityTests(unittest.TestCase):
    """MIK-R81 rule 3 (F07): suppression binds positively identified own effects only."""

    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_recreated_file_after_a_noted_removal_is_a_genuine_event(self) -> None:
        registry = OwnWriteRegistry()
        gone = self.tmp / "snapshot.json"
        gone.write_text("{}\n", encoding="utf-8")
        gone.unlink()
        registry.note_removed(gone.as_posix())
        self.assertTrue(registry.is_own_echo(gone.as_posix()))
        gone.write_text('{"recreated": true}\n', encoding="utf-8")
        self.assertFalse(
            registry.is_own_echo(gone.as_posix()),
            "a recreation is an outside mutation, never our delete echo",
        )

    def test_recreation_below_a_removed_directory_is_a_genuine_event(self) -> None:
        registry = OwnWriteRegistry()
        lifecycle = self.tmp / "lifecycles" / "L1"
        lifecycle.mkdir(parents=True)
        registry.note_removed(lifecycle.as_posix())
        recreated = lifecycle / "events.jsonl"
        recreated.parent.mkdir(parents=True, exist_ok=True)
        recreated.write_text("{}\n", encoding="utf-8")
        self.assertFalse(registry.is_own_echo(recreated.as_posix()))
        # The foreign create touched the directory, voiding the subtree voucher: even
        # the still-absent sibling no longer reads as our echo (F07). Only a directory
        # untouched since its note vouches for the absent paths below it.
        self.assertFalse(
            registry.is_own_echo((lifecycle / "heartbeat.json").as_posix()),
            "the foreign touch voided the tombstoned directory's voucher",
        )

    def test_same_size_mtime_restored_replacement_is_a_genuine_event(self) -> None:
        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        registry.note_written(path.as_posix())
        self.assertTrue(registry.is_own_echo(path.as_posix()))
        stamp = path.stat()
        path.unlink()
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(path.stat().st_size, stamp.st_size)
        self.assertFalse(
            registry.is_own_echo(path.as_posix()),
            "restored size+mtime still changed ctime and inode",
        )

    def test_outside_delete_of_a_noted_file_is_a_genuine_event(self) -> None:
        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        registry.note_written(path.as_posix())
        path.unlink()
        self.assertFalse(
            registry.is_own_echo(path.as_posix()),
            "no removal tombstone: a foreign delete wakes us",
        )

    def test_outside_write_between_commit_and_note_is_not_certified(self) -> None:
        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            handle.write("{}\n")
            handle.flush()
            committed = os.fstat(handle.fileno())
        # An outside append wins before the note lands.
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"outside": true}\n')
        registry.note_written_identity(path.as_posix(), committed)
        self.assertFalse(
            registry.is_own_echo(path.as_posix()),
            "the descriptor-anchored entry cannot certify the later bytes",
        )

    def test_genuine_event_during_own_work_still_wakes(self) -> None:
        registry = OwnWriteRegistry()
        own = self.tmp / "own.jsonl"
        own.write_text("{}\n", encoding="utf-8")
        registry.note_written(own.as_posix())
        foreign = self.tmp / "foreign.jsonl"
        foreign.write_text("{}\n", encoding="utf-8")
        self.assertTrue(registry.is_own_echo(own.as_posix()))
        self.assertFalse(registry.is_own_echo(foreign.as_posix()))

    def test_noop_sweep_with_outside_append_wakes(self) -> None:
        """A sweep that writes nothing notes nothing: the outside append wakes (F07)."""

        registry = OwnWriteRegistry()
        shared = self.tmp / "operator-inbox.jsonl"
        shared.write_text("{}\n", encoding="utf-8")
        with shared.open("a", encoding="utf-8") as handle:
            handle.write('{"outside": true}\n')
        self.assertFalse(registry.is_own_echo(shared.as_posix()))


class DashboardWriteBoundaryTests(unittest.TestCase):
    """MIK-R81 rule 3 (F08): watched dashboard writes note at their boundary."""

    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_burst_beyond_capacity_keeps_the_newest_suppressed(self) -> None:
        registry = OwnWriteRegistry(max_entries=8)
        paths = []
        for index in range(20):
            path = self.tmp / f"event-{index}.json"
            path.write_text("{}\n", encoding="utf-8")
            registry.note_written(path.as_posix())
            paths.append(path)
        self.assertGreater(registry.dropped_entries, 0)
        self.assertFalse(registry.is_own_echo(paths[0].as_posix()))
        self.assertTrue(registry.is_own_echo(paths[-1].as_posix()))

    def test_stat_only_notes_cannot_survive_eviction(self) -> None:
        """F08 honest limit: without a content proof an evicted path wakes.

        Descriptor-less notes bind stat identity only; once capacity drops the
        entry there is nothing left to prove, so the delayed event reads as
        genuine (safe direction: a wake, never a loss).
        """
        registry = OwnWriteRegistry(max_entries=4)
        first = self.tmp / "first.json"
        first.write_text("{}\n", encoding="utf-8")
        registry.note_written(first.as_posix())
        for index in range(6):
            path = self.tmp / f"later-{index}.json"
            path.write_text("{}\n", encoding="utf-8")
            registry.note_written(path.as_posix())
        self.assertFalse(registry.is_own_echo(first.as_posix()))

    def test_digest_bound_burst_composes_past_capacity(self) -> None:
        """F08: digest-bound writes survive eviction by content proof.

        The stat entry leaves first, but the evicted content proof stays for the
        window, so a delayed event still reads as our echo while a foreign
        rewrite (different bytes) still wakes. Both maps stay bounded: a burst
        past their joint capacity wakes its oldest paths once (safe direction),
        never loses them.
        """
        registry = OwnWriteRegistry(max_entries=8)
        paths = []
        for index in range(12):
            path = self.tmp / f"burst-{index}.json"
            payload = f'{{"burst": {index}}}\n'.encode()
            path.write_bytes(payload)
            registry.note_written_identity(
                path.as_posix(),
                os.stat(path.as_posix()),
                content_sha256=hashlib.sha256(payload).hexdigest(),
            )
            paths.append(path)
        self.assertGreater(registry.dropped_entries, 0)
        for path in paths:
            self.assertTrue(registry.is_own_echo(path.as_posix()), f"lost echo for {path.name}")
        # A foreign rewrite of an evicted path still wakes: the fallback proves
        # content, so different bytes are a genuine event.
        paths[0].write_bytes(b'{"foreign": true}\n')
        self.assertFalse(registry.is_own_echo(paths[0].as_posix()))
        # A removal supersedes the evicted proof: the delete is not our echo.
        paths[1].unlink()
        self.assertFalse(registry.is_own_echo(paths[1].as_posix(), change="deleted"))

    def test_atomic_write_binds_content_and_suppresses_its_echo(self) -> None:
        """F07: the atomic boundary records a digest; its echo suppresses."""

        path = self.tmp / "landing-final.json"
        atomic_write_text(path, '{"final": true}\n', note_own=True)
        # The module registry (not our fresh one) holds the boundary note.
        self.assertTrue(OWN_WRITES.is_own_echo(path.as_posix()))

    def test_digest_bound_entry_rejects_a_stat_match_with_foreign_bytes(self) -> None:
        """F07: same stat but foreign bytes is never our echo.

        A same-inode, same-size, mtime-restored forgery keeps the noted stat
        identity; only the content digest tells it apart. The filesystem always
        moves ctime on write, so the test binds the live stat with a wrong digest
        directly: stat matches, bytes do not, and the echo must be refused.
        """

        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        path.write_text('{"own": true}\n', encoding="utf-8")
        live = os.stat(path.as_posix())
        registry.note_written_identity(
            path.as_posix(), live, content_sha256=hashlib.sha256(b"forged").hexdigest()
        )
        self.assertFalse(registry.is_own_echo(path.as_posix()))

    def test_recreate_below_a_fresh_directory_tombstone_then_delete_is_genuine(
        self,
    ) -> None:
        """F07: a foreign create+delete below a rebuilt directory is not our echo."""
        registry = OwnWriteRegistry()
        directory = self.tmp / "lc-1"
        directory.mkdir()
        target = directory / "events.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        target.unlink()
        directory.rmdir()
        registry.note_removed(directory.as_posix())
        # While the ancestor stays absent, paths below it are still our echo.
        self.assertTrue(registry.is_own_echo(target.as_posix()))
        # A foreign lifecycle rebuilds the directory, writes, then deletes again.
        directory.mkdir()
        target.write_text('{"foreign": true}\n', encoding="utf-8")
        target.unlink()
        self.assertFalse(
            registry.is_own_echo(target.as_posix()),
            "the ancestor is back: the delete belongs to the foreign lifecycle",
        )

    def test_whole_edge_burst_of_boundary_notes_composes(self) -> None:
        """F08: every write of one edge suppresses, including delayed events."""
        paths = []
        for index in range(100):
            path = self.tmp / f"edge-{index}.json"
            atomic_write_text(path, f'{{"edge": {index}}}\n', note_own=True)
            paths.append(path)
        for path in paths:
            self.assertTrue(OWN_WRITES.is_own_echo(path.as_posix()), f"lost echo for {path.name}")
        self.assertEqual(OWN_WRITES.dropped_entries, 0)


class NativeEventKindTests(unittest.TestCase):
    """MIK-R81 rule 3 (F07): the native event kind gates the echo decision."""

    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)

    def test_added_arrival_never_rides_an_ancestor_voucher(self) -> None:
        registry = OwnWriteRegistry()
        directory = self.tmp / "lc-9"
        directory.mkdir()
        registry.note_removed(directory.as_posix())
        target = directory / "events.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        self.assertFalse(registry.is_own_echo(target.as_posix(), "added"))

    def test_modified_present_file_never_rides_an_ancestor_voucher(self) -> None:
        registry = OwnWriteRegistry()
        directory = self.tmp / "lc-9"
        directory.mkdir()
        heartbeat = directory / "heartbeat.json"
        heartbeat.write_text("{}\n", encoding="utf-8")
        registry.note_removed(directory.as_posix())
        # A foreign content rewrite touches no parent identity: without the kind
        # gate the untouched voucher would certify bytes it never observed.
        heartbeat.write_text('{"foreign": true}\n', encoding="utf-8")
        self.assertFalse(registry.is_own_echo(heartbeat.as_posix(), "modified"))

    def test_deleted_event_is_never_a_write_echo(self) -> None:
        registry = OwnWriteRegistry()
        path = self.tmp / "gates.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        registry.note_written(path.as_posix())
        path.unlink()
        self.assertFalse(registry.is_own_echo(path.as_posix(), "deleted"))

    def test_deleted_member_below_an_untouched_voucher_stays_our_echo(self) -> None:
        registry = OwnWriteRegistry()
        directory = self.tmp / "lc-9"
        directory.mkdir()
        target = directory / "events.jsonl"
        target.write_text("{}\n", encoding="utf-8")
        target.unlink()
        registry.note_removed(directory.as_posix())
        self.assertTrue(registry.is_own_echo(target.as_posix(), "deleted"))
