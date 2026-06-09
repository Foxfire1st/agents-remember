"""GrepAI runner image and watcher lifecycle."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import time
from typing import Any

from agents_remember.providers.grepai.lifecycle.backend import grepai_project_migration
from agents_remember.providers.grepai.lifecycle.compose import (
    grepai_compose_render,
    grepai_compose_summary,
)
from agents_remember.providers.grepai.lifecycle.core import *
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.compose_runtime import (
    compose_plan,
    provider_asset_path,
    run_compose,
)
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_container_networks,
    docker_container_running,
    docker_container_state_summary,
    docker_image_exists,
    docker_inspect_container,
    docker_repo_digest,
)
from agents_remember.providers.lifecycle.state_files import (
    write_json,
)


def grepai_runner_image_build(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
) -> dict[str, Any]:
    _, provider_settings, layout = grepai_layout_from_args(args)
    backend = grepai_backend_settings(provider_settings, layout)
    render = grepai_compose_render(provider_settings, layout, runner, backend)
    no_cache = bool(getattr(args, "no_cache", False))
    command_args = ["build", "--no-cache", "watcher"] if no_cache else ["build", "watcher"]
    dockerfile = provider_asset_path("docker", "grepai", "Dockerfile")
    if args.dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "image": runner["image"],
            "dockerfile": dockerfile.as_posix(),
            "buildContext": dockerfile.parent.as_posix(),
            "compose": grepai_compose_summary(render),
            "command": compose_plan(render, command_args, cwd=layout.coordination_root),
        }
    if not no_cache and docker_image_exists(
        runner["image"], cwd=layout.coordination_root, timeout=args.timeout
    ):
        return {"ok": True, "image": runner["image"], "alreadyExists": True}
    result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    image_digest = docker_repo_digest(
        runner["image"], cwd=layout.coordination_root, timeout=args.timeout
    )
    write_json(runner["imageLockFile"], {"image": runner["image"], "repoDigest": image_digest})
    return {
        "ok": result["returncode"] == 0,
        "image": runner["image"],
        "dockerfile": dockerfile.as_posix(),
        "buildContext": dockerfile.parent.as_posix(),
        "compose": grepai_compose_summary(render),
        "command": result,
    }


GREPAI_SCAN_PROGRESS_MARKERS = ("Indexing [", "Initial scan", "Embedding")
GREPAI_SCAN_COMPLETE_MARKER = "Initial scan complete"


def grepai_watcher_container_status(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    runner = grepai_runner_settings(provider_settings, layout)
    network_name = grepai_network_name(provider_settings)
    inspect_data = grepai_watcher_inspect(args, layout, runner)
    running = docker_container_running(inspect_data)
    connected_networks = sorted(docker_container_networks(inspect_data))
    network_connected = network_name in connected_networks if inspect_data else False
    workspace_status = grepai_watcher_workspace_status(args, layout, runner, running)
    return {
        "provider": "grepai",
        "action": "watcher-status",
        "ok": grepai_watcher_status_ok(running, network_connected, workspace_status),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "containerName": runner["containerName"],
        "containerState": docker_container_state_summary(inspect_data),
        "image": runner["image"],
        "running": running,
        "network": {
            "name": network_name,
            "connected": network_connected,
            "containerNetworks": connected_networks,
        },
        "workspaceStatus": workspace_status,
        "initialScan": grepai_watcher_initial_scan(args, layout, runner, inspect_data, running),
    }


def grepai_watcher_initial_scan(
    args: argparse.Namespace,
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    running: bool,
) -> dict[str, Any]:
    """Read the watcher's own scan markers from its container log.

    The watcher prints `Indexing [...] N% (i/n)` lines during the initial scan
    and `Initial scan complete` when done — the same marker mechanism the CGC
    probe uses, giving GrepAI indexing-state parity instead of `unknown`."""
    if not running or args.dry_run or not inspect_data:
        return {"state": "unknown"}
    started_at = docker_container_state_summary(inspect_data).get("startedAt")
    if not started_at:
        return {"state": "unknown"}
    result = run_command(
        [docker_command(), "logs", "--since", str(started_at), runner["containerName"]],
        cwd=layout.coordination_root,
        timeout=15,
        allow_timeout=True,
    )
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    return {"state": grepai_scan_state_from_log(text)}


def grepai_scan_state_from_log(text: str) -> str:
    if GREPAI_SCAN_COMPLETE_MARKER in text:
        return "complete"
    if any(marker in text for marker in GREPAI_SCAN_PROGRESS_MARKERS):
        return "in-progress"
    return "unknown"


def grepai_watcher_inspect(
    args: argparse.Namespace, layout: GrepaiRuntimeLayout, runner: dict[str, Any]
) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        runner["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )


def grepai_watcher_workspace_status(
    args: argparse.Namespace,
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
    running: bool,
) -> dict[str, Any] | None:
    if not running or args.dry_run:
        return None
    return run_command(
        [
            docker_command(),
            "exec",
            runner["containerName"],
            "grepai",
            "workspace",
            "status",
            layout.workspace_name,
        ],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )


def grepai_watcher_status_ok(
    running: bool,
    network_connected: bool,
    workspace_status: dict[str, Any] | None,
) -> bool:
    return all(
        [
            bool(running),
            network_connected,
            workspace_status is None or workspace_status.get("returncode") == 0,
        ]
    )


def grepai_watcher_start_prerequisites(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    network_name: str,
) -> tuple[GrepaiRuntimeLayout, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    _, _, layout = grepai_layout_from_args(args)
    network_result = {"ok": True, "name": network_name, "managedBy": "docker-compose"}
    image = grepai_runner_image_build(args, runner=runner)
    if network_result.get("ok") and image.get("ok"):
        return layout, network_result, image, None
    return layout, network_result, image, {
        "provider": "grepai",
        "action": "watcher-start",
        "ok": False,
        "network": network_result,
        "image": image,
    }


def grepai_watcher_dry_run_start_result(
    *,
    runner: dict[str, Any],
    network_result: dict[str, Any],
    image: dict[str, Any],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": "watcher-start",
        "ok": True,
        "dryRun": True,
        "containerName": runner["containerName"],
        "network": network_result,
        "image": image,
        "commands": commands,
    }


def grepai_watcher_container_start(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    network_name: str,
    postgres_port: int | str | None = None,
    ollama_port: int | str | None = None,
) -> dict[str, Any]:
    layout, network_result, image, error = grepai_watcher_start_prerequisites(
        args, runner=runner, network_name=network_name
    )
    if error:
        return error
    return grepai_watcher_create_start_result(
        args,
        runner=runner,
        network_result=network_result,
        image=image,
        layout=layout,
        postgres_port=postgres_port,
        ollama_port=ollama_port,
    )


def grepai_watcher_create_start_result(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    network_result: dict[str, Any],
    image: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    postgres_port: int | str | None,
    ollama_port: int | str | None,
) -> dict[str, Any]:
    _, provider_settings, _ = grepai_layout_from_args(args)
    backend = grepai_backend_settings(provider_settings, layout)
    render = grepai_compose_render(
        provider_settings,
        layout,
        runner,
        backend,
        postgres_port=postgres_port,
        ollama_port=ollama_port,
    )
    command_args = ["up", "-d", "watcher"]
    migration = grepai_project_migration(args, provider_settings, layout, backend)
    if args.dry_run:
        return grepai_watcher_dry_run_start_result(
            runner=runner,
            network_result=network_result,
            image=image,
            commands=[compose_plan(render, command_args, cwd=layout.coordination_root)],
        )
    up_result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    inspect_data = docker_inspect_container(
        runner["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    return {
        "provider": "grepai",
        "action": "watcher-start",
        "ok": up_result["returncode"] == 0 and docker_container_running(inspect_data),
        "containerName": runner["containerName"],
        "network": network_result,
        "image": image,
        "commands": {"migration": migration, "up": up_result},
        "compose": grepai_compose_summary(render),
    }


def grepai_watcher_container_stop(args: argparse.Namespace) -> dict[str, Any]:
    _, provider_settings, layout = grepai_layout_from_args(args)
    runner = grepai_runner_settings(provider_settings, layout)
    backend = grepai_backend_settings(provider_settings, layout)
    render = grepai_compose_render(provider_settings, layout, runner, backend)
    command_args = ["rm", "-sf", "watcher"]
    if args.dry_run:
        return {
            "provider": "grepai",
            "action": "watcher-stop",
            "ok": True,
            "dryRun": True,
            "containerName": runner["containerName"],
            "command": compose_plan(render, command_args, cwd=layout.coordination_root),
            "compose": grepai_compose_summary(render),
        }
    inspect_data = docker_inspect_container(
        runner["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    if not inspect_data:
        return {
            "provider": "grepai",
            "action": "watcher-stop",
            "ok": True,
            "containerName": runner["containerName"],
            "alreadyStopped": True,
        }
    result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    return {
        "provider": "grepai",
        "action": "watcher-stop",
        "ok": result["returncode"] == 0,
        "containerName": runner["containerName"],
        "compose": grepai_compose_summary(render),
        "command": result,
    }


def grepai_docker_state(
    layout: GrepaiRuntimeLayout,
    *,
    action: str,
    runner: dict[str, Any],
    backend_result: dict[str, Any] | None,
    embedder_result: dict[str, Any] | None,
    watcher_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "workspace": layout.workspace_name,
        "roots": [
            {
                "projectId": root.project_id,
                "path": root.path.as_posix(),
            }
            for root in layout.roots
        ],
        "runtimeRoot": layout.runtime_root.as_posix(),
        "workspaceConfigFile": layout.workspace_config_file.as_posix(),
        "process": {
            "mode": "docker-container-watch",
            "containerName": runner["containerName"],
            "image": runner["image"],
            "logDir": layout.logs_root.as_posix(),
        },
        "lastAction": action,
        "backend": backend_result,
        "embedder": embedder_result,
        "watcher": watcher_result,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
