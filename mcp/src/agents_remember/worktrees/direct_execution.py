"""Direct-execution helpers below the application layer.

The grant store read lives in ``kernel``; the give/take-back boundary (which
proves the leaf unstarted) lives in ``application``. This module names the
direct leaf in work for callers that cannot reach the application layer, from
the leaf's document alone, and stays silent when nothing is determinable --
so a refusal that names no leaf reads exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agents_remember.kernel.direct_execution_grants import (
    code_checkout_dirty,
    grant_store_dir,
    iter_grant_leaves,
    memory_checkout_dirty,
    sibling_occupies_checkout,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    direct_pair_proof,
)
from agents_remember.worktrees.worktree_contract import load_contract


def _doc_in_work(
    document: Any,
    *,
    checkout_dirty: bool | None = None,
    landed: bool | None = None,
) -> bool:
    """Whether a granted leaf's document shows it started and still open.

    A ``Completed`` leaf with started markers occupies the checkout while
    either side is dirty or no landing proof exists (F05); pass the
    checkout's dirt and the leaf's pair proof when determinable.
    """

    return sibling_occupies_checkout(document, checkout_dirty=checkout_dirty, landed=landed)


def _stored_grant_leaves(
    store: Path, *, repository: str | None = None, master: str | None = None
) -> Iterator[tuple[str, str]]:
    """Every granted ``(repository, path)`` in stable order; skips unreadable files."""
    yield from iter_grant_leaves(store, repository=repository, master=master)


def _master_leaf_doc(
    coordination_root: Path, task_root: Path, repository: str, leaf_path: str
) -> Any | None:
    """The sibling's document when it lies in this master's task root."""
    parts = Path(leaf_path).parts
    if len(parts) < 2 or parts[0] != task_root.name:
        return None
    candidate = (task_root / Path(*parts[1:])).resolve(strict=False)
    expected = (coordination_root / "tasks" / repository / leaf_path).resolve(strict=False)
    if candidate != expected:
        return None
    try:
        return read_task_doc(expected)
    except (OSError, ValueError):
        return None


def _direct_checkout_dirty(contract: Any) -> bool | None:
    """Whether either checkout of the contract's branches has uncommitted work."""

    code_repo_path = getattr(contract, "code_repo_path", None)
    work_branch = getattr(contract, "code_work_branch", None)
    memory_repo_path = getattr(contract, "memory_repo_path", None)
    memory_work_branch = getattr(contract, "memory_work_branch", "") or ""
    if code_repo_path is None or not work_branch:
        return None
    try:
        code = code_checkout_dirty(Path(code_repo_path), work_branch)
        memory = memory_checkout_dirty(
            Path(memory_repo_path) if memory_repo_path is not None else None,
            memory_work_branch,
        )
    except (OSError, ValueError):
        return None
    if code is True or memory is True:
        return True
    if code is None or memory is None:
        return None
    return False


def _open_grant_candidate(
    contract: Any,
    series_contract: Any | None,
    checkout_dirty: bool | None,
    granted: tuple[str, str],
) -> str | None:
    """``repo:path`` when the granted leaf is started and still in work."""

    coordination_root = Path(contract.coordination_root)
    task_root = Path(contract.task_root).resolve(strict=False)
    own_leaf_id = getattr(contract, "leaf_id", "") or ""
    repository, leaf_path = granted
    if repository != contract.repo_name:
        return None
    doc = _master_leaf_doc(coordination_root, task_root, repository, leaf_path)
    if doc is None:
        return None
    if own_leaf_id and doc.id == own_leaf_id:
        return None
    try:
        sibling_ref = TaskDocumentRef(repository=repository, path=leaf_path)
    except ValueError:
        return None
    landed = (
        direct_pair_proof(series_contract, sibling_ref) if series_contract is not None else None
    )
    if _doc_in_work(doc, checkout_dirty=checkout_dirty, landed=landed):
        return f"{repository}:{leaf_path}"
    return None


def open_direct_leaf_name(contract: Any) -> str | None:
    """``repo:path`` of the granted in-work direct leaf of this master's line.

    ``None`` when no granted sibling of the same master shows an open leaf in
    its document -- then the caller's refusal reads exactly as before.
    """
    coordination_root = Path(contract.coordination_root)
    task_root = Path(contract.task_root).resolve(strict=False)
    checkout_dirty = _direct_checkout_dirty(contract)
    try:
        series_contract = load_contract(task_root / "series-contract.md")
    except (OSError, ValueError):
        series_contract = None
    for granted in _stored_grant_leaves(
        grant_store_dir(coordination_root),
        repository=contract.repo_name,
        master=task_root.name,
    ):
        found = _open_grant_candidate(
            contract,
            series_contract,
            checkout_dirty,
            granted,
        )
        if found is not None:
            return found
    return None
