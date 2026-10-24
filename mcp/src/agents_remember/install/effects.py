"""Call-local observations of installer mutations, including an interrupted operation."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_EFFECTS: ContextVar[list[dict[str, Any]] | None] = ContextVar("install_effects", default=None)


@contextmanager
def capture_install_effects() -> Iterator[list[dict[str, Any]]]:
    """Observe only this call; ordinary installs keep their existing result and behavior."""

    effects: list[dict[str, Any]] = []
    token = _EFFECTS.set(effects)
    try:
        yield effects
    finally:
        _EFFECTS.reset(token)


def _state(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return {"kind": "symlink", "target": str(path.readlink())}
        if stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            return {"kind": "file", "sha256": digest, "mode": stat.S_IMODE(info.st_mode)}
        return {"kind": "directory" if stat.S_ISDIR(info.st_mode) else "other"}
    except FileNotFoundError:
        return {"kind": "absent"}
    except OSError as error:
        return {"kind": "unreadable", "detail": str(error)}


def _before_states(path: Path, action: str) -> dict[Path, dict[str, Any]]:
    before = {path: _state(path)}
    if action == "remove" and before[path]["kind"] == "directory":
        before.update((child, _state(child)) for child in path.rglob("*"))
    if action == "copy":
        # copy_file delegates parent creation to the observed ensure_dir operation.
        return before
    parent = path.parent
    while parent != parent.parent:
        state = _state(parent)
        if state["kind"] != "absent":
            break
        before[parent] = state
        parent = parent.parent
    return before


@contextmanager
def observe_install_effect(path: Path, action: str, dry_run: bool) -> Iterator[None]:
    """Name changed entries after success or failure without masking the operation's error.

    Missing ancestors are included because mkdir(parents=True) can create more than one
    directory. An interrupted copy is observed after its error, so bytes written before
    a failing copy/stat operation are still part of the refusal's account.
    """

    effects = _EFFECTS.get()
    if effects is None or dry_run:
        yield
        return
    before = _before_states(path, action)
    completed = False
    try:
        yield
        completed = True
    finally:
        for entry, prior in reversed(before.items()):
            current = _state(entry)
            if current != prior:
                effects.append(
                    {
                        "path": entry.as_posix(),
                        "action": action if entry == path or action == "remove" else "directory",
                        "completed": completed,
                        "before": prior,
                        "after": current,
                    }
                )
