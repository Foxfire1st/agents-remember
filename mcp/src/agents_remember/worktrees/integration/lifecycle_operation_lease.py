"""One cross-operation lease for every task enclosure lifecycle."""

from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.models.lifecycles.operation import LifecycleOperationKind
from agents_remember.worktrees.integration.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

_ACTIVE = frozenset({"queued", "running", "input-required"})


def _lease_path(contract: WorktreeContract) -> Path:
    identity = contract.contract_path.resolve().as_posix()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return contract.worktree_group / "reports" / f"lifecycle-{digest}.lock"


def _active_operation_kinds(contract: WorktreeContract) -> list[LifecycleOperationKind]:
    active: list[LifecycleOperationKind] = []
    for kind in ("closeout", "integrate"):
        record = LifecycleOperationStore(
            operation_record_path(contract.worktree_group, kind)
        ).read()
        if record is not None and record.status in _ACTIVE:
            active.append(kind)
    return active


@contextmanager
def contract_lifecycle_lease(
    contract: WorktreeContract,
    *,
    operation_kind: LifecycleOperationKind | None,
) -> Iterator[None]:
    """Serialize cross-kind launch/cancel and terminal mutation for one contract.

    A same-kind caller may observe or recover its existing journal. A different
    operation kind, cleanup, or abandon must wait until the durable owner is terminal.
    """

    path = _lease_path(contract)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            active = _active_operation_kinds(contract)
            blockers = [kind for kind in active if kind != operation_kind]
            if blockers:
                label = "terminal mutation" if operation_kind is None else operation_kind
                raise RuntimeError(
                    f"{label} cannot proceed while task lifecycle operation(s) are active: "
                    f"{', '.join(blockers)}"
                )
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
