"""Git interaction helpers and source change notes for drift detection."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git


def current_branch_name(repo_root: Path) -> str:
    branch = run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0:
        return "unknown-branch"
    return branch.stdout.strip() or "unknown-branch"
