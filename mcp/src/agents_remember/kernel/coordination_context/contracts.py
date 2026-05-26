from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.worktrees.worktree_contract import (
    ContractError,
    load_contract,
    task_root_candidates,
)


def resolve_contract(
    contract_path: Path | None,
    coordination_root: Path,
    code_repository_name: str,
    task_name: str | None,
) -> tuple[Any | None, Path | None]:
    candidate = contract_path.resolve() if contract_path else None
    if candidate is None and task_name:
        candidate = find_task_contract(coordination_root, code_repository_name, task_name)
    if candidate is None:
        return None, None
    if not candidate.exists():
        return None, candidate
    try:
        return load_contract(candidate), candidate
    except ContractError:
        return None, candidate


def find_task_contract(
    coordination_root: Path, code_repository_name: str, task_name: str
) -> Path | None:
    for task_root in task_root_candidates(coordination_root, code_repository_name, task_name):
        possible = task_root / "contract.md"
        if possible.exists():
            return possible
    return None
