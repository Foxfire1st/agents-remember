"""Projection-side drift-snapshot pruning.

The shared path/removal primitives live in ``kernel.drift_snapshot``; this
module owns the pruning policy over the contract snapshot.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.controlplane.attention_dismissals import AttentionObservation
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.own_writes import OWN_WRITES
from agents_remember.kernel.primitives.observer_paths import (
    DRIFT_SNAPSHOT_SCHEMA,
    drift_snapshot_dir,
)
from agents_remember.serving.projections.contract_snapshot import (
    ContractSnapshot,
    build_contract_snapshot,
)
from agents_remember.serving.projections.snapshots_impl._common import surface_paths


def prune_orphaned_drift_snapshots(
    config: Any,
    *,
    contracts: ContractSnapshot | None = None,
    observation: AttentionObservation | None = None,
    note_own: bool = False,
) -> dict[str, object]:
    """Remove valid snapshots only after a complete native branch census.

    260712-PTS-L2: the projection tick passes its shared per-tick
    :class:`ContractSnapshot` so pruning adds ZERO contract parses; a standalone
    call (``contracts=None``) builds a local snapshot with identical behavior.
    """
    directory = drift_snapshot_dir(config.coordination_root)
    proof = observation or AttentionObservation("actionable-drift", datetime.now(UTC))
    snapshot = contracts or build_contract_snapshot(config.coordination_root / "tasks")
    _observe_branches(config, snapshot, proof)
    removed: list[dict[str, object]] = []
    kept = 0
    skipped = 0
    valid: list[tuple[Path, dict[str, Any]]] = []
    for path in surface_paths(directory, "*.json", proof):
        payload = read_drift_snapshot(path)
        if payload is None:
            skipped += 1
            proof.complete = False
            continue
        valid.append((path, payload))
        if "actionableFingerprint" in payload and not payload["actionableFingerprint"]:
            proof.complete = False
    for path, payload in valid:
        repository = str(payload.get("repository", ""))
        branch = str(payload.get("branch", ""))
        if not proof.complete or f"actionable-drift:{repository}:{branch}" in proof.existing:
            kept += 1
            continue
        removed.append(
            _remove_snapshot_file(
                path, repository=repository, branch=branch, dry_run=False, note_own=note_own
            )
        )
    return {"removed": removed, "kept": kept, "skipped": skipped}


def _observe_branches(config: Any, snapshot: ContractSnapshot, proof: AttentionObservation) -> None:
    """One ref listing per repository, shared by configured and worktree snapshot names.

    Branch existence, rather than a current snapshot or checkout directory, owns
    dismissal retention. A failed census or skipped contract proves no removal.
    This runs only on drift/task invalidation, never an idle heartbeat.
    """
    proof.complete = proof.complete and not snapshot.skipped
    roots = {scope.path for scope in config.repositories.values()}
    roots.update(contract.code_repo_path for contract in snapshot.contracts.values())
    branches: dict[Path, set[str]] = {}
    for root in sorted(roots):
        try:
            result = run_git(root, ["for-each-ref", "--format=%(refname:strip=2)", "refs/heads/"])
        except (OSError, subprocess.SubprocessError):
            proof.complete = False
            continue
        if result.returncode:
            proof.complete = False
            continue
        branches[root] = set(result.stdout.splitlines())
    for scope in config.repositories.values():
        for branch in branches.get(scope.path, ()):
            proof.observe(f"actionable-drift:{scope.path.name}:{branch}")
    for contract in snapshot.contracts.values():
        if contract.code_work_branch in branches.get(contract.code_repo_path, ()):
            proof.observe(
                f"actionable-drift:{contract.code_worktree.name}:{contract.code_work_branch}"
            )


def read_drift_snapshot(path: Path) -> dict[str, Any] | None:
    """The snapshot reader shared by pruning and projection; malformed is unread."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != DRIFT_SNAPSHOT_SCHEMA:
        return None
    count = payload.get("actionableCount")
    if (
        any(
            not isinstance(payload.get(key), str) or not payload[key]
            for key in ("repository", "branch")
        )
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
    ):
        return None
    counts = payload.get("counts", {})
    if not isinstance(counts, dict) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        return None
    fingerprint = payload.get("actionableFingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not fingerprint):
        return None
    return payload


def _remove_snapshot_file(
    path: Path, *, repository: str, branch: str, dry_run: bool, note_own: bool = False
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
        if note_own:
            with OWN_WRITES.announcing(path.as_posix()) as announcement:
                path.unlink()
                OWN_WRITES.note_removed(path.as_posix(), announcement=announcement)
        else:
            path.unlink()
    except OSError as error:
        return {**result, "reason": str(error)}
    return {**result, "removed": True}
