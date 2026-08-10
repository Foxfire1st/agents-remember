"""Provider lifecycle runtime for worktree start/teardown (composition layer).

Moved out of the worktrees package: worktrees ranks below providers, so the
provider setup/teardown mechanics live here and are bound into
``WorktreeServices`` by the composition root.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers import provider_setup
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import docker_command
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


WORKTREE_SETTINGS_RELATIVE = Path("provider-runtime") / "settings" / "provider-settings.json"


def teardown_worktree_providers(contract: WorktreeContract, *, dry_run: bool) -> dict[str, Any]:
    """Remove a worktree's isolated containers, networks, and provider-runtime tree."""
    provider_runtime = contract.worktree_group / "provider-runtime"
    settings = _load_worktree_provider_settings(contract.worktree_group)
    containers, networks = _worktree_provider_docker_resources(settings) if settings else ([], [])
    cwd = contract.coordination_root
    return {
        "state": "would-teardown" if dry_run else "torn-down",
        "settingsFound": settings is not None,
        "containers": [_docker_rm_f(name, cwd=cwd, dry_run=dry_run) for name in containers],
        "networks": [_docker_network_rm(name, cwd=cwd, dry_run=dry_run) for name in networks],
        # Provider data (postgres/ollama) is written root-owned by the containers, so a
        # plain rmtree by the host user fails; remove_tree reclaims ownership via docker.
        "providerRuntime": remove_tree(
            provider_runtime,
            dry_run=dry_run,
            reclaim_image=_reclaim_image(settings),
            reclaim_cwd=cwd,
        ),
    }


def _load_worktree_provider_settings(worktree_group: Path) -> dict[str, Any] | None:
    settings_path = worktree_group / WORKTREE_SETTINGS_RELATIVE
    if not settings_path.is_file():
        return None
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _worktree_provider_docker_resources(
    settings: dict[str, Any],
) -> tuple[list[str], list[str]]:
    providers = settings.get("contextProviders", {})
    providers = providers.get("providers", {}) if isinstance(providers, dict) else {}
    containers: list[str] = []
    networks: list[str] = []
    if not isinstance(providers, dict):
        return containers, networks
    for provider in providers.values():
        if not isinstance(provider, dict):
            continue
        _collect_provider_containers(provider, containers)
        _collect_provider_networks(provider, networks)
    return _dedupe(containers), _dedupe(networks)


def _collect_provider_containers(provider: dict[str, Any], out: list[str]) -> None:
    runtime = provider.get("runtime", {}) if isinstance(provider.get("runtime"), dict) else {}
    runner = runtime.get("runner", {}) if isinstance(runtime.get("runner"), dict) else {}
    backend = provider.get("backend", {}) if isinstance(provider.get("backend"), dict) else {}
    embedder = provider.get("embedder", {}) if isinstance(provider.get("embedder"), dict) else {}
    embedder_backend = (
        embedder.get("backend", {}) if isinstance(embedder.get("backend"), dict) else {}
    )
    for name in (
        runner.get("containerName"),
        backend.get("containerName"),
        embedder_backend.get("containerName"),
    ):
        if isinstance(name, str) and name:
            out.append(name)
    # CGC names its per-repo watchers from a template; expand it for each root repo.
    template = runner.get("containerNameTemplate")
    if isinstance(template, str) and "<repoId>" in template:
        for root in provider.get("roots", []) or []:
            repo_id = root.get("repoId") if isinstance(root, dict) else None
            if isinstance(repo_id, str) and repo_id:
                out.append(template.replace("<repoId>", repo_id))


def _collect_provider_networks(provider: dict[str, Any], out: list[str]) -> None:
    runtime = provider.get("runtime", {}) if isinstance(provider.get("runtime"), dict) else {}
    backend = provider.get("backend", {}) if isinstance(provider.get("backend"), dict) else {}
    for section in (runtime.get("network"), backend.get("network")):
        name = section.get("name") if isinstance(section, dict) else None
        if isinstance(name, str) and name:
            out.append(name)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _docker_rm_f(name: str, *, cwd: Path, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"container": name, "removed": False, "would_remove": True}
    result = run_command([docker_command(), "rm", "-f", name], cwd=cwd, timeout=60)
    if result["returncode"] == 0:
        return {"container": name, "removed": True}
    if _absent(result.get("stderr", "")):
        return {"container": name, "removed": False, "reason": "already-absent"}
    return {
        "container": name,
        "removed": False,
        "reason": (result.get("stderr") or "").strip() or "docker rm failed",
    }


def _docker_network_rm(name: str, *, cwd: Path, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"network": name, "removed": False, "would_remove": True}
    result = run_command([docker_command(), "network", "rm", name], cwd=cwd, timeout=60)
    if result["returncode"] == 0:
        return {"network": name, "removed": True}
    if _absent(result.get("stderr", "")):
        return {"network": name, "removed": False, "reason": "already-absent"}
    return {
        "network": name,
        "removed": False,
        "reason": (result.get("stderr") or "").strip() or "docker network rm failed",
    }


def _absent(stderr: str) -> bool:
    lowered = stderr.lower()
    return "no such" in lowered or "not found" in lowered


def remove_tree(
    path: Path,
    *,
    dry_run: bool,
    reclaim_image: str | None = None,
    reclaim_cwd: Path | None = None,
) -> dict[str, Any]:
    if not path.exists():
        return {"path": path.as_posix(), "removed": False, "reason": "already-absent"}
    if dry_run:
        return {"path": path.as_posix(), "removed": False, "would_remove": True}
    try:
        shutil.rmtree(path)
        return {"path": path.as_posix(), "removed": True}
    except PermissionError as error:
        # Containers write provider data root-owned; the host user cannot delete it.
        # Reclaim ownership via a one-shot privileged container, then remove.
        reclaim = _reclaim_ownership(path, reclaim_image, reclaim_cwd)
        if not reclaim.get("ok"):
            return {
                "path": path.as_posix(),
                "removed": False,
                "reason": f"permission denied: {error}",
                "reclaim": reclaim,
            }
        shutil.rmtree(path, ignore_errors=True)
        removed = not path.exists()
        result: dict[str, Any] = {
            "path": path.as_posix(),
            "removed": removed,
            "reclaimedViaDocker": True,
        }
        if not removed:
            result["reason"] = "still present after docker ownership reclaim"
        return result


def _reclaim_ownership(path: Path, image: str | None, cwd: Path | None) -> dict[str, Any]:
    owner = _host_owner()
    if image is None or owner is None:
        return {"ok": False, "error": _reclaim_unavailable_reason(image)}
    # --entrypoint chown is required: provider images (falkordb/postgres/ollama) set
    # their own entrypoint, so args alone would be handed to the DB launcher, not chown.
    result = run_command(
        [
            docker_command(),
            "run",
            "--rm",
            "--entrypoint",
            "chown",
            "-v",
            f"{path.as_posix()}:/reclaim",
            image,
            "-R",
            owner,
            "/reclaim",
        ],
        cwd=cwd or path.parent,
        timeout=300,
    )
    return _reclaim_result(result, image, owner)


def _host_owner() -> str | None:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return None
    return f"{getuid()}:{getgid()}"


def _reclaim_unavailable_reason(image: str | None) -> str:
    if image is None:
        return "no reclaim image available in worktree settings"
    return "ownership reclaim is unsupported on this platform"


def _reclaim_result(result: dict[str, Any], image: str, owner: str) -> dict[str, Any]:
    if result["returncode"] == 0:
        return {"ok": True, "image": image, "owner": owner}
    return {
        "ok": False,
        "error": (result.get("stderr") or "").strip() or "docker chown reclaim failed",
        "image": image,
    }


def _reclaim_image(settings: dict[str, Any] | None) -> str | None:
    """A container image already present locally (it created the data) for the chown step."""
    if not settings:
        return None
    providers = settings.get("contextProviders", {})
    providers = providers.get("providers", {}) if isinstance(providers, dict) else {}
    if not isinstance(providers, dict):
        return None
    for provider in providers.values():
        if not isinstance(provider, dict):
            continue
        backend = provider.get("backend", {}) if isinstance(provider.get("backend"), dict) else {}
        embedder = (
            provider.get("embedder", {}) if isinstance(provider.get("embedder"), dict) else {}
        )
        embedder_backend = (
            embedder.get("backend", {}) if isinstance(embedder.get("backend"), dict) else {}
        )
        for image in (backend.get("image"), embedder_backend.get("image")):
            if isinstance(image, str) and image:
                return image
    return None
