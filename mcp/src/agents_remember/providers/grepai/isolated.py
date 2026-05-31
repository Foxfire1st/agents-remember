"""Worktree/isolated GrepAI provider settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.context import (
    GREPAI_NETWORK_NAME,
    GREPAI_OLLAMA_IMAGE,
    GREPAI_PIN,
    GREPAI_RUNNER_IMAGE_REPOSITORY,
)
from agents_remember.providers.identity import (
    provider_instance_id,
    provider_ownership_labels,
    scoped_name,
)
from agents_remember.providers.setup_common import provider_settings, stable_provider_id

GREPAI_PROVIDER_ID = "grepai-memory"


@dataclass(frozen=True)
class IsolatedGrepaiOptions:
    runtime_root: Path | None = None
    settings_path: Path | None = None
    project_id: str | None = None
    target_memory_root: Path | None = None
    allow_missing_roots: bool = False


def isolated_grepai_settings(args: Any, settings: dict[str, Any]) -> dict[str, Any] | None:
    provider = provider_settings(settings, GREPAI_PROVIDER_ID)
    if provider is None or args.grepai_isolated_runtime_root is None:
        return None
    _require_isolated_grepai_target(args)
    isolated_root = args.grepai_isolated_runtime_root.resolve()
    project_id = stable_provider_id(args.grepai_seed_project_id)
    instance_id = provider_instance_id(
        "worktree",
        isolated_root,
        workspace_name=args.coordination_root.parent.name,
    )
    grepai = json.loads(json.dumps(provider))
    grepai.update(
        _isolated_grepai_base_fields(
            args,
            isolated_root=isolated_root,
            instance_id=instance_id,
        )
    )
    grepai["roots"] = _isolated_grepai_roots(
        grepai,
        project_id=project_id,
        target_memory_root=args.grepai_seed_target_memory_root.resolve(),
    )
    grepai["runtime"] = _isolated_grepai_runtime(grepai.get("runtime"), instance_id)
    grepai["backend"] = _isolated_grepai_backend(
        args,
        grepai.get("backend"),
        isolated_root=isolated_root,
        instance_id=instance_id,
    )
    grepai["embedder"] = _isolated_grepai_embedder(
        args,
        grepai.get("embedder"),
        isolated_root=isolated_root,
        instance_id=instance_id,
    )
    return _isolated_settings_payload(grepai)


def _require_isolated_grepai_target(args: Any) -> None:
    if not args.grepai_seed_project_id:
        raise RuntimeError("--grepai-seed-project-id is required for isolated GrepAI")
    if args.grepai_seed_target_memory_root is None:
        raise RuntimeError("--grepai-seed-target-memory-root is required for isolated GrepAI")


def _isolated_settings_payload(grepai: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "contextProviders": {
            "enabled": True,
            "providers": {
                GREPAI_PROVIDER_ID: grepai,
            },
            "policy": {
                "discoveryOnly": True,
                "sourceProofRequired": True,
            },
        },
    }


def _isolated_grepai_base_fields(
    args: Any,
    *,
    isolated_root: Path,
    instance_id: str,
) -> dict[str, Any]:
    runtime_root = isolated_root / "providers" / "runners" / "grepai" / instance_id
    log_root = isolated_root / "logs" / "providers" / "grepai" / instance_id
    return {
        "enabled": True,
        "instance": {
            "id": instance_id,
            "scope": "worktree",
            "labels": provider_ownership_labels(
                provider_id=GREPAI_PROVIDER_ID,
                instance_id=instance_id,
                scope="worktree",
                coordination_root=args.coordination_root,
            ),
        },
        "workspace": scoped_name("agents-remember-memory", instance_id),
        "runtimeRoot": runtime_root.as_posix(),
        "requirementsFile": (
            args.coordination_root / "providers" / "requirements" / "grepai.txt"
        ).as_posix(),
        "stateFile": (runtime_root / "state" / "provider-state.json").as_posix(),
        "watch": {
            "mode": "background",
            "cwd": (isolated_root / "memory-roots").as_posix(),
            "logDir": log_root.as_posix(),
        },
        "allowMissingRoots": bool(getattr(args, "grepai_allow_missing_roots", False)),
    }


def _isolated_grepai_roots(
    provider: dict[str, Any],
    *,
    project_id: str,
    target_memory_root: Path,
) -> list[dict[str, str]]:
    roots = provider.get("roots")
    if not isinstance(roots, list):
        roots = []
    replaced = False
    isolated_roots: list[dict[str, str]] = []
    for root in roots:
        normalized = _normalized_root(root)
        if normalized is None:
            continue
        if normalized["projectId"] == project_id:
            normalized["path"] = target_memory_root.as_posix()
            replaced = True
        isolated_roots.append(normalized)
    if not replaced:
        isolated_roots.append({"projectId": project_id, "path": target_memory_root.as_posix()})
    return isolated_roots


def _normalized_root(root: Any) -> dict[str, str] | None:
    if isinstance(root, str):
        return {"projectId": stable_provider_id(Path(root).name), "path": root}
    if not isinstance(root, dict):
        return None
    raw_path = str(root.get("path", ""))
    if not raw_path:
        return None
    project_id = stable_provider_id(
        str(root.get("projectId") or root.get("repoId") or Path(raw_path).name)
    )
    return {"projectId": project_id, "path": raw_path}


def _isolated_grepai_runtime(runtime_settings: Any, instance_id: str) -> dict[str, Any]:
    version = GREPAI_PIN.split("==", 1)[1]
    runtime = runtime_settings if isinstance(runtime_settings, dict) else {}
    runtime = dict(runtime)
    runner = runtime.get("runner")
    runner = dict(runner) if isinstance(runner, dict) else {}
    runner.update(
        {
            "image": f"{GREPAI_RUNNER_IMAGE_REPOSITORY}:{version}",
            "containerName": scoped_name("ar-grepai-watcher", instance_id),
            "imageLockFile": "<coordination_root>/providers/requirements/grepai-runner-docker.lock",
            "buildRoot": "<runtimeRoot>/image",
            "runtimeMount": "/grepai/runtime",
            "logsMount": "/grepai/logs",
        }
    )
    runtime["mode"] = "docker"
    runtime["composeProject"] = scoped_name("agents-remember-grepai", instance_id)
    runtime["network"] = {"name": scoped_name(GREPAI_NETWORK_NAME, instance_id)}
    runtime["runner"] = runner
    return runtime


def _isolated_grepai_backend(
    args: Any,
    backend_settings: Any,
    *,
    isolated_root: Path,
    instance_id: str,
) -> dict[str, Any]:
    backend = backend_settings if isinstance(backend_settings, dict) else {}
    backend = dict(backend)
    backend.update(
        {
            "runtimeRoot": (
                isolated_root / "providers" / "data" / "grepai" / instance_id / "postgres"
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "image": "pgvector/pgvector:pg16",
            "imageLockFile": (
                args.coordination_root / "providers" / "requirements" / "grepai-postgres-docker.lock"
            ).as_posix(),
            "containerName": scoped_name("ar-grepai-postgres", instance_id),
            "postgres": {
                "user": "grepai",
                "password": "grepai",
                "database": "grepai",
            },
            "ports": {
                "postgres": {
                    "bindHost": "127.0.0.1",
                    "hostPort": "auto",
                    "containerPort": 5432,
                }
            },
        }
    )
    return backend


def _isolated_grepai_embedder(
    args: Any,
    embedder_settings: Any,
    *,
    isolated_root: Path,
    instance_id: str,
) -> dict[str, Any]:
    embedder = embedder_settings if isinstance(embedder_settings, dict) else {}
    embedder = dict(embedder)
    ollama_container = scoped_name("ar-grepai-ollama", instance_id)
    backend = embedder.get("backend")
    backend = dict(backend) if isinstance(backend, dict) else {}
    backend.update(
        {
            "mode": "docker",
            "image": GREPAI_OLLAMA_IMAGE,
            "imageLockFile": (
                args.coordination_root / "providers" / "requirements" / "grepai-ollama-docker.lock"
            ).as_posix(),
            "runtimeRoot": (
                isolated_root / "providers" / "data" / "grepai" / instance_id / "ollama"
            ).as_posix(),
            "dataRoot": "<embedderRuntimeRoot>/data",
            "dataDestination": "/root/.ollama",
            "containerName": ollama_container,
            "ports": {
                "http": {
                    "bindHost": "127.0.0.1",
                    "hostPort": "auto",
                    "containerPort": 11434,
                }
            },
        }
    )
    embedder.update(
        {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "endpoint": f"http://{ollama_container}:11434",
            "dimensions": 768,
            "backend": backend,
        }
    )
    return embedder
