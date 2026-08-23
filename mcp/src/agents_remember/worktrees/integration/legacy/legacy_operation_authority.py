"""Location and serialization authority for the explicit legacy bridge."""

from __future__ import annotations

import fcntl
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind
from agents_remember.worktrees.integration.legacy.legacy_operation_failures import LegacyBridgeError
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_lease import (
    contract_lifecycle_lease,
    require_legacy_operation_compatible,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    require_matching_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
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
        with contract_lifecycle_lease(contract):
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


def require_explicit_bridge_compatible(
    contract: WorktreeContract,
    target: LegacyOperationTarget,
    operation_kind: LifecycleOperationKind,
    *,
    publish_worker_exits: bool,
) -> None:
    if not target.pre_adoption:
        try:
            require_legacy_operation_compatible(
                contract,
                target_kind=operation_kind,
                publish_worker_exits=publish_worker_exits,
            )
        except RuntimeError as error:
            raise LegacyBridgeError(
                "legacy-cross-kind-authority-active",
                "another canonical lifecycle authority prevents legacy publication",
                observed={
                    "failure": public_failure_evidence(
                        stage="legacy-compatibility",
                        side="journal",
                        name="current-operation.json",
                        error_type=type(error).__name__,
                        observed={"state": "incompatible"},
                    )
                },
            ) from error
        return
    active: list[str] = []
    for kind in ("closeout", "integrate", "direct-landing"):
        if kind == operation_kind:
            continue
        path = contract.worktree_group / "reports" / f"{kind}-operation.json"
        try:
            record = LifecycleOperationStore(path).read()
        except RuntimeError as error:
            raise LegacyBridgeError(
                "legacy-cross-kind-evidence-invalid",
                "another exact historic operation path is unreadable or non-current-schema",
                expected={"name": path.name, "schemaVersion": "3.0"},
                observed={
                    "failure": public_failure_evidence(
                        stage="legacy-cross-kind-read",
                        side="journal",
                        name=path.name,
                        error_type=type(error).__name__,
                        observed={"state": "unreadable"},
                    )
                },
            ) from error
        if record is not None and (
            record.status in {"queued", "running", "input-required", "termination-required"}
            or record.workerPid is not None
        ):
            active.append(kind)
    if active:
        raise LegacyBridgeError(
            "legacy-cross-kind-authority-active",
            "legacy lifecycle repair cannot proceed while another task operation is active: "
            + ", ".join(active),
        )
