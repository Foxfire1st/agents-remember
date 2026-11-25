"""The ``briefs/`` folder of one memory folder: where briefs lie and how they are written.

* **Where.** ``<memory folder>/briefs/<source path>.md``: the card's path under another top folder.
  Source-brief folders and their parents hold one ``INDEX.md``. ``briefs/TREE`` holds the last
  full writing's memory tree. ``briefs/_task/`` holds task moments, has no index, and a full
  writing preserves it.
* **Derived, never in Git.** A memory writer prepares the ignore rule before publishing briefs.
* **One writer at a time.** Every writing holds the folder's lock file, so two writings of one
  folder never interleave. A file is written only when its bytes change, through a temporary file
  renamed into place, so a reader never sees half a brief.
* **Reclamation.** A full writing replaces the folder's content: the brief of a file that was
  renamed or removed, and an index of a folder left without briefs, go with it. Deleting
  ``briefs/`` loses nothing; the next writing makes it again.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from agents_remember.application.file_briefs.facts import BriefSource
from agents_remember.application.file_briefs.model import FileBrief
from agents_remember.application.file_briefs.render_brief import brief_markdown
from agents_remember.application.file_briefs.render_index import (
    IndexRow,
    index_markdown,
    index_row,
)

BRIEFS: Final = "briefs"
TASK_FOLDER: Final = "_task"
TREE_FILE: Final = "TREE"
INDEX_FILE: Final = "INDEX.md"
_LOCK_FILE: Final = ".lock"


@dataclass(frozen=True)
class WriteReport:
    """What one writing did, in files."""

    written: int
    unchanged: int
    removed: int
    briefs: int
    seconds: float


def brief_location(memory_root: Path, source_path: str) -> Path:
    """Where the brief of ``source_path`` lies in ``memory_root``."""

    return memory_root / BRIEFS / f"{source_path}.md"


def tree_named(file: Path) -> str | None:
    """Read the first-line tree. A missing file raises ``FileNotFoundError``.

    An unreadable or unrecognized header returns ``None``, indicating an unknown tree.
    """

    try:
        with file.open("rb") as handle:
            first = handle.readline()
    except FileNotFoundError:
        raise
    except OSError:
        return None
    match = re.fullmatch(rb"<!-- memory tree ([0-9a-f]{40}|[0-9a-f]{64}) -->\s*", first)
    return match[1].decode("ascii") if match is not None else None


def last_full_tree(memory_root: Path) -> str | None:
    """The tree of the last full writing (``briefs/TREE``), or ``None``."""

    try:
        return (memory_root / BRIEFS / TREE_FILE).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


class BriefFolder:
    """The ``briefs/`` folder of one memory folder."""

    def __init__(self, memory_root: Path) -> None:
        self.memory_root = memory_root
        self.root = memory_root / BRIEFS

    # -- writings ------------------------------------------------------------------------------

    def write_all(self, source: BriefSource) -> WriteReport:
        """Write every brief of the tree, every folder's index and ``TREE``; remove the rest."""

        started = time.monotonic()
        source.measure_every_rule()
        briefs = [one for one in map(source.assemble, source.brief_paths()) if one is not None]
        wanted = {f"{one.head.path}.md": brief_markdown(one) for one in briefs}
        wanted.update(_indexes(briefs))
        wanted[TREE_FILE] = f"{source.memory_tree}\n"
        with self._locked():
            written, unchanged = self._write(wanted)
            removed = self._remove_all_but(set(wanted))
        return WriteReport(written, unchanged, removed, len(briefs), time.monotonic() - started)

    def write_some(self, source: BriefSource, paths: Iterable[str]) -> WriteReport:
        """Write the briefs of ``paths`` again (or remove them) and renew their folders' indexes."""

        started = time.monotonic()
        asked = sorted(set(paths))
        folders = {_folder(one) for one in asked}
        briefs = _briefs_beside(source, asked, folders)
        wanted = {f"{path}.md": brief_markdown(briefs[path]) for path in asked if path in briefs}
        wanted.update(_indexes(briefs.values(), only=folders, subfolders=self._subfolders))
        gone = [f"{path}.md" for path in asked if path not in briefs]
        with self._locked():
            written, unchanged = self._write(wanted)
            removed = sum(_unlink(self.root / one) for one in gone)
        return WriteReport(written, unchanged, removed, len(wanted), time.monotonic() - started)

    # -- internals -----------------------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / _LOCK_FILE).open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _write(self, wanted: Mapping[str, str]) -> tuple[int, int]:
        written = unchanged = 0
        for relative, text in wanted.items():
            target = self.root / relative
            data = text.encode("utf-8")
            try:
                if target.read_bytes() == data:
                    unchanged += 1
                    continue
            except OSError:
                pass
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
            written += 1
        return written, unchanged

    def _remove_all_but(self, kept: set[str]) -> int:
        removed = 0
        for path in sorted(self.root.rglob("*"), key=lambda one: len(one.parts), reverse=True):
            relative = path.relative_to(self.root).as_posix()
            if relative == TASK_FOLDER or relative.startswith(f"{TASK_FOLDER}/"):
                continue
            if relative == _LOCK_FILE:
                continue
            if path.is_dir():
                with contextlib.suppress(OSError):
                    path.rmdir()  # only an emptied folder goes
                continue
            if relative not in kept:
                removed += _unlink(path)
        return removed

    def _subfolders(self, folder: str) -> list[str]:
        directory = self.root / folder if folder else self.root
        try:
            return [
                one.name
                for one in directory.iterdir()
                if one.is_dir() and not (folder == "" and one.name == TASK_FOLDER)
            ]
        except OSError:
            return []


def _briefs_beside(
    source: BriefSource, asked: Sequence[str], folders: set[str]
) -> dict[str, FileBrief]:
    """The briefs of ``asked`` and of every brief path in the same folders, by source path.

    The neighbours are assembled too, because a folder's index lists every brief in it.
    """

    neighbours = [one for one in source.brief_paths() if _folder(one) in folders]
    source.measure_paths({*neighbours, *asked})
    return {
        one.head.path: one
        for one in map(source.assemble, sorted(set(neighbours) | set(asked)))
        if one is not None
    }


def _folder(source_path: str) -> str:
    parent = PurePosixPath(source_path).parent.as_posix()
    return "" if parent == "." else parent


def _indexes(
    briefs: Iterable[FileBrief],
    *,
    only: set[str] | None = None,
    subfolders: Callable[[str], Iterable[str]] | None = None,
) -> dict[str, str]:
    """``INDEX.md`` of every folder holding a brief, and of every folder above one."""

    rows: dict[str, list[IndexRow]] = defaultdict(list)
    children: dict[str, set[str]] = defaultdict(set)
    for brief in briefs:
        folder = _folder(brief.head.path)
        rows[folder].append(index_row(brief))
        parts = folder.split("/") if folder else []
        for depth in range(len(parts)):
            parent = "/".join(parts[:depth])
            children[parent].add(parts[depth])
            rows.setdefault(parent, [])
    found = sorted(set(rows) | set(children))
    if only is not None:
        found = [one for one in found if one in only]
    return {
        (f"{folder}/{INDEX_FILE}" if folder else INDEX_FILE): index_markdown(
            folder,
            rows.get(folder, []),
            children.get(folder, set()) | set(subfolders(folder) if subfolders else ()),
        )
        for folder in found
    }


def _unlink(path: Path) -> int:
    try:
        path.unlink()
    except FileNotFoundError:
        return 0
    return 1


__all__ = [
    "BRIEFS",
    "INDEX_FILE",
    "TASK_FOLDER",
    "TREE_FILE",
    "BriefFolder",
    "WriteReport",
    "brief_location",
    "last_full_tree",
    "tree_named",
]
