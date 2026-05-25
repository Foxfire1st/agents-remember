"""GrepAI PostgreSQL backend lifecycle."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.context import (
    ContextProviderError,
    ensure_grepai_runtime_layout,
)
from agents_remember.providers.lifecycle_modules.common import (
    allocate_host_port,
    docker_command,
    docker_container_networks,
    docker_container_port,
    docker_container_running,
    docker_data_mount_source,
    docker_ensure_container_network,
    docker_ensure_network,
    docker_host_path_matches,
    docker_inspect_container,
    docker_repo_digest,
    read_json,
    run_command,
    write_json,
)
from agents_remember.providers.lifecycle_modules.grepai.core import *


def docker_wait_for_postgres(
    backend: dict[str, Any],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        ready = docker_pg_isready(backend, cwd=cwd)
        last_result = ready
        if postgres_ready(ready):
            database = docker_psql(backend, cwd=cwd, timeout=15, sql="SELECT 1;")
            last_result = database
            if postgres_ready(database):
                return docker_postgres_success_result(ready, database)
        time.sleep(2)
    raise_postgres_timeout(last_result)


def docker_pg_isready(backend: dict[str, Any], *, cwd: Path) -> dict[str, Any]:
    return run_command(
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


def postgres_ready(result: dict[str, Any]) -> bool:
    return result["returncode"] == 0


def docker_postgres_success_result(
    ready: dict[str, Any], database: dict[str, Any]
) -> dict[str, Any]:
    return {
        "returncode": 0,
        "stdout": database.get("stdout", ""),
        "stderr": database.get("stderr", ""),
        "pgReady": ready,
        "database": database,
    }


def raise_postgres_timeout(last_result: dict[str, Any] | None) -> None:
    if last_result is None:
        raise ContextProviderError("timed out waiting for GrepAI PostgreSQL health check")
    raise ContextProviderError(
        f"GrepAI PostgreSQL health check failed: {last_result['stderr'] or last_result['stdout']}"
    )


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


def grepai_backend_health_checks(
    args: argparse.Namespace,
    layout: Any,
    backend: dict[str, Any],
    *,
    running: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not running or args.dry_run:
        return None, None
    try:
        ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
        extension = docker_verify_pgvector(
            backend, cwd=layout.coordination_root, timeout=args.timeout
        )
        return ping, extension
    except (ContextProviderError, subprocess.TimeoutExpired, OSError) as error:
        failed = {"returncode": 1, "stderr": str(error), "stdout": ""}
        return failed, failed


def grepai_backend_runtime_details(
    layout: Any,
    backend: dict[str, Any],
    network_name: str,
    state: dict[str, Any],
    inspect_data: dict[str, Any] | None,
) -> dict[str, Any]:
    postgres_mapping = (
        docker_container_port(inspect_data, backend["postgresContainerPort"])
        if inspect_data
        else None
    )
    actual_data_mount = docker_data_mount_source(inspect_data, backend["dataDestination"])
    connected_networks = sorted(docker_container_networks(inspect_data))
    return {
        "actualDataMount": actual_data_mount,
        "dataMountMatches": docker_host_path_matches(actual_data_mount, layout.backend_data_root)
        if inspect_data
        else False,
        "connectedNetworks": connected_networks,
        "networkConnected": network_name in connected_networks if inspect_data else False,
        "postgresEndpoint": postgres_mapping
        or (
            state.get("backend", {})
            .get("ports", {})
            .get("postgres", {})
            .get("bindHost", backend["postgresHost"]),
            state.get("backend", {})
            .get("ports", {})
            .get("postgres", {})
            .get("hostPort", backend["postgresHostPort"]),
        ),
    }


def grepai_backend_status_ok(
    *,
    running: bool,
    details: dict[str, Any],
    ping: dict[str, Any] | None,
    extension: dict[str, Any] | None,
) -> bool:
    return all(
        (
            bool(running),
            bool(details["dataMountMatches"]),
            bool(details["networkConnected"]),
            command_ok(ping),
            command_ok(extension),
        )
    )


def grepai_backend_status(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    backend = grepai_backend_settings(provider_settings, layout)
    network_name = grepai_network_name(provider_settings)
    state = read_json(layout.backend_state_file)
    inspect_data = (
        None
        if args.dry_run
        else docker_inspect_container(
            backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
        )
    )
    running = docker_container_running(inspect_data)
    ping, extension = grepai_backend_health_checks(args, layout, backend, running=running)
    details = grepai_backend_runtime_details(layout, backend, network_name, state, inspect_data)
    postgres_host, postgres_port = details["postgresEndpoint"]
    return {
        "provider": "grepai",
        "action": "backend-status",
        "ok": grepai_backend_status_ok(
            running=running,
            details=details,
            ping=ping,
            extension=extension,
        ),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "containerName": backend["containerName"],
        "image": backend["image"],
        "running": running,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "network": {
            "name": network_name,
            "connected": details["networkConnected"],
            "containerNetworks": details["connectedNetworks"],
        },
        "dataMount": {
            "expected": layout.backend_data_root.as_posix(),
            "actual": details["actualDataMount"],
            "matches": details["dataMountMatches"],
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


def grepai_backend_start_context(
    args: argparse.Namespace,
) -> tuple[Path, Any, dict[str, Any], str]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    if not args.dry_run:
        ensure_grepai_runtime_layout(layout)
    backend = grepai_backend_settings(provider_settings, layout)
    if backend["type"] != "postgres" or backend["mode"] != "docker":
        raise ContextProviderError("managed GrepAI backend must be postgres docker")
    return settings_path, layout, backend, grepai_network_name(provider_settings)


def grepai_backend_remove_mismatched_container(
    args: argparse.Namespace,
    layout: Any,
    backend: dict[str, Any],
    inspect_data: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if not inspect_data:
        return inspect_data, None, None
    actual_mount = docker_data_mount_source(inspect_data, backend["dataDestination"])
    if docker_host_path_matches(actual_mount, layout.backend_data_root):
        return inspect_data, None, None
    result = run_command(
        [docker_command(), "rm", "-f", backend["containerName"]],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    if result["returncode"] != 0:
        return inspect_data, result, {
            "provider": "grepai",
            "action": "backend-start",
            "ok": False,
            "command": result,
        }
    return None, result, None


def grepai_backend_existing_start_result(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    layout: Any,
    backend: dict[str, Any],
    network_name: str,
    network_result: dict[str, Any],
    inspect_data: dict[str, Any],
) -> dict[str, Any]:
    network_connect = docker_ensure_container_network(
        backend["containerName"],
        network_name,
        inspect_data=inspect_data,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    if not network_connect.get("ok"):
        return {
            "provider": "grepai",
            "action": "backend-start",
            "ok": False,
            "network": network_result,
            "networkConnect": network_connect,
        }
    inspect_data = docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    postgres_host, postgres_port = docker_container_port(
        inspect_data, backend["postgresContainerPort"]
    ) or (backend["postgresHost"], backend["postgresHostPort"])
    ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
    extension = docker_ensure_pgvector(backend, cwd=layout.coordination_root, timeout=args.timeout)
    backend_state = grepai_backend_state(
        layout,
        backend,
        settings_path=settings_path,
        status="running",
        postgres_host=str(postgres_host),
        postgres_port=int(postgres_port),
        image_digest=docker_repo_digest(backend["image"], cwd=layout.coordination_root, timeout=args.timeout),
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
        "network": network_result,
        "networkConnect": network_connect,
        "ports": backend_state["backend"]["ports"],
        "dataMount": {
            "expected": layout.backend_data_root.as_posix(),
            "actual": docker_data_mount_source(inspect_data, backend["dataDestination"]),
            "matches": True,
        },
        "ping": ping,
        "extension": extension,
    }


def grepai_backend_host_port(args: argparse.Namespace, backend: dict[str, Any]) -> int:
    configured_port = backend["postgresHostPort"]
    if args.dry_run:
        return 5432 if str(configured_port) == "auto" else int(configured_port)
    return allocate_host_port(backend["postgresHost"], configured_port, 5432)


def grepai_backend_run_command_line(
    layout: Any,
    backend: dict[str, Any],
    network_name: str,
    postgres_port: int,
) -> list[str]:
    return [
        docker_command(),
        "run",
        "-d",
        "--name",
        backend["containerName"],
        "--restart",
        "unless-stopped",
        "--network",
        network_name,
        "-p",
        f"{backend['postgresHost']}:{postgres_port}:{backend['postgresContainerPort']}",
        "-v",
        f"{layout.backend_data_root!s}:{backend['dataDestination']}",
        "-e",
        f"POSTGRES_USER={backend['postgresUser']}",
        "-e",
        f"POSTGRES_PASSWORD={backend['postgresPassword']}",
        "-e",
        f"POSTGRES_DB={backend['postgresDatabase']}",
        backend["image"],
    ]


def grepai_backend_planned_commands(
    backend: dict[str, Any],
    network_name: str,
    run_command_line: list[str],
    inspect_data: dict[str, Any] | None,
) -> list[list[str]]:
    commands = [
        [docker_command(), "network", "inspect", network_name],
        [docker_command(), "network", "create", network_name],
        [docker_command(), "pull", backend["image"]],
    ]
    if inspect_data:
        commands.append([docker_command(), "rm", backend["containerName"]])
    commands.append(run_command_line)
    return commands


def grepai_backend_dry_run_result(
    *,
    settings_path: Path,
    layout: Any,
    backend: dict[str, Any],
    network_result: dict[str, Any],
    commands: list[list[str]],
    postgres_port: int,
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": "backend-start",
        "ok": True,
        "dryRun": True,
        "settingsFile": settings_path.as_posix(),
        "commands": commands,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "network": network_result,
        "ports": {
            "postgres": {
                "bindHost": backend["postgresHost"],
                "hostPort": postgres_port,
                "containerPort": backend["postgresContainerPort"],
            },
        },
    }


def grepai_backend_new_start_result(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    layout: Any,
    backend: dict[str, Any],
    network_result: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    forced_remove_result: dict[str, Any] | None,
    run_command_line: list[str],
    postgres_port: int,
) -> dict[str, Any]:
    commands, error = grepai_backend_start_commands(
        args,
        layout=layout,
        backend=backend,
        inspect_data=inspect_data,
        run_command_line=run_command_line,
    )
    if error:
        return error
    ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
    extension = docker_ensure_pgvector(backend, cwd=layout.coordination_root, timeout=args.timeout)
    inspect_data = docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    backend_state = grepai_backend_state(
        layout,
        backend,
        settings_path=settings_path,
        status="running",
        postgres_host=backend["postgresHost"],
        postgres_port=postgres_port,
        image_digest=docker_repo_digest(backend["image"], cwd=layout.coordination_root, timeout=args.timeout),
        container_id=str(inspect_data.get("Id", "")) if inspect_data else None,
    )
    write_json(layout.backend_state_file, backend_state)
    write_json(backend["imageLockFile"], backend_state["backend"]["imageLock"])
    return {
        "provider": "grepai",
        "action": "backend-start",
        "ok": extension["returncode"] == 0,
        "containerName": backend["containerName"],
        "network": network_result,
        "ports": backend_state["backend"]["ports"],
        "commands": {
            "pull": commands["pull"],
            "remove": commands["remove"],
            "forcedRemove": forced_remove_result,
            "run": commands["run"],
        },
        "ping": ping,
        "extension": extension,
    }


def grepai_backend_start_commands(
    args: argparse.Namespace,
    *,
    layout: Any,
    backend: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    run_command_line: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    pull_result, error = grepai_run_checked_command(
        [docker_command(), "pull", backend["image"]],
        layout=layout,
        timeout=args.timeout,
        action="backend-start",
    )
    if error:
        return {"pull": pull_result, "remove": None, "run": None}, error
    rm_result, error = grepai_backend_remove_existing(args, layout, backend, inspect_data)
    if error:
        return {"pull": pull_result, "remove": rm_result, "run": None}, error
    run_result, error = grepai_run_checked_command(
        run_command_line,
        layout=layout,
        timeout=args.timeout,
        action="backend-start",
    )
    return {"pull": pull_result, "remove": rm_result, "run": run_result}, error


def grepai_backend_remove_existing(
    args: argparse.Namespace,
    layout: Any,
    backend: dict[str, Any],
    inspect_data: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not inspect_data:
        return None, None
    return grepai_run_checked_command(
        [docker_command(), "rm", backend["containerName"]],
        layout=layout,
        timeout=args.timeout,
        action="backend-start",
    )


def grepai_backend_start(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, layout, backend, network_name = grepai_backend_start_context(args)
    network_result = grepai_backend_ensure_network(args, layout, network_name)
    if not network_result.get("ok"):
        return grepai_backend_network_error(network_result)
    inspect_data = grepai_backend_inspect(args, layout, backend)
    inspect_data, forced_remove_result, error = grepai_backend_remove_mismatched_container(
        args, layout, backend, inspect_data
    )
    if error:
        return error
    if grepai_backend_already_running(inspect_data):
        return grepai_backend_existing_start_result(
            args,
            settings_path=settings_path,
            layout=layout,
            backend=backend,
            network_name=network_name,
            network_result=network_result,
            inspect_data=inspect_data,
        )
    return grepai_backend_create_start_result(
        args,
        settings_path=settings_path,
        layout=layout,
        backend=backend,
        network_name=network_name,
        network_result=network_result,
        inspect_data=inspect_data,
        forced_remove_result=forced_remove_result,
    )


def grepai_backend_ensure_network(
    args: argparse.Namespace, layout: Any, network_name: str
) -> dict[str, Any]:
    return docker_ensure_network(
        network_name,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def grepai_backend_network_error(network_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": "backend-start",
        "ok": False,
        "network": network_result,
    }


def grepai_backend_inspect(
    args: argparse.Namespace, layout: Any, backend: dict[str, Any]
) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )


def grepai_backend_already_running(inspect_data: dict[str, Any] | None) -> bool:
    return bool(inspect_data) and docker_container_running(inspect_data)


def grepai_backend_create_start_result(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    layout: Any,
    backend: dict[str, Any],
    network_name: str,
    network_result: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    forced_remove_result: dict[str, Any] | None,
) -> dict[str, Any]:
    postgres_port = grepai_backend_host_port(args, backend)
    run_command_line = grepai_backend_run_command_line(
        layout, backend, network_name, postgres_port
    )
    commands = grepai_backend_planned_commands(
        backend, network_name, run_command_line, inspect_data
    )
    if args.dry_run:
        return grepai_backend_dry_run_result(
            settings_path=settings_path,
            layout=layout,
            backend=backend,
            network_result=network_result,
            commands=commands,
            postgres_port=postgres_port,
        )
    return grepai_backend_new_start_result(
        args,
        settings_path=settings_path,
        layout=layout,
        backend=backend,
        network_result=network_result,
        inspect_data=inspect_data,
        forced_remove_result=forced_remove_result,
        run_command_line=run_command_line,
        postgres_port=postgres_port,
    )
