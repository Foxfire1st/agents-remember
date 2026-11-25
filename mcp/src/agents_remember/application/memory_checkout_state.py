"""The Git state of a memory checkout a read was answered from: its commit and whether it is dirty.

Both are best effort: a Git call that fails or hangs past the bound answers "unknown" (no commit,
not dirty) rather than failing the read that asked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_SECONDS = 20


def memory_checkout_dirty(memory_root: Path) -> bool:
    """Whether the memory checkout holds uncommitted knowledge changes.

    Tracked modifications and untracked files both count: an untracked
    record file is a change the index has not seen, so it dirties the
    checkout like any other uncommitted change.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", str(memory_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


def memory_checkout_commit(memory_root: Path) -> str | None:
    """The commit the memory checkout was read at, best effort."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(memory_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else None
