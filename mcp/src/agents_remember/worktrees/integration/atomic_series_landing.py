"""Canonical atomic-series ownership checks for protected landing targets."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from agents_remember.worktrees.modules.git import repository_identity
from agents_remember.worktrees.scheduling_mode import TERMINAL_SERIES_CLEANUP
from agents_remember.worktrees.task_resolver import iter_active_series_contracts
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


@dataclass(frozen=True)
class AtomicLandingBlocker:
    contract_path: Path
    master: str
    state: str


class AtomicLandingBlocked(RuntimeError):
    def __init__(self, blocker: AtomicLandingBlocker, detail: str) -> None:
        self.blocker = blocker
        self.status = "atomic-series-landing-blocked"
        self.detail = detail
        super().__init__(detail)


def require_atomic_landing_authority(contract: WorktreeContract) -> None:
    """Refuse a protected landing that conflicts with a live atomic series owner."""

    current_path = contract.contract_path.resolve()
    try:
        landing_targets = _landing_ref_keys(contract)
    except (OSError, RuntimeError, ValueError) as exc:
        raise AtomicLandingBlocked(
            AtomicLandingBlocker(current_path, contract.task_name, "authority-unreadable"),
            "current protected landing target authority is unreadable",
        ) from exc
    repository_tasks = contract.coordination_root / "tasks" / contract.repo_name
    for path in iter_active_series_contracts(repository_tasks):
        owner = _active_atomic_owner(path, current_path)
        if owner is None:
            continue
        try:
            owner_targets = _owned_ref_keys(owner)
        except (OSError, RuntimeError, ValueError) as exc:
            raise AtomicLandingBlocked(
                AtomicLandingBlocker(path, owner.task_name, "authority-unreadable"),
                "live series ref authority is unreadable and blocks landing",
            ) from exc
        if _is_declared_parent_owner(contract, owner, path, landing_targets, owner_targets):
            continue
        if landing_targets & owner_targets:
            raise AtomicLandingBlocked(
                AtomicLandingBlocker(path, owner.task_name, "live-nonterminal"),
                "a distinct live atomic series contract owns this protected landing target",
            )


def _is_declared_parent_owner(
    contract: WorktreeContract,
    owner: WorktreeContract,
    owner_path: Path,
    landing_targets: set[tuple[str, str, str]],
    owner_targets: set[tuple[str, str, str]],
) -> bool:
    """Recognize only the exact parent whose protected refs this leaf is meant to advance."""

    parent = contract.parent_contract_path
    return bool(
        contract.kind == "leaf"
        and parent is not None
        and parent.resolve(strict=False) == owner_path.resolve(strict=False)
        and contract.parent_task_name == owner.task_name
        and contract.repo_name == owner.repo_name
        and landing_targets == owner_targets
    )


def _active_atomic_owner(path: Path, current_path: Path) -> WorktreeContract | None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise AtomicLandingBlocked(
            AtomicLandingBlocker(path, path.parent.name, "present-unreadable"),
            "present series authority cannot be inspected and blocks landing",
        ) from exc
    if not stat.S_ISREG(mode):
        raise AtomicLandingBlocked(
            AtomicLandingBlocker(path, path.parent.name, "present-unreadable"),
            "present series authority is non-regular and blocks landing",
        )
    try:
        owner = load_contract(path)
    except (ContractError, OSError, RuntimeError, UnicodeError, ValueError) as exc:
        raise AtomicLandingBlocked(
            AtomicLandingBlocker(path, path.parent.name, "present-unreadable"),
            "present series authority is unreadable and blocks conflicting landings",
        ) from exc
    if not _canonical_owner(owner, path):
        raise AtomicLandingBlocked(
            AtomicLandingBlocker(path, owner.task_name, "invalid"),
            "present series authority is not its exact canonical contract",
        )
    if owner.integration_status == "completed" or owner.cleanup in TERMINAL_SERIES_CLEANUP:
        return None
    if owner.contract_path.resolve() == current_path:
        return None
    return owner


def _canonical_owner(owner: WorktreeContract, path: Path) -> bool:
    return bool(
        owner.kind == "series"
        and owner.contract_path.resolve() == path.resolve()
        and owner.task_root.resolve() == path.parent.resolve()
        and owner.task_artifact.with_suffix(".json").resolve()
        == (path.parent / "task.json").resolve()
    )


def _landing_ref_keys(contract: WorktreeContract) -> set[tuple[str, str, str]]:
    return _contract_ref_keys(contract, code_branch=contract.code_source_branch, owner=False)


def _owned_ref_keys(contract: WorktreeContract) -> set[tuple[str, str, str]]:
    return _contract_ref_keys(contract, code_branch=contract.code_work_branch, owner=True)


def _contract_ref_keys(
    contract: WorktreeContract,
    *,
    code_branch: str,
    owner: bool,
) -> set[tuple[str, str, str]]:
    keys = {_ref_key("code", contract.code_repo_path, code_branch)}
    if contract.memory_mode != "external":
        return keys
    if contract.memory_repo_path is None:
        raise RuntimeError("external-memory series ref authority has no repository")
    memory_branch = contract.memory_work_branch if owner else contract.memory_source_branch
    keys.add(_ref_key("memory", contract.memory_repo_path, memory_branch))
    return keys


def _ref_key(side: str, repository_path: Path, branch: str) -> tuple[str, str, str]:
    if not branch:
        raise RuntimeError(f"{side} series ref authority has no branch")
    repository = repository_identity(repository_path)
    if repository is None:
        raise RuntimeError("landing target repository identity is unavailable")
    return side, repository.as_posix(), branch
