"""Preserve files in a real workspace messages directory before retirement."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from io import BufferedReader
from itertools import count
from pathlib import Path

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.atomic_write import _fsync_directory
from agents_remember.worktrees.role_reports import MESSAGES_LINK, canonical_group_messages
from agents_remember.worktrees.worktree_contract import WorktreeContract


class GroupMessageCollectionError(AgentsRememberError):
    """A workspace message could not be preserved in its canonical record."""


def collect_group_messages(contract: WorktreeContract, *, dry_run: bool) -> None:
    source = contract.worktree_group / MESSAGES_LINK
    try:
        if not stat.S_ISDIR(_path_mode(source)):
            return
        destination = canonical_group_messages(contract)
        _check_destination(destination)
        for file in _message_files(source):
            _preserve_message(file, file.relative_to(source), destination, dry_run=dry_run)
    except (OSError, ValueError) as error:
        raise GroupMessageCollectionError(
            f"Could not preserve messages from {source}: {error}. "
            "Reconcile the named path before repeating cleanup or abandon."
        ) from error


def _path_mode(path: Path) -> int:
    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return 0


def _check_destination(directory: Path) -> None:
    for path in reversed((directory, *directory.parents)):
        mode = _path_mode(path)
        if mode and not stat.S_ISDIR(mode):
            raise GroupMessageCollectionError(
                f"Canonical message directory {path} is not a real directory"
            )


def _message_files(directory: Path) -> Iterator[Path]:
    pending = [directory]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield path


def _open_regular(path: Path) -> BufferedReader:
    _check_destination(path.parent)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise GroupMessageCollectionError(f"Workspace message {path} is no longer a regular file")
    stream = path.open("rb")
    if not os.path.samestat(before, os.fstat(stream.fileno())):
        stream.close()
        raise GroupMessageCollectionError(f"Workspace message {path} changed before it was opened")
    return stream


def _digest(path: Path) -> str:
    with _open_regular(path) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _message_destinations(relative: Path, directory: Path, digest: str) -> Iterator[Path]:
    preferred = directory / relative
    _check_destination(preferred.parent)
    yield preferred
    # The source pathname is part of the identity so colliding basenames from different
    # old subfolders can both be found and recovered at the same address on a retry.
    identity = hashlib.sha256(os.fsencode(relative)).hexdigest()[:16]
    stem = f"recovered-{identity}-{digest}"
    for number in count():
        version = f"-{number}" if number else ""
        yield preferred.with_name(f"{stem}{version}{relative.suffix[:16]}")


def _same_message(target: Path, digest: str) -> bool:
    return stat.S_ISREG(_path_mode(target)) and _digest(target) == digest


def _preserve_message(source: Path, relative: Path, directory: Path, *, dry_run: bool) -> None:
    digest = _digest(source)
    destinations = _message_destinations(relative, directory, digest)
    for target in destinations:
        if _same_message(target, digest):
            return
        if _path_mode(target):
            continue
        existing_parent = next(parent for parent in target.parents if parent.exists())
        if not os.access(existing_parent, os.W_OK | os.X_OK):
            raise GroupMessageCollectionError(
                f"Canonical message directory {existing_parent} cannot be written"
            )
        if dry_run:
            return
        if _place_message(source, target, digest):
            return


def _place_message(source: Path, target: Path, digest: str) -> bool:
    _check_destination(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _check_destination(target.parent)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".recovered-message-", delete=False
    ) as stream:
        staged = Path(stream.name)
    try:
        with _open_regular(source) as origin, staged.open("wb") as stream:
            shutil.copyfileobj(origin, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if _digest(staged) != digest:
            raise GroupMessageCollectionError(
                f"Workspace message {source} changed during collection"
            )
        _check_destination(target.parent)
        try:
            os.link(staged, target)
        except FileExistsError:
            return _same_message(target, digest)
        _fsync_directory(target.parent)
        return True
    finally:
        staged.unlink(missing_ok=True)
