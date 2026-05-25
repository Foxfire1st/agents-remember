"""Shared provider lifecycle utilities."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.context import ContextProviderError


def runtime_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def subprocess_env(env: dict[str, str] | None = None) -> dict[str, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    merged_env["PYTHONUTF8"] = "1"
    merged_env["PYTHONIOENCODING"] = "utf-8"
    return merged_env


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def default_coordination_root() -> Path:
    """Infer the installed coordination root from this script's location."""

    return Path(__file__).resolve().parents[1]


def process_namespace_warning() -> str | None:
    if os.name == "nt":
        return None

    try:
        init_cmdline = (
            Path("/proc/1/cmdline")
            .read_bytes()
            .replace(b"\x00", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        )
    except OSError:
        return None

    if "--die-with-parent" not in init_cmdline:
        return None

    supervisor = init_cmdline.split(maxsplit=1)[0] if init_cmdline else "PID 1"
    return (
        f"current PID namespace is supervised by {supervisor} with --die-with-parent; "
        "background provider processes started here may be killed when this command exits, "
        "and host-side provider PIDs may not be visible from this namespace"
    )


def process_namespace_status() -> dict[str, Any]:
    warning = process_namespace_warning()
    return {
        "durableForDaemons": warning is None,
        "warning": warning,
    }


def require_durable_process_namespace(action: str) -> None:
    warning = process_namespace_warning()
    if warning is None:
        return
    raise ContextProviderError(
        f"{action} manages long-running provider processes and must run outside this ephemeral process namespace: "
        f"{warning}. Run the lifecycle command from a normal host terminal or another durable host execution context."
    )


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 60,
    allow_timeout: bool = False,
) -> dict[str, Any]:
    merged_env = subprocess_env(env)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=merged_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        if not allow_timeout:
            raise
        return timeout_command_result(command, cwd, started, timeout, error)
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timedOut": False,
    }


def timeout_command_result(
    command: list[str],
    cwd: Path,
    started: float,
    timeout: int,
    error: subprocess.TimeoutExpired,
) -> dict[str, Any]:
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": None,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": timeout_stream_text(error.stdout),
        "stderr": timeout_stream_text(error.stderr),
        "timedOut": True,
        "timeoutSeconds": timeout,
    }


def timeout_stream_text(stream: str | bytes | None) -> str:
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream or ""


def run_foreground_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    merged_env = subprocess_env(env)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=merged_env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
    }


def popen_detached_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdout: Any = subprocess.DEVNULL,
    stderr: Any = subprocess.DEVNULL,
) -> subprocess.Popen[bytes]:
    """Start a long-running command without owning its lifetime."""

    merged_env = subprocess_env(env)
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "DETACHED_PROCESS", 0)
        popen_kwargs["creationflags"] |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=merged_env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        **popen_kwargs,
    )


def host_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def allocate_host_port(host: str, configured: Any, default: int) -> int:
    text = str(configured) if configured is not None else "auto"
    if text == "auto":
        return allocate_auto_host_port(host, default)

    port = parse_configured_host_port(configured, text)
    require_host_port_available(host, port)
    return port


def allocate_auto_host_port(host: str, default: int) -> int:
    if host_port_available(host, default):
        return default
    return allocate_ephemeral_host_port(host)


def allocate_ephemeral_host_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def parse_configured_host_port(configured: Any, text: str) -> int:
    try:
        return int(text)
    except ValueError as error:
        raise ContextProviderError(f"invalid host port: {configured}") from error


def require_host_port_available(host: str, port: int) -> None:
    if not host_port_available(host, port):
        raise ContextProviderError(f"configured host port is already in use: {host}:{port}")


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        return windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def windows_process_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access denied means the PID exists but cannot be queried by this
        # process token. Other OpenProcess failures usually mean no process.
        return ctypes.get_last_error() == 5
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def docker_command() -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise ContextProviderError("docker command not found")
    return executable


def python_executable(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def process_cmdline(pid: int) -> str:
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    if not proc_cmdline.exists():
        return ""
    try:
        return (
            proc_cmdline.read_bytes()
            .replace(b"\x00", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        )
    except OSError:
        return ""


def render(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
        return

    status = "ok" if data.get("ok") else "needs-attention"
    print(f"{data['provider']} {data['action']}: {status}")
    for key, value in data.items():
        render_plain_field(key, value)


def render_plain_field(key: str, value: Any) -> None:
    if key in {"provider", "action", "ok"}:
        return
    if isinstance(value, (dict, list)):
        print(f"{key}: {json.dumps(value, sort_keys=True)}")
        return
    print(f"{key}: {value}")


def render_captured_command_output(data: dict[str, Any]) -> bool:
    command = data.get("command")
    if not isinstance(command, dict):
        return False

    print_optional_stream(command.get("stdout") or "")
    print_optional_stream(command.get("stderr") or "", stderr=True)
    return True


def print_optional_stream(text: str, *, stderr: bool = False) -> None:
    if not text:
        return
    output = sys.stderr if stderr else sys.stdout
    print(text, end="" if text.endswith("\n") else "\n", file=output)


def cgc_run_api_payload(data: dict[str, Any]) -> dict[str, Any]:
    command = data.get("command")
    if not isinstance(command, dict):
        return data

    payload: dict[str, Any] = {
        key: data[key]
        for key in ("provider", "action", "ok", "repoId", "workspace", "dryRun")
        if key in data
    }
    payload.update(command_metadata(command))
    payload["outputLines"] = command_output_lines(command)
    return payload


def command_metadata(command: dict[str, Any]) -> dict[str, Any]:
    return {key: command[key] for key in ("returncode", "durationSeconds") if key in command}


def command_output_lines(command: dict[str, Any]) -> list[str]:
    return [
        line
        for stream_name in ("stdout", "stderr")
        for line in str(command.get(stream_name) or "").splitlines()
    ]


def render_cgc_run_result(data: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "lifecycle_json", False):
        render(cgc_run_api_payload(data), as_json=True)
        return True
    if not getattr(args, "dry_run", False):
        return render_captured_command_output(data)
    return False


def render_grepai_run_result(data: dict[str, Any], args: argparse.Namespace) -> bool:
    return render_cgc_run_result(data, args)


def cgc_settings_from_file(
    coordination_root: Path, settings_path: Path | None
) -> tuple[Path, dict[str, Any]]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    provider = data.get("contextProviders", {}).get("providers", {}).get("codegraphcontext-code")
    if not isinstance(provider, dict):
        raise ContextProviderError(
            f"settings file does not define contextProviders.providers.codegraphcontext-code: {path}"
        )
    return path, provider


def context_provider_enabled(
    coordination_root: Path, settings_path: Path | None, provider_id: str
) -> tuple[Path, bool]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    return path, provider_enabled(data, provider_id)


def provider_enabled(data: dict[str, Any], provider_id: str) -> bool:
    context = data.get("contextProviders")
    if not context_providers_enabled(context):
        return False
    providers = context.get("providers")
    if not isinstance(providers, dict):
        return False
    provider = providers.get(provider_id)
    return isinstance(provider, dict) and provider.get("enabled") is True


def context_providers_enabled(context: Any) -> bool:
    return isinstance(context, dict) and context.get("enabled") is True


def grepai_settings_from_file(
    coordination_root: Path, settings_path: Path | None
) -> tuple[Path, dict[str, Any]]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    provider = data.get("contextProviders", {}).get("providers", {}).get("grepai-memory")
    return path, provider if isinstance(provider, dict) else {}


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
