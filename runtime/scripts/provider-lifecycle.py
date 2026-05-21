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
    GREPAI_PIN,
    GREPAI_PROVIDER,
    ContextProviderError,
    apply_cgc_cgcignore_patch,
    apply_cgc_delete_patch,
    apply_cgc_discovery_extensions_patch,
    apply_cgc_graph_builder_extensions_patch,
    cgc_cgcignore_patch_applied,
    cgc_delete_patch_applied,
    cgc_discovery_extensions_patch_applied,
    cgc_graph_builder_extensions_patch_applied,
    cleanup_cgc_runtime_artifacts,
    cgc_runtime_layout,
    cgc_runtime_layout_from_provider_settings,
    ensure_cgc_runtime_layout,
    ensure_grepai_requirements_file,
    expand_template,
    file_sha256,
    find_cgc_cgcignore_module,
    find_cgc_discovery_module,
    find_cgc_graph_builder_module,
    find_cgc_writer_module,
    provider_requirements_file,
    read_provider_pin,
    source_provider_artifacts,
    stable_provider_id,
    write_provider_state,
)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    started = time.monotonic()
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
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
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


def provider_executable(coordination_root: Path, name: str) -> str | None:
    suffix = ".exe" if os.name == "nt" and not name.endswith(".exe") else ""
    candidate = coordination_root / "providers" / "_bin" / f"{name}{suffix}"
    if candidate.exists():
        return str(candidate)
    return shutil.which(f"{name}{suffix}") or shutil.which(name)


def provider_bin_path(coordination_root: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" and not name.endswith(".exe") else ""
    return coordination_root / "providers" / "_bin" / f"{name}{suffix}"


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
        print(json.dumps(data, indent=2, sort_keys=True))
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


def cgc_uses_settings(args: argparse.Namespace) -> bool:
    return getattr(args, "from_settings", None) is not None or getattr(args, "code_repo_root", None) is None


def cgc_layout_from_args(args: argparse.Namespace):
    if cgc_uses_settings(args):
        _, provider_settings = cgc_settings_from_file(args.coordination_root, args.from_settings)
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
    settings_path, provider_settings = cgc_settings_from_file(args.coordination_root, args.from_settings)
    if args.repo_id:
        selected = [cgc_root_from_settings(provider_settings, args.repo_id)]
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


def docker_data_mount_source(inspect_data: dict[str, Any] | None) -> str | None:
    if not inspect_data:
        return None
    mounts = inspect_data.get("Mounts", [])
    if not isinstance(mounts, list):
        return None
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("Destination") == "/data":
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
            cgcignore_applied = cgc_cgcignore_patch_applied(cgcignore_module)
            delete_applied = cgc_delete_patch_applied(writer_module)
            graph_builder_applied = cgc_graph_builder_extensions_patch_applied(graph_builder_module)
            discovery_applied = cgc_discovery_extensions_patch_applied(discovery_module)
            patch["module"] = cgcignore_module.as_posix()
            patch["applied"] = all(
                [cgcignore_applied, delete_applied, graph_builder_applied, discovery_applied]
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


def grepai_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    root = args.root or args.coordination_root / "memory-repos"
    runtime_root = args.runtime_root or args.coordination_root / "providers" / "grepai" / "memory-repos"
    return root.resolve(), runtime_root.resolve()


def grepai_state_file(runtime_root: Path) -> Path:
    return runtime_root / "provider-state.json"


def grepai_init_command(command_name: str, provider_settings: dict[str, Any]) -> list[str]:
    init_settings = provider_settings.get("init")
    if not isinstance(init_settings, dict):
        init_settings = {}
    embedding_settings = provider_settings.get("embedding")
    if not isinstance(embedding_settings, dict):
        embedding_settings = {}
    store_settings = provider_settings.get("store")
    if not isinstance(store_settings, dict):
        store_settings = {}

    provider = init_settings.get("provider") or embedding_settings.get("provider") or "ollama"
    model = init_settings.get("model") or embedding_settings.get("model") or "nomic-embed-text"
    backend = init_settings.get("backend") or store_settings.get("backend") or "gob"
    return [
        command_name,
        "init",
        "--yes",
        "--provider",
        str(provider),
        "--model",
        str(model),
        "--backend",
        str(backend),
    ]


def ensure_grepai_project(args: argparse.Namespace, command_name: str, root: Path) -> dict[str, Any]:
    config_file = root / ".grepai" / "config.yaml"
    settings_path, provider_settings = grepai_settings_from_file(
        args.coordination_root,
        getattr(args, "from_settings", None),
    )
    command = grepai_init_command(command_name, provider_settings)
    if config_file.exists():
        return {
            "ok": True,
            "initialized": False,
            "configFile": config_file.as_posix(),
            "settingsFile": settings_path.as_posix(),
        }
    if args.dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "initialized": True,
            "configFile": config_file.as_posix(),
            "settingsFile": settings_path.as_posix(),
            "command": command,
        }

    root.mkdir(parents=True, exist_ok=True)
    result = run_command(command, cwd=root, timeout=args.timeout)
    return {
        "ok": result["returncode"] == 0,
        "initialized": result["returncode"] == 0,
        "configFile": config_file.as_posix(),
        "settingsFile": settings_path.as_posix(),
        "command": result,
    }


def grepai_version(executable: str, cwd: Path, timeout: int) -> str | None:
    result = run_command([executable, "version"], cwd=cwd, timeout=timeout)
    if result["returncode"] != 0:
        return None
    match = re.search(r"\bversion\s+([0-9][^\s]*)", result["stdout"])
    return match.group(1) if match else None


def grepai_install(args: argparse.Namespace) -> dict[str, Any]:
    root, runtime_root = grepai_paths(args)
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
    destination = provider_bin_path(args.coordination_root, "grepai")
    installed = provider_executable(args.coordination_root, "grepai")
    installed_version = grepai_version(installed, root, args.timeout) if installed else None

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

    if installed_version == version and destination.exists() and not args.force:
        return {
            "provider": "grepai",
            "action": "install",
            "ok": True,
            "requirementsFile": requirements_file.as_posix(),
            "version": version,
            "destination": destination.as_posix(),
            "message": "pinned version already installed",
        }

    runtime_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grepai-install-") as tmp_dir:
        archive_path = Path(tmp_dir) / asset_name
        download_file(archive_url, archive_path)
        expected = checksum_for_asset(download_text(checksums_url), asset_name)
        actual = file_sha256(archive_path)
        if actual.lower() != expected.lower():
            raise ContextProviderError(f"checksum mismatch for {asset_name}: expected {expected}, got {actual}")
        extract_binary_from_archive(archive_path, destination, {"grepai", "grepai.exe"})

    installed_version = grepai_version(destination.as_posix(), root, args.timeout)
    ok = installed_version == version
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
    }


def grepai_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
    root, runtime_root = grepai_paths(args)
    if action == "install":
        return grepai_install(args)

    executable = provider_executable(args.coordination_root, "grepai")
    command_name = executable or "grepai"
    if not executable:
        return {
            "provider": "grepai",
            "action": action,
            "ok": False,
            "root": root.as_posix(),
            "message": "grepai command not found",
        }
    runtime_root.mkdir(parents=True, exist_ok=True)
    state_file = grepai_state_file(runtime_root)

    if action == "status":
        commands = [[command_name, "status", "--no-ui"], [command_name, "watch", "--status"]]
        if args.dry_run:
            results = [{"command": command, "cwd": root.as_posix()} for command in commands]
        else:
            results = [run_command(command, cwd=root, timeout=args.timeout) for command in commands]
        state = read_json(state_file)
        pid = state.get("process", {}).get("pid")
        managed_alive = process_alive(int(pid)) if isinstance(pid, int) else False
        native_watcher_running = any(
            "Watcher: running" in result.get("stdout", "") or "Status: running" in result.get("stdout", "")
            for result in results
        )
        return {
            "provider": "grepai",
            "action": action,
            "ok": all(result.get("returncode", 0) == 0 for result in results)
            and (managed_alive or native_watcher_running),
            "dryRun": args.dry_run,
            "root": root.as_posix(),
            "runtimeRoot": runtime_root.as_posix(),
            "managedProcess": {"pid": pid, "alive": managed_alive},
            "watcherRunning": managed_alive or native_watcher_running,
            "commands": results,
        }

    if action == "start":
        command = [command_name, "watch", "--no-ui"]
        if args.dry_run:
            return {
                "provider": "grepai",
                "action": action,
                "ok": True,
                "dryRun": True,
                "root": root.as_posix(),
                "runtimeRoot": runtime_root.as_posix(),
                "command": command,
            }
        init_result = ensure_grepai_project(args, command_name, root)
        if not init_result.get("ok"):
            return {
                "provider": "grepai",
                "action": action,
                "ok": False,
                "root": root.as_posix(),
                "runtimeRoot": runtime_root.as_posix(),
                "init": init_result,
            }
        state = read_json(state_file)
        existing_pid = state.get("process", {}).get("pid")
        if isinstance(existing_pid, int) and process_alive(existing_pid):
            return {
                "provider": "grepai",
                "action": action,
                "ok": True,
                "root": root.as_posix(),
                "runtimeRoot": runtime_root.as_posix(),
                "message": "managed process already running",
                "pid": existing_pid,
                "init": init_result,
            }
        watch_log = runtime_root / "logs" / "watch.log"
        watch_log.parent.mkdir(parents=True, exist_ok=True)
        log_handle = watch_log.open("ab")
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **popen_kwargs,
        )
        write_json(
            state_file,
            {
                "provider": "grepai",
                "root": root.as_posix(),
                "runtimeRoot": runtime_root.as_posix(),
                "process": {"pid": process.pid, "logFile": watch_log.as_posix(), "mode": "watch"},
                "lastAction": action,
                "init": init_result,
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        return {
            "provider": "grepai",
            "action": action,
            "ok": True,
            "root": root.as_posix(),
            "runtimeRoot": runtime_root.as_posix(),
            "pid": process.pid,
            "logFile": watch_log.as_posix(),
            "init": init_result,
        }

    if action == "stop":
        state = read_json(state_file)
        pid = state.get("process", {}).get("pid")
        stopped_pid = None
        if isinstance(pid, int) and process_alive(pid):
            stopped_pid = pid
            if not args.dry_run:
                os.kill(pid, signal.SIGTERM)
        fallback = None if args.dry_run else run_command([command_name, "watch", "--stop"], cwd=root, timeout=args.timeout)
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
            "root": root.as_posix(),
            "runtimeRoot": runtime_root.as_posix(),
            "stoppedPid": stopped_pid,
            "fallback": fallback,
        }

    if action == "refresh":
        if args.dry_run:
            return {
                "provider": "grepai",
                "action": action,
                "ok": True,
                "dryRun": True,
                "root": root.as_posix(),
                "runtimeRoot": runtime_root.as_posix(),
                "commands": [[command_name, "watch", "--stop"], [command_name, "watch", "--no-ui"]],
            }
        stop_result = grepai_run(args, "stop")
        start_result = grepai_run(args, "start")
        return {
            "provider": "grepai",
            "action": action,
            "ok": bool(stop_result.get("ok")) and bool(start_result.get("ok")),
            "root": root.as_posix(),
            "runtimeRoot": runtime_root.as_posix(),
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


def watchers_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
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
        results.append(grepai_run(watcher_scoped_args(args, "grepai", grepai_action), grepai_action))
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
            results.append(cgc_handlers[cgc_action](scoped))

    return {
        "provider": "watchers",
        "action": action,
        "ok": all(result.get("ok") for result in results),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "enabled": {
            "grepai-memory": grepai_enabled,
            "codegraphcontext-code": cgc_enabled,
        },
        "results": results,
    }


def add_common_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    providers = parser.add_subparsers(dest="provider", required=True)

    cgc = providers.add_parser("cgc", help="Manage CodeGraphContext code provider settings and instances.")
    cgc.add_argument(
        "action",
        choices=[
            "apply-settings",
            "backend-start",
            "backend-status",
            "status",
            "install",
            "install-all",
            "init-layout",
            "patch",
            "doctor",
            "start",
            "start-all",
            "stop",
            "stop-all",
            "shutdown-all",
            "refresh",
            "refresh-all",
            "run",
        ],
    )
    add_common_provider_args(cgc)
    cgc.add_argument("--from-settings", type=Path, help="Coordinator settings.json containing codegraphcontext-code.")
    cgc.add_argument("--repo-id", help="Stable provider id for this code repository.")
    cgc.add_argument("--code-repo-root", type=Path)
    cgc.add_argument("--python", default=sys.executable, help="Python executable used to create the provider venv.")
    cgc.add_argument(
        "native_args",
        nargs=argparse.REMAINDER,
        help="Native CGC arguments for cgc run. Put lifecycle options before run, then use -- before native args.",
    )

    grepai = providers.add_parser("grepai", help="Manage a GrepAI memory provider instance.")
    grepai.add_argument("action", choices=["status", "install", "start", "stop", "refresh"])
    add_common_provider_args(grepai)
    grepai.add_argument("--force", action="store_true", help="Reinstall even when the pinned version is already present.")
    grepai.add_argument("--root", type=Path, help="Indexed memory root. Defaults to <coordination-root>/memory-repos.")
    grepai.add_argument(
        "--runtime-root",
        type=Path,
        help="Provider runtime root. Defaults to <coordination-root>/providers/grepai/memory-repos.",
    )

    watchers = providers.add_parser("watchers", help="Start, stop, or check every enabled provider watcher.")
    watchers.add_argument("action", choices=["status", "start", "stop", "shutdown-all"])
    add_common_provider_args(watchers)
    watchers.add_argument("--from-settings", type=Path, help="Debug override for coordinator settings.json.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.coordination_root = args.coordination_root.resolve()
    if getattr(args, "from_settings", None):
        args.from_settings = args.from_settings.resolve()
    if hasattr(args, "code_repo_root"):
        if args.code_repo_root is not None:
            args.code_repo_root = args.code_repo_root.resolve()
        if args.repo_id is not None:
            args.repo_id = stable_provider_id(args.repo_id)

    try:
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

    render(data, as_json=args.json)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
