"""GrepAI PostgreSQL backend lifecycle."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

# Leaf imports, not the providers.context aggregator: the aggregator
# star-imports grepai context back, which is a circular import for any entry
# point that touches grepai modules first.
from agents_remember.providers.context_common import ContextProviderError
from agents_remember.providers.grepai.context import ensure_grepai_runtime_layout
from agents_remember.providers.grepai.context.constants import GREPAI_POSTGRES_DEFAULT_PORT
from agents_remember.providers.grepai.lifecycle.compose import (
    grepai_compose_project,
    grepai_compose_render,
    grepai_compose_summary,
)
from agents_remember.providers.grepai.lifecycle.core import *
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.compose_runtime import (
    BackendStartReconciliation,
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
)
from agents_remember.providers.lifecycle.host_ports import allocate_host_port
from agents_remember.providers.lifecycle.state_files import (
    read_json,
    write_json,
)


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


def raise_postgres_timeout(last_result: dict[str, Any] | None) -> NoReturn:
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


@dataclass(frozen=True)
class GrepaiBackendContext:
    """The GrepAI backend resolved from one lifecycle invocation's settings.

    Which settings file the invocation read, the provider entry it found, the
    runtime layout, the resolved backend settings, and the docker network the
    backend joins. Every backend command needs all of it.
    """

    settings_path: Path
    provider_settings: dict[str, Any]
    layout: GrepaiRuntimeLayout
    backend: dict[str, Any]
    network_name: str


def grepai_backend_state(
    context: GrepaiBackendContext,
    *,
    status: str,
    postgres_port: int,
    image_digest: str | None,
    container_id: str | None,
) -> dict[str, Any]:
    layout = context.layout
    backend = context.backend
    postgres_host = backend["postgresHost"]
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
        "settingsFile": context.settings_path.as_posix(),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def grepai_backend_health_checks(
    args: argparse.Namespace,
    layout: GrepaiRuntimeLayout,
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
    layout: GrepaiRuntimeLayout,
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
        "containerState": docker_container_state_summary(inspect_data),
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


def grepai_backend_start_context(args: argparse.Namespace) -> GrepaiBackendContext:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    if not args.dry_run:
        ensure_grepai_runtime_layout(layout)
    backend = grepai_backend_settings(provider_settings, layout)
    if backend["type"] != "postgres" or backend["mode"] != "docker":
        raise ContextProviderError("managed GrepAI backend must be postgres docker")
    return GrepaiBackendContext(
        settings_path=settings_path,
        provider_settings=provider_settings,
        layout=layout,
        backend=backend,
        network_name=grepai_network_name(provider_settings),
    )


def grepai_backend_remove_mismatched_container(
    args: argparse.Namespace,
    layout: GrepaiRuntimeLayout,
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
        return (
            inspect_data,
            result,
            {
                "provider": "grepai",
                "action": "backend-start",
                "ok": False,
                "command": result,
            },
        )
    return None, result, None


def grepai_existing_backend_host_port(
    backend: dict[str, Any], inspect_data: dict[str, Any] | None
) -> int | None:
    if not inspect_data:
        return None
    mapping = docker_container_port(inspect_data, backend["postgresContainerPort"])
    return mapping[1] if mapping else None


def grepai_backend_host_port(
    args: argparse.Namespace, backend: dict[str, Any], inspect_data: dict[str, Any] | None
) -> int:
    existing = grepai_existing_backend_host_port(backend, inspect_data)
    if existing is not None:
        return existing
    configured_port = backend["postgresHostPort"]
    preferred_port = int(GREPAI_POSTGRES_DEFAULT_PORT)
    if args.dry_run:
        return preferred_port if str(configured_port) == "auto" else int(configured_port)
    return allocate_host_port(backend["postgresHost"], configured_port, preferred_port)


def grepai_backend_dry_run_result(
    context: GrepaiBackendContext,
    reconciliation: BackendStartReconciliation,
    *,
    commands: list[dict[str, Any]],
    postgres_port: int,
    compose: dict[str, Any] | None = None,
) -> dict[str, Any]:
    layout = context.layout
    backend = context.backend
    return {
        "provider": "grepai",
        "action": "backend-start",
        "ok": True,
        "dryRun": True,
        "settingsFile": context.settings_path.as_posix(),
        "commands": commands,
        "compose": compose,
        "migration": reconciliation.migration,
        "backendRuntimeRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "network": reconciliation.network,
        "ports": {
            "postgres": {
                "bindHost": backend["postgresHost"],
                "hostPort": postgres_port,
                "containerPort": backend["postgresContainerPort"],
            },
        },
    }


def grepai_backend_start(args: argparse.Namespace) -> dict[str, Any]:
    context = grepai_backend_start_context(args)
    layout, backend = context.layout, context.backend
    migration = grepai_project_migration(args, context.provider_settings, layout, backend)
    inspect_data = grepai_backend_inspect(args, layout, backend)
    inspect_data, forced_remove_result, error = grepai_backend_remove_mismatched_container(
        args, layout, backend, inspect_data
    )
    if error:
        return error
    return grepai_backend_create_start_result(
        args,
        context,
        BackendStartReconciliation(
            network={"ok": True, "name": context.network_name, "managedBy": "docker-compose"},
            migration=migration,
            forced_remove=forced_remove_result,
        ),
        inspect_data=inspect_data,
    )


def grepai_backend_inspect(
    args: argparse.Namespace, layout: GrepaiRuntimeLayout, backend: dict[str, Any]
) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )


def grepai_backend_create_start_result(
    args: argparse.Namespace,
    context: GrepaiBackendContext,
    reconciliation: BackendStartReconciliation,
    *,
    inspect_data: dict[str, Any] | None,
) -> dict[str, Any]:
    layout = context.layout
    backend = context.backend
    provider_settings = context.provider_settings
    postgres_port = grepai_backend_host_port(args, backend, inspect_data)
    runner = grepai_runner_settings(provider_settings, layout)
    render = grepai_compose_render(
        provider_settings,
        layout,
        runner,
        backend,
        GrepaiServicePorts(postgres=postgres_port),
    )
    command_args = ["up", "-d", "postgres"]
    if args.dry_run:
        return grepai_backend_dry_run_result(
            context,
            reconciliation,
            commands=[compose_plan(render, command_args, cwd=layout.coordination_root)],
            postgres_port=postgres_port,
            compose=grepai_compose_summary(render),
        )
    up_result = run_compose(
        render, command_args, cwd=layout.coordination_root, timeout=args.timeout
    )
    if up_result["returncode"] != 0:
        return {
            "provider": "grepai",
            "action": "backend-start",
            "ok": False,
            "command": up_result,
            "compose": grepai_compose_summary(render),
            "migration": reconciliation.migration,
        }
    ping = docker_wait_for_postgres(backend, cwd=layout.coordination_root, timeout=args.timeout)
    extension = docker_ensure_pgvector(backend, cwd=layout.coordination_root, timeout=args.timeout)
    inspect_data = docker_inspect_container(
        backend["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    backend_state = grepai_backend_state(
        context,
        status="running",
        postgres_port=postgres_port,
        image_digest=docker_repo_digest(
            backend["image"], cwd=layout.coordination_root, timeout=args.timeout
        ),
        container_id=str(inspect_data.get("Id", "")) if inspect_data else None,
    )
    write_json(layout.backend_state_file, backend_state)
    write_json(backend["imageLockFile"], backend_state["backend"]["imageLock"])
    return {
        "provider": "grepai",
        "action": "backend-start",
        "ok": extension["returncode"] == 0,
        "containerName": backend["containerName"],
        "network": reconciliation.network,
        "ports": backend_state["backend"]["ports"],
        "commands": {
            "forcedRemove": reconciliation.forced_remove,
            "migration": reconciliation.migration,
            "up": up_result,
        },
        "compose": grepai_compose_summary(render),
        "ping": ping,
        "extension": extension,
    }


def grepai_project_migration(
    args: argparse.Namespace,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    backend: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend = backend or grepai_backend_settings(provider_settings, layout)
    embedder = grepai_embedder_backend_settings(provider_settings, layout)
    runner = grepai_runner_settings(provider_settings, layout)
    project_name = grepai_compose_project(provider_settings)
    return {
        "containers": {
            "postgres": remove_unmanaged_compose_container(
                backend["containerName"],
                project_name=project_name,
                cwd=layout.coordination_root,
                timeout=args.timeout,
                dry_run=args.dry_run,
            ),
            "ollama": remove_unmanaged_compose_container(
                embedder["containerName"],
                project_name=project_name,
                cwd=layout.coordination_root,
                timeout=args.timeout,
                dry_run=args.dry_run,
            ),
            "watcher": remove_unmanaged_compose_container(
                runner["containerName"],
                project_name=project_name,
                cwd=layout.coordination_root,
                timeout=args.timeout,
                dry_run=args.dry_run,
            ),
        },
        "network": remove_unmanaged_compose_network(
            grepai_network_name(provider_settings),
            project_name=project_name,
            cwd=layout.coordination_root,
            timeout=args.timeout,
            dry_run=args.dry_run,
        ),
    }
