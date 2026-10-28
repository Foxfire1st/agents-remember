"""Record the files a computation read outside any Git tree, with their exact identities.

A result computed from Git trees is a function of their content-addressed IDs, so a caller may
reuse it for the same IDs. A computation that also reads plain files -- a task's requirement
manifest, coordination settings -- can only be reused while those files are unchanged. Inside
:func:`recorded_reads` every reader that calls :func:`record_read` adds ``{path: identity}``:

* ``sha256:<hex>`` of the exact bytes read;
* ``absent`` when the file does not exist;
* ``unreadable (<error>)`` when it exists but cannot be read.

Actual locator resolutions and root predicates use separate ``resolve:<absolute logical path>``
and ``exists:<absolute logical path>`` rows. They describe selection, not bytes of an unconsumed
target. A directory listing a computation walked is an ``entries:<absolute path>`` row: every
direct name with its kind (file, directory, link, other), or ``absent``. Currentness checks these
rows first, before following a possibly retargeted byte locator.

A path recorded twice with different identities in one computation (the file changed while it was
being read) is marked :data:`CONFLICTING`, so the caller knows the result cannot be reused.
:func:`changed_observations` rechecks the same operations and bytes now. Outside a recording
block, :func:`record_read` does nothing.

**A file's bytes are read again only when its stat moved** (:func:`file_identity`, D569): the same
device, inode, size, mode, modification and change times prove the same bytes, as Git's index
proves an unchanged file. A file written less than :data:`RACY_NS` before its bytes were read is
never held -- a write within the same clock tick could leave its stat as it was -- and neither is
one whose stat moved while it was read. At most :data:`HELD_IDENTITIES` files are held; the least
recently used is read again.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Final

__all__ = [
    "ABSENT",
    "CONFLICTING",
    "bytes_identity",
    "changed_observations",
    "file_identity",
    "has_failed_observation",
    "observation_identity",
    "observed_entries",
    "observed_exists",
    "observed_json_files",
    "observed_path_exists",
    "observed_resolve",
    "observed_text",
    "record_read",
    "recorded_reads",
    "replay_reads",
    "valid_observation",
]

ABSENT: Final = "absent"
CONFLICTING: Final = "conflicting reads"
_READS: ContextVar[dict[str, str] | None] = ContextVar("recorded_reads", default=None)
_EXAMINED: ContextVar[dict[str, str] | None] = ContextVar("examined_reads", default=None)
_FILE_IDENTITY = re.compile(r"sha256:[0-9a-f]{64}|absent")
_FAILED_IDENTITY = re.compile(r"conflicting reads|unreadable \([A-Za-z0-9_]+\)")
#: The rows that describe a selection -- a resolution, a predicate, a listing -- not bytes.
_SELECTIONS: Final = frozenset({"resolve", "exists", "json-files", "entries"})


def bytes_identity(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


#: A write this close before a read could share its stat with the bytes read (Git's racy rule).
RACY_NS: Final = 2_000_000_000
#: How many files' identities are held against their stats.
HELD_IDENTITIES: Final = 8192

type _Stat = tuple[int, int, int, int, int, int]

_HELD: OrderedDict[str, tuple[_Stat, str]] = OrderedDict()
_HELD_GUARD = threading.Lock()


def _stat_of(path: Path) -> _Stat:
    info = os.stat(path)
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def file_identity(path: Path) -> str:
    """The identity of a file now: its bytes' SHA-256, ``absent``, or ``unreadable (...)``.

    The bytes are read again only when the file's stat moved since they were last read.
    """

    try:
        stat = _stat_of(path)
    except FileNotFoundError:
        return ABSENT
    except OSError as error:
        return f"unreadable ({type(error).__name__})"
    key = os.path.abspath(path)
    with _HELD_GUARD:
        held = _HELD.get(key)
        if held is not None and held[0] == stat:
            _HELD.move_to_end(key)
            return held[1]
    read_at = time.time_ns()
    try:
        identity = bytes_identity(path.read_bytes())
        settled = read_at - max(stat[4], stat[5]) > RACY_NS and _stat_of(path) == stat
    except FileNotFoundError:
        return ABSENT
    except OSError as error:
        return f"unreadable ({type(error).__name__})"
    if settled:
        with _HELD_GUARD:
            _HELD[key] = (stat, identity)
            _HELD.move_to_end(key)
            while len(_HELD) > HELD_IDENTITIES:
                _HELD.popitem(last=False)
    return identity


@contextmanager
def recorded_reads(*, examined: dict[str, str] | None = None) -> Iterator[dict[str, str]]:
    """Record dependencies; optionally collect examined, non-dependent lookup observations.

    Only strict_leaf_doc may mark ruled-out siblings examined. That caller-owned audit
    bucket is inherited by nested recorders and never exported as a kept read set.
    """

    reads: dict[str, str] = {}
    token = _READS.set(reads)
    examined_token = _EXAMINED.set(_EXAMINED.get() if examined is None else examined)
    try:
        yield reads
    finally:
        _READS.reset(token)
        _EXAMINED.reset(examined_token)


def record_read(path: Path, identity: str | None = None, *, examined: bool = False) -> None:
    """Record one read of ``path`` (its identity now, unless the reader passes what it read)."""

    if (_EXAMINED.get() if examined else _READS.get()) is None:
        return
    key = path.as_posix()
    seen = file_identity(path) if identity is None else identity
    _record(key, seen, examined=examined)


def _record(key: str, seen: str, *, examined: bool = False) -> None:
    reads = _EXAMINED.get() if examined else _READS.get()
    if reads is None:
        return
    previous = reads.get(key)
    reads[key] = seen if previous is None or previous == seen else CONFLICTING


def replay_reads(reads: Mapping[str, str]) -> None:
    """Import another process's consumed-byte observations through the same conflict owner."""

    for path, identity in reads.items():
        _record(path, identity)


def valid_observation(key: str, identity: str) -> bool:
    """Validate byte rows and witnessed path operations."""

    if "\0" in key or "\0" in identity:
        return False
    kind, _, logical = key.partition(":")
    if kind in _SELECTIONS:
        if not logical or not Path(logical).is_absolute():
            return False
        return bool(_FAILED_IDENTITY.fullmatch(identity)) or _valid_selection(kind, identity)
    return bool(key) and bool(
        _FILE_IDENTITY.fullmatch(identity) or _FAILED_IDENTITY.fullmatch(identity)
    )


def _valid_selection(kind: str, identity: str) -> bool:
    if kind == "entries":
        return bool(_FILE_IDENTITY.fullmatch(identity))
    if kind == "exists":
        return identity in ("present", ABSENT)
    if kind == "json-files":
        return bool(_FILE_IDENTITY.fullmatch(identity)) and identity != ABSENT
    target = identity.removeprefix("resolved:")
    return identity.startswith("resolved:") and Path(target).is_absolute()


def observation_identity(key: str) -> str:
    """Recheck the same observed bytes or path operation, without reading a selector's target."""

    kind, _, logical = key.partition(":")
    if kind not in _SELECTIONS:
        return file_identity(Path(key))
    try:
        return _selection_identity(kind, Path(logical))
    except FileNotFoundError:
        return ABSENT if kind == "entries" else f"unreadable ({FileNotFoundError.__name__})"
    except (OSError, RuntimeError) as error:
        return f"unreadable ({type(error).__name__})"


def _selection_identity(kind: str, path: Path) -> str:
    if kind == "entries":
        return _entries_identity(_entries_of(path))
    if kind == "resolve":
        return f"resolved:{path.resolve().as_posix()}"
    if kind == "json-files":
        return _json_files_identity(tuple(sorted(path.glob("*.json"))))
    return "present" if path.exists() else ABSENT


def changed_observations(reads: Mapping[str, str]) -> tuple[str, ...]:
    """Recheck selections before bytes; a moved locator must not cause an outside-target read."""

    selections = {key for key in reads if key.partition(":")[0] in _SELECTIONS}
    moved = tuple(key for key in selections if observation_identity(key) != reads[key])
    if moved:
        return moved
    return tuple(
        key
        for key, seen in reads.items()
        if key not in selections and observation_identity(key) != seen
    )


def has_failed_observation(reads: Mapping[str, str]) -> bool:
    """A caught input failure or conflicting actual reads must never occupy a memo slot."""

    return any(seen == CONFLICTING or seen.startswith("unreadable (") for seen in reads.values())


def observed_resolve(path: Path) -> Path:
    """Observe the existing non-strict resolution, retaining its absolute logical locator."""

    key = f"resolve:{path.absolute().as_posix()}"
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as error:
        _record(key, f"unreadable ({type(error).__name__})")
        raise
    _record(key, f"resolved:{resolved.as_posix()}")
    return resolved


def observed_path_exists(path: Path) -> bool:
    """Observe an actual root predicate, including a present directory, without hashing it."""

    key = f"exists:{path.absolute().as_posix()}"
    try:
        exists = path.exists()
    except OSError as error:
        _record(key, f"unreadable ({type(error).__name__})")
        raise
    _record(key, "present" if exists else ABSENT)
    return exists


def observed_exists(path: Path) -> bool:
    """Observe a selector's missing input at its actual existence probe, without a second read."""

    try:
        exists = path.exists()
    except OSError as error:
        record_read(path, f"unreadable ({type(error).__name__})")
        raise
    if not exists:
        # A false existence probe is not a failed byte read (e.g. a parent is a file).
        # Recheck the predicate we actually used, including the errors exists() suppresses.
        _record(f"exists:{path.absolute().as_posix()}", ABSENT)
    return exists


def _entries_of(root: Path) -> tuple[tuple[str, str], ...]:
    with os.scandir(root) as listing:
        return tuple(
            sorted(
                (
                    entry.name,
                    "link"
                    if entry.is_symlink()
                    else "directory"
                    if entry.is_dir()
                    else "file"
                    if entry.is_file()
                    else "other",
                )
                for entry in listing
            )
        )


def _entries_identity(entries: tuple[tuple[str, str], ...]) -> str:
    return bytes_identity("\0".join(f"{kind}:{name}" for name, kind in entries).encode("utf-8"))


def observed_entries(root: Path) -> tuple[tuple[str, str], ...]:
    """Record one directory's direct listing a computation walks: each name with its kind.

    An absent directory is an empty listing recorded as ``absent``, so its creation moves it.
    """

    root = root.absolute()
    key = f"entries:{root.as_posix()}"
    try:
        entries = _entries_of(root)
    except FileNotFoundError:
        _record(key, ABSENT)
        return ()
    except OSError as error:
        _record(key, f"unreadable ({type(error).__name__})")
        raise
    _record(key, _entries_identity(entries))
    return entries


def _json_files_identity(paths: tuple[Path, ...]) -> str:
    return bytes_identity("\0".join(path.as_posix() for path in paths).encode("utf-8"))


def observed_json_files(root: Path) -> tuple[Path, ...]:
    """Record the exact direct JSON-file listing a task lookup consumes, including an empty one."""

    root = root.absolute()
    key = f"json-files:{root.as_posix()}"
    try:
        paths = tuple(sorted(root.glob("*.json")))
    except OSError as error:
        _record(key, f"unreadable ({type(error).__name__})")
        raise
    _record(key, _json_files_identity(paths))
    return paths


def observed_text(path: Path) -> str:
    """Read UTF-8/universal-newline text once, recording the exact raw bytes or IO failure."""

    try:
        data = path.read_bytes()
    except FileNotFoundError:
        record_read(path, ABSENT)
        raise
    except OSError as error:
        record_read(path, f"unreadable ({type(error).__name__})")
        raise
    record_read(path, bytes_identity(data))
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
