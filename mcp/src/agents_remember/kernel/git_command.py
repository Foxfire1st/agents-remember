"""Shared low-level git command runner for kernel modules."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=5,
        check=False,
    )
