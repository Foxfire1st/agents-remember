"""Observe the build answering a dashboard port and its actual listening process."""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import ConfigError
from agents_remember.models.build_currentness import BuildCurrentnessPayload
from agents_remember.models.core import ServingBuildPayload
from agents_remember.serving.daemon_processes import listening_dashboard_pid, process_arguments


@dataclass(frozen=True)
class DashboardIdentity:
    pid: int
    build: ServingBuildPayload | None
    config_path: str
    currentness: BuildCurrentnessPayload | None


def observe_dashboard(host: str, port: int) -> DashboardIdentity | None:
    """Prove socket ownership, and bind an available HTTP build to that owner.

    Process ownership comes from the platform's native socket and argv inspection.
    An inaccessible owner is unknown, never permission to signal a saved process id.
    """
    connect_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    address = f"[{connect_host}]" if ":" in connect_host else connect_host
    try:
        initial_pid = listening_dashboard_pid(connect_host, port)
        if initial_pid is None:
            return None
        payload = _dashboard_payload(address, port)
        pid = listening_dashboard_pid(connect_host, port)
        if pid is None or pid != initial_pid:
            raise ConfigError(
                "the answering dashboard and socket owner do not prove the same process"
            )
        if payload is not None and payload.get("servingProcessId", pid) != pid:
            raise ConfigError(
                "the answering dashboard and socket owner do not prove the same process"
            )
        words = process_arguments(pid)
        config_path = ""
        if b"--config" in words:
            config_path = words[words.index(b"--config") + 1].decode()
        build = (
            ServingBuildPayload.model_validate(payload["servingBuild"])
            if payload is not None
            else None
        )
        reported = payload.get("buildCurrentness") if payload is not None else None
        currentness = (
            BuildCurrentnessPayload.model_validate(reported) if reported is not None else None
        )
        return DashboardIdentity(pid, build, config_path, currentness)
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        urllib.error.URLError,
        subprocess.TimeoutExpired,
    ) as error:
        raise ConfigError(f"dashboard identity is unknown: {error}") from error


def _dashboard_payload(address: str, port: int) -> dict[str, object] | None:
    """A starting or hung HTTP handler does not erase native process ownership."""
    try:
        with urllib.request.urlopen(
            f"http://{address}:{port}/api/state?status=true", timeout=5
        ) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("dashboard state is not an object")
        return payload
    except TimeoutError:
        return None
    except urllib.error.HTTPError as error:
        if error.code == 503:
            error.close()
            return None
        raise
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            return None
        raise


def dashboard_coordination_root(identity: DashboardIdentity) -> Path:
    """Read the observed process's settings root; missing ownership authorizes no signal."""
    if not identity.config_path:
        raise ConfigError(
            f"dashboard pid {identity.pid} has no observable --config; ownership is unknown"
        )
    try:
        data = json.loads(Path(identity.config_path).read_text(encoding="utf-8"))
        root = data.get("coordinationRoot") if isinstance(data, dict) else None
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise ConfigError(
                f"dashboard pid {identity.pid} settings {identity.config_path} do not declare an absolute coordinationRoot; ownership is unknown"
            )
        return Path(root).resolve()
    except (OSError, ValueError) as error:
        raise ConfigError(
            f"dashboard pid {identity.pid} settings ownership is unknown: {error}"
        ) from error


def require_dashboard_root(identity: DashboardIdentity, expected_root: Path) -> None:
    actual_root = dashboard_coordination_root(identity)
    if actual_root != expected_root.resolve():
        raise ConfigError(
            f"dashboard pid {identity.pid} uses {identity.config_path} for coordination root {actual_root}, "
            f"not the selected root {expected_root}; no signal is authorized. "
            "Use that root's --config to manage it, or choose a different dashboard port for the selected root."
        )
