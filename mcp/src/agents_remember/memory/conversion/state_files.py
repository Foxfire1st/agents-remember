"""The files the state conversion reads from and writes into one memory working tree.

It reads record files and history under the memory root and writes the changes its plan names.
An optional report file is written first. Every target must still hold the bytes the plan was
computed from. A failed write restores reached memory files and, when that restoration is complete,
the previous report. A target is registered before its write starts, because the atomic write
replaces the file before syncing the directory. Restoration verifies the original bytes or absence
by reading them back. :class:`PartialStateWrite` names any target still changed after restoration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import AgentsRememberError, StateConversionError
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    LAYOUT_MARKER_PATH,
    RECORD_DIRECTORIES,
)

_READ_DIRECTORIES = (*RECORD_DIRECTORIES.values(), "history")


def _knowledge_directories(memory_root: Path) -> tuple[Path, ...]:
    return tuple(memory_root / KNOWLEDGE_ROOT / name for name in _READ_DIRECTORIES)


def selects_knowledge_path(memory_root: Path, path: Path) -> bool:
    """Whether a file at this path would be read as a record or history document."""

    parent = path.parent.resolve()
    return path.match("*.json") and any(
        directory.resolve() == parent for directory in _knowledge_directories(memory_root)
    )


def read_knowledge_json(memory_root: Path) -> dict[str, bytes]:
    """The bytes of every record and history file under ``memory_root``, by relative path."""

    if not (memory_root / LAYOUT_MARKER_PATH).is_file():
        raise StateConversionError(
            f"{memory_root} holds no {LAYOUT_MARKER_PATH}: it is no converted memory tree; point "
            "--memory-root at the working tree of a memory repository in the text format"
        )
    files: dict[str, bytes] = {}
    for directory in _knowledge_directories(memory_root):
        for path in sorted(directory.glob("*.json")):
            if path.is_file():
                files[path.relative_to(memory_root).as_posix()] = path.read_bytes()
    return files


def _current(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


@dataclass(frozen=True)
class ConversionReportFile:
    path: Path
    payload: bytes


@dataclass(frozen=True)
class _ConversionWrite:
    path: Path
    name: str
    payload: bytes
    original: bytes | None


class PartialStateWrite(AgentsRememberError):
    """A failed write whose restoring left files changed; ``remainder`` names them.

    No :class:`StateConversionError`: that one promises that nothing was written.
    """

    def __init__(self, cause: BaseException, remainder: list[str]) -> None:
        self.remainder = remainder
        super().__init__(
            f"the write failed ({cause}) and {len(remainder)} file(s) could not be restored: "
            f"{', '.join(remainder)}; they hold other bytes than before the run, every other "
            "file was restored. Run the command again once the cause is removed: it converts "
            "what is left and keeps what is already converted"
        )


def write_all_or_nothing(
    memory_root: Path,
    changes: Mapping[str, bytes],
    originals: Mapping[str, bytes | None],
    *,
    report: ConversionReportFile | None = None,
) -> None:
    """Publish the report before the memory files and restore all reached targets on failure."""

    writes = [
        _ConversionWrite(memory_root / name, name, data, originals.get(name))
        for name, data in sorted(changes.items())
    ]
    report_write = None
    if report is not None:
        report_write = _ConversionWrite(
            report.path, str(report.path), report.payload, _current(report.path)
        )
        writes.insert(0, report_write)
    stale = sorted(write.name for write in writes if _current(write.path) != write.original)
    if stale:
        raise StateConversionError(
            f"{len(stale)} file(s) changed after the plan was read, first {stale[0]}; nothing "
            "was written: run the command again"
        )
    _commit_writes(writes, report_write)


def _commit_writes(writes: Sequence[_ConversionWrite], report: _ConversionWrite | None) -> None:
    reached: list[_ConversionWrite] = []
    try:
        for write in writes:
            reached.append(write)
            atomic_write_bytes(write.path, write.payload)
    except BaseException as error:
        remainder = _restore(reached[1:] if report is not None else reached)
        if not remainder and report is not None:
            remainder = _restore([report])
        if remainder:
            raise PartialStateWrite(error, remainder) from error
        if not isinstance(error, Exception):
            raise
        raise StateConversionError(
            f"writing {reached[-1].name} failed ({error}); every file the run reached was restored "
            "to its bytes or its absence before the run, so nothing changed"
        ) from error


def _restore(reached: Sequence[_ConversionWrite]) -> list[str]:
    """Put back the original of every reached target; the targets that still differ."""

    for write in reversed(reached):
        target, original = write.path, write.original
        try:
            if _current(target) == original:
                continue
            if original is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write_bytes(target, original)
        except Exception:  # the read-back below names what stayed changed
            continue
    remainder = []
    for write in reached:
        try:
            same = _current(write.path) == write.original
        except OSError:
            same = False
        if not same:
            remainder.append(write.name)
    return sorted(remainder)
