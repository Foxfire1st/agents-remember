"""Background provider setup for worktree start (GitHub #53).

`worktree_start` returns within seconds once worktrees and contract exist; the
provider chain (installs, GrepAI DB clone, CGC seed or reindex fallback,
watchers) runs on a daemon thread that reports through a durable
`SetupProgressFile`. `worktree_status` polls that file; a dead server mid-setup
surfaces as a stale heartbeat, and `worktree_start(retry_provider_setup=true)`
is the recovery path.
"""

from __future__ import annotations

import json
import subprocess
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers import provider_setup
from agents_remember.providers.setup_progress import (
    SetupProgressFile,
    progress_status,
    read_setup_progress,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

SETUP_PROGRESS_FILENAME = "setup-progress.json"

# The exception set provider_setup.main treats as reportable setup failures;
# the thread boundary mirrors it so a failure becomes a `failed` progress state
# instead of a silent thread death (anything else escapes to the threading
# excepthook on stderr and surfaces as a stale heartbeat).
_SETUP_ERRORS = (
    RuntimeError,
    OSError,
    subprocess.TimeoutExpired,
    zipfile.BadZipFile,
    json.JSONDecodeError,
)


def setup_progress_path(worktree_group: Path) -> Path:
    return worktree_group / "provider-runtime" / SETUP_PROGRESS_FILENAME


def progress_identity(contract: WorktreeContract) -> dict[str, Any]:
    """Top-level identity fields so dashboards need not parse file paths."""
    return {
        "repoName": contract.repo_name,
        "taskName": contract.task_name,
        "worktreeGroup": contract.worktree_group.as_posix(),
    }


@dataclass(frozen=True)
class ProviderSetupJob:
    """One background provider setup: the request to run, the enclosure it belongs to, where
    its result state file is written, and the temporary settings file whose lifetime the
    setup thread owns (``None`` when the caller keeps it). The four are what the daemon
    thread closes over -- the thread cannot be started with a subset of them.
    """

    request: provider_setup.ProviderSetupRequest
    contract: WorktreeContract
    write_state_file: Callable[[dict[str, Any]], Path]
    settings_cleanup: Path | None = None


def launch_provider_setup(
    job: ProviderSetupJob,
    *,
    runner: Callable[..., dict[str, Any]] = provider_setup.run_provider_setup,
    thread_factory: Callable[..., threading.Thread] = threading.Thread,
) -> dict[str, Any]:
    """Run provider setup on a daemon thread; return the `starting` state now."""
    request = job.request
    contract = job.contract
    write_state_file = job.write_state_file
    settings_cleanup = job.settings_cleanup
    progress_path = setup_progress_path(contract.worktree_group)
    progress = SetupProgressFile(progress_path, identity=progress_identity(contract))

    def _run() -> None:
        try:
            payload = runner(request, progress)
            summary: dict[str, Any] = {
                "resultCounts": payload.get("resultCounts"),
                "setupSummary": payload.get("setupSummary"),
            }
            if payload.get("ok"):
                summary["providerStateFile"] = write_state_file(payload).as_posix()
            progress.finish(
                state=str(payload.get("state") or "failed"),
                summary=summary,
            )
        except _SETUP_ERRORS as error:
            progress.finish(state="failed", error=f"{type(error).__name__}: {error}")
        finally:
            if settings_cleanup is not None:
                settings_cleanup.unlink(missing_ok=True)

    thread = thread_factory(
        target=_run,
        name=f"provider-setup-{contract.task_name}",
        daemon=True,
    )
    thread.start()
    return {
        "state": "starting",
        "progressFile": progress_path.as_posix(),
        "pollTool": "worktree_status",
        "expectation": (
            "seed copy completes in seconds; a refused seed falls back to a full "
            "reindex (minutes, scales with repo size) — progress reports "
            "seedFallback the moment that happens"
        ),
    }


def provider_setup_status(contract: WorktreeContract) -> dict[str, Any] | None:
    """Project the worktree's provider setup state for status payloads.

    None when this contract never ran background setup (pre-async contracts,
    skipped providers); `prepared` when only the legacy provider-state.json
    exists; otherwise the live progress projection (running / stale / ok /
    ready-with-failed-phases / failed / failed-unchecked).
    """
    progress = read_setup_progress(setup_progress_path(contract.worktree_group))
    if progress is None:
        state_file = contract.worktree_group / "provider-runtime" / "provider-state.json"
        if state_file.exists():
            return {"state": "prepared"}
        return None
    status = progress_status(progress)
    status["progressFile"] = setup_progress_path(contract.worktree_group).as_posix()
    if status["state"] in ("failed", "failed-unchecked", "stale"):
        status["retryArgs"] = {
            "repo_id": contract.repo_name,
            "task_name": contract.task_name,
            "worktree_name": contract.code_worktree.name,
            "retry_provider_setup": True,
        }
    return status


def provider_setup_running(contract: WorktreeContract) -> bool:
    """True while a live (fresh-heartbeat) background setup owns this worktree."""
    progress = read_setup_progress(setup_progress_path(contract.worktree_group))
    if progress is None:
        return False
    return progress_status(progress)["state"] == "running"
