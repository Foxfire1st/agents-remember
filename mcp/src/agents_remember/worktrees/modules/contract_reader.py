"""Worktree-backed contract reader bound into the kernel coordination resolver.

The kernel resolver declares ``ContractReaderPort``; this adapter implements it
with the worktree contract file primitives (contract loading, task-root and
leaf-enclosure path resolution).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


class WorktreeContractReader:
    """The production implementation of :class:`ContractReaderPort`."""

    def load_contract(self, path: Path) -> Any:
        return load_contract(path)

    def worktree_group_for(
        self,
        coordination_root: Path,
        code_repository_name: str,
        worktree_name: str,
    ) -> Path:
        return worktree_group_for(coordination_root, code_repository_name, worktree_name)

    def resolve_active_task_root(
        self,
        coordination_root: Path,
        code_repository_name: str,
        task_name: str,
        *,
        parent_task: str | None = None,
    ) -> Path:
        return resolve_active_task_root(
            coordination_root,
            code_repository_name,
            task_name,
            parent_task=parent_task,
        )

    def find_task_contract(
        self,
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
            coordination_root,
            code_repository_name,
            task_name,
            parent_task=parent_task,
        )
        possible = series_contract_path(task_root)
        if possible.exists():
            return possible
        return None

    def find_worktree_contract(
        self,
        coordination_root: Path,
        code_repository_name: str,
        worktree_name: str,
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
        target = self.worktree_group_for(
            coordination_root, code_repository_name, worktree_name
        ).name
        tasks_root = coordination_root / "tasks" / code_repository_name
        if not tasks_root.is_dir():
            return None
        for contract_file in sorted(tasks_root.rglob(SERIES_CONTRACT_FILENAME)):
            if is_archived_path(contract_file):
                continue
            try:
                contract = self.load_contract(contract_file)
            except (ContractError, OSError):
                continue
            if contract.worktree_group.name == target:
                return contract_file
        return None


__all__ = ["WorktreeContractReader"]
