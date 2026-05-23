"""Provider lifecycle settings derived from MCP authority settings."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, ProviderScope


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
    return {
        "type": "semantic",
        "scope": "memory",
        "enabled": True,
        "roots": roots,
        "runtimeRoot": provider.runtime_root.as_posix(),
        "requirementsFile": config.coordination_root.joinpath(
            "providers",
            "requirements",
            "grepai.txt",
        ).as_posix(),
        "stateFile": provider.runtime_root.joinpath("state", "provider-state.json").as_posix(),
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
                "postgres",
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "containerName": "ar-grepai-postgres",
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
        "watch": {
            "mode": "background",
            "cwd": config.coordination_root.joinpath("memory-repos").as_posix(),
            "logDir": provider.log_root.as_posix(),
        },
    }


def _cgc_settings(provider: ProviderScope, config: McpRuntimeConfig) -> dict[str, Any]:
    return {
        "type": "relationship",
        "scope": "code",
        "enabled": True,
        "roots": [
            {"repoId": repo.repo_id, "path": repo.path.as_posix()}
            for repo in config.repositories.values()
        ],
        "runtimeRoot": provider.runtime_root.as_posix(),
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "venvRoot": config.coordination_root.joinpath(
            "providers",
            "_venvs",
            "codegraphcontext",
        ).as_posix(),
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
        "backend": {
            "id": "codegraphcontext-falkordb",
            "type": "falkordb-remote",
            "mode": "docker",
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
                "falkordb",
            ).as_posix(),
            "dataRoot": "<backendRuntimeRoot>/data",
            "containerName": "ar-cgc-falkordb",
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
