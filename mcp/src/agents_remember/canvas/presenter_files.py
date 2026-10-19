"""Descriptor-anchored presenter reads and scans (L104-B22).

The authority directory is opened once. Every subsequent component is opened relative to an
already held descriptor, without following links. Renaming a directory after it was checked
cannot redirect either enumeration or a later file read to a different tree.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import BinaryIO

from agents_remember.kernel.atomic_write import atomic_write_bytes

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class ScanRefused(OSError):
    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(detail)


@contextmanager
def directory_fd(path: Path, *, create: bool = False) -> Iterator[int]:
    """Open an absolute directory, refusing a symlink at any component."""

    absolute = path.absolute()
    if ".." in absolute.parts:
        raise OSError("a descriptor path must not contain '..'")
    descriptor = os.open(absolute.anchor, _DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(part, dir_fd=descriptor)
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def open_at(directory: int, name: str) -> BinaryIO:
    """Open one regular child of a held directory, with no path re-resolution."""

    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise OSError("not one file name")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def open_binary(path: Path) -> BinaryIO:
    with directory_fd(path.parent) as descriptor:
        return open_at(descriptor, path.name)


def descriptor_path(directory: int, name: str) -> Path:
    """A native path tied to a held parent for the shared atomic-write and lock owners."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise OSError("not one file name")
    return Path(f"/proc/{os.getpid()}/fd/{directory}") / name


def write_file(path: Path, payload: bytes) -> None:
    """Publish through the atomic writer while its physical parent remains held."""
    with directory_fd(path.parent) as parent:
        atomic_write_bytes(descriptor_path(parent, path.name), payload)


def create_directory(root: Path, target: Path, *, exist_ok: bool) -> None:
    """Create each component relative to the already admitted physical parent."""
    parts = target.relative_to(root).parts
    if any(part in {".", ".."} for part in parts):
        raise OSError("not a confined directory path")
    with directory_fd(root, create=True) as anchor:
        descriptor = os.dup(anchor)
        try:
            for index, part in enumerate(parts):
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    if index == len(parts) - 1 and not exist_ok:
                        raise
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
        finally:
            os.close(descriptor)


def unlink_file(path: Path) -> None:
    """Unlink only a child of a held parent, without resolving an ancestor again."""
    with directory_fd(path.parent) as parent, suppress(FileNotFoundError):
        os.unlink(path.name, dir_fd=parent)


def remove_directory(path: Path) -> None:
    """Recursively reclaim a child through Python's descriptor-based, link-safe remover."""
    with directory_fd(path.parent) as parent:
        shutil.rmtree(path.name, dir_fd=parent)


def write_output(root: Path, relative: str, payload: bytes) -> None:
    """Atomically replace an output through held directories; create no links or special files."""

    parts = relative.split("/")
    if not parts or any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise OSError("not a plain relative output path")
    with directory_fd(root) as anchor:
        descriptor = os.dup(anchor)
        try:
            for part in parts[:-1]:
                with suppress(FileExistsError):
                    os.mkdir(part, dir_fd=descriptor)
                child = os.open(part, _DIRECTORY, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            # Keep the package's atomic/fsync owner; its lexical parent is this held directory.
            # It cannot be redirected by a rename/symlink of the original ancestor path.
            anchored = descriptor_path(descriptor, parts[-1])
            atomic_write_bytes(anchored, payload)
        finally:
            os.close(descriptor)


def ensure_child(root: Path, name: str) -> None:
    """Create/validate a single child through a held parent, never a path-following mkdir."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise OSError("not one directory name")
    with directory_fd(root) as parent:
        with suppress(FileExistsError):
            os.mkdir(name, dir_fd=parent)
        child = os.open(name, _DIRECTORY, dir_fd=parent)
        os.close(child)


def scan_files(
    descriptor: int, *, depth_limit: int, entry_limit: int
) -> Iterator[tuple[str, int, str, int]]:
    """Yield (relative path, parent descriptor, name, size) while the parent remains open.

    The consumer must read a yielded child before advancing this iterator. Entries are sized with
    no following of links; the read independently checks the opened file is regular.
    """

    seen = 0

    def descend(parent: int, prefix: str, depth: int):
        nonlocal seen
        if depth > depth_limit:
            raise ScanRefused("assets-too-deep", f"directory depth exceeds {depth_limit}")
        with os.scandir(parent) as entries:
            for entry in entries:
                seen += 1
                if seen > entry_limit:
                    raise ScanRefused("too-many-entries", f"entry count exceeds {entry_limit}")
                relative = f"{prefix}{entry.name}"
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    child = os.open(entry.name, _DIRECTORY, dir_fd=parent)
                    try:
                        yield from descend(child, f"{relative}/", depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(metadata.st_mode):
                    yield relative, parent, entry.name, metadata.st_size
                else:
                    raise ScanRefused(
                        "symlinked-asset", f"non-regular entry or symlink: {relative}"
                    )

    yield from descend(descriptor, "", 0)
