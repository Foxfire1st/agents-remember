"""The dashboard's own-write registry: its own files never wake its own projector (MIK-R81 rule 3).

The projection tick and the serving loops write files that live under watched
roots: gate-log compaction rewrites ``gates.jsonl``, event retention rewrites
per-lifecycle ``events.jsonl``, the provider refresh rewrites
``logs/providers/status``, and the landing refresher writes
``landing-final.json`` beside enclosure contracts. Without this registry each of
those writes returns through inotify as a change and wakes a self-echo tick.

Every dashboard-owned write site records its file here *at* the write, with the
writing descriptor's stat identity. The watcher filter drops an event whose file
still carries that recorded identity: an outside write to the same file in between
changes the identity, so only a file nobody else touched is suppressed. A
pending commit is judged on the next watch pass, after the writer records its
identity or aborts. Unknown identity, expired announcements and outside writes
wake the projector; file operations never hold the registry's metadata lock.

Bounded by construction (D2/D3): at most :data:`OWN_WRITE_ENTRIES` paths are
kept; beyond that the oldest entries leave first and a counter says so. Entries
older than :data:`OWN_WRITE_SUPPRESS_SECONDS` are ignored (and reaped on
access), so the registry cannot hold a file suppressed across restarts of the
watch.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

#: How long after its own write a file's event is treated as the dashboard's echo.
OWN_WRITE_SUPPRESS_SECONDS = 5.0

#: How many own-written paths are remembered; beyond this the oldest leaves first.
OWN_WRITE_ENTRIES = 512


@dataclass
class _OwnWrite:
    mtime_ns: int
    size: int
    ctime_ns: int
    ino: int
    at: float
    # SHA-256 of the exact bytes we published (atomic-write boundary only). A stat
    # match alone never suppresses when a digest is bound: the candidate file is
    # hashed and must match, so a same-inode, same-size, mtime-restored forgery
    # cannot certify itself as our echo (MIK-R81 rule 3, F07). Entries without a
    # digest keep stat binding.
    content_sha256: str | None = None


@dataclass(frozen=True)
class OwnWriteAnnouncement:
    """One live owner operation, in the registry's existing bounded entries."""

    at: float


@dataclass
class _OwnRemoval:
    at: float
    # The tombstoned path's stat identity at note time, None when already absent.
    # An ancestor tombstone vouches for absent paths below it only while the
    # ancestor itself is untouched: a recreate + foreign create/delete cycle moves
    # the ancestor's kernel-controlled ctime/inode, voiding the voucher (F07).
    identity: tuple[int, int, int, int] | None = None


class OwnWriteRegistry:
    """Which files this process wrote itself, keyed by path with stat identity.

    Identity is (mtime, size, ctime, inode) (F07): an outside replacement that restores
    size and mtime still changes ctime and inode, so only a file nobody else touched
    is suppressed. Identities are captured from the writing file descriptor by the
    owning transaction (see :func:`note_written_identity`), never re-stated after the
    fact, so an outside write between the commit and the note changes the identity and
    wakes the projector instead of being certified as our own echo.
    """

    def __init__(
        self,
        *,
        suppress_seconds: float = OWN_WRITE_SUPPRESS_SECONDS,
        max_entries: int = OWN_WRITE_ENTRIES,
    ) -> None:
        self._suppress_seconds = suppress_seconds
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: dict[str, _OwnWrite | OwnWriteAnnouncement] = {}
        self._removals: dict[str, _OwnRemoval] = {}
        self._digests: dict[str, _OwnWrite] = {}
        self.dropped_entries = 0
        self.dropped_digests = 0

    def note_written_identity(
        self,
        path: str,
        stat: os.stat_result,
        *,
        content_sha256: str | None = None,
        announcement: OwnWriteAnnouncement | None = None,
    ) -> None:
        """Record the writing descriptor's own identity for ``path`` (F07).

        The identity must come from the file descriptor the owner wrote -- ``os.fstat``
        on the writing handle, or the temp file's descriptor after an atomic rename --
        so the entry binds the owner's bytes, not whatever the path holds later. An
        atomic rename captures the descriptor immediately after the rename, including
        the kernel's new ctime. Publication never substitutes a later destination stat.
        """
        with self._lock:
            if announcement is not None and self._entries.get(path) is not announcement:
                return
            self._make_room_locked(path)
            self._entries[path] = _OwnWrite(
                stat.st_mtime_ns,
                stat.st_size,
                stat.st_ctime_ns,
                stat.st_ino,
                time.monotonic(),
                content_sha256,
            )
            if content_sha256 is not None:
                # A rewrite supersedes any evicted proof of older bytes.
                self._digests.pop(path, None)

    def _make_room_locked(self, path: str) -> None:
        if path in self._entries or len(self._entries) < self._max_entries:
            return
        oldest = min(self._entries.items(), key=lambda item: item[1].at)[0]
        evicted = self._entries.pop(oldest)
        self.dropped_entries += 1
        if isinstance(evicted, _OwnWrite) and evicted.content_sha256 is not None:
            self._keep_digest_locked(oldest, evicted)

    @contextmanager
    def announcing(self, path: str) -> Iterator[OwnWriteAnnouncement]:
        """Announce before committing; unfinished and failed operations never suppress."""
        announcement = OwnWriteAnnouncement(time.monotonic())
        with self._lock:
            self._make_room_locked(path)
            self._entries[path] = announcement
            self._digests.pop(path, None)
            self._removals.pop(path, None)
        try:
            yield announcement
        finally:
            with self._lock:
                if self._entries.get(path) is announcement:
                    del self._entries[path]

    def is_pending(self, path: str) -> bool:
        """Whether this path or a removing ancestor has a live owner announcement."""
        with self._lock:
            for _ in range(5):
                entry = self._entries.get(path)
                if isinstance(entry, OwnWriteAnnouncement):
                    if time.monotonic() - entry.at <= self._suppress_seconds:
                        return True
                    del self._entries[path]
                parent = os.path.dirname(path)
                if parent == path:
                    break
                path = parent
        return False

    def _keep_digest_locked(self, path: str, entry: _OwnWrite) -> None:
        """Retain the whole evicted identity and digest under the same bound (F07/F08)."""
        if len(self._digests) >= self._max_entries:
            oldest = min(self._digests.items(), key=lambda item: item[1].at)[0]
            del self._digests[oldest]
            self.dropped_digests += 1
        self._digests[path] = entry

    def note_written(self, path: str) -> None:
        """Record that this process just wrote ``path``; its next event is our echo.

        Fallback for writers without descriptor capture: the identity is stated after
        the write, so prefer :meth:`note_written_identity` wherever the writing
        descriptor is available.
        """
        try:
            stat = os.stat(path)
        except OSError:
            return
        self.note_written_identity(path, stat)

    def note_removed(self, path: str, *, announcement: OwnWriteAnnouncement | None = None) -> None:
        """Record that this process just removed ``path``; its delete event is our echo.

        A deletion leaves no stat identity to compare, so the path alone suppresses its
        event within the window -- but only while the path stays absent (F07): a file
        recreated at the same path inside the window exists again, so it is read as a
        genuine event, not as our echo. A noted directory vouches for absent paths below
        it only while the directory itself is untouched (note-time identity): a foreign
        recreate + create/delete cycle moves its ctime/inode and voids the voucher.
        Stale recreations heal on the next full refresh (MIK-R81 rule 5) only when the
        recreation itself is missed, which it is not.
        """
        try:
            noted = os.stat(path)
            identity = (noted.st_mtime_ns, noted.st_size, noted.st_ctime_ns, noted.st_ino)
        except FileNotFoundError:
            identity = None
        except OSError:
            return
        # A completed removal cannot certify a foreign recreation observed afterward.
        if announcement is not None and identity is not None:
            return
        with self._lock:
            if announcement is not None and self._entries.get(path) is not announcement:
                return
            if len(self._removals) >= self._max_entries:
                oldest = min(self._removals.items(), key=lambda item: item[1].at)[0]
                del self._removals[oldest]
                self.dropped_entries += 1
            self._removals[path] = _OwnRemoval(time.monotonic(), identity)
            self._entries.pop(path, None)
            # A removal supersedes any evicted proof of older bytes.
            self._digests.pop(path, None)

    def _effect_version(self, path: str) -> tuple[object, ...]:
        with self._lock:
            entries: list[object] = [self._digests.get(path)]
            for _ in range(5):
                entries.extend((self._entries.get(path), self._removals.get(path)))
                path = os.path.dirname(path)
            return tuple(entries)

    def is_own_echo(self, path: str, change: str | None = None) -> bool | None:
        """Judge a stable completed effect; None defers an announced or changing one."""
        before = self._effect_version(path)
        if self.is_pending(path):
            return None
        result = self._noted_echo(path, change)
        after = self._effect_version(path)
        if any(old is not new for old, new in zip(before, after, strict=True)):
            return None
        return result

    def _noted_echo(self, path: str, change: str | None) -> bool:
        """Whether ``path``'s current state is still this process's own write.

        ``change`` is the native event kind (``"added"``/``"modified"``/``"deleted"``,
        ``None`` when unknown): arrivals never ride a directory voucher, and a delete
        is never a write echo, so an outside mutation cannot certify itself through
        the wrong effect kind (MIK-R81 rule 3, F07).
        """
        with self._lock:
            matched_removal = self._fresh_removal_locked(path)
            removal = self._removals[matched_removal] if matched_removal is not None else None
        if removal is not None:
            assert matched_removal is not None
            return self._removal_echo(matched_removal, removal, path, change)
        with self._lock:
            if change == "deleted":
                # A delete is never a write echo: only a removal note suppresses one.
                return False
            entry = self._entries.get(path)
            if isinstance(entry, OwnWriteAnnouncement):
                return False
            proven = self._digests.get(path) if entry is None else None
            live = entry if entry is not None else proven
            if live is None:
                return False
            if time.monotonic() - live.at > self._suppress_seconds:
                if entry is not None:
                    del self._entries[path]
                else:
                    del self._digests[path]
                return False
        return _write_echo(live, live.content_sha256, path)

    def _removal_echo(
        self, matched: str, removal: _OwnRemoval, path: str, change: str | None
    ) -> bool:
        """Whether a delete event under fresh tombstone ``matched`` is our echo (F07)."""
        if matched == path:
            return _absent(path)
        if change == "added":
            # An arrival never rides an ancestor voucher: whoever created the path
            # touched the directory after the note, or the event is stale and the
            # post-note refresh already observed the arrival.
            return False
        try:
            os.stat(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        else:
            if change == "modified":
                # A content arrival never rides a directory voucher: modifying a file
                # does not touch its parent, so the voucher cannot vouch for bytes it
                # never observed. Our own modifications carry write entries instead.
                return False
        # Matched an ancestor's tombstone (a prune of a directory above us, which the
        # prune itself usually leaves in place). The voucher holds only while the
        # ancestor is still absent or untouched since the note: a foreign recreate +
        # create/delete cycle moves its kernel-controlled ctime/inode, so the delete
        # below it belongs to that foreign lifecycle, not to our echo.
        return self._ancestor_voucher_holds(matched, removal.identity)

    def _ancestor_voucher_holds(
        self, matched: str, noted: tuple[int, int, int, int] | None
    ) -> bool:
        """Whether the tombstoned ancestor is still absent or untouched (F07)."""
        try:
            current = os.stat(matched)
        except FileNotFoundError:
            # The whole subtree is still gone: our own delete event.
            return True
        except OSError:
            return False
        if noted is None:
            # Absent at note time, present now: someone rebuilt it.
            return False
        return (
            current.st_mtime_ns,
            current.st_size,
            current.st_ctime_ns,
            current.st_ino,
        ) == noted

    def _fresh_removal_locked(self, path: str) -> str | None:
        """The fresh tombstoned path covering ``path`` (itself or an ancestor), if any.

        A pruned lifecycle directory vanishes file by file; the prune records the
        directory, and every path under it reads as our echo. The walk stops four
        levels up, past any watched root. Returns the matched tombstone so callers
        can tell a self tombstone (absent path is our echo) from an ancestor one
        (suppressed only while the ancestor stays absent, F07).
        """
        now = time.monotonic()
        current: str | None = path
        for _ in range(5):
            if current is None:
                return None
            removal = self._removals.get(current)
            if removal is not None:
                if now - removal.at > self._suppress_seconds:
                    del self._removals[current]
                else:
                    return current
            parent = os.path.dirname(current)
            current = parent if parent and parent != current else None
        return None


def _absent(path: str) -> bool:
    """Only ENOENT proves absence; permissions and other I/O faults fail open."""
    try:
        os.stat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _write_echo(entry: _OwnWrite, digest: str | None, path: str) -> bool:
    """Whether the file at ``path`` still carries the noted write identity (F07)."""
    try:
        stat = os.stat(path)
    except OSError:
        # Gone with no removal tombstone: an outside delete, not our echo.
        # Our own removals always note first, so only a foreign delete lands here.
        return False
    if not (
        stat.st_mtime_ns == entry.mtime_ns
        and stat.st_size == entry.size
        and stat.st_ctime_ns == entry.ctime_ns
        and stat.st_ino == entry.ino
    ):
        return False
    return digest is None or _digest_matches(path, digest)


def _digest_matches(path: str, digest: str) -> bool:
    """Whether the file at ``path`` still holds the exact published bytes (F07).

    A digest is bound at the atomic-write boundary; a same-inode, same-size,
    mtime-restored forgery fails here, and an unreadable candidate fails open to
    a genuine wake.
    """
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest() == digest
    except OSError:
        return False


#: The process-wide registry the tick's write sites record into and the watcher
#: filter reads. Module-level (not per-app) because both the projection worker
#: thread and the watcher task of every app in this process share one filesystem
#: identity: a file this process wrote is its own echo whichever app wrote it.
OWN_WRITES = OwnWriteRegistry()


def note_own_write(path: str | os.PathLike[str]) -> None:
    """Record one dashboard-owned file write."""
    OWN_WRITES.note_written(os.fspath(path))


def note_own_write_identity(path: str | os.PathLike[str], stat: os.stat_result) -> None:
    """Record one dashboard-owned file write with its descriptor-captured identity (F07)."""
    OWN_WRITES.note_written_identity(os.fspath(path), stat)


def note_own_removal(path: str | os.PathLike[str]) -> None:
    """Record one dashboard-owned file removal (a prune of its own inputs)."""
    OWN_WRITES.note_removed(os.fspath(path))
