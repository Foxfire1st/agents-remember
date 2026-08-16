"""The one irreversible child-admission seal for an atomic series."""

from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


def require_series_accepting_leaves(
    series: WorktreeContract,
    *,
    operation: str,
) -> None:
    """Refuse new or reopened child work after series closeout begins."""

    if series.kind != "series":
        raise RuntimeError(f"{operation} requires an atomic series contract")
    lifecycle = (series.closeout_status, series.integration_status, series.cleanup)
    if lifecycle != ("not-started", "not-started", "pending"):
        raise RuntimeError(
            f"{operation} refused: atomic series is sealed against new or reopened leaves "
            f"after closeout begins: {lifecycle!r}"
        )


def require_series_path_accepting_leaves(path: Path, *, operation: str) -> WorktreeContract:
    """Load and validate the exact task-owned series seal."""

    if not path.is_file():
        raise RuntimeError(f"{operation} requires its task-owned series contract")
    try:
        series = load_contract(path)
    except (ContractError, OSError) as exc:
        raise RuntimeError(
            f"{operation} cannot read its task-owned series contract: {exc}"
        ) from exc
    require_series_accepting_leaves(series, operation=operation)
    return series
