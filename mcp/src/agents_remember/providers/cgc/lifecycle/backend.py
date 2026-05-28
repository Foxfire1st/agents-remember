"""CodeGraphContext backend container lifecycle."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.lifecycle.compose import (
    cgc_compose_project,
    cgc_compose_render,
    cgc_compose_summary,
)
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_backend_settings,
)
from agents_remember.providers.context import (
    ContextProviderError,
    ensure_cgc_runtime_layout,
)
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.compose_runtime import (
    compose_plan,
    remove_unmanaged_compose_container,
    remove_unmanaged_compose_network,
    run_compose,
)
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_container_networks,
    docker_container_port,
    docker_container_running,
    docker_container_state_summary,
    docker_data_mount_source,
    docker_host_path_matches,
    docker_inspect_container,
    docker_repo_digest,
    docker_wait_for_ping,
)
from agents_remember.providers.lifecycle.host_ports import allocate_host_port
from agents_remember.providers.lifecycle.state_files import (
    read_json,
    write_json,
)


def cgc_primary_backend_context(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], list[Any], Any, dict[str, Any]]:
    settings_path, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    if not layouts:
        raise ContextProviderError("codegraphcontext-code.roots must define at least one root")
    layout = layouts[0]
    return (
        settings_path,
        provider_settings,
        layouts,
        layout,
        cgc_backend_settings(provider_settings, layout),
    )


def cgc_backend_ping(
    args: argparse.Namespace,
    layout: Any,
    backend: dict[str, Any],
    *,
    running: bool,
) -> dict[str, Any] | None:
    if not running or args.dry_run:
        return None
    try:
        return run_command(
            [docker_command(), "exec", backend["containerName"], "redis-cli", "ping"],
            cwd=layout.coordination_root,
            timeout=15,
        )
    except (ContextProviderError, subprocess.TimeoutExpired, OSError) as error:
        return {"returncode": 1, "stderr": str(error), "stdout": ""}


def cgc_backend_runtime_details(
    layout: Any,
    backend: dict[str, Any],
    state: dict[str, Any],
    inspect_data: dict[str, Any] | None,
) -> dict[str, Any]:
    actual_data_mount = docker_data_mount_source(inspect_data)
    return {
        "actualDataMount": actual_data_mount,
        "dataMountMatches": cgc_backend_data_mount_matches(
            inspect_data, actual_data_mount, layout.backend_data_root
        ),
        "falkordbEndpoint": cgc_backend_endpoint(
            state,
            backend,
            inspect_data,
            port_key="falkordb",
            host_default_key="falkordbHost",
            host_port_default_key="falkordbHostPort",
            container_port_key="falkordbContainerPort",
        ),
        "browserEndpoint": cgc_backend_endpoint(
            state,
            backend,
            inspect_data,
            port_key="browser",
            host_default_key="browserHost",
            host_port_default_key="browserHostPort",
            container_port_key="browserContainerPort",
        ),
    }


def cgc_backend_data_mount_matches(
    inspect_data: dict[str, Any] | None, actual_data_mount: str | None, expected: Path
) -> bool:
    return bool(inspect_data) and docker_host_path_matches(actual_data_mount, expected)


def cgc_backend_endpoint(
    state: dict[str, Any],
    backend: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    *,
    port_key: str,
    host_default_key: str,
    host_port_default_key: str,
    container_port_key: str,
) -> tuple[Any, Any]:
    inspected = cgc_container_endpoint(inspect_data, backend[container_port_key])
    return inspected or cgc_state_endpoint(
        state, port_key, backend[host_default_key], backend[host_port_default_key]
    )


def cgc_container_endpoint(
    inspect_data: dict[str, Any] | None, container_port: int
) -> tuple[Any, Any] | None:
    if not inspect_data:
        return None
    return docker_container_port(inspect_data, container_port)


def cgc_state_endpoint(
    state: dict[str, Any], port_key: str, host_default: Any, port_default: Any
) -> tuple[Any, Any]:
    port_state = state.get("backend", {}).get("ports", {}).get(port_key, {})
    return (
        port_state.get("bindHost", host_default),
        port_state.get("hostPort", port_default),
    )


def cgc_backend_status(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, _, _, layout, backend = cgc_primary_backend_context(args)
    state = read_json(layout.backend_state_file)
    inspect_data = cgc_backend_inspect(args, layout, backend)
    running = docker_container_running(inspect_data)
    ping = cgc_backend_ping(args, layout, backend, running=running)
    details = cgc_backend_runtime_details(layout, backend, state, inspect_data)
    network = cgc_backend_network_status(inspect_data, backend)
    falkordb_host, falkordb_port = details["falkordbEndpoint"]
    browser_host, browser_port = details["browserEndpoint"]
    return {
        "provider": "codegraphcontext",
        "action": "backend-status",
        "ok": cgc_backend_status_ok(running, details, network, ping),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "containerName": backend["containerName"],
        "containerState": docker_container_state_summary(inspect_data),
        "image": backend["image"],
        "running": running,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "dataMount": {
            "expected": layout.backend_data_root.as_posix(),
            "actual": details["actualDataMount"],
            "matches": details["dataMountMatches"],
        },
        "network": network,
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
        "browserUrl": cgc_browser_url(browser_host, browser_port),
        "ping": ping,
    }


def cgc_backend_inspect(
    args: argparse.Namespace, layout: Any, backend: dict[str, Any]
) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )


def cgc_backend_status_ok(
    running: bool,
    details: dict[str, Any],
    network: dict[str, Any],
    ping: dict[str, Any] | None,
) -> bool:
    checks = [
        bool(running),
        bool(details["dataMountMatches"]),
        bool(network["connected"]),
        ping is None or ping.get("returncode") == 0,
    ]
    return all(checks)


def cgc_backend_network_status(
    inspect_data: dict[str, Any] | None,
    backend: dict[str, Any],
) -> dict[str, Any]:
    networks = docker_container_networks(inspect_data)
    return {
        "name": backend["networkName"],
        "connected": backend["networkName"] in networks,
        "containerNetworks": sorted(networks),
    }


def cgc_browser_url(host: str, port: int | str) -> str | None:
    return None if str(port) == "auto" else f"http://{host}:{port}"


def cgc_backend_start_context(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], list[Any], Any, dict[str, Any]]:
    settings_path, provider_settings, layouts, layout, backend = cgc_primary_backend_context(args)
    ensure_cgc_runtime_layout(layout)
    if backend["type"] != "falkordb-remote" or backend["mode"] != "docker":
        raise ContextProviderError("managed CGC backend must be falkordb-remote docker")
    return settings_path, provider_settings, layouts, layout, backend


def cgc_backend_remove_mismatched_container(
    args: argparse.Namespace,
    layout: Any,
    backend: dict[str, Any],
    inspect_data: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if not inspect_data:
        return inspect_data, None, None
    if docker_host_path_matches(docker_data_mount_source(inspect_data), layout.backend_data_root):
        return inspect_data, None, None
    result = run_command(
        [docker_command(), "rm", "-f", backend["containerName"]],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    if result["returncode"] != 0:
        return (
            inspect_data,
            result,
            {
                "provider": "codegraphcontext",
                "action": "backend-start",
                "ok": False,
                "command": result,
            },
        )
    return None, result, None


def cgc_existing_backend_host_ports(
    backend: dict[str, Any], inspect_data: dict[str, Any] | None
) -> tuple[int, int] | None:
    if not inspect_data:
        return None
    falkordb = docker_container_port(inspect_data, backend["falkordbContainerPort"])
    browser = docker_container_port(inspect_data, backend["browserContainerPort"])
    if not falkordb or not browser:
        return None
    return falkordb[1], browser[1]


def cgc_backend_host_ports(
    args: argparse.Namespace, backend: dict[str, Any], inspect_data: dict[str, Any] | None
) -> tuple[int, int]:
    existing = cgc_existing_backend_host_ports(backend, inspect_data)
    if existing:
        return existing
    if args.dry_run:
        falkordb = backend["falkordbHostPort"]
        browser = backend["browserHostPort"]
        return (
            6379 if str(falkordb) == "auto" else int(falkordb),
            3000 if str(browser) == "auto" else int(browser),
        )
    return (
        allocate_host_port(backend["falkordbHost"], backend["falkordbHostPort"], 6379),
        allocate_host_port(backend["browserHost"], backend["browserHostPort"], 3000),
    )


def cgc_backend_dry_run_result(
    *,
    settings_path: Path,
    layout: Any,
    backend: dict[str, Any],
    commands: list[list[str]],
    falkordb_port: int,
    browser_port: int,
    network: dict[str, Any],
    compose: dict[str, Any] | None = None,
    migration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "backend-start",
        "ok": True,
        "dryRun": True,
        "settingsFile": settings_path.as_posix(),
        "commands": commands,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "network": network,
        "compose": compose,
        "migration": migration,
        "ports": {
            "falkordb": {
                "bindHost": backend["falkordbHost"],
                "hostPort": falkordb_port,
                "containerPort": backend["falkordbContainerPort"],
            },
            "browser": {
                "bindHost": backend["browserHost"],
                "hostPort": browser_port,
                "containerPort": backend["browserContainerPort"],
            },
        },
        "browserUrl": f"http://{backend['browserHost']}:{browser_port}",
    }


def cgc_backend_start(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layouts, layout, backend = cgc_backend_start_context(args)
    network_result = {"ok": True, "name": backend["networkName"], "managedBy": "docker-compose"}
    migration = cgc_project_migration(args, provider_settings, layouts, backend)
    inspect_data = cgc_backend_inspect(args, layout, backend)
    inspect_data, forced_remove_result, error = cgc_backend_remove_mismatched_container(
        args, layout, backend, inspect_data
    )
    if error:
        return error
    return cgc_backend_create_start_result(
        args,
        settings_path=settings_path,
        provider_settings=provider_settings,
        layouts=layouts,
        layout=layout,
        backend=backend,
        inspect_data=inspect_data,
        forced_remove_result=forced_remove_result,
        network_result=network_result,
        migration=migration,
    )


def cgc_backend_create_start_result(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    provider_settings: dict[str, Any],
    layouts: list[Any],
    layout: Any,
    backend: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    forced_remove_result: dict[str, Any] | None,
    network_result: dict[str, Any],
    migration: dict[str, Any] | None,
) -> dict[str, Any]:
    falkordb_port, browser_port = cgc_backend_host_ports(args, backend, inspect_data)
    render = cgc_compose_render(
        provider_settings,
        layouts,
        falkordb_port=falkordb_port,
        browser_port=browser_port,
    )
    command_args = ["up", "-d", "falkordb"]
    if args.dry_run:
        return cgc_backend_dry_run_result(
            settings_path=settings_path,
            layout=layout,
            backend=backend,
            commands=[compose_plan(render, command_args, cwd=layout.coordination_root)],
            falkordb_port=falkordb_port,
            browser_port=browser_port,
            network=network_result,
            compose=cgc_compose_summary(render),
            migration=migration,
        )
    up_result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    if up_result["returncode"] != 0:
        return {
            "provider": "codegraphcontext",
            "action": "backend-start",
            "ok": False,
            "command": up_result,
            "compose": cgc_compose_summary(render),
            "migration": migration,
        }
    ping = docker_wait_for_ping(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    inspect_data = docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    backend_state = cgc_backend_state(
        layout,
        backend,
        settings_path=settings_path,
        status="running",
        falkordb_host=backend["falkordbHost"],
        falkordb_port=falkordb_port,
        browser_host=backend["browserHost"],
        browser_port=browser_port,
        image_digest=docker_repo_digest(
            backend["image"], cwd=layout.coordination_root, timeout=args.timeout
        ),
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
        "commands": {
            "forcedRemove": forced_remove_result,
            "migration": migration,
            "up": up_result,
        },
        "network": network_result,
        "compose": cgc_compose_summary(render),
        "ping": ping,
    }


def cgc_project_migration(
    args: argparse.Namespace,
    provider_settings: dict[str, Any],
    layouts: list[Any],
    backend: dict[str, Any],
) -> dict[str, Any]:
    project_name = cgc_compose_project(provider_settings)
    return {
        "containers": {
            "falkordb": remove_unmanaged_compose_container(
                backend["containerName"],
                project_name=project_name,
                cwd=layouts[0].coordination_root,
                timeout=args.timeout,
                dry_run=args.dry_run,
            ),
            "watchers": {
                layout.repo_id: remove_unmanaged_compose_container(
                    layout.watcher_container_name,
                    project_name=project_name,
                    cwd=layout.coordination_root,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                )
                for layout in layouts
            },
        },
        "network": remove_unmanaged_compose_network(
            backend["networkName"],
            project_name=project_name,
            cwd=layouts[0].coordination_root,
            timeout=args.timeout,
            dry_run=args.dry_run,
        ),
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
