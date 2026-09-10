"""Location and serialization authority for the explicit legacy bridge."""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind
from agents_remember.worktrees.integration.legacy.legacy_operation_failures import LegacyBridgeError
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class LegacyOperationTarget:
    path: Path
    pre_adoption: bool


def legacy_pre_adoption(contract: WorktreeContract) -> bool:
    try:
        require_matching_lifecycle_operation_location(contract)
    except LifecycleOperationLocationError as error:
        if error.status == "operation-location-adoption-required":
            return True
        raise
    return False


def revalidated_legacy_target(
    accepted: WorktreeContract,
    operation_kind: LifecycleOperationKind,
    *,
    pre_adoption: bool,
    revalidate_contract: Callable[[], WorktreeContract],
) -> tuple[WorktreeContract, LegacyOperationTarget]:
    """Reload authority before deriving the one canonical raw-record target."""

    current = revalidate_contract()
    if current != accepted:
        raise LegacyBridgeError(
            "legacy-contract-changed",
            "contract changed before legacy evidence inspection",
            expected={"state": "exact-accepted-contract"},
            observed={"state": "changed"},
        )
    target = _legacy_operation_target(current, operation_kind)
    if target.pre_adoption != pre_adoption:
        raise LegacyBridgeError(
            "legacy-operation-location-changed",
            "canonical legacy operation location changed before evidence inspection",
            expected={"preAdoption": pre_adoption},
            observed={"preAdoption": target.pre_adoption},
        )
    return current, target


def _legacy_operation_target(
    contract: WorktreeContract,
    operation_kind: LifecycleOperationKind,
) -> LegacyOperationTarget:
    """Resolve only the root journal or the explicit historic bridge address."""

    try:
        location = require_matching_lifecycle_operation_location(contract)
    except LifecycleOperationLocationError as error:
        if error.status != "operation-location-adoption-required":
            raise
        return LegacyOperationTarget(
            contract.worktree_group / "reports" / f"{operation_kind}-operation.json",
            pre_adoption=True,
        )
    return LegacyOperationTarget(location.journal_path(operation_kind), pre_adoption=False)


@contextmanager
def legacy_lifecycle_lease(
    contract: WorktreeContract,
    *,
    pre_adoption: bool,
) -> Iterator[None]:
    if not pre_adoption:
        yield
        return
    lock_path = contract.worktree_group / "reports" / ".legacy-lifecycle.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
