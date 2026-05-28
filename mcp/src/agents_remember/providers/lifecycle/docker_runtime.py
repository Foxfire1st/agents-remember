"""Docker adapter helpers for provider lifecycle commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.lifecycle.command_runner import run_command


def docker_command() -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise ContextProviderError("docker command not found")
    return executable


def docker_inspect_container(
    container_name: str, *, cwd: Path, timeout: int
) -> dict[str, Any] | None:
    result = run_command([docker_command(), "inspect", container_name], cwd=cwd, timeout=timeout)
    if result["returncode"] != 0:
        return None
    try:
        data = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or not data:
        return None
    return data[0]


def docker_container_running(inspect_data: dict[str, Any] | None) -> bool:
    if not inspect_data:
        return False
    state = inspect_data.get("State", {})
    return bool(isinstance(state, dict) and state.get("Running"))


def docker_container_state_summary(inspect_data: dict[str, Any] | None) -> dict[str, Any]:
    if not inspect_data:
        return {
            "containerState": "missing",
            "running": False,
            "startedAt": None,
            "uptimeSeconds": None,
            "health": None,
        }
    state = inspect_data.get("State", {})
    state = state if isinstance(state, dict) else {}
    started_at = str(state.get("StartedAt") or "")
    started = parse_docker_timestamp(started_at)
    running = bool(state.get("Running"))
    return {
        "containerState": str(state.get("Status") or ("running" if running else "unknown")),
        "running": running,
        "startedAt": started.isoformat() if started else None,
        "uptimeSeconds": docker_container_uptime_seconds(started) if running else None,
        "health": docker_container_health(state),
    }


def docker_container_uptime_seconds(started: datetime | None) -> int | None:
    if started is None:
        return None
    return max(0, int((datetime.now(UTC) - started).total_seconds()))


def docker_container_health(state: dict[str, Any]) -> str | None:
    health = state.get("Health")
    if not isinstance(health, dict):
        return None
    status = health.get("Status")
    return str(status) if status else None


def parse_docker_timestamp(value: str) -> datetime | None:
    if not value or value.startswith("0001-"):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    if "." in text:
        prefix, suffix = text.split(".", 1)
        fraction = suffix
        timezone = ""
        for marker in ("+", "-"):
            marker_index = suffix.find(marker)
            if marker_index > 0:
                fraction = suffix[:marker_index]
                timezone = suffix[marker_index:]
                break
        text = f"{prefix}.{fraction[:6].ljust(6, '0')}{timezone}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def docker_repo_digest(image: str, *, cwd: Path, timeout: int) -> str | None:
    result = run_command(
        [docker_command(), "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
        cwd=cwd,
        timeout=timeout,
    )
    if result["returncode"] != 0:
        return None
    try:
        digests = json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None
    if isinstance(digests, list) and digests:
        return str(digests[0])
    return None


def docker_container_port(
    inspect_data: dict[str, Any], container_port: int
) -> tuple[str, int] | None:
    ports = inspect_data.get("NetworkSettings", {}).get("Ports", {})
    mapping = ports.get(f"{container_port}/tcp") if isinstance(ports, dict) else None
    first = first_port_mapping(mapping)
    if not isinstance(first, dict):
        return None
    host_ip = first.get("HostIp") or "127.0.0.1"
    host_port = first.get("HostPort")
    if host_port is None:
        return None
    return str(host_ip), int(host_port)


def first_port_mapping(mapping: Any) -> Any:
    return mapping[0] if isinstance(mapping, list) and mapping else None


def docker_data_mount_source(
    inspect_data: dict[str, Any] | None, destination: str = "/data"
) -> str | None:
    if not inspect_data:
        return None
    mounts = inspect_data.get("Mounts", [])
    if not isinstance(mounts, list):
        return None
    for mount in mounts:
        source = mount_source_for_destination(mount, destination)
        if source:
            return source
    return None


def mount_source_for_destination(mount: Any, destination: str) -> str | None:
    if not isinstance(mount, dict) or mount.get("Destination") != destination:
        return None
    source = mount.get("Source")
    return str(source) if source else None


def docker_host_path_matches(actual: str | None, expected: Path) -> bool:
    if not actual:
        return False

    def normalize(value: str) -> str:
        text = value.replace("\\", "/").rstrip("/")
        match = re.match(r"^/run/desktop/mnt/host/([a-zA-Z])/(.*)$", text)
        if match:
            text = f"{match.group(1)}:/{match.group(2)}"
        return os.path.normcase(text)

    return normalize(actual) == normalize(str(expected.resolve()))


def docker_image_exists(image: str, *, cwd: Path, timeout: int) -> bool:
    result = run_command([docker_command(), "image", "inspect", image], cwd=cwd, timeout=timeout)
    return result["returncode"] == 0


def docker_ensure_network(name: str, *, cwd: Path, timeout: int, dry_run: bool) -> dict[str, Any]:
    inspect_command = [docker_command(), "network", "inspect", name]
    create_command = [docker_command(), "network", "create", name]
    if dry_run:
        return {
            "ok": True,
            "name": name,
            "dryRun": True,
            "commands": [inspect_command, create_command],
        }

    inspect = run_command(inspect_command, cwd=cwd, timeout=timeout)
    if inspect["returncode"] == 0:
        return {"ok": True, "name": name, "alreadyExists": True, "inspect": inspect}
    create = run_command(create_command, cwd=cwd, timeout=timeout)
    return {
        "ok": create["returncode"] == 0,
        "name": name,
        "inspect": inspect,
        "create": create,
    }


def docker_container_networks(inspect_data: dict[str, Any] | None) -> set[str]:
    if not inspect_data:
        return set()
    networks = inspect_data.get("NetworkSettings", {}).get("Networks", {})
    if not isinstance(networks, dict):
        return set()
    return {str(name) for name in networks}


def docker_ensure_container_network(
    container_name: str,
    network_name: str,
    *,
    inspect_data: dict[str, Any] | None,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    command = [docker_command(), "network", "connect", network_name, container_name]
    if inspect_data and network_name in docker_container_networks(inspect_data):
        return {"ok": True, "containerName": container_name, "network": network_name}
    if dry_run:
        return {
            "ok": True,
            "containerName": container_name,
            "network": network_name,
            "dryRun": True,
            "command": command,
        }
    result = run_command(command, cwd=cwd, timeout=timeout)
    already_connected = "already exists" in (
        f"{result.get('stdout', '')}\n{result.get('stderr', '')}".lower()
    )
    return {
        "ok": result["returncode"] == 0 or already_connected,
        "containerName": container_name,
        "network": network_name,
        "command": result,
    }


def docker_wait_for_ping(container_name: str, *, cwd: Path, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_result = docker_ping_result(container_name, cwd=cwd)
        if docker_ping_ok(last_result):
            return last_result
        time.sleep(2)
    raise_docker_ping_timeout(last_result)


def docker_ping_result(container_name: str, *, cwd: Path) -> dict[str, Any]:
    return run_command(
        [docker_command(), "exec", container_name, "redis-cli", "ping"],
        cwd=cwd,
        timeout=15,
    )


def docker_ping_ok(result: dict[str, Any]) -> bool:
    return result["returncode"] == 0 and "PONG" in result["stdout"]


def raise_docker_ping_timeout(last_result: dict[str, Any] | None) -> None:
    if last_result is None:
        raise ContextProviderError("timed out waiting for FalkorDB container health check")
    raise ContextProviderError(
        f"FalkorDB health check failed: {last_result['stderr'] or last_result['stdout']}"
    )
