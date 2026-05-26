"""GrepAI Ollama embedder lifecycle."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.context import (
    ContextProviderError,
)
from agents_remember.providers.grepai.lifecycle.core import *
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import (
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
)
from agents_remember.providers.lifecycle.host_ports import allocate_host_port
from agents_remember.providers.lifecycle.state_files import (
    write_json,
)


def docker_wait_for_ollama(embedder: dict[str, Any], *, cwd: Path, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_result: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last_result = run_command(
            [docker_command(), "exec", embedder["containerName"], "ollama", "list"],
            cwd=cwd,
            timeout=30,
        )
        if last_result["returncode"] == 0:
            return last_result
        time.sleep(2)
    if last_result is None:
        raise ContextProviderError("timed out waiting for Ollama health check")
    raise ContextProviderError(
        f"Ollama health check failed: {last_result['stderr'] or last_result['stdout']}"
    )


def ollama_model_present(list_output: str, model: str) -> bool:
    wanted = model.split(":", 1)[0]
    for line in list_output.splitlines():
        first = line.split(maxsplit=1)[0] if line.split() else ""
        if first in {model, f"{wanted}:latest"} or first.split(":", 1)[0] == wanted:
            return True
    return False


def docker_ensure_ollama_model(
    embedder: dict[str, Any], *, cwd: Path, timeout: int
) -> dict[str, Any]:
    listed = docker_wait_for_ollama(embedder, cwd=cwd, timeout=timeout)
    if ollama_model_present(listed.get("stdout", ""), embedder["model"]):
        return {"ok": True, "model": embedder["model"], "alreadyPresent": True, "list": listed}
    pull = run_command(
        [docker_command(), "exec", embedder["containerName"], "ollama", "pull", embedder["model"]],
        cwd=cwd,
        timeout=timeout,
    )
    return {
        "ok": pull["returncode"] == 0,
        "model": embedder["model"],
        "alreadyPresent": False,
        "list": listed,
        "pull": pull,
    }


def grepai_embedder_health(
    args: argparse.Namespace,
    layout: Any,
    embedder: dict[str, Any],
    *,
    running: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not running or args.dry_run:
        return None, None
    try:
        list_result = docker_wait_for_ollama(
            embedder, cwd=layout.coordination_root, timeout=args.timeout
        )
        model = {
            "name": embedder["model"],
            "present": ollama_model_present(list_result.get("stdout", ""), embedder["model"]),
        }
        return list_result, model
    except (ContextProviderError, subprocess.TimeoutExpired, OSError) as error:
        return {"returncode": 1, "stdout": "", "stderr": str(error)}, {
            "name": embedder["model"],
            "present": False,
        }


def grepai_embedder_runtime_details(
    embedder: dict[str, Any],
    network_name: str,
    inspect_data: dict[str, Any] | None,
) -> dict[str, Any]:
    http_mapping = (
        docker_container_port(inspect_data, embedder["httpContainerPort"])
        if inspect_data
        else None
    )
    actual_data_mount = docker_data_mount_source(inspect_data, embedder["dataDestination"])
    connected_networks = sorted(docker_container_networks(inspect_data))
    return {
        "actualDataMount": actual_data_mount,
        "dataMountMatches": docker_host_path_matches(actual_data_mount, embedder["dataRoot"])
        if inspect_data
        else False,
        "connectedNetworks": connected_networks,
        "networkConnected": network_name in connected_networks if inspect_data else False,
        "httpEndpoint": http_mapping or (embedder["httpHost"], embedder["httpHostPort"]),
    }


def grepai_embedder_status_ok(
    *,
    running: bool,
    details: dict[str, Any],
    list_result: dict[str, Any] | None,
    model: dict[str, Any] | None,
) -> bool:
    return all(
        (
            bool(running),
            bool(details["dataMountMatches"]),
            bool(details["networkConnected"]),
            command_ok(list_result),
            model_present(model),
        )
    )


def grepai_embedder_backend_status(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    embedder = grepai_embedder_backend_settings(provider_settings, layout)
    if embedder.get("provider") != "ollama" or embedder.get("mode") != "docker":
        return {
            "provider": "grepai",
            "action": "embedder-status",
            "ok": True,
            "settingsFile": settings_path.as_posix(),
            "embedder": embedder,
        }
    network_name = grepai_network_name(provider_settings)
    inspect_data = (
        None
        if args.dry_run
        else docker_inspect_container(
            embedder["containerName"], cwd=layout.coordination_root, timeout=args.timeout
        )
    )
    running = docker_container_running(inspect_data)
    list_result, model = grepai_embedder_health(args, layout, embedder, running=running)
    details = grepai_embedder_runtime_details(embedder, network_name, inspect_data)
    http_host, http_port = details["httpEndpoint"]
    return {
        "provider": "grepai",
        "action": "embedder-status",
        "ok": grepai_embedder_status_ok(
            running=running,
            details=details,
            list_result=list_result,
            model=model,
        ),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "containerName": embedder["containerName"],
        "image": embedder["image"],
        "running": running,
        "runtimeRoot": embedder["runtimeRoot"].as_posix(),
        "dataRoot": embedder["dataRoot"].as_posix(),
        "network": {
            "name": network_name,
            "connected": details["networkConnected"],
            "containerNetworks": details["connectedNetworks"],
        },
        "dataMount": {
            "expected": embedder["dataRoot"].as_posix(),
            "actual": details["actualDataMount"],
            "matches": details["dataMountMatches"],
        },
        "ports": {
            "http": {
                "bindHost": http_host,
                "hostPort": http_port,
                "containerPort": embedder["httpContainerPort"],
            },
        },
        "list": list_result,
        "model": model,
    }


def grepai_embedder_start_context(
    args: argparse.Namespace,
) -> tuple[Path, dict[str, Any], Any, dict[str, Any], str]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    embedder = grepai_embedder_backend_settings(provider_settings, layout)
    return settings_path, provider_settings, layout, embedder, grepai_network_name(provider_settings)


def grepai_embedder_external_start_result(
    settings_path: Path, embedder: dict[str, Any]
) -> dict[str, Any] | None:
    if embedder.get("provider") == "ollama" and embedder.get("mode") == "docker":
        return None
    return {
        "provider": "grepai",
        "action": "embedder-start",
        "ok": True,
        "settingsFile": settings_path.as_posix(),
        "embedder": embedder,
    }


def grepai_embedder_prepare_storage(args: argparse.Namespace, embedder: dict[str, Any]) -> None:
    if args.dry_run:
        return
    embedder["dataRoot"].mkdir(parents=True, exist_ok=True)
    embedder["imageLockFile"].parent.mkdir(parents=True, exist_ok=True)


def grepai_embedder_remove_mismatched_container(
    args: argparse.Namespace,
    layout: Any,
    embedder: dict[str, Any],
    inspect_data: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if not inspect_data:
        return inspect_data, None, None
    actual_mount = docker_data_mount_source(inspect_data, embedder["dataDestination"])
    if docker_host_path_matches(actual_mount, embedder["dataRoot"]):
        return inspect_data, None, None
    result = run_command(
        [docker_command(), "rm", "-f", embedder["containerName"]],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    if result["returncode"] != 0:
        return inspect_data, result, {
            "provider": "grepai",
            "action": "embedder-start",
            "ok": False,
            "command": result,
        }
    return None, result, None


def grepai_embedder_existing_start_result(
    args: argparse.Namespace,
    *,
    layout: Any,
    embedder: dict[str, Any],
    network_name: str,
    network_result: dict[str, Any],
    inspect_data: dict[str, Any],
) -> dict[str, Any]:
    network_connect = docker_ensure_container_network(
        embedder["containerName"],
        network_name,
        inspect_data=inspect_data,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    if not network_connect.get("ok"):
        return {
            "provider": "grepai",
            "action": "embedder-start",
            "ok": False,
            "network": network_result,
            "networkConnect": network_connect,
        }
    model = docker_ensure_ollama_model(embedder, cwd=layout.coordination_root, timeout=args.timeout)
    write_json(
        embedder["imageLockFile"],
        {
            "image": embedder["image"],
            "repoDigest": docker_repo_digest(
                embedder["image"], cwd=layout.coordination_root, timeout=args.timeout
            ),
        },
    )
    return {
        "provider": "grepai",
        "action": "embedder-start",
        "ok": bool(model.get("ok")),
        "alreadyRunning": True,
        "containerName": embedder["containerName"],
        "network": network_result,
        "networkConnect": network_connect,
        "model": model,
    }


def grepai_embedder_host_port(args: argparse.Namespace, embedder: dict[str, Any]) -> int:
    configured_port = embedder["httpHostPort"]
    if args.dry_run:
        return 11434 if str(configured_port) == "auto" else int(configured_port)
    return allocate_host_port(embedder["httpHost"], configured_port, 11434)


def grepai_embedder_run_command_line(
    embedder: dict[str, Any], network_name: str, http_port: int
) -> list[str]:
    return [
        docker_command(),
        "run",
        "-d",
        "--name",
        embedder["containerName"],
        "--restart",
        "unless-stopped",
        "--network",
        network_name,
        "-p",
        f"{embedder['httpHost']}:{http_port}:{embedder['httpContainerPort']}",
        "-v",
        f"{embedder['dataRoot']}:{embedder['dataDestination']}",
        embedder["image"],
    ]


def grepai_embedder_planned_commands(
    embedder: dict[str, Any],
    network_name: str,
    run_command_line: list[str],
    inspect_data: dict[str, Any] | None,
) -> list[list[str]]:
    commands = [
        [docker_command(), "network", "inspect", network_name],
        [docker_command(), "network", "create", network_name],
        [docker_command(), "pull", embedder["image"]],
    ]
    if inspect_data:
        commands.append([docker_command(), "rm", embedder["containerName"]])
    commands.append(run_command_line)
    return commands


def grepai_embedder_dry_run_result(
    *,
    settings_path: Path,
    embedder: dict[str, Any],
    network_result: dict[str, Any],
    commands: list[list[str]],
    http_port: int,
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": "embedder-start",
        "ok": True,
        "dryRun": True,
        "settingsFile": settings_path.as_posix(),
        "network": network_result,
        "commands": commands,
        "ports": {
            "http": {
                "bindHost": embedder["httpHost"],
                "hostPort": http_port,
                "containerPort": embedder["httpContainerPort"],
            },
        },
    }


def grepai_embedder_new_start_result(
    args: argparse.Namespace,
    *,
    layout: Any,
    embedder: dict[str, Any],
    network_result: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    forced_remove_result: dict[str, Any] | None,
    run_command_line: list[str],
    http_port: int,
) -> dict[str, Any]:
    pull_result, error = grepai_run_checked_command(
        [docker_command(), "pull", embedder["image"]],
        layout=layout,
        timeout=args.timeout,
        action="embedder-start",
    )
    if error:
        return error
    rm_result = None
    if inspect_data:
        rm_result, error = grepai_run_checked_command(
            [docker_command(), "rm", embedder["containerName"]],
            layout=layout,
            timeout=args.timeout,
            action="embedder-start",
        )
        if error:
            return error
    run_result, error = grepai_run_checked_command(
        run_command_line,
        layout=layout,
        timeout=args.timeout,
        action="embedder-start",
    )
    if error:
        return error
    model = docker_ensure_ollama_model(embedder, cwd=layout.coordination_root, timeout=args.timeout)
    write_json(
        embedder["imageLockFile"],
        {
            "image": embedder["image"],
            "repoDigest": docker_repo_digest(
                embedder["image"], cwd=layout.coordination_root, timeout=args.timeout
            ),
        },
    )
    return {
        "provider": "grepai",
        "action": "embedder-start",
        "ok": bool(model.get("ok")),
        "containerName": embedder["containerName"],
        "network": network_result,
        "ports": {
            "http": {
                "bindHost": embedder["httpHost"],
                "hostPort": http_port,
                "containerPort": embedder["httpContainerPort"],
            },
        },
        "commands": {
            "pull": pull_result,
            "remove": rm_result,
            "forcedRemove": forced_remove_result,
            "run": run_result,
        },
        "model": model,
    }


def grepai_embedder_backend_start(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, _, layout, embedder, network_name = grepai_embedder_start_context(args)
    external_result = grepai_embedder_external_start_result(settings_path, embedder)
    if external_result:
        return external_result
    network_result = grepai_embedder_ensure_network(args, layout, network_name)
    if not network_result.get("ok"):
        return grepai_embedder_network_error(network_result)
    grepai_embedder_prepare_storage(args, embedder)
    inspect_data = grepai_embedder_inspect(args, layout, embedder)
    inspect_data, forced_remove_result, error = grepai_embedder_remove_mismatched_container(
        args, layout, embedder, inspect_data
    )
    if error:
        return error
    if grepai_embedder_already_running(inspect_data):
        return grepai_embedder_existing_start_result(
            args,
            layout=layout,
            embedder=embedder,
            network_name=network_name,
            network_result=network_result,
            inspect_data=inspect_data,
        )
    return grepai_embedder_create_start_result(
        args,
        settings_path=settings_path,
        embedder=embedder,
        layout=layout,
        network_name=network_name,
        network_result=network_result,
        inspect_data=inspect_data,
        forced_remove_result=forced_remove_result,
    )


def grepai_embedder_ensure_network(
    args: argparse.Namespace, layout: Any, network_name: str
) -> dict[str, Any]:
    return docker_ensure_network(
        network_name,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def grepai_embedder_network_error(network_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": "embedder-start",
        "ok": False,
        "network": network_result,
    }


def grepai_embedder_inspect(
    args: argparse.Namespace, layout: Any, embedder: dict[str, Any]
) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        embedder["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )


def grepai_embedder_already_running(inspect_data: dict[str, Any] | None) -> bool:
    return bool(inspect_data) and docker_container_running(inspect_data)


def grepai_embedder_create_start_result(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    embedder: dict[str, Any],
    layout: Any,
    network_name: str,
    network_result: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    forced_remove_result: dict[str, Any] | None,
) -> dict[str, Any]:
    http_port = grepai_embedder_host_port(args, embedder)
    run_command_line = grepai_embedder_run_command_line(embedder, network_name, http_port)
    commands = grepai_embedder_planned_commands(
        embedder, network_name, run_command_line, inspect_data
    )
    if args.dry_run:
        return grepai_embedder_dry_run_result(
            settings_path=settings_path,
            embedder=embedder,
            network_result=network_result,
            commands=commands,
            http_port=http_port,
        )
    return grepai_embedder_new_start_result(
        args,
        layout=layout,
        embedder=embedder,
        network_result=network_result,
        inspect_data=inspect_data,
        forced_remove_result=forced_remove_result,
        run_command_line=run_command_line,
        http_port=http_port,
    )
