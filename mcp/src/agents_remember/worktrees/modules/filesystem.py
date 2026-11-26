"""Filesystem helpers for worktree lifecycle operations."""

from __future__ import annotations

import shutil
from pathlib import Path


def remove_tree(path: Path, *, dry_run: bool) -> dict[str, object]:
    """Remove one directory tree when present, or report what would be removed."""
    if not path.exists():
        return {"path": path.as_posix(), "removed": False, "reason": "already-absent"}
    if dry_run:
        return {"path": path.as_posix(), "removed": False, "would_remove": True}
    try:
        shutil.rmtree(path)
    except PermissionError as error:
        return {
            "path": path.as_posix(),
            "removed": False,
            "reason": f"permission denied: {error}",
        }
    except OSError as error:
        return {
            "path": path.as_posix(),
            "removed": False,
            "reason": str(error),
        }
    return {"path": path.as_posix(), "removed": True}
