"""Establish the memory base recorded in a leaf's worktree contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.errors import RecordedMemoryBaseError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = ["RecordedMemoryBaseError", "recorded_memory_base"]


def recorded_memory_base(contract: WorktreeContract, *, memory_commit: str | None = None) -> str:
    """Read the recorded base and verify ancestry against the judged commit or worktree HEAD."""

    recorded = contract.memory_base_commit
    if not recorded:
        raise RecordedMemoryBaseError("the leaf contract has no recorded memory.base_commit")
    worktree = contract.memory_worktree
    repository = contract.memory_repo_path or worktree
    if repository is None:
        raise RecordedMemoryBaseError(f"no memory repository can read the recorded base {recorded}")

    commit = _read_revision(repository, f"{recorded}^{{commit}}", "recorded memory base")
    _read_revision(repository, f"{commit}^{{tree}}", "tree of the recorded memory base")
    if memory_commit is None:
        if worktree is None:
            raise RecordedMemoryBaseError("the leaf contract has no memory worktree to read HEAD")
        memory_commit = _read_revision(worktree, "HEAD^{commit}", "memory worktree HEAD")

    result = _git(repository, ["merge-base", "--is-ancestor", commit, memory_commit])
    if result.returncode == 1:
        raise RecordedMemoryBaseError(
            f"the recorded memory base {recorded} is not an ancestor of "
            f"the memory candidate commit {memory_commit}"
        )
    if result.returncode:
        raise RecordedMemoryBaseError(
            f"cannot check ancestry of the recorded memory base {recorded} against "
            f"the memory candidate commit {memory_commit}: {result.stderr.strip()}"
        )
    return commit


def _read_revision(repository: Path, revision: str, label: str) -> str:
    result = _git(repository, ["rev-parse", "--verify", "--quiet", revision])
    value = result.stdout.strip()
    if result.returncode or not value:
        raise RecordedMemoryBaseError(f"cannot read the {label} {revision!r} in {repository}")
    return value


def _git(repository: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return run_git(
            repository,
            ["--no-replace-objects", *args],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RecordedMemoryBaseError(f"cannot read the recorded memory base: {error}") from error
