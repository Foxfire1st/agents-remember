"""Configuration loading for the Agents Remember MCP server."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when MCP authority settings are missing or unsafe."""


@dataclass(frozen=True)
class RepositoryScope:
    repo_id: str
    path: Path
    memory_root: Path | None = None
    memory_settings_includes: tuple[Path, ...] = ()
    contract_path: Path | None = None


@dataclass(frozen=True)
class ProviderScope:
    provider_id: str
    runtime_root: Path
    log_root: Path


@dataclass(frozen=True)
class McpRuntimeConfig:
    config_path: Path
    coordination_root: Path
    workspace_root: Path
    transcript_root: Path
    harness_skill_root: Path | None = None
    repositories: dict[str, RepositoryScope] = field(default_factory=dict)
    providers: dict[str, ProviderScope] = field(default_factory=dict)
    timeout_caps: dict[str, int] = field(default_factory=dict)

    @property
    def allowed_repo_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.repositories))

    @property
    def allowed_provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.providers))


def load_config(config_path: str | Path) -> McpRuntimeConfig:
    path = require_config_path(config_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"cannot parse MCP settings JSON: {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"MCP settings must be a JSON object: {path}")
    return config_from_mapping(data, path)


def require_config_path(config_path: str | Path) -> Path:
    if not config_path:
        raise ConfigError("--config is required")

    path = Path(config_path)
    if not path.is_absolute():
        raise ConfigError("--config must be an absolute path")
    path = path.resolve()
    if path.name == "settings.json" and path.parent.name == "system":
        raise ConfigError("coordinator system/settings.json is not an MCP authority settings file")
    if not path.exists():
        raise ConfigError(f"MCP settings file does not exist: {path}")
    if path.is_dir():
        raise ConfigError(f"MCP settings path must be a file: {path}")
    return path


def config_from_mapping(data: dict[str, Any], config_path: Path) -> McpRuntimeConfig:
    coordination_root = required_absolute_path(data, "coordinationRoot")
    workspace_root = required_absolute_path(data, "workspaceRoot")
    transcript_root = optional_absolute_path(data, "transcriptRoot", owner="MCP settings")
    if transcript_root is None:
        transcript_root = coordination_root / "providers" / "logs" / "mcp"
    harness_skill_root = optional_absolute_path(
        data,
        "harnessSkillRoot",
        owner="MCP settings",
    ) or infer_harness_skill_root(config_path)

    if path_is_relative_to(config_path, coordination_root):
        raise ConfigError("MCP settings must not live inside the coordinator root")

    repositories = parse_repositories(
        data.get("repositories", {}),
        coordination_root,
        workspace_root,
    )
    providers = parse_providers(data.get("providers", {}), coordination_root)
    timeout_caps = parse_timeout_caps(data.get("timeoutCaps", {}))

    return McpRuntimeConfig(
        config_path=config_path,
        coordination_root=coordination_root,
        workspace_root=workspace_root,
        transcript_root=transcript_root,
        harness_skill_root=harness_skill_root,
        repositories=repositories,
        providers=providers,
        timeout_caps=timeout_caps,
    )


def parse_repositories(
    raw: object,
    coordination_root: Path,
    workspace_root: Path,
) -> dict[str, RepositoryScope]:
    if not isinstance(raw, dict):
        raise ConfigError("repositories must be an object keyed by repo id")

    repositories: dict[str, RepositoryScope] = {}
    for repo_id, value in raw.items():
        if not isinstance(repo_id, str) or not repo_id:
            raise ConfigError("repository ids must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"repository settings for {repo_id!r} must be an object")

        repo_path = workspace_root / repo_id
        memory_root = coordination_root / "memory-repos" / f"ar-{repo_id}"
        contract_path = optional_coordination_path(
            value,
            "contractPath",
            owner=f"repository {repo_id}",
            coordination_root=coordination_root,
        )
        if contract_path is not None and not path_is_relative_to(contract_path, coordination_root):
            raise ConfigError(
                f"repository {repo_id} contractPath must be inside the coordinator root"
            )
        includes = parse_path_list(
            value.get("memorySettingsIncludes", []),
            owner=f"repository {repo_id} memorySettingsIncludes",
        )
        allowed_roots = (repo_path, memory_root)
        for include_path in includes:
            if not any(path_is_relative_to(include_path, root) for root in allowed_roots):
                raise ConfigError(
                    f"repository {repo_id} include points outside configured repo "
                    f"boundaries: {include_path}"
                )

        repositories[repo_id] = RepositoryScope(
            repo_id=repo_id,
            path=repo_path,
            memory_root=memory_root,
            memory_settings_includes=tuple(includes),
            contract_path=contract_path,
        )
    return repositories


def infer_harness_skill_root(config_path: Path) -> Path | None:
    if config_path.parent.name != "mcp":
        return None
    return (config_path.parent.parent / "skills").resolve()


def parse_providers(raw: object, coordination_root: Path) -> dict[str, ProviderScope]:
    if not isinstance(raw, dict):
        raise ConfigError("providers must be an object keyed by provider id")

    providers: dict[str, ProviderScope] = {}
    for provider_id, value in raw.items():
        if not isinstance(provider_id, str) or not provider_id:
            raise ConfigError("provider ids must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"provider settings for {provider_id!r} must be an object")

        if value:
            unsupported = ", ".join(sorted(value))
            raise ConfigError(
                f"provider {provider_id} settings are derived by the server; "
                f"remove unsupported fields: {unsupported}"
            )
        provider_name = provider_runtime_name(provider_id)

        providers[provider_id] = ProviderScope(
            provider_id=provider_id,
            runtime_root=coordination_root / "providers" / "runners" / provider_name,
            log_root=coordination_root / "providers" / "logs" / provider_name,
        )
    return providers


def provider_runtime_name(provider_id: str) -> str:
    names = {
        "codegraphcontext-code": "codegraphcontext",
        "grepai-memory": "grepai",
    }
    try:
        return names[provider_id]
    except KeyError as error:
        raise ConfigError(f"unsupported provider id: {provider_id}") from error


def parse_timeout_caps(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ConfigError("timeoutCaps must be an object")

    parsed: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ConfigError("timeout cap names must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(f"timeout cap {key!r} must be a positive integer")
        parsed[key] = value
    return parsed


def required_absolute_path(data: dict[str, Any], key: str, *, owner: str = "MCP settings") -> Path:
    if key not in data:
        raise ConfigError(f"{owner} must define {key}")
    return require_absolute_json_path(data[key], f"{owner}.{key}")


def optional_absolute_path(data: dict[str, Any], key: str, *, owner: str) -> Path | None:
    if key not in data or data[key] is None:
        return None
    return require_absolute_json_path(data[key], f"{owner}.{key}")


def optional_coordination_path(
    data: dict[str, Any],
    key: str,
    *,
    owner: str,
    coordination_root: Path,
) -> Path | None:
    if key not in data or data[key] is None:
        return None
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{owner}.{key} must be a non-empty string")
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (coordination_root / path).resolve()
    if not path_is_relative_to(resolved, coordination_root):
        raise ConfigError(f"{owner} {key} must be inside the coordinator root")
    return resolved


def parse_path_list(raw: object, *, owner: str) -> list[Path]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"{owner} must be a list")
    return [
        require_absolute_json_path(value, f"{owner}[{index}]")
        for index, value in enumerate(raw)
    ]


def require_absolute_json_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError(f"{label} must be an absolute path")
    return path.resolve()


def path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
