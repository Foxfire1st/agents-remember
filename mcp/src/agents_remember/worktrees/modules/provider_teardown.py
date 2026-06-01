"""Full-reclaim teardown of a worktree's isolated provider stack.

The lifecycle `stop`/`shutdown-all` actions only stop the *watchers*; they leave
the backend containers (FalkorDB, Postgres, Ollama) and the per-worktree networks
running. When a worktree is removed (integrated-then-cleaned, or abandoned) its
whole isolated stack should be reclaimed. This module derives every docker
resource by name from the worktree's persisted provider settings and removes
them, then recursively removes the worktree's `provider-runtime/` tree (data,
logs, settings, state — all of which live inside the worktree group).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import docker_command
from agents_remember.worktrees.worktree_contract import WorktreeContract

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
