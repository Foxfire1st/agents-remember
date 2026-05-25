"""GrepAI runner image and watcher lifecycle."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import time
from typing import Any

from agents_remember.providers.lifecycle_modules.common import (
    docker_command,
    docker_container_networks,
    docker_container_running,
    docker_ensure_container_network,
    docker_ensure_network,
    docker_image_exists,
    docker_inspect_container,
    docker_repo_digest,
    run_command,
    write_json,
)
from agents_remember.providers.lifecycle_modules.grepai.core import *


def grepai_runner_dockerfile(version: str, arch: str) -> str:
    asset_name = f"grepai_{version}_linux_{arch}.tar.gz"
    release_base = f"https://github.com/yoanbernabeu/grepai/releases/download/v{version}"
    return f"""FROM debian:bookworm-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates curl tar \\
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \\
    curl -fsSL -o /tmp/grepai.tar.gz {release_base}/{asset_name}; \\
    tar -xzf /tmp/grepai.tar.gz -C /tmp; \\
    find /tmp -type f -name grepai -exec install -m 0755 {{}} /usr/local/bin/grepai \\; -quit; \\
    test -x /usr/local/bin/grepai; \\
    rm -rf /tmp/*
ENTRYPOINT ["grepai"]
"""


def grepai_runner_image_build(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
) -> dict[str, Any]:
    _, _, layout = grepai_layout_from_args(args)
    dockerfile = runner["buildRoot"] / "Dockerfile"
    command = [docker_command(), "build", "-t", runner["image"], runner["buildRoot"].as_posix()]
    if args.dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "image": runner["image"],
            "dockerfile": dockerfile.as_posix(),
            "command": command,
        }
    if docker_image_exists(runner["image"], cwd=layout.coordination_root, timeout=args.timeout):
        return {"ok": True, "image": runner["image"], "alreadyExists": True}
    runner["buildRoot"].mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        grepai_runner_dockerfile(runner["version"], runner["releaseArch"]),
        encoding="utf-8",
    )
    result = run_command(command, cwd=layout.coordination_root, timeout=args.timeout)
    image_digest = docker_repo_digest(
        runner["image"], cwd=layout.coordination_root, timeout=args.timeout
    )
    write_json(runner["imageLockFile"], {"image": runner["image"], "repoDigest": image_digest})
    return {
        "ok": result["returncode"] == 0,
        "image": runner["image"],
        "dockerfile": dockerfile.as_posix(),
        "command": result,
    }


def grepai_watcher_container_command(
    layout: Any,
    runner: dict[str, Any],
    network_name: str,
) -> list[str]:
    command = [
        docker_command(),
        "run",
        "-d",
        "--name",
        runner["containerName"],
        "--restart",
        "unless-stopped",
        "--network",
        network_name,
    ]
    for env_value in grepai_container_env(runner):
        command.extend(["-e", env_value])
    command.extend(
        [
            "-v",
            f"{layout.runtime_root}:{runner['runtimeMount']}",
            "-v",
            f"{layout.logs_root}:{runner['logsMount']}",
            runner["image"],
            "watch",
            "--workspace",
            layout.workspace_name,
            "--log-dir",
            runner["logsMount"],
        ]
    )
    return command


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
        "image": runner["image"],
        "running": running,
        "network": {
            "name": network_name,
            "connected": network_connected,
            "containerNetworks": connected_networks,
        },
        "workspaceStatus": workspace_status,
    }


def grepai_watcher_inspect(
    args: argparse.Namespace, layout: Any, runner: dict[str, Any]
) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        runner["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )


def grepai_watcher_workspace_status(
    args: argparse.Namespace,
    layout: Any,
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
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    _, _, layout = grepai_layout_from_args(args)
    network_result = docker_ensure_network(
        network_name,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
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


def grepai_watcher_existing_start_result(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    network_name: str,
    network_result: dict[str, Any],
    image: dict[str, Any],
    layout: Any,
    inspect_data: dict[str, Any],
) -> dict[str, Any]:
    network_connect = docker_ensure_container_network(
        runner["containerName"],
        network_name,
        inspect_data=inspect_data,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    return {
        "provider": "grepai",
        "action": "watcher-start",
        "ok": bool(network_connect.get("ok")),
        "alreadyRunning": True,
        "containerName": runner["containerName"],
        "network": network_result,
        "networkConnect": network_connect,
        "image": image,
    }


def grepai_watcher_planned_commands(
    runner: dict[str, Any],
    run_command_line: list[str],
    inspect_data: dict[str, Any] | None,
) -> list[list[str]]:
    commands = []
    if inspect_data:
        commands.append([docker_command(), "rm", runner["containerName"]])
    commands.append(run_command_line)
    return commands


def grepai_watcher_dry_run_start_result(
    *,
    runner: dict[str, Any],
    network_result: dict[str, Any],
    image: dict[str, Any],
    commands: list[list[str]],
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


def grepai_watcher_new_start_result(
    args: argparse.Namespace,
    *,
    layout: Any,
    runner: dict[str, Any],
    network_result: dict[str, Any],
    image: dict[str, Any],
    inspect_data: dict[str, Any] | None,
    run_command_line: list[str],
) -> dict[str, Any]:
    rm_result = None
    if inspect_data:
        rm_result, error = grepai_run_checked_command(
            [docker_command(), "rm", runner["containerName"]],
            layout=layout,
            timeout=args.timeout,
            action="watcher-start",
        )
        if error:
            return error
    run_result = run_command(run_command_line, cwd=layout.coordination_root, timeout=args.timeout)
    inspect_data = docker_inspect_container(
        runner["containerName"], cwd=layout.coordination_root, timeout=args.timeout
    )
    return {
        "provider": "grepai",
        "action": "watcher-start",
        "ok": run_result["returncode"] == 0 and docker_container_running(inspect_data),
        "containerName": runner["containerName"],
        "network": network_result,
        "image": image,
        "commands": {"remove": rm_result, "run": run_result},
    }


def grepai_watcher_container_start(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    network_name: str,
) -> dict[str, Any]:
    layout, network_result, image, error = grepai_watcher_start_prerequisites(
        args, runner=runner, network_name=network_name
    )
    if error:
        return error
    inspect_data = grepai_watcher_inspect(args, layout, runner)
    if grepai_watcher_already_running(inspect_data):
        return grepai_watcher_existing_start_result(
            args,
            runner=runner,
            network_name=network_name,
            network_result=network_result,
            image=image,
            layout=layout,
            inspect_data=inspect_data,
        )
    return grepai_watcher_create_start_result(
        args,
        runner=runner,
        network_name=network_name,
        network_result=network_result,
        image=image,
        layout=layout,
        inspect_data=inspect_data,
    )


def grepai_watcher_already_running(inspect_data: dict[str, Any] | None) -> bool:
    return bool(inspect_data) and docker_container_running(inspect_data)


def grepai_watcher_create_start_result(
    args: argparse.Namespace,
    *,
    runner: dict[str, Any],
    network_name: str,
    network_result: dict[str, Any],
    image: dict[str, Any],
    layout: Any,
    inspect_data: dict[str, Any] | None,
) -> dict[str, Any]:
    run_command_line = grepai_watcher_container_command(layout, runner, network_name)
    commands = grepai_watcher_planned_commands(runner, run_command_line, inspect_data)
    if args.dry_run:
        return grepai_watcher_dry_run_start_result(
            runner=runner,
            network_result=network_result,
            image=image,
            commands=commands,
        )
    return grepai_watcher_new_start_result(
        args,
        layout=layout,
        runner=runner,
        network_result=network_result,
        image=image,
        inspect_data=inspect_data,
        run_command_line=run_command_line,
    )


def grepai_watcher_container_stop(args: argparse.Namespace) -> dict[str, Any]:
    _, provider_settings, layout = grepai_layout_from_args(args)
    runner = grepai_runner_settings(provider_settings, layout)
    command = [docker_command(), "stop", runner["containerName"]]
    if args.dry_run:
        return {
            "provider": "grepai",
            "action": "watcher-stop",
            "ok": True,
            "dryRun": True,
            "containerName": runner["containerName"],
            "command": command,
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
    result = run_command(command, cwd=layout.coordination_root, timeout=args.timeout)
    return {
        "provider": "grepai",
        "action": "watcher-stop",
        "ok": result["returncode"] == 0,
        "containerName": runner["containerName"],
        "command": result,
    }


def grepai_docker_state(
    layout: Any,
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
                "sourcePath": root.source_path.as_posix() if root.source_path else None,
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
