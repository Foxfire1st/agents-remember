"""Durable progress for the pre-contract window of ``worktree_start`` (slice 5e, §5.4).

``worktree_start`` prepares a code worktree and can then block on external memory (or providers,
or a stale base) *before* a contract is written. The contract is the dashboard's durable anchor
-- the enclosures surface reads leaf ``series-contract.md`` files -- so a start that is stuck before its
contract is currently invisible. This module writes a small, transient progress file on each
blocked early return and clears it once the contract lands, so the Engine Room can show a start
that is gated before it ever produces a contract.

It lives in the worktrees layer (``start.py`` writes it; :mod:`agents_remember.observer.snapshots`
reads it) so neither import direction creates an observer<->worktrees cycle. Every write is
best-effort: observability must never make a start fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

START_PROGRESS_SCHEMA = "ar-worktree-start-progress/v1"


@dataclass(frozen=True)
class StartingEnclosure:
    """The enclosure a start-progress beat is about.

    These are the contract's own front-matter facts -- task, repo, worktree name and group,
    memory mode, and the code branch/commit/paths the start has resolved so far -- written
    early precisely because the contract that would normally carry them does not exist yet.
    Every writer builds it from the in-flight contract, which is why they travel as one thing.
    """

    repo_name: str
    task_name: str
    worktree_name: str
    worktree_group: str
    memory_mode: str
    code_source_branch: str = ""
    code_base_commit: str = ""
    code_repo_path: str = ""
    code_worktree: str = ""


@dataclass(frozen=True)
class StartBeat:
    """How far a start has got: the phase it is in, the phases already behind it, the choices
    it is waiting on, and why it is blocked (``None`` on a happy-path beat)."""

    phase: str
    completed_phases: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    blocked_reason: str | None = None


def start_progress_dir(coordination_root: Path) -> Path:
    return coordination_root / "temp" / "worktree-start"


def start_progress_path(coordination_root: Path, repo_name: str, worktree_name: str) -> Path:
    return start_progress_dir(coordination_root) / repo_name / f"{worktree_name}.json"


def write_start_progress(
    coordination_root: Path,
    enclosure: StartingEnclosure,
    beat: StartBeat,
) -> None:
    """Write the transient start-progress file. Never raises -- a start must not fail on this."""
    payload: dict[str, Any] = {
        "schema": START_PROGRESS_SCHEMA,
        "repoName": enclosure.repo_name,
        "taskName": enclosure.task_name,
        "worktreeName": enclosure.worktree_name,
        "worktreeGroup": enclosure.worktree_group,
        "phase": beat.phase,
        "memoryMode": enclosure.memory_mode,
        "codeSourceBranch": enclosure.code_source_branch,
        "codeBaseCommit": enclosure.code_base_commit,
        "codeRepoPath": enclosure.code_repo_path,
        "codeWorktree": enclosure.code_worktree,
        "completedPhases": list(beat.completed_phases),
        "choices": list(beat.choices),
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    if beat.blocked_reason is not None:
        payload["blockedReason"] = beat.blocked_reason
    try:
        path = start_progress_path(coordination_root, enclosure.repo_name, enclosure.worktree_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return  # observability is best-effort; never break the start


def clear_start_progress(coordination_root: Path, repo_name: str, worktree_name: str) -> None:
    """Remove the start-progress file once a contract supersedes it. Never raises."""
    try:
        start_progress_path(coordination_root, repo_name, worktree_name).unlink(missing_ok=True)
    except OSError:
        return


def read_start_progress(path: Path) -> dict[str, Any] | None:
    """Parse one start-progress file, gating on the schema; ``None`` when missing/invalid."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != START_PROGRESS_SCHEMA:
        return None
    return payload
