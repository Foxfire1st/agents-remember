"""Docker Compose rendering and execution helpers for provider lifecycles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_inspect_container,
)


@dataclass(frozen=True)
class ComposeRender:
    project_name: str
    base_file: Path
    override_yaml: str

    @property
    def override_sha256(self) -> str:
        return hashlib.sha256(self.override_yaml.encode("utf-8")).hexdigest()


def provider_asset_path(*parts: str) -> Path:
    resource = files("agents_remember").joinpath("package_data", "runtime", "providers", *parts)
    return Path(str(resource))


def provider_asset_text(*parts: str) -> str:
    return provider_asset_path(*parts).read_text(encoding="utf-8")


def compose_command(render: ComposeRender, args: list[str]) -> list[str]:
    return [
        docker_command(),
        "compose",
        "--project-name",
        render.project_name,
        "-f",
        render.base_file.as_posix(),
        "-f",
        "-",
        *args,
    ]


def run_compose(
    render: ComposeRender,
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    return run_command(
        compose_command(render, args),
        cwd=cwd,
        stdin_text=render.override_yaml,
        timeout=timeout,
    )


def compose_plan(render: ComposeRender, args: list[str], *, cwd: Path) -> dict[str, Any]:
    return {
        "command": compose_command(render, args),
        "cwd": cwd.as_posix(),
        "baseFile": render.base_file.as_posix(),
        "overrideSha256": render.override_sha256,
        "overrideMode": "stdin",
        "project": render.project_name,
    }


def render_template(template: str, values: dict[str, str]) -> str:
    text = template
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    unresolved = sorted(set(re.findall(r"@[A-Z0-9_]+@", text)))
    if unresolved:
        raise ContextProviderError(
            f"unresolved Compose template placeholders: {', '.join(unresolved)}"
        )
    return text.rstrip() + "\n"


def yaml_scalar(value: Any) -> str:
    return json.dumps(str(value))


def yaml_port_mapping(host: Any, host_port: Any, container_port: Any) -> str:
    published = "" if host_port is None or str(host_port) == "auto" else str(host_port)
    return yaml_scalar(f"{host}:{published}:{container_port}")


def yaml_environment(env: dict[str, Any], *, indent: int = 6) -> str:
    prefix = " " * indent
    return "\n".join(
        f"{prefix}{key}: {yaml_scalar(value)}" for key, value in sorted(env.items())
    )


def optional_yaml_line(name: str, value: str | None, *, indent: int = 4) -> str:
    if not value:
        return ""
    return f"{' ' * indent}{name}: {yaml_scalar(value)}\n"


def container_compose_project(inspect_data: dict[str, Any] | None) -> str | None:
    if not inspect_data:
        return None
    labels = inspect_data.get("Config", {}).get("Labels", {})
    if not isinstance(labels, dict):
        return None
    project = labels.get("com.docker.compose.project")
    return str(project) if project else None


def container_managed_by_project(
    inspect_data: dict[str, Any] | None, project_name: str
) -> bool:
    return inspect_data is not None and container_compose_project(inspect_data) == project_name


def docker_inspect_network(network_name: str, *, cwd: Path, timeout: int) -> dict[str, Any] | None:
    result = run_command(
        [docker_command(), "network", "inspect", network_name], cwd=cwd, timeout=timeout
    )
    if result["returncode"] != 0:
        return None
    try:
        data = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    return data[0]


def network_compose_project(inspect_data: dict[str, Any] | None) -> str | None:
    if not inspect_data:
        return None
    labels = inspect_data.get("Labels", {})
    if not isinstance(labels, dict):
        return None
    project = labels.get("com.docker.compose.project")
    return str(project) if project else None


def network_managed_by_project(inspect_data: dict[str, Any] | None, project_name: str) -> bool:
    return inspect_data is not None and network_compose_project(inspect_data) == project_name


def remove_container_command(container_name: str) -> list[str]:
    return [docker_command(), "rm", "-f", container_name]


def remove_network_command(network_name: str) -> list[str]:
    return [docker_command(), "network", "rm", network_name]


def remove_container_dry_run(container_name: str, command: list[str]) -> dict[str, Any]:
    return {"containerName": container_name, "command": command, "dryRun": True}


def remove_network_dry_run(network_name: str, command: list[str]) -> dict[str, Any]:
    return {"networkName": network_name, "command": command, "dryRun": True}


def remove_container_result(
    container_name: str,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    result = run_command(command, cwd=cwd, timeout=timeout)
    return {
        "containerName": container_name,
        "removed": result["returncode"] == 0,
        "command": result,
    }


def remove_network_result(
    network_name: str,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    result = run_command(command, cwd=cwd, timeout=timeout)
    return {
        "networkName": network_name,
        "removed": result["returncode"] == 0,
        "command": result,
    }


def remove_unmanaged_compose_container(
    container_name: str,
    *,
    project_name: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any] | None:
    command = remove_container_command(container_name)
    if dry_run:
        return remove_container_dry_run(container_name, command)

    inspect_data = docker_inspect_container(container_name, cwd=cwd, timeout=timeout)
    if container_managed_by_project(inspect_data, project_name):
        return None
    if inspect_data is None:
        return None
    return remove_container_result(container_name, command, cwd=cwd, timeout=timeout)


def remove_unmanaged_compose_network(
    network_name: str,
    *,
    project_name: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any] | None:
    command = remove_network_command(network_name)
    if dry_run:
        return remove_network_dry_run(network_name, command)

    inspect_data = docker_inspect_network(network_name, cwd=cwd, timeout=timeout)
    if network_managed_by_project(inspect_data, project_name):
        return None
    if inspect_data is None:
        return None
    return remove_network_result(network_name, command, cwd=cwd, timeout=timeout)
