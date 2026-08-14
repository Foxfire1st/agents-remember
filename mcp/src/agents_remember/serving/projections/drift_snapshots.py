"""Projection-side drift-snapshot pruning.

The shared path/removal primitives live in ``kernel.drift_snapshot``; this
module owns the pruning policy over the contract snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.observer_paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
)
from agents_remember.serving.projections.contract_snapshot import (
    ContractSnapshot,
    build_contract_snapshot,
)


def prune_orphaned_drift_snapshots(
    config: Any, *, contracts: ContractSnapshot | None = None
) -> dict[str, object]:
    """Remove valid drift snapshots that no longer map to a configured repo or live worktree.

    260712-PTS-L2: the projection tick passes its shared per-tick
    :class:`ContractSnapshot` so pruning adds ZERO contract parses; a standalone
    call (``contracts=None``) builds a local snapshot with identical behavior.
    """
    directory = drift_snapshot_dir(config.coordination_root)
    if not directory.is_dir():
        return {"removed": [], "kept": 0, "skipped": 0}

    configured_repositories = {
        scope.path.name for scope in config.repositories.values() if scope.path.name
    }
    active_worktrees = _active_worktree_snapshot_keys(config.coordination_root, contracts=contracts)
    removed: list[dict[str, object]] = []
    kept = 0
    skipped = 0
    for path in sorted(directory.glob("*.json")):
        payload = _read_valid_snapshot(path)
        if payload is None:
            skipped += 1
            continue
        repository = str(payload.get("repository", ""))
        branch = str(payload.get("branch", ""))
        if repository in configured_repositories or (repository, branch) in active_worktrees:
            kept += 1
            continue
        removed.append(
            _remove_snapshot_file(path, repository=repository, branch=branch, dry_run=False)
        )
    return {"removed": removed, "kept": kept, "skipped": skipped}


def _active_worktree_snapshot_keys(
    coordination_root: Path, *, contracts: ContractSnapshot | None = None
) -> set[tuple[str, str]]:
    snapshot = (
        contracts if contracts is not None else build_contract_snapshot(coordination_root / "tasks")
    )
    keys: set[tuple[str, str]] = set()
    for contract in snapshot.contracts.values():
        if contract.code_worktree.exists():
            keys.add((contract.code_worktree.name, contract.code_work_branch))
    return keys


def _read_valid_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != DRIFT_SNAPSHOT_SCHEMA:
        return None
    return payload


def _remove_snapshot_file(
    path: Path, *, repository: str, branch: str, dry_run: bool
) -> dict[str, object]:
    result: dict[str, object] = {
        "path": path.as_posix(),
        "repository": repository,
        "branch": branch,
        "removed": False,
    }
    if not path.exists():
        return {**result, "reason": "already-absent"}
    if dry_run:
        return {**result, "would_remove": True}
    try:
        path.unlink()
    except OSError as error:
        return {**result, "reason": str(error)}
    return {**result, "removed": True}
