"""Shared low-level git command runner for kernel modules."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_REPOSITORY_SELECTOR_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)


def git_environment() -> dict[str, str]:
    """Return the ambient environment without Git repository selectors."""

    environment = os.environ.copy()
    for name in GIT_REPOSITORY_SELECTOR_ENV:
        environment.pop(name, None)
    return environment


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=git_environment(),
        timeout=5,
        check=False,
    )
