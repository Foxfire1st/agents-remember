"""Native input generations observed while preparing one task document body."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

type FileIdentity = tuple[int, ...]
_active: ContextVar[BodyGeneration | None] = ContextVar("task_body_generation", default=None)


def _identity(path: Path) -> FileIdentity | None:
    try:
        link = path.lstat()
    except FileNotFoundError:
        return ()
    except OSError:
        return None
    try:
        target = path.stat()
    except FileNotFoundError:
        target = None
    except OSError:
        return None
    return tuple(
        value
        for info in (link, target)
        if info is not None
        for value in (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
    )


@dataclass
class BodyGeneration:
    inputs: dict[Path, FileIdentity | None] = field(default_factory=dict)
    consistent: bool = True

    def current(self) -> bool:
        return self.consistent and all(
            identity is not None and _identity(path) == identity
            for path, identity in self.inputs.items()
        )


@contextmanager
def capture_body_generation() -> Iterator[BodyGeneration]:
    generation = BodyGeneration()
    token = _active.set(generation)
    try:
        yield generation
    finally:
        _active.reset(token)


def capturing_body_generation() -> bool:
    return _active.get() is not None


@contextmanager
def body_input(path: Path) -> Iterator[None]:
    generation = _active.get()
    if generation is None:
        yield
        return
    before = _identity(path)
    if before is None or (path in generation.inputs and generation.inputs[path] != before):
        generation.consistent = False
    generation.inputs[path] = before
    try:
        yield
    finally:
        if _identity(path) != before:
            generation.consistent = False
