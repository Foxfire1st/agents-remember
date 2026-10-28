"""Exact work-branch ref inputs for the existing projection watch plan.

L201 F8 / 8ce6b7fc: a landing moves a branch, and that input offers preparation
immediately. These are event inputs, never additions to a task-document digest.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.tasks.task_paths import SERIES_CONTRACT_FILENAME
from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.worktree_contract import ContractError, load_contract


@dataclass(frozen=True)
class MasterSeriesInput:
    repo_id: str
    master: str
    ref_paths: tuple[Path, ...]


def work_branch_ref_paths(repository: Path, branch: str) -> tuple[Path, ...]:
    """Let Git resolve shared refs for ordinary and linked worktrees alike."""
    if not branch:
        return ()
    ref = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    paths = require_git(
        repository,
        [
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            ref,
            "--git-path",
            "packed-refs",
        ],
    ).splitlines()
    if len(paths) != 2:
        raise OSError(f"{repository}: Git did not name the loose and packed work-branch inputs")
    return tuple(sorted({Path(path) for path in paths}))


def series_input_plan(task_folders: Iterable[Path]) -> tuple[MasterSeriesInput, ...]:
    """Use canonical root contracts, just as the master endpoint selection does.

    Missing or malformed contracts are not master selections. Other failures remain
    explicit; a watcher must not claim a complete registration without its inputs.
    Call off-loop alongside task and receipt discovery.
    """
    found: list[MasterSeriesInput] = []
    for folder in sorted(task_folders):
        try:
            contract = load_contract(folder / SERIES_CONTRACT_FILENAME)
        except (FileNotFoundError, ContractError):
            continue
        paths: set[Path] = set()
        for repository, branch in (
            (contract.code_repo_path, contract.code_work_branch),
            (contract.memory_repo_path, contract.memory_work_branch),
        ):
            if repository is not None:
                paths.update(work_branch_ref_paths(repository, branch))
        found.append(MasterSeriesInput(folder.parent.name, folder.name, tuple(sorted(paths))))
    return tuple(found)
