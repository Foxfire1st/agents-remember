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
target. Currentness checks these rows first, before following a possibly retargeted byte locator.

A path recorded twice with different identities in one computation (the file changed while it was
being read) is marked :data:`CONFLICTING`, so the caller knows the result cannot be reused.
:func:`changed_observations` rechecks the same operations and bytes now. Outside a recording
block, :func:`record_read` does nothing.
"""

from __future__ import annotations

import hashlib
import re
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
_FILE_IDENTITY = re.compile(r"sha256:[0-9a-f]{64}|absent")
_FAILED_IDENTITY = re.compile(r"conflicting reads|unreadable \([A-Za-z0-9_]+\)")


def bytes_identity(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_identity(path: Path) -> str:
    """The identity of a file now: its bytes' SHA-256, ``absent``, or ``unreadable (...)``."""

    try:
        return bytes_identity(path.read_bytes())
    except FileNotFoundError:
        return ABSENT
    except OSError as error:
        return f"unreadable ({type(error).__name__})"


@contextmanager
def recorded_reads() -> Iterator[dict[str, str]]:
    """Record every read inside the block as ``{path: identity}``."""

    reads: dict[str, str] = {}
    token = _READS.set(reads)
    try:
        yield reads
    finally:
        _READS.reset(token)


def record_read(path: Path, identity: str | None = None) -> None:
    """Record one read of ``path`` (its identity now, unless the reader passes what it read)."""

    if _READS.get() is None:
        return
    key = path.as_posix()
    seen = file_identity(path) if identity is None else identity
    _record(key, seen)


def _record(key: str, seen: str) -> None:
    reads = _READS.get()
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
    if kind in ("resolve", "exists", "json-files"):
        if not logical or not Path(logical).is_absolute():
            return False
        if _FAILED_IDENTITY.fullmatch(identity):
            return True
        if kind in ("exists", "json-files"):
            return (
                identity in ("present", ABSENT)
                if kind == "exists"
                else bool(_FILE_IDENTITY.fullmatch(identity)) and identity != ABSENT
            )
        target = identity.removeprefix("resolved:")
        return identity.startswith("resolved:") and Path(target).is_absolute()
    return bool(key) and bool(
        _FILE_IDENTITY.fullmatch(identity) or _FAILED_IDENTITY.fullmatch(identity)
    )


def observation_identity(key: str) -> str:
    """Recheck the same observed bytes or path operation, without reading a selector's target."""

    kind, _, logical = key.partition(":")
    if kind not in ("resolve", "exists", "json-files"):
        return file_identity(Path(key))
    try:
        path = Path(logical)
        if kind == "resolve":
            return f"resolved:{path.resolve().as_posix()}"
        if kind == "json-files":
            return _json_files_identity(tuple(sorted(path.glob("*.json"))))
        return "present" if path.exists() else ABSENT
    except (OSError, RuntimeError) as error:
        return f"unreadable ({type(error).__name__})"


def changed_observations(reads: Mapping[str, str]) -> tuple[str, ...]:
    """Recheck selections before bytes; a moved locator must not cause an outside-target read."""

    selections = {key for key in reads if key.startswith(("resolve:", "exists:", "json-files:"))}
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
