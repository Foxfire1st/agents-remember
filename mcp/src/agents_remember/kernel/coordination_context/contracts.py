from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.worktrees.leaf_refs import resolve_leaf_enclosure_contract_for_ref
from agents_remember.worktrees.task_resolver import (
    SERIES_CONTRACT_FILENAME,
    is_archived_path,
    resolve_active_task_root,
    series_contract_path,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    load_contract,
    worktree_group_for,
)


def resolve_contract(
    selector: EnclosureSelector,
    coordination_root: Path,
    code_repository_name: str,
) -> tuple[Any | None, Path | None]:
    candidate = selector.contract_path.resolve() if selector.contract_path else None
    if candidate is None and selector.task_name:
        candidate = find_task_contract(
            coordination_root,
            code_repository_name,
            selector.task_name,
            parent_task=selector.parent_task,
            leaf_id=selector.leaf_id,
        )
    if candidate is None and selector.worktree_name:
        candidate = find_worktree_contract(
            coordination_root, code_repository_name, selector.worktree_name
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
        return resolve_leaf_enclosure_contract_for_ref(
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


def find_worktree_contract(
    coordination_root: Path, code_repository_name: str, worktree_name: str
) -> Path | None:
    """Locate a task contract from ``worktree_name`` when no task name is known.

    ``worktree_name`` cannot be reversed to ``task_name`` (``slugify`` preserves both
    ``-`` and ``_``, so the prefix boundary is lossy), and the canonical
    ``series-contract.md`` lives at ``tasks/<repo>/<task_name>/series-contract.md`` and
    nests further under master + ``enclosures/<leaf-id>/`` rather than inside the
    worktree dir. The lossless join key is the derived worktree-group folder name
    matched against each contract's recorded ``coordination.worktree_group``. Archived
    (``0_archive/``) contracts are skipped so a retired task cannot shadow an active one.
    """
    target = worktree_group_for(coordination_root, code_repository_name, worktree_name).name
    tasks_root = coordination_root / "tasks" / code_repository_name
    if not tasks_root.is_dir():
        return None
    # The series-contract.md is canonical and nests under master + leaf enclosures, so search
    # recursively (main's original flat `*/contract.md` glob predates the enclosure layout).
    for contract_file in sorted(tasks_root.rglob(SERIES_CONTRACT_FILENAME)):
        if is_archived_path(contract_file):
            continue
        try:
            contract = load_contract(contract_file)
        except (ContractError, OSError):
            continue
        if contract.worktree_group.name == target:
            return contract_file
    return None
