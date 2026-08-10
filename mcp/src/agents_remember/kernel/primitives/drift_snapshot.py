"""Drift snapshot path and removal primitives (kernel-owned).

Shared by the serving projection readers, worktrees and memory_quality so they
can resolve and remove drift snapshots without crossing packages.
"""

from __future__ import annotations

import re
from pathlib import Path

from agents_remember.kernel.primitives.observer_paths import drift_snapshot_dir


def sanitize_report_token(token: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", token.strip())
    normalized = normalized.strip(".-_")
    return normalized or "unknown"


def drift_snapshot_path(coordination_root: Path, *, repository: str, branch: str) -> Path:
    repo_token = sanitize_report_token(repository)
    branch_token = sanitize_report_token(branch)
    return drift_snapshot_dir(coordination_root) / f"{repo_token}__{branch_token}.json"


def remove_drift_snapshot(
    coordination_root: Path, *, repository: str, branch: str, dry_run: bool
) -> dict[str, object]:
    return _remove_snapshot_file(
        drift_snapshot_path(coordination_root, repository=repository, branch=branch),
        repository=repository,
        branch=branch,
        dry_run=dry_run,
    )


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
