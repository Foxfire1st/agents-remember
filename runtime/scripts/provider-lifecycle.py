#!/usr/bin/env python3
"""Manage optional Agents Remember context providers."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


def runtime_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def install_shared_import_path() -> None:
    shared_root = runtime_root_from_script() / "skills" / "U-01-core-skills" / "_shared"
    sys.path.insert(0, str(shared_root))


install_shared_import_path()

from agents_remember.context_providers import (  # noqa: E402
    CGC_CGCIGNORE_PATCH_ID,
    CGC_DELETE_PATCH_ID,
    CGC_DISCOVERY_EXTENSIONS_PATCH_ID,
    CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID,
    CGC_VIZ_CLI_ROUTE_PATCH_ID,
    CGC_VIZ_REPO_QUERY_PATCH_ID,
    CGC_VIZ_SERVER_ROUTE_PATCH_ID,
    GREPAI_PIN,
    GREPAI_PROVIDER,
    GREPAI_POSTGRES_BACKEND_ID,
    GREPAI_POSTGRES_CONTAINER_NAME,
    GREPAI_POSTGRES_DEFAULT_HOST,
    GREPAI_POSTGRES_DEFAULT_PORT,
    GrepaiMemoryRoot,
    ContextProviderError,
    apply_cgc_cgcignore_patch,
    apply_cgc_delete_patch,
    apply_cgc_discovery_extensions_patch,
    apply_cgc_graph_builder_extensions_patch,
    apply_cgc_viz_cli_route_patch,
    apply_cgc_viz_repo_query_patch,
    apply_cgc_viz_server_route_patch,
    cgc_cgcignore_patch_applied,
    cgc_delete_patch_applied,
    cgc_discovery_extensions_patch_applied,
    cgc_graph_builder_extensions_patch_applied,
    cgc_viz_cli_route_patch_applied,
    cgc_viz_repo_query_patch_applied,
    cgc_viz_server_route_patch_applied,
    assert_no_grepai_root_provider_artifacts,
    cleanup_cgc_runtime_artifacts,
    cgc_runtime_layout,
    cgc_runtime_layout_from_provider_settings,
    ensure_cgc_runtime_layout,
    ensure_grepai_runtime_layout,
    ensure_grepai_requirements_file,
    expand_template,
    file_sha256,
    find_cgc_cli_helpers_module,
    find_cgc_cgcignore_module,
    find_cgc_discovery_module,
    find_cgc_graph_builder_module,
    find_cgc_viz_server_module,
    find_cgc_writer_module,
    grepai_runtime_layout,
    grepai_runtime_layout_from_provider_settings,
    provider_requirements_file,
    read_provider_pin,
    remove_grepai_root_provider_artifacts,
    source_provider_artifacts,
    stable_provider_id,
    sync_grepai_index_roots,
    write_grepai_workspace_config,
    write_provider_state,
)


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
        stdout = error.stdout
        stderr = error.stderr
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {
            "command": command,
            "cwd": cwd.as_posix(),
            "returncode": None,
            "durationSeconds": round(time.monotonic() - started, 3),
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timedOut": True,
            "timeoutSeconds": timeout,
        }
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timedOut": False,
    }


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
        check=False,
    )
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
    }


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
        if host_port_available(host, default):
            return default
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])

    try:
        port = int(text)
    except ValueError as error:
        raise ContextProviderError(f"invalid host port: {configured}") from error
    if not host_port_available(host, port):
        raise ContextProviderError(f"configured host port is already in use: {host}:{port}")
    return port


def process_alive(pid: int) -> bool:
    if os.name == "nt":
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
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def docker_command() -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise ContextProviderError("docker command not found")
    return executable


def python_executable(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def detect_release_platform() -> tuple[str, str]:
    system = platform.system().lower()
    if system == "darwin":
        os_name = "darwin"
    elif system == "linux":
        os_name = "linux"
    elif system == "windows":
        os_name = "windows"
    else:
        raise ContextProviderError(f"unsupported operating system for provider install: {platform.system()}")

    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise ContextProviderError(f"unsupported CPU architecture for provider install: {platform.machine()}")
    return os_name, arch


def download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "agents-remember-provider-lifecycle"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def download_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "agents-remember-provider-lifecycle"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def checksum_for_asset(checksums_text: str, asset_name: str) -> str:
    for line in checksums_text.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1] == asset_name:
            return parts[0]
    raise ContextProviderError(f"checksum for {asset_name} was not found in release checksums.txt")


def extract_binary_from_archive(archive_path: Path, destination: Path, binary_names: set[str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                member
                for member in archive.infolist()
                if not member.is_dir() and Path(member.filename).name in binary_names
            ]
            if len(members) != 1:
                raise ContextProviderError(f"expected one provider binary in {archive_path}, found {len(members)}")
            with archive.open(members[0]) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name in binary_names
            ]
            if len(members) != 1:
                raise ContextProviderError(f"expected one provider binary in {archive_path}, found {len(members)}")
            source = archive.extractfile(members[0])
            if source is None:
                raise ContextProviderError(f"could not extract provider binary from {archive_path}")
            with source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    destination.chmod(destination.stat().st_mode | 0o755)


def process_cmdline(pid: int) -> str:
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    if not proc_cmdline.exists():
        return ""
    try:
        return proc_cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def render(data: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
        return

    status = "ok" if data.get("ok") else "needs-attention"
    print(f"{data['provider']} {data['action']}: {status}")
    for key, value in data.items():
        if key in {"provider", "action", "ok"}:
            continue
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, sort_keys=True)}")
        else:
            print(f"{key}: {value}")


def render_captured_command_output(data: dict[str, Any]) -> bool:
    command = data.get("command")
    if not isinstance(command, dict):
        return False

    stdout = command.get("stdout") or ""
    stderr = command.get("stderr") or ""
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
    return True


def cgc_run_api_payload(data: dict[str, Any]) -> dict[str, Any]:
    command = data.get("command")
    if not isinstance(command, dict):
        return data

    payload: dict[str, Any] = {
        key: data[key]
        for key in ("provider", "action", "ok", "repoId", "workspace", "dryRun")
        if key in data
    }
    for key in ("returncode", "durationSeconds"):
        if key in command:
            payload[key] = command[key]

    output_lines: list[str] = []
    for stream_name in ("stdout", "stderr"):
        stream_text = command.get(stream_name) or ""
        if stream_text:
            output_lines.extend(str(stream_text).splitlines())
    payload["outputLines"] = output_lines
    return payload


def render_cgc_run_result(data: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "lifecycle_json", False):
        render(cgc_run_api_payload(data), as_json=True)
        return True
    if not getattr(args, "dry_run", False):
        return render_captured_command_output(data)
    return False


def render_grepai_run_result(data: dict[str, Any], args: argparse.Namespace) -> bool:
    return render_cgc_run_result(data, args)


def cgc_uses_settings(args: argparse.Namespace) -> bool:
    return getattr(args, "from_settings", None) is not None or getattr(args, "code_repo_root", None) is None


def cgc_layout_from_args(args: argparse.Namespace):
    if cgc_uses_settings(args):
        _, provider_settings = cgc_settings_from_file(args.coordination_root, getattr(args, "from_settings", None))
        root_settings = cgc_root_from_settings(provider_settings, args.repo_id)
        return cgc_runtime_layout_from_provider_settings(
            coordination_root=args.coordination_root,
            provider_settings=provider_settings,
            root_settings=root_settings,
        )
    if not args.repo_id or not args.code_repo_root:
        raise ContextProviderError("CGC manual override commands require --repo-id and --code-repo-root")
    return cgc_runtime_layout(
        coordination_root=args.coordination_root,
        repo_id=args.repo_id,
        code_repo_root=args.code_repo_root,
    )


def cgc_settings_from_file(coordination_root: Path, settings_path: Path | None) -> tuple[Path, dict[str, Any]]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    provider = (
        data.get("contextProviders", {})
        .get("providers", {})
        .get("codegraphcontext-code")
    )
    if not isinstance(provider, dict):
        raise ContextProviderError(f"settings file does not define contextProviders.providers.codegraphcontext-code: {path}")
    return path, provider


def context_provider_enabled(coordination_root: Path, settings_path: Path | None, provider_id: str) -> tuple[Path, bool]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    context = data.get("contextProviders")
    if not isinstance(context, dict) or context.get("enabled") is not True:
        return path, False
    providers = context.get("providers")
    if not isinstance(providers, dict):
        return path, False
    provider = providers.get(provider_id)
    return path, isinstance(provider, dict) and provider.get("enabled") is True


def grepai_settings_from_file(coordination_root: Path, settings_path: Path | None) -> tuple[Path, dict[str, Any]]:
    path = settings_path or coordination_root / "system" / "settings.json"
    data = read_json(path)
    provider = (
        data.get("contextProviders", {})
        .get("providers", {})
        .get("grepai-memory")
    )
    return path, provider if isinstance(provider, dict) else {}


def cgc_root_from_settings(provider_settings: dict[str, Any], repo_id: str | None) -> dict[str, Any]:
    roots = provider_settings.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ContextProviderError("codegraphcontext-code.roots must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    for root in roots:
        if not isinstance(root, dict) or "repoId" not in root or "path" not in root:
            raise ContextProviderError("each codegraphcontext-code root must define repoId and path")
        normalized.append(root)
    if repo_id is None:
        if len(normalized) != 1:
            raise ContextProviderError("select one configured CGC root with --repo-id")
        return normalized[0]
    stable = stable_provider_id(repo_id)
    for root in normalized:
        if stable_provider_id(str(root["repoId"])) == stable:
            return root
    raise ContextProviderError(f"repo id is not configured in codegraphcontext-code.roots: {repo_id}")


def cgc_all_layouts_from_settings(args: argparse.Namespace) -> tuple[Path, dict[str, Any], list[Any]]:
    settings_path, provider_settings = cgc_settings_from_file(args.coordination_root, getattr(args, "from_settings", None))
    repo_id = getattr(args, "repo_id", None)
    if repo_id:
        selected = [cgc_root_from_settings(provider_settings, repo_id)]
    else:
        roots = provider_settings.get("roots")
        if not isinstance(roots, list) or not roots:
            raise ContextProviderError("codegraphcontext-code.roots must be a non-empty array")
        selected = []
        for root in roots:
            if not isinstance(root, dict) or "repoId" not in root or "path" not in root:
                raise ContextProviderError("each codegraphcontext-code root must define repoId and path")
            selected.append(root)
    layouts = [
        cgc_runtime_layout_from_provider_settings(
            coordination_root=args.coordination_root,
            provider_settings=provider_settings,
            root_settings=root_settings,
        )
        for root_settings in selected
    ]
    return settings_path, provider_settings, layouts


def cgc_backend_settings(provider_settings: dict[str, Any], layout: Any) -> dict[str, Any]:
    backend_settings = provider_settings.get("backend", {})
    if not isinstance(backend_settings, dict):
        backend_settings = {}
    ports = backend_settings.get("ports", {})
    if not isinstance(ports, dict):
        ports = {}
    falkordb_port = ports.get("falkordb", {})
    browser_port = ports.get("browser", {})
    if not isinstance(falkordb_port, dict):
        falkordb_port = {}
    if not isinstance(browser_port, dict):
        browser_port = {}

    image = str(backend_settings.get("image", "")).strip()
    if not image or "<" in image or ">" in image:
        raise ContextProviderError("codegraphcontext backend.image must be a concrete falkordb/falkordb tag or digest")

    base_variables = {
        "coordination_root": layout.coordination_root.as_posix(),
        "runtimeRoot": layout.runtime_root.parent.as_posix(),
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
    }
    image_lock_file = backend_settings.get("imageLockFile")
    if image_lock_file:
        image_lock_path = Path(expand_template(str(image_lock_file), base_variables)).resolve()
    else:
        image_lock_path = layout.coordination_root / "providers" / "requirements" / "codegraphcontext-falkordb-docker.lock"

    falkordb_host = str(falkordb_port.get("bindHost", "127.0.0.1"))
    browser_host = str(browser_port.get("bindHost", "127.0.0.1"))
    return {
        "id": backend_settings.get("id", "codegraphcontext-falkordb"),
        "type": backend_settings.get("type", "falkordb-remote"),
        "mode": backend_settings.get("mode", "docker"),
        "image": image,
        "imageLockFile": image_lock_path,
        "containerName": str(backend_settings.get("containerName", "ar-cgc-falkordb")),
        "falkordbHost": falkordb_host,
        "falkordbHostPort": falkordb_port.get("hostPort", "auto"),
        "falkordbContainerPort": int(falkordb_port.get("containerPort", 6379)),
        "browserHost": browser_host,
        "browserHostPort": browser_port.get("hostPort", "auto"),
        "browserContainerPort": int(browser_port.get("containerPort", 3000)),
    }


def docker_inspect_container(container_name: str, *, cwd: Path, timeout: int) -> dict[str, Any] | None:
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


def docker_container_port(inspect_data: dict[str, Any], container_port: int) -> tuple[str, int] | None:
    ports = inspect_data.get("NetworkSettings", {}).get("Ports", {})
    mapping = ports.get(f"{container_port}/tcp") if isinstance(ports, dict) else None
    if not isinstance(mapping, list) or not mapping:
        return None
    first = mapping[0]
    if not isinstance(first, dict):
        return None
    host_ip = first.get("HostIp") or "127.0.0.1"
    host_port = first.get("HostPort")
    if host_port is None:
        return None
    return str(host_ip), int(host_port)


def docker_data_mount_source(inspect_data: dict[str, Any] | None, destination: str = "/data") -> str | None:
    if not inspect_data:
        return None
    mounts = inspect_data.get("Mounts", [])
    if not isinstance(mounts, list):
        return None
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("Destination") == destination:
            source = mount.get("Source")
            return str(source) if source else None
    return None


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


def docker_wait_for_ping(container_name: str, *, cwd: Path, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_result = run_command(
            [docker_command(), "exec", container_name, "redis-cli", "ping"],
            cwd=cwd,
            timeout=15,
        )
        if last_result["returncode"] == 0 and "PONG" in last_result["stdout"]:
            return last_result
        time.sleep(2)
    if last_result is None:
        raise ContextProviderError("timed out waiting for FalkorDB container health check")
    raise ContextProviderError(f"FalkorDB health check failed: {last_result['stderr'] or last_result['stdout']}")


def cgc_scoped_args(args: argparse.Namespace, repo_id: str, action: str | None = None) -> argparse.Namespace:
    """Return a copy of args scoped to one configured CGC repository."""

    scoped = argparse.Namespace(**vars(args))
    scoped.repo_id = repo_id
    if action is not None:
        scoped.action = action
    return scoped


def cgc_apply_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    backend_settings = provider_settings.get("backend", {})
    cleanup = cleanup_cgc_runtime_artifacts(layouts, dry_run=args.dry_run)
    instances: list[dict[str, Any]] = []
    for layout in layouts:
        if not args.dry_run:
            ensure_cgc_runtime_layout(layout)
            state = read_json(layout.state_file)
            state.update(
                {
                    "provider": "codegraphcontext",
                    "repoId": layout.repo_id,
                    "codeRepoRoot": layout.code_repo_root.as_posix(),
                    "runtimeRoot": layout.runtime_root.as_posix(),
                    "stateFile": layout.state_file.as_posix(),
                    "backendStateFile": layout.backend_state_file.as_posix(),
                    "lastAction": "apply-settings",
                    "settingsFile": settings_path.as_posix(),
                    "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            write_json(layout.state_file, state)
        instances.append(
            {
                "repoId": layout.repo_id,
                "codeRepoRoot": layout.code_repo_root.as_posix(),
                "runtimeRoot": layout.runtime_root.as_posix(),
                "stateFile": layout.state_file.as_posix(),
                "watchLog": layout.watch_log_file.as_posix(),
                "graphName": layout.env()["FALKORDB_GRAPH_NAME"],
            }
        )

    if layouts and not args.dry_run:
        backend_state = read_json(layouts[0].backend_state_file)
        backend_state.setdefault("provider", "codegraphcontext")
        backend_state.setdefault("backend", {})
        backend_state["backend"].update(
            {
                "id": backend_settings.get("id", "codegraphcontext-falkordb"),
                "type": backend_settings.get("type", "falkordb-remote"),
                "mode": backend_settings.get("mode", "docker"),
                "image": backend_settings.get("image"),
                "imageLockFile": backend_settings.get("imageLockFile"),
                "containerName": backend_settings.get("containerName"),
                "runtimeRoot": layouts[0].backend_root.as_posix(),
                "dataRoot": layouts[0].backend_data_root.as_posix(),
                "status": backend_state.get("backend", {}).get("status", "configured"),
            }
        )
        backend_state["settingsFile"] = settings_path.as_posix()
        backend_state["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json(layouts[0].backend_state_file, backend_state)

    return {
        "provider": "codegraphcontext",
        "action": "apply-settings",
        "ok": True,
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "backendRuntimeRoot": layouts[0].backend_root.as_posix() if layouts else None,
        "backendDataRoot": layouts[0].backend_data_root.as_posix() if layouts else None,
        "removedArtifacts": cleanup,
        "instances": instances,
    }


def cgc_backend_status(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    if not layouts:
        raise ContextProviderError("codegraphcontext-code.roots must define at least one root")
    layout = layouts[0]
    backend = cgc_backend_settings(provider_settings, layout)
    state = read_json(layout.backend_state_file)
    inspect_data = None if args.dry_run else docker_inspect_container(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    running = docker_container_running(inspect_data)
    ping = None
    if running and not args.dry_run:
        try:
            ping = run_command(
                [docker_command(), "exec", backend["containerName"], "redis-cli", "ping"],
                cwd=layout.coordination_root,
                timeout=15,
            )
        except (ContextProviderError, subprocess.TimeoutExpired, OSError) as error:
            ping = {"returncode": 1, "stderr": str(error), "stdout": ""}

    falkordb_mapping = docker_container_port(inspect_data, backend["falkordbContainerPort"]) if inspect_data else None
    browser_mapping = docker_container_port(inspect_data, backend["browserContainerPort"]) if inspect_data else None
    actual_data_mount = docker_data_mount_source(inspect_data)
    data_mount_matches = docker_host_path_matches(actual_data_mount, layout.backend_data_root) if inspect_data else False
    falkordb_host, falkordb_port = falkordb_mapping or (
        state.get("backend", {}).get("ports", {}).get("falkordb", {}).get("bindHost", backend["falkordbHost"]),
        state.get("backend", {}).get("ports", {}).get("falkordb", {}).get("hostPort", backend["falkordbHostPort"]),
    )
    browser_host, browser_port = browser_mapping or (
        state.get("backend", {}).get("ports", {}).get("browser", {}).get("bindHost", backend["browserHost"]),
        state.get("backend", {}).get("ports", {}).get("browser", {}).get("hostPort", backend["browserHostPort"]),
    )
    return {
        "provider": "codegraphcontext",
        "action": "backend-status",
        "ok": bool(running) and data_mount_matches and (ping is None or ping.get("returncode") == 0),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "containerName": backend["containerName"],
        "image": backend["image"],
        "running": running,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "dataMount": {
            "expected": layout.backend_data_root.as_posix(),
            "actual": actual_data_mount,
            "matches": data_mount_matches,
        },
        "ports": {
            "falkordb": {"bindHost": falkordb_host, "hostPort": falkordb_port, "containerPort": backend["falkordbContainerPort"]},
            "browser": {"bindHost": browser_host, "hostPort": browser_port, "containerPort": backend["browserContainerPort"]},
        },
        "browserUrl": f"http://{browser_host}:{browser_port}" if str(browser_port) != "auto" else None,
        "ping": ping,
    }


def cgc_backend_start(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    if not layouts:
        raise ContextProviderError("codegraphcontext-code.roots must define at least one root")
    layout = layouts[0]
    ensure_cgc_runtime_layout(layout)
    backend = cgc_backend_settings(provider_settings, layout)
    if backend["type"] != "falkordb-remote" or backend["mode"] != "docker":
        raise ContextProviderError("managed CGC backend must be falkordb-remote docker")

    inspect_data = None if args.dry_run else docker_inspect_container(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    forced_remove_result = None
    if inspect_data and not docker_host_path_matches(docker_data_mount_source(inspect_data), layout.backend_data_root):
        if args.dry_run:
            inspect_data = None
        else:
            forced_remove_result = run_command(
                [docker_command(), "rm", "-f", backend["containerName"]],
                cwd=layout.coordination_root,
                timeout=args.timeout,
            )
            if forced_remove_result["returncode"] != 0:
                return {"provider": "codegraphcontext", "action": "backend-start", "ok": False, "command": forced_remove_result}
            inspect_data = None
    if inspect_data and docker_container_running(inspect_data):
        falkordb_host, falkordb_port = docker_container_port(inspect_data, backend["falkordbContainerPort"]) or (
            backend["falkordbHost"],
            backend["falkordbHostPort"],
        )
        browser_host, browser_port = docker_container_port(inspect_data, backend["browserContainerPort"]) or (
            backend["browserHost"],
            backend["browserHostPort"],
        )
        ping = docker_wait_for_ping(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
        image_digest = docker_repo_digest(backend["image"], cwd=layout.coordination_root, timeout=args.timeout)
        backend_state = cgc_backend_state(
            layout,
            backend,
            settings_path=settings_path,
            status="running",
            falkordb_host=str(falkordb_host),
            falkordb_port=int(falkordb_port),
            browser_host=str(browser_host),
            browser_port=int(browser_port),
            image_digest=image_digest,
            container_id=str(inspect_data.get("Id", "")),
        )
        write_json(layout.backend_state_file, backend_state)
        write_json(backend["imageLockFile"], backend_state["backend"]["imageLock"])
        return {
            "provider": "codegraphcontext",
            "action": "backend-start",
            "ok": True,
            "alreadyRunning": True,
            "containerName": backend["containerName"],
            "ports": backend_state["backend"]["ports"],
            "browserUrl": backend_state["backend"]["browserUrl"],
            "dataMount": {
                "expected": layout.backend_data_root.as_posix(),
                "actual": docker_data_mount_source(inspect_data),
                "matches": True,
            },
            "ping": ping,
        }

    if args.dry_run:
        configured_falkordb_port = backend["falkordbHostPort"]
        configured_browser_port = backend["browserHostPort"]
        falkordb_port = 6379 if str(configured_falkordb_port) == "auto" else int(configured_falkordb_port)
        browser_port = 3000 if str(configured_browser_port) == "auto" else int(configured_browser_port)
    else:
        falkordb_port = allocate_host_port(backend["falkordbHost"], backend["falkordbHostPort"], 6379)
        browser_port = allocate_host_port(backend["browserHost"], backend["browserHostPort"], 3000)
    volume_arg = f"{str(layout.backend_data_root)}:/data"
    run_command_line = [
        docker_command(),
        "run",
        "-d",
        "--name",
        backend["containerName"],
        "--restart",
        "unless-stopped",
        "-p",
        f"{backend['falkordbHost']}:{falkordb_port}:{backend['falkordbContainerPort']}",
        "-p",
        f"{backend['browserHost']}:{browser_port}:{backend['browserContainerPort']}",
        "-v",
        volume_arg,
        "-e",
        "REDIS_ARGS=--appendonly yes",
        backend["image"],
    ]
    commands = [[docker_command(), "pull", backend["image"]]]
    if inspect_data:
        commands.append([docker_command(), "rm", backend["containerName"]])
    commands.append(run_command_line)
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "backend-start",
            "ok": True,
            "dryRun": True,
            "settingsFile": settings_path.as_posix(),
            "commands": commands,
            "backendRuntimeRoot": layout.backend_root.as_posix(),
            "backendDataRoot": layout.backend_data_root.as_posix(),
            "ports": {
                "falkordb": {"bindHost": backend["falkordbHost"], "hostPort": falkordb_port, "containerPort": backend["falkordbContainerPort"]},
                "browser": {"bindHost": backend["browserHost"], "hostPort": browser_port, "containerPort": backend["browserContainerPort"]},
            },
            "browserUrl": f"http://{backend['browserHost']}:{browser_port}",
        }

    pull_result = run_command(commands[0], cwd=layout.coordination_root, timeout=args.timeout)
    if pull_result["returncode"] != 0:
        return {"provider": "codegraphcontext", "action": "backend-start", "ok": False, "command": pull_result}
    rm_result = None
    if inspect_data:
        rm_result = run_command(commands[1], cwd=layout.coordination_root, timeout=args.timeout)
        if rm_result["returncode"] != 0:
            return {"provider": "codegraphcontext", "action": "backend-start", "ok": False, "command": rm_result}
    run_result = run_command(run_command_line, cwd=layout.coordination_root, timeout=args.timeout)
    if run_result["returncode"] != 0:
        return {"provider": "codegraphcontext", "action": "backend-start", "ok": False, "command": run_result}
    ping = docker_wait_for_ping(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    inspect_data = docker_inspect_container(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    image_digest = docker_repo_digest(backend["image"], cwd=layout.coordination_root, timeout=args.timeout)
    backend_state = cgc_backend_state(
        layout,
        backend,
        settings_path=settings_path,
        status="running",
        falkordb_host=backend["falkordbHost"],
        falkordb_port=falkordb_port,
        browser_host=backend["browserHost"],
        browser_port=browser_port,
        image_digest=image_digest,
        container_id=str(inspect_data.get("Id", "")) if inspect_data else None,
    )
    write_json(layout.backend_state_file, backend_state)
    write_json(backend["imageLockFile"], backend_state["backend"]["imageLock"])
    return {
        "provider": "codegraphcontext",
        "action": "backend-start",
        "ok": True,
        "containerName": backend["containerName"],
        "ports": backend_state["backend"]["ports"],
        "browserUrl": backend_state["backend"]["browserUrl"],
        "commands": {"pull": pull_result, "remove": rm_result, "forcedRemove": forced_remove_result, "run": run_result},
        "ping": ping,
    }


def cgc_backend_state(
    layout: Any,
    backend: dict[str, Any],
    *,
    settings_path: Path,
    status: str,
    falkordb_host: str,
    falkordb_port: int,
    browser_host: str,
    browser_port: int,
    image_digest: str | None,
    container_id: str | None,
) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "backend": {
            "id": backend["id"],
            "type": backend["type"],
            "mode": backend["mode"],
            "image": backend["image"],
            "imageLock": {"image": backend["image"], "repoDigest": image_digest},
            "imageLockFile": backend["imageLockFile"].as_posix(),
            "containerName": backend["containerName"],
            "containerId": container_id,
            "runtimeRoot": layout.backend_root.as_posix(),
            "dataRoot": layout.backend_data_root.as_posix(),
            "status": status,
            "ports": {
                "falkordb": {
                    "bindHost": falkordb_host,
                    "hostPort": falkordb_port,
                    "containerPort": backend["falkordbContainerPort"],
                },
                "browser": {
                    "bindHost": browser_host,
                    "hostPort": browser_port,
                    "containerPort": backend["browserContainerPort"],
                },
            },
            "browserUrl": f"http://{browser_host}:{browser_port}",
        },
        "settingsFile": settings_path.as_posix(),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def cgc_install(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_install_all(args)

    layout = cgc_layout_from_args(args)
    venv_python = python_executable(layout.venv_root)
    commands = [
        [args.python, "-m", "venv", layout.venv_root.as_posix()],
        [venv_python.as_posix(), "-m", "pip", "install", "-r", layout.requirements_file.as_posix()],
        [layout.cgc_executable().as_posix(), "doctor"],
    ]
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "install",
            "ok": True,
            "dryRun": True,
            "repoId": layout.repo_id,
            "venvRoot": layout.venv_root.as_posix(),
            "requirementsFile": layout.requirements_file.as_posix(),
            "commands": commands,
        }

    ensure_cgc_runtime_layout(layout)
    results: list[dict[str, Any]] = []
    if not venv_python.exists():
        venv_result = run_command(commands[0], cwd=layout.coordination_root, timeout=args.timeout)
        results.append(venv_result)
        if venv_result["returncode"] != 0:
            return {
                "provider": "codegraphcontext",
                "action": "install",
                "ok": False,
                "repoId": layout.repo_id,
                "commands": results,
            }
    else:
        results.append(
            {
                "command": commands[0],
                "cwd": layout.coordination_root.as_posix(),
                "returncode": 0,
                "durationSeconds": 0,
                "stdout": "provider venv already exists",
                "stderr": "",
            }
        )

    install_result = run_command(commands[1], cwd=layout.coordination_root, timeout=args.timeout)
    results.append(install_result)
    if install_result["returncode"] != 0:
        return {
            "provider": "codegraphcontext",
            "action": "install",
            "ok": False,
            "repoId": layout.repo_id,
            "commands": results,
        }

    backend_result = cgc_backend_start(args) if cgc_uses_settings(args) else None
    if backend_result is not None and not backend_result.get("ok"):
        return {**backend_result, "action": "install", "ok": False, "repoId": layout.repo_id}

    patch_result = cgc_patch(args)
    doctor_result = cgc_doctor(args)
    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "venvRoot": layout.venv_root.as_posix(),
            "requirementsFile": layout.requirements_file.as_posix(),
            "requirementsSha256": file_sha256(layout.requirements_file),
            "lastAction": "install",
            "lastInstall": {
                "pipReturncode": install_result["returncode"],
                "backendOk": backend_result.get("ok") if backend_result else None,
                "patchOk": patch_result.get("ok"),
                "doctorOk": doctor_result.get("ok"),
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    write_json(layout.state_file, state)
    return {
        "provider": "codegraphcontext",
        "action": "install",
        "ok": bool(patch_result.get("ok")) and bool(doctor_result.get("ok")),
        "repoId": layout.repo_id,
        "venvRoot": layout.venv_root.as_posix(),
        "requirementsFile": layout.requirements_file.as_posix(),
        "commands": results,
        "backend": backend_result,
        "patch": patch_result,
        "doctor": doctor_result,
    }


def cgc_install_all(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    cleanup = cleanup_cgc_runtime_artifacts(layouts, dry_run=args.dry_run)
    backend = cgc_backend_start(args)
    if not backend.get("ok"):
        return {
            "provider": "codegraphcontext",
            "action": "install-all",
            "ok": False,
            "settingsFile": settings_path.as_posix(),
            "backend": backend,
        }

    results: list[dict[str, Any]] = []
    for layout in layouts:
        scoped = cgc_scoped_args(args, layout.repo_id, "install")
        try:
            result = cgc_install(scoped)
        except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
            result = {
                "provider": "codegraphcontext",
                "action": "install",
                "ok": False,
                "repoId": layout.repo_id,
                "error": str(error),
            }
        results.append(result)

    return {
        "provider": "codegraphcontext",
        "action": "install-all",
        "ok": all(result.get("ok") for result in results),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "backend": backend,
        "count": len(results),
        "removedArtifacts": cleanup,
        "results": results,
    }


def cgc_status(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    artifacts = [path.as_posix() for path in source_provider_artifacts(layout.code_repo_root)]
    cgc_executable = layout.cgc_executable()
    state = read_json(layout.state_file)
    pid = state.get("process", {}).get("pid")
    patch = {
        "module": None,
        "applied": False,
        "error": None,
        "patches": {},
    }
    if cgc_executable.exists():
        try:
            cgcignore_module = find_cgc_cgcignore_module(layout.venv_root)
            writer_module = find_cgc_writer_module(layout.venv_root)
            graph_builder_module = find_cgc_graph_builder_module(layout.venv_root)
            discovery_module = find_cgc_discovery_module(layout.venv_root)
            viz_server_module = find_cgc_viz_server_module(layout.venv_root)
            cli_helpers_module = find_cgc_cli_helpers_module(layout.venv_root)
            cgcignore_applied = cgc_cgcignore_patch_applied(cgcignore_module)
            delete_applied = cgc_delete_patch_applied(writer_module)
            graph_builder_applied = cgc_graph_builder_extensions_patch_applied(graph_builder_module)
            discovery_applied = cgc_discovery_extensions_patch_applied(discovery_module)
            viz_repo_query_applied = cgc_viz_repo_query_patch_applied(viz_server_module)
            viz_server_route_applied = cgc_viz_server_route_patch_applied(viz_server_module)
            viz_cli_route_applied = cgc_viz_cli_route_patch_applied(cli_helpers_module)
            patch["module"] = cgcignore_module.as_posix()
            patch["applied"] = all(
                [
                    cgcignore_applied,
                    delete_applied,
                    graph_builder_applied,
                    discovery_applied,
                    viz_repo_query_applied,
                    viz_server_route_applied,
                    viz_cli_route_applied,
                ]
            )
            patch["patches"] = {
                CGC_CGCIGNORE_PATCH_ID: {
                    "module": cgcignore_module.as_posix(),
                    "applied": cgcignore_applied,
                },
                CGC_DELETE_PATCH_ID: {
                    "module": writer_module.as_posix(),
                    "applied": delete_applied,
                },
                CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID: {
                    "module": graph_builder_module.as_posix(),
                    "applied": graph_builder_applied,
                },
                CGC_DISCOVERY_EXTENSIONS_PATCH_ID: {
                    "module": discovery_module.as_posix(),
                    "applied": discovery_applied,
                },
                CGC_VIZ_REPO_QUERY_PATCH_ID: {
                    "module": viz_server_module.as_posix(),
                    "applied": viz_repo_query_applied,
                },
                CGC_VIZ_SERVER_ROUTE_PATCH_ID: {
                    "module": viz_server_module.as_posix(),
                    "applied": viz_server_route_applied,
                },
                CGC_VIZ_CLI_ROUTE_PATCH_ID: {
                    "module": cli_helpers_module.as_posix(),
                    "applied": viz_cli_route_applied,
                },
            }
        except ContextProviderError as error:
            patch["error"] = str(error)

    return {
        "provider": "codegraphcontext",
        "action": "status",
        "ok": cgc_executable.exists() and not artifacts and bool(patch["applied"]),
        "repoId": layout.repo_id,
        "codeRepoRoot": layout.code_repo_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "cgcRoot": layout.cgc_root.as_posix(),
        "venvRoot": layout.venv_root.as_posix(),
        "cgcExecutable": cgc_executable.as_posix(),
        "cgcExecutableExists": cgc_executable.exists(),
        "requirementsFile": layout.requirements_file.as_posix(),
        "patchesRoot": layout.patches_root.as_posix(),
        "backendRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "watchCwd": layout.watch_cwd.as_posix(),
        "watchLog": layout.watch_log_file.as_posix(),
        "sourceArtifacts": artifacts,
        "patch": patch,
        "process": {"pid": pid, "alive": process_alive(int(pid)) if isinstance(pid, int) else False},
        "processNamespace": process_namespace_status(),
    }


def cgc_init_layout(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    if not args.dry_run:
        ensure_cgc_runtime_layout(layout)
        write_provider_state(
            layout,
            {
                "provider": "codegraphcontext",
                "repoId": layout.repo_id,
                "codeRepoRoot": layout.code_repo_root.as_posix(),
                "runtimeRoot": layout.runtime_root.as_posix(),
                "requirementsFile": layout.requirements_file.as_posix(),
                "requirementsSha256": file_sha256(layout.requirements_file),
                "patchesRoot": layout.patches_root.as_posix(),
                "lastAction": "init-layout",
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    return {
        "provider": "codegraphcontext",
        "action": "init-layout",
        "ok": True,
        "dryRun": args.dry_run,
        "repoId": layout.repo_id,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "cgcRoot": layout.cgc_root.as_posix(),
        "venvRoot": layout.venv_root.as_posix(),
        "requirementsFile": layout.requirements_file.as_posix(),
        "patchesRoot": layout.patches_root.as_posix(),
        "stateFile": layout.state_file.as_posix(),
    }


def cgc_patch(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    cgcignore_module = find_cgc_cgcignore_module(layout.venv_root)
    writer_module = find_cgc_writer_module(layout.venv_root)
    graph_builder_module = find_cgc_graph_builder_module(layout.venv_root)
    discovery_module = find_cgc_discovery_module(layout.venv_root)
    viz_server_module = find_cgc_viz_server_module(layout.venv_root)
    cli_helpers_module = find_cgc_cli_helpers_module(layout.venv_root)
    patch_targets = [
        (CGC_CGCIGNORE_PATCH_ID, cgcignore_module, cgc_cgcignore_patch_applied, apply_cgc_cgcignore_patch),
        (CGC_DELETE_PATCH_ID, writer_module, cgc_delete_patch_applied, apply_cgc_delete_patch),
        (
            CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID,
            graph_builder_module,
            cgc_graph_builder_extensions_patch_applied,
            apply_cgc_graph_builder_extensions_patch,
        ),
        (
            CGC_DISCOVERY_EXTENSIONS_PATCH_ID,
            discovery_module,
            cgc_discovery_extensions_patch_applied,
            apply_cgc_discovery_extensions_patch,
        ),
        (
            CGC_VIZ_REPO_QUERY_PATCH_ID,
            viz_server_module,
            cgc_viz_repo_query_patch_applied,
            apply_cgc_viz_repo_query_patch,
        ),
        (
            CGC_VIZ_SERVER_ROUTE_PATCH_ID,
            viz_server_module,
            cgc_viz_server_route_patch_applied,
            apply_cgc_viz_server_route_patch,
        ),
        (
            CGC_VIZ_CLI_ROUTE_PATCH_ID,
            cli_helpers_module,
            cgc_viz_cli_route_patch_applied,
            apply_cgc_viz_cli_route_patch,
        ),
    ]
    patch_results: dict[str, dict[str, Any]] = {}
    changed_any = False
    applied_any = False
    for patch_id, module, check_applied, apply_patch in patch_targets:
        already_applied = check_applied(module)
        changed = False
        if not args.dry_run and not already_applied:
            changed = apply_patch(module)
        elif args.dry_run:
            changed = not already_applied
        applied = check_applied(module) if not args.dry_run else already_applied
        patch_results[patch_id] = {
            "module": module.as_posix(),
            "alreadyApplied": already_applied,
            "applied": applied,
            "changed": changed,
            "dryRunWouldChange": changed if args.dry_run else False,
        }
        changed_any = changed_any or changed
        applied_any = applied_any or already_applied or changed

    applied_patch_ids = {
        patch_id for patch_id, result in patch_results.items() if result["applied"] or result["changed"]
    }

    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "appliedPatches": sorted(set(state.get("appliedPatches", [])) | applied_patch_ids)
            if applied_any
            else state.get("appliedPatches", []),
            "patchVerification": patch_results,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    if not args.dry_run:
        write_json(layout.state_file, state)

    return {
        "provider": "codegraphcontext",
        "action": "patch",
        "ok": all(result["applied"] or result["changed"] for result in patch_results.values()),
        "dryRun": args.dry_run,
        "patches": patch_results,
        "changed": changed_any,
    }


def cgc_doctor(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    status = cgc_status(args)
    checks: list[dict[str, Any]] = [
        {"name": "runtime-root-contained", "ok": layout.runtime_root.is_relative_to(layout.providers_root)},
        {"name": "source-artifact-clean", "ok": not status["sourceArtifacts"], "artifacts": status["sourceArtifacts"]},
        {"name": "cgc-executable", "ok": status["cgcExecutableExists"], "path": status["cgcExecutable"]},
        {"name": "cgcignore-patch", "ok": bool(status["patch"]["applied"]), "details": status["patch"]},
    ]
    command_result = None
    if status["cgcExecutableExists"] and not args.dry_run:
        command_result = run_command(
            [layout.cgc_executable().as_posix(), "doctor"],
            cwd=layout.runtime_root,
            env=layout.env(),
            timeout=args.timeout,
        )
        checks.append({"name": "cgc-doctor-command", "ok": command_result["returncode"] == 0})
    elif args.dry_run:
        command_result = {
            "command": [layout.cgc_executable().as_posix(), "doctor"],
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }

    ok = all(check["ok"] for check in checks)
    return {
        "provider": "codegraphcontext",
        "action": "doctor",
        "ok": ok,
        "dryRun": args.dry_run,
        "checks": checks,
        "command": command_result,
    }


def cgc_start(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_start_all(args)

    layout = cgc_layout_from_args(args)
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "start",
            "ok": True,
            "repoId": layout.repo_id,
            "dryRun": True,
            "command": [layout.cgc_executable().as_posix(), "watch", layout.code_repo_root.as_posix()],
            "cwd": layout.watch_cwd.as_posix(),
            "env": layout.env(),
        }
    require_durable_process_namespace("cgc start")
    ensure_cgc_runtime_layout(layout)
    backend_result = None
    if cgc_uses_settings(args):
        backend_result = cgc_backend_start(args)
        if not backend_result.get("ok"):
            return {**backend_result, "action": "start", "ok": False, "repoId": layout.repo_id}

    state = read_json(layout.state_file)
    process_state = state.get("process", {})
    if not isinstance(process_state, dict):
        process_state = {}
    pid = process_state.get("pid")
    if isinstance(pid, int) and process_alive(pid):
        return {
            "provider": "codegraphcontext",
            "action": "start",
            "ok": True,
            "repoId": layout.repo_id,
            "alreadyRunning": True,
            "pid": pid,
            "logFile": process_state.get("logFile", layout.watch_log_file.as_posix()),
            "backend": backend_result,
        }

    doctor = cgc_doctor(args)
    if not doctor["ok"]:
        return {**doctor, "action": "start", "ok": False, "repoId": layout.repo_id}

    watch_log = layout.watch_log_file
    watch_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = watch_log.open("ab")
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [layout.cgc_executable().as_posix(), "watch", layout.code_repo_root.as_posix()],
        cwd=str(layout.watch_cwd),
        env={**os.environ, **layout.env()},
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        **popen_kwargs,
    )
    write_provider_state(
        layout,
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "process": {"pid": process.pid, "logFile": watch_log.as_posix(), "mode": "watch"},
            "lastAction": "start",
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": True,
        "repoId": layout.repo_id,
        "pid": process.pid,
        "logFile": watch_log.as_posix(),
        "backend": backend_result,
    }


def cgc_start_all(args: argparse.Namespace) -> dict[str, Any]:
    if args.repo_id is not None:
        raise ContextProviderError("cgc start-all does not accept --repo-id; use cgc start for one root")
    if not args.dry_run:
        require_durable_process_namespace("cgc start-all")

    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    backend = cgc_backend_start(args)
    if not backend.get("ok"):
        return {
            "provider": "codegraphcontext",
            "action": "start-all",
            "ok": False,
            "dryRun": args.dry_run,
            "settingsFile": settings_path.as_posix(),
            "backend": backend,
        }

    results: list[dict[str, Any]] = []
    for layout in layouts:
        scoped = cgc_scoped_args(args, layout.repo_id, "start")
        try:
            result = cgc_start(scoped)
        except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
            result = {
                "provider": "codegraphcontext",
                "action": "start",
                "ok": False,
                "repoId": layout.repo_id,
                "error": str(error),
            }
        results.append(result)

    return {
        "provider": "codegraphcontext",
        "action": "start-all",
        "ok": all(result.get("ok") for result in results),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "backend": backend,
        "count": len(results),
        "results": results,
    }


def cgc_stop(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_stop_all(args)

    layout = cgc_layout_from_args(args)
    if not args.dry_run:
        require_durable_process_namespace("cgc stop")
    state = read_json(layout.state_file)
    process_state = state.get("process", {})
    if not isinstance(process_state, dict):
        process_state = {}
    pid = process_state.get("pid")
    if not isinstance(pid, int):
        return {
            "provider": "codegraphcontext",
            "action": "stop",
            "ok": True,
            "repoId": layout.repo_id,
            "message": "no managed pid",
        }
    cmdline = process_cmdline(pid)
    if cmdline and "cgc" not in cmdline and "codegraphcontext" not in cmdline.lower():
        return {
            "provider": "codegraphcontext",
            "action": "stop",
            "ok": False,
            "repoId": layout.repo_id,
            "message": "managed pid no longer looks like a CGC process",
            "pid": pid,
            "cmdline": cmdline,
        }
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "stop",
            "ok": True,
            "repoId": layout.repo_id,
            "dryRun": True,
            "pid": pid,
        }
    if process_alive(pid):
        os.kill(pid, signal.SIGTERM)
    state["process"] = {"pid": pid, "alive": False, "stoppedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    state["lastAction"] = "stop"
    write_json(layout.state_file, state)
    return {"provider": "codegraphcontext", "action": "stop", "ok": True, "repoId": layout.repo_id, "pid": pid}


def cgc_stop_all(args: argparse.Namespace) -> dict[str, Any]:
    if args.repo_id is not None:
        raise ContextProviderError(f"cgc {args.action} does not accept --repo-id; use cgc stop for one root")
    if not args.dry_run:
        require_durable_process_namespace(f"cgc {args.action}")

    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    results: list[dict[str, Any]] = []
    for layout in layouts:
        scoped = cgc_scoped_args(args, layout.repo_id, "stop")
        try:
            result = cgc_stop(scoped)
        except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
            result = {
                "provider": "codegraphcontext",
                "action": "stop",
                "ok": False,
                "repoId": layout.repo_id,
                "error": str(error),
            }
        results.append(result)

    return {
        "provider": "codegraphcontext",
        "action": args.action if args.action in {"stop-all", "shutdown-all"} else "stop-all",
        "ok": all(result.get("ok") for result in results),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "count": len(results),
        "results": results,
    }


def cgc_refresh(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_refresh_all(args)

    layout = cgc_layout_from_args(args)
    command = [layout.cgc_executable().as_posix(), "index", layout.code_repo_root.as_posix(), "--force"]
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "refresh",
            "ok": True,
            "repoId": layout.repo_id,
            "dryRun": True,
            "command": command,
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }
    backend_result = None
    if cgc_uses_settings(args):
        backend_result = cgc_backend_start(args)
        if not backend_result.get("ok"):
            return {**backend_result, "action": "refresh", "ok": False, "repoId": layout.repo_id}

    doctor = cgc_doctor(args)
    if not doctor["ok"]:
        return {**doctor, "action": "refresh", "ok": False}
    result = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout)
    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "lastAction": "refresh",
            "lastRefresh": {
                "returncode": result["returncode"],
                "durationSeconds": result["durationSeconds"],
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
    )
    write_json(layout.state_file, state)
    return {
        "provider": "codegraphcontext",
        "action": "refresh",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "command": result,
        "backend": backend_result,
    }


def cgc_refresh_all(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    backend = cgc_backend_start(args)
    if not backend.get("ok"):
        return {
            "provider": "codegraphcontext",
            "action": "refresh-all",
            "ok": False,
            "dryRun": args.dry_run,
            "settingsFile": settings_path.as_posix(),
            "backend": backend,
        }

    results: list[dict[str, Any]] = []
    for layout in layouts:
        scoped = cgc_scoped_args(args, layout.repo_id, "refresh")
        try:
            result = cgc_refresh(scoped)
        except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
            result = {
                "provider": "codegraphcontext",
                "action": "refresh",
                "ok": False,
                "repoId": layout.repo_id,
                "error": str(error),
            }
        results.append(result)

    return {
        "provider": "codegraphcontext",
        "action": "refresh-all",
        "ok": all(result.get("ok") for result in results),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "backend": backend,
        "count": len(results),
        "results": results,
    }


def cgc_run(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        raise ContextProviderError("cgc run requires --repo-id when using settings-backed roots")

    layout = cgc_layout_from_args(args)
    native_args = list(getattr(args, "native_args", []) or [])
    if native_args and native_args[0] == "--":
        native_args = native_args[1:]
    if not native_args:
        raise ContextProviderError("cgc run requires native CGC arguments after --")
    if native_args[0] == "visualize":
        raise ContextProviderError("cgc run is for bounded native CGC commands; use cgc visualize for the visualizer server")

    command = [layout.cgc_executable().as_posix(), *native_args]
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "run",
            "ok": True,
            "dryRun": True,
            "repoId": layout.repo_id,
            "command": command,
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }

    ensure_cgc_runtime_layout(layout)
    status = cgc_status(args)
    if not status["ok"]:
        return {**status, "action": "run", "ok": False}

    result = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout)
    return {
        "provider": "codegraphcontext",
        "action": "run",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "command": result,
    }


def cgc_visualize(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        raise ContextProviderError("cgc visualize requires --repo-id when using settings-backed roots")
    if args.port < 1 or args.port > 65535:
        raise ContextProviderError("cgc visualize requires --port between 1 and 65535")

    layout = cgc_layout_from_args(args)
    command = [
        layout.cgc_executable().as_posix(),
        "visualize",
        "--repo",
        layout.code_repo_root.as_posix(),
        "--port",
        str(args.port),
    ]
    if args.context:
        command.extend(["--context", args.context])

    url = f"http://127.0.0.1:{args.port}"
    if args.dry_run:
        return {
            "provider": "codegraphcontext",
            "action": "visualize",
            "ok": True,
            "dryRun": True,
            "repoId": layout.repo_id,
            "url": url,
            "longRunning": True,
            "command": command,
            "cwd": layout.runtime_root.as_posix(),
            "env": layout.env(),
        }

    require_durable_process_namespace("cgc visualize")
    ensure_cgc_runtime_layout(layout)
    status = cgc_status(args)
    if not status["ok"]:
        return {**status, "action": "visualize", "ok": False}

    result = run_foreground_command(command, cwd=layout.runtime_root, env=layout.env())
    return {
        "provider": "codegraphcontext",
        "action": "visualize",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "url": url,
        "longRunning": True,
        "command": result,
    }


def grepai_layout_from_args(args: argparse.Namespace) -> tuple[Path, dict[str, Any], Any]:
    settings_path, provider_settings = grepai_settings_from_file(
        args.coordination_root,
        getattr(args, "from_settings", None),
    )
    if provider_settings and getattr(args, "root", None) is None and getattr(args, "runtime_root", None) is None:
        return (
            settings_path,
            provider_settings,
            grepai_runtime_layout_from_provider_settings(
                coordination_root=args.coordination_root,
                provider_settings=provider_settings,
            ),
        )

    root = (args.root or args.coordination_root / "memory-repos").resolve()
    runtime_root = (args.runtime_root or args.coordination_root / "providers" / "grepai").resolve()
    return (
        settings_path,
        provider_settings,
        grepai_runtime_layout(
            coordination_root=args.coordination_root,
            workspace_name=str(provider_settings.get("workspace", "agents-remember-memory")),
            roots=(GrepaiMemoryRoot(project_id=stable_provider_id(root.name), path=root),),
            runtime_root=runtime_root,
        ),
    )


def grepai_version(executable: str, cwd: Path, timeout: int, env: dict[str, str] | None = None) -> str | None:
    result = run_command([executable, "version"], cwd=cwd, env=env, timeout=timeout)
    if result["returncode"] != 0:
        return None
    match = re.search(r"\bversion\s+([0-9][^\s]*)", result["stdout"])
    return match.group(1) if match else None


def grepai_watch_pid_from_output(output: str) -> int | None:
    match = re.search(r"\bPID\s+([0-9]+)\b", output)
    return int(match.group(1)) if match else None


def grepai_watch_already_running_from_output(output: str) -> bool:
    text = output.lower()
    return "already" in text and "running" in text


def grepai_watch_running_from_output(output: str) -> bool:
    text = output.lower()
    if "not running" in text:
        return False
    return "watcher: running" in text or "status: running" in text or grepai_watch_already_running_from_output(output)


def grepai_state_matches_layout(state: dict[str, Any], layout: Any) -> bool:
    return (
        state.get("workspace") == layout.workspace_name
        and state.get("runtimeRoot") == layout.runtime_root.as_posix()
        and state.get("workspaceConfigFile") == layout.workspace_config_file.as_posix()
    )


def grepai_watch_state(
    layout: Any,
    *,
    action: str,
    pid: int | None,
    backend_result: dict[str, Any] | None,
    adopted: bool = False,
    startup_timed_out: bool = False,
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "workspace": layout.workspace_name,
        "roots": [
            {
                "projectId": root.project_id,
                "path": root.path.as_posix(),
                "sourcePath": root.source_path.as_posix() if root.source_path else None,
            }
            for root in layout.roots
        ],
        "runtimeRoot": layout.runtime_root.as_posix(),
        "workspaceConfigFile": layout.workspace_config_file.as_posix(),
        "process": {
            "pid": pid,
            "logDir": layout.logs_root.as_posix(),
            "mode": "native-background-watch",
            "adopted": adopted,
            "startupTimedOut": startup_timed_out,
        },
        "lastAction": action,
        "backend": backend_result,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def grepai_probe_watcher(command_name: str, layout: Any, state_file: Path, timeout: int) -> dict[str, Any]:
    state = read_json(state_file)
    state_pid = state.get("process", {}).get("pid")
    managed_alive = bool(grepai_state_matches_layout(state, layout) and isinstance(state_pid, int) and process_alive(state_pid))
    command = [command_name, "watch", "--status", "--workspace", layout.workspace_name]
    status = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=timeout, allow_timeout=True)
    output = f"{status.get('stdout', '')}\n{status.get('stderr', '')}"
    native_running = grepai_watch_running_from_output(output)
    native_pid = grepai_watch_pid_from_output(output)
    pid = state_pid if managed_alive and isinstance(state_pid, int) else native_pid
    return {
        "running": managed_alive or native_running,
        "pid": pid,
        "managedAlive": managed_alive,
        "nativeRunning": native_running,
        "status": status,
    }


def prepare_grepai_workspace(
    layout: Any,
    provider_settings: dict[str, Any],
    *,
    dsn: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    removals = remove_grepai_root_provider_artifacts(layout.roots, dry_run=dry_run)
    synced = [] if dry_run else sync_grepai_index_roots(layout)
    if not dry_run:
        write_grepai_workspace_config(layout, dsn=dsn, embedder_settings=grepai_embedder_settings(provider_settings))
    return {
        "removedRootArtifacts": removals,
        "syncedRoots": synced,
        "workspaceConfigFile": layout.workspace_config_file.as_posix(),
    }


def grepai_backend_settings(provider_settings: dict[str, Any], layout: Any) -> dict[str, Any]:
    backend_settings = provider_settings.get("backend", {})
    if not isinstance(backend_settings, dict):
        backend_settings = {}
    ports = backend_settings.get("ports", {})
    if not isinstance(ports, dict):
        ports = {}
    postgres_port = ports.get("postgres", {})
    if not isinstance(postgres_port, dict):
        postgres_port = {}
    postgres_settings = backend_settings.get("postgres", {})
    if not isinstance(postgres_settings, dict):
        postgres_settings = {}

    image = str(backend_settings.get("image", "pgvector/pgvector:pg16")).strip()
    if not image or "<" in image or ">" in image:
        raise ContextProviderError("grepai backend.image must be a concrete pgvector/postgres tag or digest")

    base_variables = {
        "coordination_root": layout.coordination_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
    }
    image_lock_file = backend_settings.get("imageLockFile")
    if image_lock_file:
        image_lock_path = Path(expand_template(str(image_lock_file), base_variables)).resolve()
    else:
        image_lock_path = layout.coordination_root / "providers" / "requirements" / "grepai-postgres-docker.lock"

    return {
        "id": backend_settings.get("id", GREPAI_POSTGRES_BACKEND_ID),
        "type": backend_settings.get("type", "postgres"),
        "mode": backend_settings.get("mode", "docker"),
        "image": image,
        "imageLockFile": image_lock_path,
        "containerName": str(backend_settings.get("containerName", GREPAI_POSTGRES_CONTAINER_NAME)),
        "postgresHost": str(postgres_port.get("bindHost", GREPAI_POSTGRES_DEFAULT_HOST)),
        "postgresHostPort": postgres_port.get("hostPort", "auto"),
        "postgresContainerPort": int(postgres_port.get("containerPort", 5432)),
        "postgresUser": str(postgres_settings.get("user", "grepai")),
        "postgresPassword": str(postgres_settings.get("password", "grepai")),
        "postgresDatabase": str(postgres_settings.get("database", "grepai")),
        "dataDestination": str(backend_settings.get("dataDestination", "/var/lib/postgresql/data")),
    }


def grepai_dsn(backend: dict[str, Any], *, host: str, port: int | str) -> str:
    user = urllib.parse.quote(str(backend["postgresUser"]), safe="")
    password = urllib.parse.quote(str(backend["postgresPassword"]), safe="")
    database = urllib.parse.quote(str(backend["postgresDatabase"]), safe="")
    return f"postgres://{user}:{password}@{host}:{port}/{database}?sslmode=disable"


def grepai_embedder_settings(provider_settings: dict[str, Any]) -> dict[str, Any]:
    embedder = provider_settings.get("embedder")
    if not isinstance(embedder, dict):
        embedder = provider_settings.get("embedding")
    return embedder if isinstance(embedder, dict) else {}


def docker_wait_for_postgres(
    backend: dict[str, Any],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        ready = run_command(
            [
                docker_command(),
                "exec",
                "-e",
                f"PGPASSWORD={backend['postgresPassword']}",
                backend["containerName"],
                "pg_isready",
                "-U",
                backend["postgresUser"],
                "-d",
                backend["postgresDatabase"],
            ],
            cwd=cwd,
            timeout=15,
        )
        last_result = ready
        if ready["returncode"] == 0:
            database = docker_psql(backend, cwd=cwd, timeout=15, sql="SELECT 1;")
            last_result = database
            if database["returncode"] == 0:
                return {
                    "returncode": 0,
                    "stdout": database.get("stdout", ""),
                    "stderr": database.get("stderr", ""),
                    "pgReady": ready,
                    "database": database,
                }
        time.sleep(2)
    if last_result is None:
        raise ContextProviderError("timed out waiting for GrepAI PostgreSQL health check")
    raise ContextProviderError(f"GrepAI PostgreSQL health check failed: {last_result['stderr'] or last_result['stdout']}")


def docker_psql(backend: dict[str, Any], *, cwd: Path, timeout: int, sql: str) -> dict[str, Any]:
    return run_command(
        [
            docker_command(),
            "exec",
            "-e",
            f"PGPASSWORD={backend['postgresPassword']}",
            backend["containerName"],
            "psql",
            "-U",
            backend["postgresUser"],
            "-d",
            backend["postgresDatabase"],
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        cwd=cwd,
        timeout=timeout,
    )


def docker_ensure_pgvector(backend: dict[str, Any], *, cwd: Path, timeout: int) -> dict[str, Any]:
    return docker_psql(
        backend,
        cwd=cwd,
        timeout=timeout,
        sql=(
            "CREATE EXTENSION IF NOT EXISTS vector; "
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') "
            "THEN RAISE EXCEPTION 'pgvector extension is not installed'; END IF; END $$;"
        ),
    )


def docker_verify_pgvector(backend: dict[str, Any], *, cwd: Path, timeout: int) -> dict[str, Any]:
    return docker_psql(
        backend,
        cwd=cwd,
        timeout=timeout,
        sql=(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') "
            "THEN RAISE EXCEPTION 'pgvector extension is not installed'; END IF; END $$;"
        ),
    )


def grepai_backend_state(
    layout: Any,
    backend: dict[str, Any],
    *,
    settings_path: Path,
    status: str,
    postgres_host: str,
    postgres_port: int,
    image_digest: str | None,
    container_id: str | None,
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "backend": {
            "id": backend["id"],
            "type": backend["type"],
            "mode": backend["mode"],
            "image": backend["image"],
            "imageLock": {"image": backend["image"], "repoDigest": image_digest},
            "imageLockFile": backend["imageLockFile"].as_posix(),
            "containerName": backend["containerName"],
            "containerId": container_id,
            "runtimeRoot": layout.backend_root.as_posix(),
            "dataRoot": layout.backend_data_root.as_posix(),
            "status": status,
            "ports": {
                "postgres": {
                    "bindHost": postgres_host,
                    "hostPort": postgres_port,
                    "containerPort": backend["postgresContainerPort"],
                },
            },
        },
        "settingsFile": settings_path.as_posix(),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def grepai_backend_status(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    backend = grepai_backend_settings(provider_settings, layout)
    state = read_json(layout.backend_state_file)
    inspect_data = None if args.dry_run else docker_inspect_container(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    running = docker_container_running(inspect_data)
    ping = None
    extension = None
    if running and not args.dry_run:
        try:
            ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
            extension = docker_verify_pgvector(backend, cwd=layout.coordination_root, timeout=args.timeout)
        except (ContextProviderError, subprocess.TimeoutExpired, OSError) as error:
            ping = {"returncode": 1, "stderr": str(error), "stdout": ""}
            extension = {"returncode": 1, "stderr": str(error), "stdout": ""}

    postgres_mapping = docker_container_port(inspect_data, backend["postgresContainerPort"]) if inspect_data else None
    actual_data_mount = docker_data_mount_source(inspect_data, backend["dataDestination"])
    data_mount_matches = docker_host_path_matches(actual_data_mount, layout.backend_data_root) if inspect_data else False
    postgres_host, postgres_port = postgres_mapping or (
        state.get("backend", {}).get("ports", {}).get("postgres", {}).get("bindHost", backend["postgresHost"]),
        state.get("backend", {}).get("ports", {}).get("postgres", {}).get("hostPort", backend["postgresHostPort"]),
    )
    return {
        "provider": "grepai",
        "action": "backend-status",
        "ok": bool(running)
        and data_mount_matches
        and (ping is None or ping.get("returncode") == 0)
        and (extension is None or extension.get("returncode") == 0),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "containerName": backend["containerName"],
        "image": backend["image"],
        "running": running,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "dataMount": {
            "expected": layout.backend_data_root.as_posix(),
            "actual": actual_data_mount,
            "matches": data_mount_matches,
        },
        "ports": {
            "postgres": {
                "bindHost": postgres_host,
                "hostPort": postgres_port,
                "containerPort": backend["postgresContainerPort"],
            },
        },
        "ping": ping,
        "extension": extension,
    }


def grepai_backend_start(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    if not args.dry_run:
        ensure_grepai_runtime_layout(layout)
    backend = grepai_backend_settings(provider_settings, layout)
    if backend["type"] != "postgres" or backend["mode"] != "docker":
        raise ContextProviderError("managed GrepAI backend must be postgres docker")

    inspect_data = None if args.dry_run else docker_inspect_container(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    forced_remove_result = None
    if inspect_data and not docker_host_path_matches(
        docker_data_mount_source(inspect_data, backend["dataDestination"]),
        layout.backend_data_root,
    ):
        if args.dry_run:
            inspect_data = None
        else:
            forced_remove_result = run_command(
                [docker_command(), "rm", "-f", backend["containerName"]],
                cwd=layout.coordination_root,
                timeout=args.timeout,
            )
            if forced_remove_result["returncode"] != 0:
                return {"provider": "grepai", "action": "backend-start", "ok": False, "command": forced_remove_result}
            inspect_data = None

    if inspect_data and docker_container_running(inspect_data):
        postgres_host, postgres_port = docker_container_port(inspect_data, backend["postgresContainerPort"]) or (
            backend["postgresHost"],
            backend["postgresHostPort"],
        )
        ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
        extension = docker_ensure_pgvector(backend, cwd=layout.coordination_root, timeout=args.timeout)
        image_digest = docker_repo_digest(backend["image"], cwd=layout.coordination_root, timeout=args.timeout)
        backend_state = grepai_backend_state(
            layout,
            backend,
            settings_path=settings_path,
            status="running",
            postgres_host=str(postgres_host),
            postgres_port=int(postgres_port),
            image_digest=image_digest,
            container_id=str(inspect_data.get("Id", "")),
        )
        write_json(layout.backend_state_file, backend_state)
        write_json(backend["imageLockFile"], backend_state["backend"]["imageLock"])
        return {
            "provider": "grepai",
            "action": "backend-start",
            "ok": extension["returncode"] == 0,
            "alreadyRunning": True,
            "containerName": backend["containerName"],
            "ports": backend_state["backend"]["ports"],
            "dataMount": {
                "expected": layout.backend_data_root.as_posix(),
                "actual": docker_data_mount_source(inspect_data, backend["dataDestination"]),
                "matches": True,
            },
            "ping": ping,
            "extension": extension,
        }

    if args.dry_run:
        configured_port = backend["postgresHostPort"]
        postgres_port = 5432 if str(configured_port) == "auto" else int(configured_port)
    else:
        postgres_port = allocate_host_port(backend["postgresHost"], backend["postgresHostPort"], 5432)
    volume_arg = f"{str(layout.backend_data_root)}:{backend['dataDestination']}"
    run_command_line = [
        docker_command(),
        "run",
        "-d",
        "--name",
        backend["containerName"],
        "--restart",
        "unless-stopped",
        "-p",
        f"{backend['postgresHost']}:{postgres_port}:{backend['postgresContainerPort']}",
        "-v",
        volume_arg,
        "-e",
        f"POSTGRES_USER={backend['postgresUser']}",
        "-e",
        f"POSTGRES_PASSWORD={backend['postgresPassword']}",
        "-e",
        f"POSTGRES_DB={backend['postgresDatabase']}",
        backend["image"],
    ]
    commands = [[docker_command(), "pull", backend["image"]]]
    if inspect_data:
        commands.append([docker_command(), "rm", backend["containerName"]])
    commands.append(run_command_line)
    if args.dry_run:
        return {
            "provider": "grepai",
            "action": "backend-start",
            "ok": True,
            "dryRun": True,
            "settingsFile": settings_path.as_posix(),
            "commands": commands,
            "backendRuntimeRoot": layout.backend_root.as_posix(),
            "backendDataRoot": layout.backend_data_root.as_posix(),
            "ports": {
                "postgres": {
                    "bindHost": backend["postgresHost"],
                    "hostPort": postgres_port,
                    "containerPort": backend["postgresContainerPort"],
                },
            },
        }

    pull_result = run_command(commands[0], cwd=layout.coordination_root, timeout=args.timeout)
    if pull_result["returncode"] != 0:
        return {"provider": "grepai", "action": "backend-start", "ok": False, "command": pull_result}
    rm_result = None
    if inspect_data:
        rm_result = run_command(commands[1], cwd=layout.coordination_root, timeout=args.timeout)
        if rm_result["returncode"] != 0:
            return {"provider": "grepai", "action": "backend-start", "ok": False, "command": rm_result}
    run_result = run_command(run_command_line, cwd=layout.coordination_root, timeout=args.timeout)
    if run_result["returncode"] != 0:
        return {"provider": "grepai", "action": "backend-start", "ok": False, "command": run_result}
    ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
    extension = docker_ensure_pgvector(backend, cwd=layout.coordination_root, timeout=args.timeout)
    inspect_data = docker_inspect_container(backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout)
    image_digest = docker_repo_digest(backend["image"], cwd=layout.coordination_root, timeout=args.timeout)
    backend_state = grepai_backend_state(
        layout,
        backend,
        settings_path=settings_path,
        status="running",
        postgres_host=backend["postgresHost"],
        postgres_port=postgres_port,
        image_digest=image_digest,
        container_id=str(inspect_data.get("Id", "")) if inspect_data else None,
    )
    write_json(layout.backend_state_file, backend_state)
    write_json(backend["imageLockFile"], backend_state["backend"]["imageLock"])
    return {
        "provider": "grepai",
        "action": "backend-start",
        "ok": extension["returncode"] == 0,
        "containerName": backend["containerName"],
        "ports": backend_state["backend"]["ports"],
        "commands": {"pull": pull_result, "remove": rm_result, "forcedRemove": forced_remove_result, "run": run_result},
        "ping": ping,
        "extension": extension,
    }


def grepai_install(args: argparse.Namespace) -> dict[str, Any]:
    _, provider_settings, layout = grepai_layout_from_args(args)
    requirements_file = provider_requirements_file(args.coordination_root, GREPAI_PROVIDER)
    if args.dry_run and not requirements_file.exists():
        version = GREPAI_PIN.split("==", 1)[1]
    else:
        requirements_file = ensure_grepai_requirements_file(args.coordination_root)
        version = read_provider_pin(requirements_file, GREPAI_PROVIDER)
    os_name, arch = detect_release_platform()
    extension = "zip" if os_name == "windows" else "tar.gz"
    asset_name = f"grepai_{version}_{os_name}_{arch}.{extension}"
    release_base = f"https://github.com/yoanbernabeu/grepai/releases/download/v{version}"
    archive_url = f"{release_base}/{asset_name}"
    checksums_url = f"{release_base}/checksums.txt"
    destination = layout.binary_path
    installed = destination.as_posix() if destination.exists() else None
    installed_version = grepai_version(installed, layout.runtime_root, args.timeout, layout.env()) if installed else None

    if args.dry_run:
        return {
            "provider": "grepai",
            "action": "install",
            "ok": True,
            "dryRun": True,
            "requirementsFile": requirements_file.as_posix(),
            "version": version,
            "asset": asset_name,
            "downloadUrl": archive_url,
            "checksumsUrl": checksums_url,
            "destination": destination.as_posix(),
            "installedVersion": installed_version,
        }

    ensure_grepai_runtime_layout(layout)
    if installed_version == version and destination.exists() and not args.force:
        backend_result = grepai_backend_start(args) if provider_settings else None
        if backend_result is not None and not backend_result.get("ok"):
            return {**backend_result, "action": "install", "ok": False}
        if backend_result is not None:
            backend = grepai_backend_settings(provider_settings, layout)
            postgres_port = backend_result["ports"]["postgres"]["hostPort"]
            dsn = grepai_dsn(backend, host=backend_result["ports"]["postgres"]["bindHost"], port=postgres_port)
            workspace = prepare_grepai_workspace(layout, provider_settings, dsn=dsn)
        return {
            "provider": "grepai",
            "action": "install",
            "ok": True,
            "requirementsFile": requirements_file.as_posix(),
            "version": version,
            "destination": destination.as_posix(),
            "backend": backend_result,
            "workspace": workspace if backend_result is not None else None,
            "message": "pinned version already installed",
        }

    with tempfile.TemporaryDirectory(prefix="grepai-install-") as tmp_dir:
        archive_path = Path(tmp_dir) / asset_name
        download_file(archive_url, archive_path)
        expected = checksum_for_asset(download_text(checksums_url), asset_name)
        actual = file_sha256(archive_path)
        if actual.lower() != expected.lower():
            raise ContextProviderError(f"checksum mismatch for {asset_name}: expected {expected}, got {actual}")
        extract_binary_from_archive(archive_path, destination, {"grepai", "grepai.exe"})

    installed_version = grepai_version(destination.as_posix(), layout.runtime_root, args.timeout, layout.env())
    ok = installed_version == version
    backend_result = grepai_backend_start(args) if provider_settings else None
    if backend_result is not None and not backend_result.get("ok"):
        ok = False
    if backend_result is not None and backend_result.get("ok"):
        backend = grepai_backend_settings(provider_settings, layout)
        postgres_port = backend_result["ports"]["postgres"]["hostPort"]
        dsn = grepai_dsn(backend, host=backend_result["ports"]["postgres"]["bindHost"], port=postgres_port)
        workspace = prepare_grepai_workspace(layout, provider_settings, dsn=dsn)
    else:
        workspace = None
    install_state = {
        "provider": "grepai",
        "requirementsFile": requirements_file.as_posix(),
        "requirementsSha256": file_sha256(requirements_file),
        "version": version,
        "installedVersion": installed_version,
        "asset": asset_name,
        "destination": destination.as_posix(),
        "lastAction": "install",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(args.coordination_root / "providers" / "grepai" / "install-state.json", install_state)
    return {
        "provider": "grepai",
        "action": "install",
        "ok": ok,
        "requirementsFile": requirements_file.as_posix(),
        "version": version,
        "installedVersion": installed_version,
        "asset": asset_name,
        "destination": destination.as_posix(),
        "backend": backend_result,
        "workspace": workspace,
    }


def grepai_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
    if action == "install":
        return grepai_install(args)
    if action == "backend-start":
        return grepai_backend_start(args)
    if action == "backend-status":
        return grepai_backend_status(args)

    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    executable = layout.binary_path
    command_name = executable.as_posix()
    if not executable.exists():
        return {
            "provider": "grepai",
            "action": action,
            "ok": False,
            "runtimeRoot": layout.runtime_root.as_posix(),
            "message": "runtime grepai command not found",
        }
    ensure_grepai_runtime_layout(layout)
    state_file = layout.state_file

    if action == "status":
        backend_status = grepai_backend_status(args) if provider_settings else None
        commands = [
            [command_name, "workspace", "status", layout.workspace_name],
            [command_name, "watch", "--status", "--workspace", layout.workspace_name],
        ]
        if args.dry_run:
            results = [{"command": command, "cwd": layout.runtime_root.as_posix(), "env": layout.env()} for command in commands]
        else:
            results = [run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout) for command in commands]
        state = read_json(state_file)
        pid = state.get("process", {}).get("pid")
        managed_alive = process_alive(int(pid)) if isinstance(pid, int) else False
        native_watcher_running = any(
            "Watcher: running" in result.get("stdout", "") or "Status: running" in result.get("stdout", "")
            for result in results
        )
        root_artifacts = [
            path.as_posix()
            for root in layout.roots
            for path in ((root.source_path or root.path) / ".grepai",)
            if path.exists()
        ]
        return {
            "provider": "grepai",
            "action": action,
            "ok": all(result.get("returncode", 0) == 0 for result in results)
            and (managed_alive or native_watcher_running)
            and not root_artifacts
            and (backend_status is None or bool(backend_status.get("ok"))),
            "dryRun": args.dry_run,
            "settingsFile": settings_path.as_posix(),
            "workspace": layout.workspace_name,
            "roots": [
                {
                    "projectId": root.project_id,
                    "path": root.path.as_posix(),
                    "sourcePath": root.source_path.as_posix() if root.source_path else None,
                }
                for root in layout.roots
            ],
            "runtimeRoot": layout.runtime_root.as_posix(),
            "workspaceConfigFile": layout.workspace_config_file.as_posix(),
            "rootArtifacts": root_artifacts,
            "backend": backend_status,
            "managedProcess": {"pid": pid, "alive": managed_alive},
            "watcherRunning": managed_alive or native_watcher_running,
            "processNamespace": process_namespace_status(),
            "commands": results,
        }

    if action == "run":
        native_args = list(getattr(args, "native_args", []) or [])
        if native_args and native_args[0] == "--":
            native_args = native_args[1:]
        if not native_args:
            raise ContextProviderError("grepai run requires native GrepAI arguments after --")
        if native_args[0] == "watch":
            raise ContextProviderError("grepai run is for bounded native GrepAI commands; use grepai start/stop/refresh for watchers")

        command = [command_name, *native_args]
        if args.dry_run:
            return {
                "provider": "grepai",
                "action": "run",
                "ok": True,
                "dryRun": True,
                "workspace": layout.workspace_name,
                "command": command,
                "cwd": layout.runtime_root.as_posix(),
                "env": layout.env(),
            }

        status = grepai_run(args, "status")
        if not status["ok"]:
            return {**status, "action": "run", "ok": False}

        result = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout)
        return {
            "provider": "grepai",
            "action": "run",
            "ok": result["returncode"] == 0,
            "workspace": layout.workspace_name,
            "command": result,
        }

    if action == "start":
        if not args.dry_run:
            require_durable_process_namespace("grepai start")
        backend_result = grepai_backend_start(args) if provider_settings else None
        if backend_result is not None and not backend_result.get("ok"):
            return {**backend_result, "action": "start", "ok": False}
        dsn = None
        if backend_result is not None:
            backend = grepai_backend_settings(provider_settings, layout)
            postgres_port = backend_result["ports"]["postgres"]["hostPort"]
            dsn = grepai_dsn(backend, host=backend_result["ports"]["postgres"]["bindHost"], port=postgres_port)
        command = [command_name, "watch", "--workspace", layout.workspace_name, "--background", "--log-dir", layout.logs_root.as_posix()]
        if args.dry_run:
            return {
                "provider": "grepai",
                "action": action,
                "ok": True,
                "dryRun": True,
                "workspace": layout.workspace_name,
                "runtimeRoot": layout.runtime_root.as_posix(),
                "env": layout.env(),
                "command": command,
                "backend": backend_result,
                "workspaceConfigPreview": {"path": layout.workspace_config_file.as_posix(), "dsn": dsn},
            }
        workspace = None
        if dsn is not None:
            workspace = prepare_grepai_workspace(layout, provider_settings, dsn=dsn)
        pre_probe = grepai_probe_watcher(command_name, layout, state_file, args.timeout)
        if pre_probe["running"]:
            write_json(
                state_file,
                grepai_watch_state(
                    layout,
                    action=action,
                    pid=pre_probe.get("pid"),
                    backend_result=backend_result,
                    adopted=True,
                ),
            )
            return {
                "provider": "grepai",
                "action": action,
                "ok": True,
                "alreadyRunning": True,
                "pid": pre_probe.get("pid"),
                "workspace": layout.workspace_name,
                "runtimeRoot": layout.runtime_root.as_posix(),
                "logDir": layout.logs_root.as_posix(),
                "watcher": pre_probe,
                "backend": backend_result,
                "workspaceState": workspace,
            }
        result = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout, allow_timeout=True)
        output = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
        watch_pid = grepai_watch_pid_from_output(output)
        post_probe = grepai_probe_watcher(command_name, layout, state_file, args.timeout)
        if watch_pid is None:
            watch_pid = post_probe.get("pid")
        startup_timed_out = bool(result.get("timedOut"))
        already_running = grepai_watch_already_running_from_output(output) or bool(post_probe["running"] and result.get("returncode") not in {0, None})
        watcher_running = bool(post_probe["running"] or (isinstance(watch_pid, int) and process_alive(watch_pid)))
        ok = bool(result["returncode"] == 0 or watcher_running)
        write_json(
            state_file,
            grepai_watch_state(
                layout,
                action=action,
                pid=watch_pid if isinstance(watch_pid, int) else None,
                backend_result=backend_result,
                adopted=already_running,
                startup_timed_out=startup_timed_out,
            ),
        )
        data = {
            "provider": "grepai",
            "action": action,
            "ok": ok,
            "pid": watch_pid,
            "workspace": layout.workspace_name,
            "runtimeRoot": layout.runtime_root.as_posix(),
            "logDir": layout.logs_root.as_posix(),
            "command": result,
            "watcher": post_probe,
            "alreadyRunning": already_running,
            "startupTimedOut": startup_timed_out,
            "backend": backend_result,
            "workspaceState": workspace,
        }
        if not ok:
            data["recoveryAction"] = "Run grepai status, then grepai refresh if no matching watcher is running."
        return data

    if action == "stop":
        if not args.dry_run:
            require_durable_process_namespace("grepai stop")
        state = read_json(state_file)
        pid = state.get("process", {}).get("pid")
        stopped_pid = None
        if isinstance(pid, int) and process_alive(pid):
            stopped_pid = pid
            if not args.dry_run:
                os.kill(pid, signal.SIGTERM)
        fallback_command = [command_name, "watch", "--stop", "--workspace", layout.workspace_name]
        fallback = None if args.dry_run else run_command(
            fallback_command,
            cwd=layout.runtime_root,
            env=layout.env(),
            timeout=args.timeout,
        )
        if not args.dry_run:
            state["process"] = {
                "pid": pid,
                "alive": False,
                "stoppedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            state["lastAction"] = action
            write_json(state_file, state)
        return {
            "provider": "grepai",
            "action": action,
            "ok": True,
            "dryRun": args.dry_run,
            "workspace": layout.workspace_name,
            "runtimeRoot": layout.runtime_root.as_posix(),
            "stoppedPid": stopped_pid,
            "fallback": fallback,
        }

    if action == "refresh":
        if not args.dry_run:
            require_durable_process_namespace("grepai refresh")
        if args.dry_run:
            return {
                "provider": "grepai",
                "action": action,
                "ok": True,
                "dryRun": True,
                "workspace": layout.workspace_name,
                "runtimeRoot": layout.runtime_root.as_posix(),
                "commands": [
                    [command_name, "watch", "--stop", "--workspace", layout.workspace_name],
                    [command_name, "watch", "--workspace", layout.workspace_name, "--background", "--log-dir", layout.logs_root.as_posix()],
                ],
            }
        stop_result = grepai_run(args, "stop")
        start_result = grepai_run(args, "start")
        return {
            "provider": "grepai",
            "action": action,
            "ok": bool(stop_result.get("ok")) and bool(start_result.get("ok")),
            "workspace": layout.workspace_name,
            "runtimeRoot": layout.runtime_root.as_posix(),
            "stop": stop_result,
            "start": start_result,
        }

    raise ValueError(action)


def watcher_scoped_args(args: argparse.Namespace, provider: str, action: str) -> argparse.Namespace:
    values = vars(args).copy()
    values["provider"] = provider
    values["action"] = action
    values.setdefault("from_settings", None)
    if provider == "cgc":
        values["repo_id"] = None
        values["code_repo_root"] = None
        values.setdefault("python", sys.executable)
    if provider == "grepai":
        values.setdefault("force", False)
        values.setdefault("root", None)
        values.setdefault("runtime_root", None)
    return argparse.Namespace(**values)


def watcher_error_result(provider: str, action: str, error: BaseException) -> dict[str, Any]:
    return {
        "provider": provider,
        "action": action,
        "ok": False,
        "error": str(error),
        "recoveryAction": f"Run provider-specific {provider} status and retry {provider} {action} after resolving the reported error.",
    }


def watcher_recovery_actions(results: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for result in results:
        if result.get("ok"):
            continue
        recovery = result.get("recoveryAction")
        if not recovery:
            recovery = f"Inspect {result.get('provider', 'provider')} {result.get('action', 'status')} output and retry the failed provider action."
        actions.append(
            {
                "provider": str(result.get("provider", "unknown")),
                "action": str(result.get("action", "unknown")),
                "recoveryAction": str(recovery),
            }
        )
    return actions


def watchers_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
    if action in {"start", "stop", "shutdown-all"} and not args.dry_run:
        require_durable_process_namespace(f"watchers {action}")

    settings_path, grepai_enabled = context_provider_enabled(
        args.coordination_root,
        getattr(args, "from_settings", None),
        "grepai-memory",
    )
    _, cgc_enabled = context_provider_enabled(
        args.coordination_root,
        getattr(args, "from_settings", None),
        "codegraphcontext-code",
    )

    results: list[dict[str, Any]] = []
    if grepai_enabled:
        grepai_action = {"start": "start", "status": "status", "stop": "stop", "shutdown-all": "stop"}[action]
        try:
            results.append(grepai_run(watcher_scoped_args(args, "grepai", grepai_action), grepai_action))
        except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
            results.append(watcher_error_result("grepai", grepai_action, error))
    if cgc_enabled:
        cgc_action = {"start": "start-all", "status": "status", "stop": "stop-all", "shutdown-all": "shutdown-all"}[action]
        scoped = watcher_scoped_args(args, "cgc", cgc_action)
        if cgc_action == "status":
            settings_file, _, layouts = cgc_all_layouts_from_settings(scoped)
            cgc_results: list[dict[str, Any]] = []
            for layout in layouts:
                repo_scoped = cgc_scoped_args(scoped, layout.repo_id, "status")
                try:
                    cgc_results.append(cgc_status(repo_scoped))
                except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
                    cgc_results.append(
                        {
                            "provider": "codegraphcontext",
                            "action": "status",
                            "ok": False,
                            "repoId": layout.repo_id,
                            "error": str(error),
                        }
                    )
            results.append(
                {
                    "provider": "codegraphcontext",
                    "action": "status-all",
                    "ok": all(result.get("ok") for result in cgc_results),
                    "settingsFile": settings_file.as_posix(),
                    "count": len(cgc_results),
                    "results": cgc_results,
                }
            )
        else:
            cgc_handlers = {
                "start-all": cgc_start_all,
                "stop-all": cgc_stop_all,
                "shutdown-all": cgc_stop_all,
            }
            try:
                results.append(cgc_handlers[cgc_action](scoped))
            except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
                results.append(watcher_error_result("codegraphcontext", cgc_action, error))

    ok = all(result.get("ok") for result in results)
    recovery_actions = watcher_recovery_actions(results)
    return {
        "provider": "watchers",
        "action": action,
        "ok": ok,
        "partial": any(result.get("ok") for result in results) and not ok,
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "processNamespace": process_namespace_status(),
        "enabled": {
            "grepai-memory": grepai_enabled,
            "codegraphcontext-code": cgc_enabled,
        },
        "results": results,
        "recoveryActions": recovery_actions,
    }


def add_common_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--coordination-root",
        type=Path,
        default=default_coordination_root(),
        help="Coordination root. Defaults to the installed script's parent directory.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)


def add_cgc_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--from-settings", type=Path, default=argparse.SUPPRESS, help="Coordinator settings.json containing codegraphcontext-code.")
    parser.add_argument("--repo-id", default=argparse.SUPPRESS, help="Stable provider id for this code repository.")
    parser.add_argument("--code-repo-root", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--python", default=argparse.SUPPRESS, help="Python executable used to create the provider venv.")


def add_cgc_action_parser(subparsers: Any, action: str, *, help_text: str) -> argparse.ArgumentParser:
    action_parser = subparsers.add_parser(action, help=help_text)
    add_cgc_common_args(action_parser)
    return action_parser


def normalize_cgc_args(args: argparse.Namespace) -> None:
    for key, value in {
        "dry_run": False,
        "json": False,
        "timeout": 60,
        "from_settings": None,
        "repo_id": None,
        "code_repo_root": None,
        "python": sys.executable,
    }.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if not getattr(args, "coordination_root", None):
        args.coordination_root = default_coordination_root()


def add_grepai_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    parser.add_argument("--from-settings", type=Path, default=argparse.SUPPRESS, help="Coordinator settings.json containing grepai-memory.")
    parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS, help="Reinstall even when the pinned version is already present.")
    parser.add_argument("--root", type=Path, default=argparse.SUPPRESS, help="Indexed memory root. Defaults to <coordination-root>/memory-repos.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Provider runtime root. Defaults to <coordination-root>/providers/grepai.",
    )


def add_grepai_action_parser(subparsers: Any, action: str, *, help_text: str) -> argparse.ArgumentParser:
    action_parser = subparsers.add_parser(action, help=help_text)
    add_grepai_common_args(action_parser)
    return action_parser


def normalize_grepai_args(args: argparse.Namespace) -> None:
    for key, value in {
        "dry_run": False,
        "json": False,
        "timeout": 60,
        "from_settings": None,
        "force": False,
        "root": None,
        "runtime_root": None,
    }.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if not getattr(args, "coordination_root", None):
        args.coordination_root = default_coordination_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    providers = parser.add_subparsers(dest="provider", required=True)

    cgc = providers.add_parser("cgc", help="Manage CodeGraphContext code provider settings and instances.")
    add_cgc_common_args(cgc)
    cgc_actions = cgc.add_subparsers(dest="action", required=True)
    for action, help_text in [
        ("apply-settings", "Apply configured CGC provider settings."),
        ("backend-start", "Start the shared FalkorDB backend."),
        ("backend-status", "Inspect the shared FalkorDB backend."),
        ("status", "Inspect one managed CGC repository runtime."),
        ("install", "Install CGC for one managed repository runtime."),
        ("install-all", "Install CGC for every configured repository runtime."),
        ("init-layout", "Create the managed CGC runtime layout."),
        ("patch", "Apply managed CGC containment patches."),
        ("doctor", "Run CGC provider health checks."),
        ("start", "Start managed CGC watchers."),
        ("start-all", "Start every configured CGC watcher."),
        ("stop", "Stop managed CGC watchers."),
        ("stop-all", "Stop every configured CGC watcher."),
        ("shutdown-all", "Stop every configured CGC watcher."),
        ("refresh", "Reindex one managed CGC repository."),
        ("refresh-all", "Reindex every configured CGC repository."),
    ]:
        add_cgc_action_parser(cgc_actions, action, help_text=help_text)
    run_parser = add_cgc_action_parser(
        cgc_actions,
        "run",
        help_text="Run a bounded native CGC command in the managed environment.",
    )
    run_parser.add_argument(
        "--lifecycle-json",
        action="store_true",
        help="Render lifecycle command metadata instead of the native provider output.",
    )
    run_parser.add_argument(
        "native_args",
        nargs=argparse.REMAINDER,
        help="Native CGC arguments. Use -- before native args.",
    )
    visualize_parser = add_cgc_action_parser(
        cgc_actions,
        "visualize",
        help_text="Launch the long-running CGC visualizer server.",
    )
    visualize_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Visualizer server port.",
    )
    visualize_parser.add_argument(
        "--context",
        help="Specific CGC context to visualize.",
    )

    grepai = providers.add_parser("grepai", help="Manage a GrepAI memory provider instance.")
    add_grepai_common_args(grepai)
    grepai_actions = grepai.add_subparsers(dest="action", required=True)
    for action, help_text in [
        ("status", "Inspect the managed GrepAI workspace."),
        ("install", "Install the managed GrepAI runtime."),
        ("backend-start", "Start the shared GrepAI backend."),
        ("backend-status", "Inspect the shared GrepAI backend."),
        ("start", "Start the managed GrepAI watcher."),
        ("stop", "Stop the managed GrepAI watcher."),
        ("refresh", "Restart the managed GrepAI watcher."),
    ]:
        add_grepai_action_parser(grepai_actions, action, help_text=help_text)
    grepai_run_parser = add_grepai_action_parser(
        grepai_actions,
        "run",
        help_text="Run a bounded native GrepAI command in the managed environment.",
    )
    grepai_run_parser.add_argument(
        "--lifecycle-json",
        action="store_true",
        help="Render lifecycle command metadata instead of the native provider output.",
    )
    grepai_run_parser.add_argument(
        "native_args",
        nargs=argparse.REMAINDER,
        help="Native GrepAI arguments. Use -- before native args.",
    )

    watchers = providers.add_parser("watchers", help="Start, stop, or check every enabled provider watcher.")
    watchers.add_argument("action", choices=["status", "start", "stop", "shutdown-all"])
    add_common_provider_args(watchers)
    watchers.add_argument("--from-settings", type=Path, help="Debug override for coordinator settings.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.provider == "cgc":
            normalize_cgc_args(args)
        elif args.provider == "grepai":
            normalize_grepai_args(args)
        args.coordination_root = args.coordination_root.resolve()
        if getattr(args, "from_settings", None):
            args.from_settings = args.from_settings.resolve()
        if hasattr(args, "code_repo_root"):
            if args.code_repo_root is not None:
                args.code_repo_root = args.code_repo_root.resolve()
            if args.repo_id is not None:
                args.repo_id = stable_provider_id(args.repo_id)

        if args.provider == "cgc":
            handlers = {
                "apply-settings": cgc_apply_settings,
                "backend-start": cgc_backend_start,
                "backend-status": cgc_backend_status,
                "status": cgc_status,
                "install": cgc_install,
                "install-all": cgc_install_all,
                "init-layout": cgc_init_layout,
                "patch": cgc_patch,
                "doctor": cgc_doctor,
                "start": cgc_start,
                "start-all": cgc_start_all,
                "stop": cgc_stop,
                "stop-all": cgc_stop_all,
                "shutdown-all": cgc_stop_all,
                "refresh": cgc_refresh,
                "refresh-all": cgc_refresh_all,
                "run": cgc_run,
                "visualize": cgc_visualize,
            }
            data = handlers[args.action](args)
        elif args.provider == "grepai":
            data = grepai_run(args, args.action)
        elif args.provider == "watchers":
            data = watchers_run(args, args.action)
        else:
            parser.error(f"unsupported provider: {args.provider}")
            return 2
    except (ContextProviderError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as error:
        data = {"provider": args.provider, "action": args.action, "ok": False, "error": str(error)}

    rendered = False
    if args.provider == "cgc" and args.action == "run":
        rendered = render_cgc_run_result(data, args)
    elif args.provider == "grepai" and args.action == "run":
        rendered = render_grepai_run_result(data, args)
    if not rendered:
        render(data, as_json=args.json)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
