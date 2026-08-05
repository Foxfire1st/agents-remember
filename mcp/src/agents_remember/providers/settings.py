"""Provider lifecycle settings derived from MCP authority settings."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope, RepositoryScope
from agents_remember.providers.cgc.context.constants import CGC_REPO_CGCIGNORE_EXTRAS
from agents_remember.providers.cgc.context.core import cgc_runner_image
from agents_remember.providers.context import (
    CGC_NETWORK_NAME,
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


@dataclass(frozen=True)
class _GrepaiNames:
    """The instance-scoped docker names one GrepAI provider owns.

    Every one of them is ``scoped_name(<base>, instance_id)``, so they are derived once and
    passed on rather than re-derived in each block that needs them -- two blocks spelling
    the same container name differently is the failure this shape removes.
    """

    workspace: str
    compose_project: str
    network: str
    watcher_container: str
    postgres_container: str
    ollama_container: str


def _grepai_names(provider: ProviderScope) -> _GrepaiNames:
    return _GrepaiNames(
        workspace=scoped_name("agents-remember-memory", provider.instance_id),
        compose_project=scoped_name("agents-remember-grepai", provider.instance_id),
        network=scoped_name(GREPAI_NETWORK_NAME, provider.instance_id),
        watcher_container=scoped_name("ar-grepai-watcher", provider.instance_id),
        postgres_container=scoped_name("ar-grepai-postgres", provider.instance_id),
        ollama_container=scoped_name("ar-grepai-ollama", provider.instance_id),
    )


def _grepai_roots(config: McpRuntimeConfig) -> list[Any]:
    """The memory roots GrepAI indexes: one per repository, or the shared root."""
    roots: list[Any] = [
        {"projectId": repo.repo_id, "path": repo.memory_root.as_posix()}
        for repo in config.repositories.values()
        if repo.memory_root is not None
    ]
    if not roots:
        roots = [config.coordination_root.joinpath("memory-repos").as_posix()]
    return roots


def _grepai_runtime(
    provider: ProviderScope, config: McpRuntimeConfig, names: _GrepaiNames
) -> dict[str, Any]:
    """The watcher container: the process that indexes the roots."""
    version = GREPAI_PIN.split("==", 1)[1]
    return {
        "mode": "docker",
        "composeProject": names.compose_project,
        "network": {"name": names.network},
        "runner": {
            "image": f"{GREPAI_RUNNER_IMAGE_REPOSITORY}:{version}",
            "containerName": names.watcher_container,
            "imageLockFile": config.coordination_root.joinpath(
                "providers",
                "requirements",
                "grepai-runner-docker.lock",
            ).as_posix(),
            "buildRoot": provider.runtime_root.joinpath("image").as_posix(),
            "runtimeMount": "/grepai/runtime",
            "logsMount": "/grepai/logs",
        },
    }


def _grepai_backend(
    provider: ProviderScope, config: McpRuntimeConfig, names: _GrepaiNames
) -> dict[str, Any]:
    """The pgvector store the embeddings land in."""
    return {
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
        "containerName": names.postgres_container,
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


def _grepai_embedder(
    provider: ProviderScope, config: McpRuntimeConfig, names: _GrepaiNames
) -> dict[str, Any]:
    """The Ollama container that turns documents into vectors."""
    container = names.ollama_container
    return {
        "provider": "ollama",
        "model": "nomic-embed-text",
        "endpoint": f"http://{container}:11434",
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
            "containerName": container,
            "ports": {
                "http": {
                    "bindHost": "127.0.0.1",
                    "hostPort": "auto",
                    "containerPort": 11434,
                }
            },
        },
    }


def _grepai_settings(provider: ProviderScope, config: McpRuntimeConfig) -> dict[str, Any]:
    """The GrepAI provider document, assembled from the four blocks above.

    Split by block rather than shortened: this is a settings document, so every line is a
    key the runtime reads, and the only honest way to make 126 lines readable is to name
    the four things it declares.
    """
    names = _grepai_names(provider)
    return {
        "type": "semantic",
        "scope": "memory",
        "enabled": True,
        "instance": {
            "id": provider.instance_id,
            "scope": provider.scope,
            "labels": provider_ownership_labels(
                provider_id=provider.provider_id,
                instance_id=provider.instance_id,
                scope=provider.scope,
                coordination_root=config.coordination_root,
            ),
        },
        "workspace": names.workspace,
        "roots": _grepai_roots(config),
        "runtimeRoot": provider.runtime_root.as_posix(),
        "requirementsFile": config.coordination_root.joinpath(
            "providers",
            "requirements",
            "grepai.txt",
        ).as_posix(),
        "stateFile": provider.runtime_root.joinpath("state", "provider-state.json").as_posix(),
        "runtime": _grepai_runtime(provider, config, names),
        "backend": _grepai_backend(provider, config, names),
        "embedder": _grepai_embedder(provider, config, names),
        "watch": {
            "mode": "background",
            "cwd": config.coordination_root.joinpath("memory-repos").as_posix(),
            "logDir": provider.log_root.as_posix(),
        },
    }


def _cgc_settings(provider: ProviderScope, config: McpRuntimeConfig) -> dict[str, Any]:
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
        "roots": [_cgc_root_settings(repo) for repo in config.repositories.values()],
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
                # Single source of truth (GitHub #50): an independent
                # repository:version f-string here dropped the image layer
                # revision, so upgrading hosts kept a cached guard-less image.
                "image": cgc_runner_image(),
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


def _cgc_root_settings(repo: RepositoryScope) -> dict[str, Any]:
    """One generated CGC root entry; per-repo managed exclusions ride along (L12).

    ``cgcignorePatterns`` feeds ``_cgcignore_patterns_from_settings`` and lands in the
    materialized .cgcignore under "# Repo-specific managed exclusions".
    """
    root: dict[str, Any] = {"repoId": repo.repo_id, "path": repo.path.as_posix()}
    extras = CGC_REPO_CGCIGNORE_EXTRAS.get(repo.repo_id)
    if extras:
        root["cgcignorePatterns"] = list(extras)
    return root
