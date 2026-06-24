from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.worktrees.task_resolver import (
    resolve_active_task_root,
    resolve_leaf_enclosure_contract,
    series_contract_path,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    load_contract,
)


def resolve_contract(
    contract_path: Path | None,
    coordination_root: Path,
    code_repository_name: str,
    task_name: str | None,
    parent_task: str | None = None,
    leaf_id: str | None = None,
) -> tuple[Any | None, Path | None]:
    candidate = contract_path.resolve() if contract_path else None
    if candidate is None and task_name:
        candidate = find_task_contract(
            coordination_root,
            code_repository_name,
            task_name,
            parent_task=parent_task,
            leaf_id=leaf_id,
        )
    if candidate is None:
        return None, None
    if not candidate.exists():
        return None, candidate
    try:
        return load_contract(candidate), candidate
    except ContractError:
        return None, candidate


def find_task_contract(
    coordination_root: Path,
    code_repository_name: str,
    task_name: str,
    *,
    parent_task: str | None = None,
    leaf_id: str | None = None,
) -> Path | None:
    if leaf_id:
        return resolve_leaf_enclosure_contract(
            coordination_root,
            code_repository_name,
            task_name,
            leaf_id=leaf_id,
            parent_task=parent_task,
        )
    task_root = resolve_active_task_root(
        coordination_root, code_repository_name, task_name, parent_task=parent_task
    )
    possible = series_contract_path(task_root)
    if possible.exists():
        return possible
    return None
