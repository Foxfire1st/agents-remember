"""GrepAI high-level lifecycle actions."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

# Leaf imports; see backend.py for the aggregator-cycle rationale.
from agents_remember.providers.context_common import (
    ContextProviderError,
    file_sha256,
    provider_requirements_file,
    read_provider_pin,
)
from agents_remember.providers.grepai.context import (
    GREPAI_PIN,
    GREPAI_PROVIDER,
    ensure_grepai_requirements_file,
    ensure_grepai_runtime_layout,
)
from agents_remember.providers.grepai.lifecycle.backend import *
from agents_remember.providers.grepai.lifecycle.compose import (
    grepai_compose_render,
    grepai_compose_summary,
)
from agents_remember.providers.grepai.lifecycle.core import *
from agents_remember.providers.grepai.lifecycle.embedder import *
from agents_remember.providers.grepai.lifecycle.runner import *
from agents_remember.providers.lifecycle.compose_runtime import compose_plan, run_compose
from agents_remember.providers.lifecycle.process_status import process_namespace_status
from agents_remember.providers.lifecycle.state_files import read_json, write_json


def grepai_roots_payload(layout: GrepaiRuntimeLayout) -> list[dict[str, Any]]:
    return [
        {
            "projectId": root.project_id,
            "path": root.path.as_posix(),
        }
        for root in layout.roots
    ]


def grepai_docker_status(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
) -> dict[str, Any]:
    backend_status = grepai_backend_status(args)
    embedder_status = grepai_embedder_backend_status(args)
    watcher_status = grepai_watcher_container_status(args)
    return {
        "provider": "grepai",
        "action": "status",
        "ok": bool(backend_status.get("ok"))
        and bool(embedder_status.get("ok"))
        and bool(watcher_status.get("ok")),
        "dryRun": args.dry_run,
        "mode": "docker",
        "settingsFile": settings_path.as_posix(),
        "workspace": layout.workspace_name,
        "roots": grepai_roots_payload(layout),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "workspaceConfigFile": layout.workspace_config_file.as_posix(),
        "backend": backend_status,
        "embedder": embedder_status,
        "watcher": watcher_status,
        "managedProcess": {
            "mode": "docker-container-watch",
            "containerName": runner["containerName"],
            "alive": bool(watcher_status.get("running")),
        },
        "watcherRunning": bool(watcher_status.get("running")),
        "processNamespace": process_namespace_status(),
    }


def grepai_run_native_args(args: argparse.Namespace) -> list[str]:
    native_args = stripped_grepai_native_args(args)
    require_grepai_native_args(native_args)
    reject_grepai_watcher_native_args(native_args)
    return native_args


def stripped_grepai_native_args(args: argparse.Namespace) -> list[str]:
    native_args = list(getattr(args, "native_args", []) or [])
    return native_args[1:] if native_args and native_args[0] == "--" else native_args


def require_grepai_native_args(native_args: list[str]) -> None:
    if not native_args:
        raise ContextProviderError("grepai run requires GrepAI CLI arguments after --")


def reject_grepai_watcher_native_args(native_args: list[str]) -> None:
    if native_args[0] == "watch":
        raise ContextProviderError(
            "grepai run is for bounded GrepAI CLI commands; use grepai start/stop/refresh for watchers"
        )


def grepai_docker_run_command(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
) -> dict[str, Any]:
    backend = grepai_backend_settings(provider_settings, layout)
    render = grepai_compose_render(provider_settings, layout, runner, backend)
    command_args = ["exec", "-T", "watcher", "grepai", *grepai_run_native_args(args)]
    if args.dry_run:
        return {
            "provider": "grepai",
            "action": "run",
            "ok": True,
            "dryRun": True,
            "mode": "docker",
            "workspace": layout.workspace_name,
            "command": compose_plan(render, command_args, cwd=layout.coordination_root),
            "compose": grepai_compose_summary(render),
            "cwd": layout.coordination_root.as_posix(),
        }
    status = grepai_docker_status(
        args,
        settings_path=settings_path,
        layout=layout,
        runner=runner,
    )
    if not status["ok"]:
        return {**status, "action": "run", "ok": False}
    result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    return {
        "provider": "grepai",
        "action": "run",
        "ok": result["returncode"] == 0,
        "mode": "docker",
        "workspace": layout.workspace_name,
        "compose": grepai_compose_summary(render),
        "command": result,
    }


def grepai_docker_workspace_state(
    args: argparse.Namespace,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
) -> dict[str, Any]:
    backend = grepai_backend_settings(provider_settings, layout)
    embedder = grepai_embedder_backend_settings(provider_settings, layout)
    state = {
        "workspaceConfigFile": layout.workspace_config_file.as_posix(),
        "dsn": grepai_container_dsn(backend),
        "projectPaths": grepai_container_project_paths(layout, runner),
        "embedder": grepai_container_embedder_settings(provider_settings, embedder),
    }
    if args.dry_run:
        return state
    return prepare_grepai_workspace(
        layout,
        provider_settings,
        GrepaiWorkspaceConfig(
            dsn=state["dsn"],
            project_paths=state["projectPaths"],
            embedder_settings=state["embedder"],
        ),
    )


def grepai_docker_start(
    args: argparse.Namespace,
    *,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
) -> dict[str, Any]:
    backend_result = grepai_backend_start(args)
    if not backend_result.get("ok"):
        return {**backend_result, "action": "start", "ok": False}
    embedder_result = grepai_embedder_backend_start(args)
    if not embedder_result.get("ok"):
        return {**embedder_result, "action": "start", "ok": False}
    workspace = grepai_docker_workspace_state(args, provider_settings, layout, runner)
    watcher_result = grepai_watcher_container_start(
        args,
        runner=runner,
        network_name=grepai_network_name(provider_settings),
        ports=GrepaiServicePorts(
            postgres=backend_result.get("ports", {}).get("postgres", {}).get("hostPort"),
            ollama=embedder_result.get("ports", {}).get("http", {}).get("hostPort"),
        ),
    )
    if not args.dry_run:
        write_json(
            layout.state_file,
            grepai_docker_state(
                layout,
                GrepaiStackResults(
                    backend=backend_result,
                    embedder=embedder_result,
                    watcher=watcher_result,
                ),
                action="start",
                runner=runner,
            ),
        )
    return {
        "provider": "grepai",
        "action": "start",
        "ok": bool(watcher_result.get("ok")),
        "dryRun": args.dry_run,
        "mode": "docker",
        "workspace": layout.workspace_name,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "logDir": layout.logs_root.as_posix(),
        "backend": backend_result,
        "embedder": embedder_result,
        "watcher": watcher_result,
        "workspaceState": workspace,
    }


def grepai_docker_stop(
    args: argparse.Namespace,
    *,
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
) -> dict[str, Any]:
    watcher_result = grepai_watcher_container_stop(args)
    if not args.dry_run:
        state = read_json(layout.state_file)
        state["process"] = {
            "mode": "docker-container-watch",
            "containerName": runner["containerName"],
            "alive": False,
            "stoppedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        state["lastAction"] = "stop"
        write_json(layout.state_file, state)
    return {
        "provider": "grepai",
        "action": "stop",
        "ok": bool(watcher_result.get("ok")),
        "dryRun": args.dry_run,
        "mode": "docker",
        "workspace": layout.workspace_name,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "watcher": watcher_result,
    }


def grepai_docker_refresh(
    args: argparse.Namespace,
    *,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
) -> dict[str, Any]:
    stop_result = grepai_docker_stop(args, layout=layout, runner=runner)
    start_result = grepai_docker_start(
        args,
        provider_settings=provider_settings,
        layout=layout,
        runner=runner,
    )
    return {
        "provider": "grepai",
        "action": "refresh",
        "ok": bool(stop_result.get("ok")) and bool(start_result.get("ok")),
        "mode": "docker",
        "workspace": layout.workspace_name,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "stop": stop_result,
        "start": start_result,
    }


def grepai_run_docker(
    args: argparse.Namespace,
    action: str,
    *,
    settings_path: Path,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
) -> dict[str, Any]:
    runner = grepai_runner_settings(provider_settings, layout)
    if not args.dry_run:
        ensure_grepai_runtime_layout(layout)
    handlers = {
        "status": lambda: grepai_docker_status(
            args,
            settings_path=settings_path,
            layout=layout,
            runner=runner,
        ),
        "run": lambda: grepai_docker_run_command(
            args,
            settings_path=settings_path,
            provider_settings=provider_settings,
            layout=layout,
            runner=runner,
        ),
        "start": lambda: grepai_docker_start(
            args,
            provider_settings=provider_settings,
            layout=layout,
            runner=runner,
        ),
        "stop": lambda: grepai_docker_stop(args, layout=layout, runner=runner),
        "refresh": lambda: grepai_docker_refresh(
            args,
            provider_settings=provider_settings,
            layout=layout,
            runner=runner,
        ),
    }
    try:
        return handlers[action]()
    except KeyError as error:
        raise ValueError(action) from error


def grepai_install_version(args: argparse.Namespace, requirements_file: Path) -> tuple[Path, str]:
    if args.dry_run and not requirements_file.exists():
        return requirements_file, GREPAI_PIN.split("==", 1)[1]
    resolved = ensure_grepai_requirements_file(args.coordination_root)
    return resolved, read_provider_pin(resolved, GREPAI_PROVIDER)


def grepai_install_state(
    requirements_file: Path,
    *,
    version: str,
    runner: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "mode": "docker",
        "requirementsFile": requirements_file.as_posix(),
        "requirementsSha256": file_sha256(requirements_file)
        if requirements_file.exists()
        else None,
        "version": version,
        "image": runner["image"],
        "containerName": runner["containerName"],
        "lastAction": "install",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def grepai_install_workspace(
    args: argparse.Namespace,
    *,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    runner: dict[str, Any],
    backend_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if args.dry_run or not backend_result or not backend_result.get("ok"):
        return None
    backend = grepai_backend_settings(provider_settings, layout)
    embedder = grepai_embedder_backend_settings(provider_settings, layout)
    return prepare_grepai_workspace(
        layout,
        provider_settings,
        GrepaiWorkspaceConfig(
            dsn=grepai_container_dsn(backend),
            project_paths=grepai_container_project_paths(layout, runner),
            embedder_settings=grepai_container_embedder_settings(provider_settings, embedder),
        ),
    )


def grepai_docker_install_result(
    args: argparse.Namespace,
    *,
    requirements_file: Path,
    provider_settings: dict[str, Any],
    layout: GrepaiRuntimeLayout,
    version: str,
) -> dict[str, Any]:
    runner = grepai_runner_settings(provider_settings, layout, version=version)
    backend_result = grepai_install_backend_result(args, provider_settings)
    embedder_result = grepai_install_embedder_result(args, provider_settings)
    image_result = grepai_runner_image_build(args, runner=runner)
    workspace = grepai_install_workspace(
        args,
        provider_settings=provider_settings,
        layout=layout,
        runner=runner,
        backend_result=backend_result,
    )
    if not args.dry_run:
        write_json(
            layout.runtime_root / "install-state.json",
            grepai_install_state(requirements_file, version=version, runner=runner),
        )
    return {
        "provider": "grepai",
        "action": "install",
        "ok": grepai_install_ok(image_result, backend_result, embedder_result),
        "dryRun": args.dry_run,
        "mode": "docker",
        "requirementsFile": requirements_file.as_posix(),
        "version": version,
        "image": image_result,
        "backend": backend_result,
        "embedder": embedder_result,
        "workspace": workspace,
    }


def grepai_install_backend_result(
    args: argparse.Namespace, provider_settings: dict[str, Any]
) -> dict[str, Any] | None:
    return grepai_backend_start(args) if provider_settings else None


def grepai_install_embedder_result(
    args: argparse.Namespace, provider_settings: dict[str, Any]
) -> dict[str, Any] | None:
    return grepai_embedder_backend_start(args) if provider_settings else None


def grepai_install_ok(
    image_result: dict[str, Any],
    backend_result: dict[str, Any] | None,
    embedder_result: dict[str, Any] | None,
) -> bool:
    return all(
        (
            bool(image_result.get("ok")),
            result_ok_or_absent(backend_result),
            result_ok_or_absent(embedder_result),
        )
    )


def result_ok_or_absent(result: dict[str, Any] | None) -> bool:
    return result is None or bool(result.get("ok"))


def grepai_unsupported_install_result(
    args: argparse.Namespace,
    *,
    requirements_file: Path,
    layout: GrepaiRuntimeLayout,
    version: str,
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": "install",
        "ok": False,
        "dryRun": args.dry_run,
        "mode": "unsupported",
        "requirementsFile": requirements_file.as_posix(),
        "version": version,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "message": "grepai-memory is Docker-only; host GrepAI installation under providers/_bin is unsupported",
        "recoveryAction": "Enable the settings-backed Docker provider so Docker owns GrepAI, Postgres, and Ollama.",
    }


def grepai_install(args: argparse.Namespace) -> dict[str, Any]:
    _, provider_settings, layout = grepai_layout_from_args(args)
    requirements_file = provider_requirements_file(args.coordination_root, GREPAI_PROVIDER)
    requirements_file, version = grepai_install_version(args, requirements_file)
    if grepai_docker_mode(provider_settings):
        return grepai_docker_install_result(
            args,
            requirements_file=requirements_file,
            provider_settings=provider_settings,
            layout=layout,
            version=version,
        )
    return grepai_unsupported_install_result(
        args,
        requirements_file=requirements_file,
        layout=layout,
        version=version,
    )


def grepai_run(args: argparse.Namespace, action: str) -> dict[str, Any]:
    direct_result = grepai_direct_action(args, action)
    if direct_result is not None:
        return direct_result

    settings_path, provider_settings, layout = grepai_layout_from_args(args)
    if grepai_docker_mode(provider_settings):
        return grepai_run_docker(
            args,
            action,
            settings_path=settings_path,
            provider_settings=provider_settings,
            layout=layout,
        )
    require_supported_grepai_action(action)
    return grepai_unsupported_settings_result(args, action, settings_path, layout)


def grepai_direct_action(args: argparse.Namespace, action: str) -> dict[str, Any] | None:
    if action == "install":
        return grepai_install(args)
    if action == "backend-start":
        return grepai_backend_start(args)
    if action == "backend-status":
        return grepai_backend_status(args)
    return None


def require_supported_grepai_action(action: str) -> None:
    if action not in {"status", "run", "start", "stop", "refresh"}:
        raise ValueError(action)


def grepai_unsupported_settings_result(
    args: argparse.Namespace, action: str, settings_path: Path, layout: GrepaiRuntimeLayout
) -> dict[str, Any]:
    return {
        "provider": "grepai",
        "action": action,
        "ok": False,
        "dryRun": args.dry_run,
        "mode": "unsupported",
        "settingsFile": settings_path.as_posix(),
        "workspace": layout.workspace_name,
        "runtimeRoot": layout.runtime_root.as_posix(),
        "message": "grepai-memory is Docker-only; configure it through settings with runtime.mode=docker",
        "recoveryAction": "Run the settings-backed grepai-memory provider so Docker owns GrepAI, Postgres, and Ollama.",
    }
