"""Ephemeral terminal capability derived from canonical atomic series authority."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, get_ident
from typing import Literal, TypeVar

from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_terminal import (
    TerminalCleanupContractAuthority,
)
from agents_remember.worktrees.modules.terminal_validation import require_series_children_retired
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import WorktreeContract

T = TypeVar("T")
AtomicSeriesTerminalOperation = Literal["worktree_cleanup", "worktree_abandon"]
_CAPABILITY = object()


@dataclass(frozen=True)
class AtomicSeriesTerminalPermit:
    contract_path: Path
    operation: AtomicSeriesTerminalOperation
    _capability: object


_ACTIVE: ContextVar[AtomicSeriesTerminalPermit | None] = ContextVar(
    "active_atomic_series_terminal_permit",
    default=None,
)
_THREAD_ACTIVE: dict[int, tuple[AtomicSeriesTerminalPermit, int]] = {}
_THREAD_LOCK = Lock()


def require_atomic_series_terminal_release(contract: WorktreeContract) -> None:
    _require_atomic_series(contract)
    require_series_children_retired(contract)


def publish_atomic_series_terminal_under_authority(
    contract: WorktreeContract,
    operation: AtomicSeriesTerminalOperation,
    publication: Callable[[AtomicSeriesTerminalPermit], T],
    *,
    terminal_authority: TerminalCleanupContractAuthority | None = None,
) -> T:
    """Run one terminal mutation with an unforgeable contract-derived capability."""

    _require_atomic_series(contract)
    if terminal_authority is None:
        require_series_children_retired(contract)
    else:
        _require_archived_terminal_authority(contract, operation, terminal_authority)
    permit = AtomicSeriesTerminalPermit(contract.contract_path.resolve(), operation, _CAPABILITY)
    with _THREAD_LOCK:
        _THREAD_ACTIVE[id(permit)] = (permit, get_ident())
    token = _ACTIVE.set(permit)
    try:
        return publication(permit)
    finally:
        _ACTIVE.reset(token)
        with _THREAD_LOCK:
            _THREAD_ACTIVE.pop(id(permit), None)


def _require_archived_terminal_authority(
    contract: WorktreeContract,
    operation: AtomicSeriesTerminalOperation,
    authority: TerminalCleanupContractAuthority,
) -> None:
    if (
        authority.state != "archive-ready"
        or authority.archive.cleanupOperation != operation
        or authority.archived_contract != contract
    ):
        raise RuntimeError(
            "atomic series terminal retry requires the exact accepted archive-ready authority"
        )


def require_atomic_series_terminal_permit(
    contract: WorktreeContract,
    operation: AtomicSeriesTerminalOperation,
    permit: AtomicSeriesTerminalPermit | None,
) -> None:
    with _THREAD_LOCK:
        active = _THREAD_ACTIVE.get(id(permit)) if permit is not None else None
    if (
        permit is None
        or permit._capability is not _CAPABILITY
        or _ACTIVE.get() is not permit
        or active != (permit, get_ident())
        or permit.operation != operation
        or permit.contract_path != contract.contract_path.resolve()
    ):
        raise RuntimeError(
            "atomic series terminal mutation requires live contract-derived authority"
        )


def _require_atomic_series(contract: WorktreeContract) -> None:
    if contract.kind != "series":
        raise RuntimeError("atomic terminal authority requires a series contract")
    if contract.contract_path.resolve() != series_contract_path(contract.task_root).resolve():
        raise RuntimeError("atomic terminal authority requires the exact series contract address")
