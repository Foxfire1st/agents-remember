"""Provider lifecycle settings derived from MCP authority settings."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope
from agents_remember.providers.context import (
    CGC_NETWORK_NAME,
    CGC_PIN,
    CGC_RUNNER_IMAGE_REPOSITORY,
    CGC_WATCHER_CONTAINER_PREFIX,
    GREPAI_NETWORK_NAME,
    GREPAI_OLLAMA_IMAGE,
    GREPAI_PIN,
    GREPAI_RUNNER_IMAGE_REPOSITORY,
)
from agents_remember.providers.identity import provider_ownership_labels, scoped_name


def lifecycle_settings_from_config(config: McpRuntimeConfig) -> dict[str, Any]:
    providers: dict[str, Any] = {}
    if "grepai-memory" in config.providers:
        providers["grepai-memory"] = _grepai_settings(config.providers["grepai-memory"], config)
    if "codegraphcontext-code" in config.providers:
        providers["codegraphcontext-code"] = _cgc_settings(
            config.providers["codegraphcontext-code"],
            config,
        )
    return {
        "contextProviders": {
            "enabled": bool(providers),
            "providers": providers,
        }
    }


def write_lifecycle_settings(config: McpRuntimeConfig) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix="-agents-remember-provider-settings.json",
    ) as handle:
        path = Path(handle.name)
        json.dump(lifecycle_settings_from_config(config), handle, indent=2)
    return path


def _grepai_settings(provider: ProviderScope, config: McpRuntimeConfig) -> dict[str, Any]:
    roots = [
        {"projectId": repo.repo_id, "path": repo.memory_root.as_posix()}
        for repo in config.repositories.values()
        if repo.memory_root is not None
    ]
    if not roots:
        roots = [config.coordination_root.joinpath("memory-repos").as_posix()]
    version = GREPAI_PIN.split("==", 1)[1]
    labels = provider_ownership_labels(
        provider_id=provider.provider_id,
        instance_id=provider.instance_id,
        scope=provider.scope,
        coordination_root=config.coordination_root,
    )
    postgres_container = scoped_name("ar-grepai-postgres", provider.instance_id)
    ollama_container = scoped_name("ar-grepai-ollama", provider.instance_id)
    watcher_container = scoped_name("ar-grepai-watcher", provider.instance_id)
    network_name = scoped_name(GREPAI_NETWORK_NAME, provider.instance_id)
    compose_project = scoped_name("agents-remember-grepai", provider.instance_id)
    workspace_name = scoped_name("agents-remember-memory", provider.instance_id)
    return {
        "type": "semantic",
        "scope": "memory",
        "enabled": True,
        "instance": {
            "id": provider.instance_id,
            "scope": provider.scope,
            "labels": labels,
        },
        "workspace": workspace_name,
        "roots": roots,
        "runtimeRoot": provider.runtime_root.as_posix(),
        "requirementsFile": config.coordination_root.joinpath(
            "providers",
            "requirements",
            "grepai.txt",
        ).as_posix(),
        "stateFile": provider.runtime_root.joinpath("state", "provider-state.json").as_posix(),
        "runtime": {
            "mode": "docker",
            "composeProject": compose_project,
            "network": {"name": network_name},
            "runner": {
                "image": f"{GREPAI_RUNNER_IMAGE_REPOSITORY}:{version}",
                "containerName": watcher_container,
                "imageLockFile": config.coordination_root.joinpath(
                    "providers",
                    "requirements",
                    "grepai-runner-docker.lock",
                ).as_posix(),
                "buildRoot": provider.runtime_root.joinpath("image").as_posix(),
                "runtimeMount": "/grepai/runtime",
                "logsMount": "/grepai/logs",
            },
        },
        "backend": {
            "id": "grepai-postgres",
            "type": "postgres",
            "mode": "docker",
            "image": "pgvector/pgvector:pg16",
            "imageLockFile": config.coordination_root.joinpath(
                "providers",
                "requirements",
                "grepai-postgres-docker.lock",
            ).as_posix(),
            "runtimeRoot": config.coordination_root.joinpath(
                "providers",
                "data",
                "grepai",
                provider.instance_id,
                "postgres",
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "containerName": postgres_container,
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
        },
        "embedder": {
            "provider": "ollama",
            "model": "nomic-embed-text",
            "endpoint": f"http://{ollama_container}:11434",
            "dimensions": 768,
            "backend": {
                "mode": "docker",
                "image": GREPAI_OLLAMA_IMAGE,
                "imageLockFile": config.coordination_root.joinpath(
                    "providers",
                    "requirements",
                    "grepai-ollama-docker.lock",
                ).as_posix(),
                "runtimeRoot": config.coordination_root.joinpath(
                    "providers",
                    "data",
                    "grepai",
                    provider.instance_id,
                    "ollama",
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
            },
        },
        "watch": {
            "mode": "background",
            "cwd": config.coordination_root.joinpath("memory-repos").as_posix(),
            "logDir": provider.log_root.as_posix(),
        },
    }


def _cgc_settings(provider: ProviderScope, config: McpRuntimeConfig) -> dict[str, Any]:
    version = CGC_PIN.split("==", 1)[1]
    labels = provider_ownership_labels(
        provider_id=provider.provider_id,
        instance_id=provider.instance_id,
        scope=provider.scope,
        coordination_root=config.coordination_root,
    )
    backend_container = scoped_name("ar-cgc-falkordb", provider.instance_id)
    watcher_template = f"{scoped_name(CGC_WATCHER_CONTAINER_PREFIX, provider.instance_id)}-<repoId>"
    network_name = scoped_name(CGC_NETWORK_NAME, provider.instance_id)
    compose_project = scoped_name("agents-remember-cgc", provider.instance_id)
    return {
        "type": "relationship",
        "scope": "code",
        "enabled": True,
        "instance": {
            "id": provider.instance_id,
            "scope": provider.scope,
            "labels": labels,
        },
        "roots": [
            {"repoId": repo.repo_id, "path": repo.path.as_posix()}
            for repo in config.repositories.values()
        ],
        "runtimeRoot": provider.runtime_root.as_posix(),
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "requirementsFile": config.coordination_root.joinpath(
            "providers",
            "requirements",
            "codegraphcontext.txt",
        ).as_posix(),
        "patchesRoot": config.coordination_root.joinpath(
            "providers",
            "patches",
            "codegraphcontext",
        ).as_posix(),
        "stateFileTemplate": "<instanceRoot>/provider-state.json",
        "runtime": {
            "mode": "docker",
            "composeProject": compose_project,
            "runner": {
                "image": f"{CGC_RUNNER_IMAGE_REPOSITORY}:{version}",
                "buildRoot": provider.runtime_root.joinpath("image").as_posix(),
                "imageLockFile": config.coordination_root.joinpath(
                    "providers",
                    "requirements",
                    "codegraphcontext-runner-docker.lock",
                ).as_posix(),
                "containerNameTemplate": watcher_template,
            },
        },
        "backend": {
            "id": "codegraphcontext-falkordb",
            "type": "falkordb-remote",
            "mode": "docker",
            "network": {"name": network_name},
            "image": "falkordb/falkordb:v4.18.7",
            "imageLockFile": config.coordination_root.joinpath(
                "providers",
                "requirements",
                "codegraphcontext-falkordb-docker.lock",
            ).as_posix(),
            "runtimeRoot": config.coordination_root.joinpath(
                "providers",
                "data",
                "codegraphcontext",
                provider.instance_id,
                "falkordb",
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "dataDestination": "/var/lib/falkordb/data",
            "containerName": backend_container,
            "ports": {
                "falkordb": {
                    "bindHost": "127.0.0.1",
                    "hostPort": "auto",
                    "containerPort": 6379,
                },
                "browser": {
                    "bindHost": "127.0.0.1",
                    "hostPort": "auto",
                    "containerPort": 3000,
                },
            },
        },
        "watch": {
            "mode": "managed-foreground",
            "cwdTemplate": "<instanceRoot>",
            "logFileTemplate": provider.log_root.joinpath("<repoId>", "watch.log").as_posix(),
        },
    }
