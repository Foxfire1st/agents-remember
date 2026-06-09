"""CodeGraphContext installation, status, and patch lifecycle."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.lifecycle.backend import cgc_backend_start
from agents_remember.providers.cgc.lifecycle.compose import (
    cgc_compose_render,
    cgc_compose_summary,
)
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_layout_from_args,
    cgc_scoped_args,
    cgc_uses_settings,
)
from agents_remember.providers.cgc.lifecycle.runner import (
    cgc_runner_image_build,
    cgc_runner_image_status,
    cgc_watcher_inspect,
)
from agents_remember.providers.context import (
    CgcRuntimeLayout,
    ContextProviderError,
    cleanup_cgc_runtime_artifacts,
    ensure_cgc_runtime_layout,
    source_provider_artifacts,
    write_provider_state,
)
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.compose_runtime import compose_plan, run_compose
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_container_running,
    docker_container_state_summary,
)
from agents_remember.providers.lifecycle.process_status import (
    process_namespace_status,
)
from agents_remember.providers.lifecycle.state_files import (
    read_json,
    write_json,
)


def cgc_install_commands(args: argparse.Namespace, layout: CgcRuntimeLayout) -> tuple[Path, list[dict[str, Any]]]:
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    return layout.image_build_root, [
        compose_plan(render, ["build", "runner"], cwd=layout.coordination_root),
        compose_plan(
            render,
            ["run", "--rm", "--no-deps", "runner", "doctor"],
            cwd=layout.coordination_root,
        ),
    ]


def cgc_install_dry_run_result(layout: CgcRuntimeLayout, commands: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "install",
        "ok": True,
        "dryRun": True,
        "repoId": layout.repo_id,
        "runnerImage": layout.runner_image,
        "imageBuildRoot": layout.image_build_root.as_posix(),
        "imageLockFile": layout.image_lock_file.as_posix(),
        "commands": commands,
    }


def cgc_failed_install_result(layout: CgcRuntimeLayout, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "install",
        "ok": False,
        "repoId": layout.repo_id,
        "commands": results,
    }


def cgc_install_backend(args: argparse.Namespace, layout: CgcRuntimeLayout) -> dict[str, Any] | None:
    backend_result = cgc_backend_start(args) if cgc_uses_settings(args) else None
    if backend_result is not None and not backend_result.get("ok"):
        return {**backend_result, "action": "install", "ok": False, "repoId": layout.repo_id}
    return backend_result


def cgc_write_install_state(
    layout: CgcRuntimeLayout,
    *,
    install_result: dict[str, Any],
    backend_result: dict[str, Any] | None,
    patch_result: dict[str, Any],
    doctor_result: dict[str, Any],
) -> None:
    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "runnerImage": layout.runner_image,
            "imageBuildRoot": layout.image_build_root.as_posix(),
            "imageLockFile": layout.image_lock_file.as_posix(),
            "lastAction": "install",
            "lastInstall": {
                "imageOk": install_result.get("ok"),
                "backendOk": backend_result.get("ok") if backend_result else None,
                "patchOk": patch_result.get("ok"),
                "doctorOk": doctor_result.get("ok"),
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    write_json(layout.state_file, state)


def cgc_install_preflight(
    args: argparse.Namespace, layout: CgcRuntimeLayout, commands: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    if args.dry_run:
        return [], cgc_install_dry_run_result(layout, commands), None
    ensure_cgc_runtime_layout(layout)
    image_result = cgc_runner_image_build(args, layout)
    results = [image_result]
    if not image_result.get("ok"):
        return results, cgc_failed_install_result(layout, results), None
    backend_result = cgc_install_backend(args, layout)
    if backend_result is not None and not backend_result.get("ok"):
        return results, backend_result, backend_result
    return results, None, backend_result


def cgc_install(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_install_all(args)
    layout = cgc_layout_from_args(args)
    _, commands = cgc_install_commands(args, layout)
    results, early_result, backend_result = cgc_install_preflight(args, layout, commands)
    if early_result:
        return early_result
    doctor_result = cgc_doctor(args)
    cgc_write_install_state(
        layout,
        install_result=results[-1],
        backend_result=backend_result,
        patch_result={"ok": True, "mode": "docker-image"},
        doctor_result=doctor_result,
    )
    return {
        "provider": "codegraphcontext",
        "action": "install",
        "ok": bool(results[-1].get("ok")) and bool(doctor_result.get("ok")),
        "repoId": layout.repo_id,
        "runnerImage": layout.runner_image,
        "imageBuildRoot": layout.image_build_root.as_posix(),
        "imageLockFile": layout.image_lock_file.as_posix(),
        "commands": results,
        "backend": backend_result,
        "patch": {"ok": True, "mode": "docker-image"},
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
        except (
            ContextProviderError,
            subprocess.TimeoutExpired,
            OSError,
            json.JSONDecodeError,
        ) as error:
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


CGC_SCAN_STARTED_MARKER = "Performing initial scan"
CGC_SCAN_COMPLETE_MARKER = "Initial scan complete"
CGC_EMPTY_GRAPH_ERROR = "empty key"


def cgc_initial_scan_in_progress(
    layout: CgcRuntimeLayout, inspect_data: dict[str, Any] | None
) -> bool:
    """True when the watcher logged a scan start since its container started
    without a matching completion marker."""
    if not inspect_data:
        return False
    started_at = docker_container_state_summary(inspect_data).get("startedAt")
    if not started_at:
        return False
    try:
        result = run_command(
            [
                docker_command(),
                "logs",
                "--since",
                str(started_at),
                layout.watcher_container_name,
            ],
            cwd=layout.coordination_root,
            timeout=15,
        )
    except (ContextProviderError, subprocess.TimeoutExpired, OSError):
        return False
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    return CGC_SCAN_STARTED_MARKER in text and CGC_SCAN_COMPLETE_MARKER not in text


def cgc_graph_content_state(layout: CgcRuntimeLayout) -> str:
    """Probe actual graph content with a read-only query.

    GRAPH.RO_QUERY never auto-creates the graph key (plain GRAPH.QUERY does,
    which would plant the very poison state this probe exists to detect)."""
    graph_name = layout.env()["FALKORDB_GRAPH_NAME"]
    try:
        result = run_command(
            [
                docker_command(),
                "exec",
                layout.backend_container_name,
                "redis-cli",
                "GRAPH.RO_QUERY",
                graph_name,
                "MATCH (f:File) RETURN count(f)",
            ],
            cwd=layout.coordination_root,
            timeout=15,
        )
    except (ContextProviderError, subprocess.TimeoutExpired, OSError):
        return "backend-unreachable"
    text = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    if result.get("returncode") != 0:
        return "backend-unreachable"
    # redis-cli exits 0 on error replies; classify from the reply text.
    if CGC_EMPTY_GRAPH_ERROR in text:
        return "empty"
    if "ERR" in text or "LOADING" in text:
        return "backend-unreachable"
    count = cgc_first_integer_line(text)
    if count is None:
        return "unknown"
    return "indexed" if count > 0 else "empty"


def cgc_first_integer_line(text: str) -> int | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def cgc_indexing_state_probe(
    layout: CgcRuntimeLayout,
    inspect_data: dict[str, Any] | None,
    *,
    watcher_running: bool,
) -> str:
    if watcher_running and cgc_initial_scan_in_progress(layout, inspect_data):
        return "indexing"
    return cgc_graph_content_state(layout)


def cgc_status(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    artifacts = [path.as_posix() for path in source_provider_artifacts(layout.code_repo_root)]
    image = cgc_runner_image_status(args, layout)
    inspect_data = cgc_watcher_inspect(args, layout)
    running = docker_container_running(inspect_data)
    patch = {"module": None, "applied": image["exists"], "error": None, "mode": "docker-image"}
    state = read_json(layout.state_file)

    return {
        "provider": "codegraphcontext",
        "action": "status",
        "ok": image["exists"] and not artifacts and running,
        "repoId": layout.repo_id,
        "codeRepoRoot": layout.code_repo_root.as_posix(),
        "runtimeRoot": layout.runtime_root.as_posix(),
        "cgcRoot": layout.cgc_root.as_posix(),
        "runnerImage": image,
        "watcherContainer": layout.watcher_container_name,
        "backendRoot": layout.backend_root.as_posix(),
        "backendDataRoot": layout.backend_data_root.as_posix(),
        "watchCwd": layout.watch_cwd.as_posix(),
        "watchLog": layout.watch_log_file.as_posix(),
        "lastRefresh": state.get("lastRefresh"),
        "indexingState": cgc_indexing_state_probe(
            layout, inspect_data, watcher_running=running
        ),
        "sourceArtifacts": artifacts,
        "patch": patch,
        "process": {
            "pid": None,
            "alive": running,
            "mode": "docker-container-watch",
            "containerName": layout.watcher_container_name,
            "containerState": docker_container_state_summary(inspect_data),
        },
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
                "runnerImage": layout.runner_image,
                "imageBuildRoot": layout.image_build_root.as_posix(),
                "imageLockFile": layout.image_lock_file.as_posix(),
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
        "runnerImage": layout.runner_image,
        "imageBuildRoot": layout.image_build_root.as_posix(),
        "imageLockFile": layout.image_lock_file.as_posix(),
        "stateFile": layout.state_file.as_posix(),
    }


def cgc_doctor(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    status = cgc_status(args)
    checks: list[dict[str, Any]] = [
        cgc_runtime_root_containment_check(layout),
        {
            "name": "source-artifact-clean",
            "ok": not status["sourceArtifacts"],
            "artifacts": status["sourceArtifacts"],
        },
        {
            "name": "cgc-runner-image",
            "ok": status["runnerImage"]["exists"],
            "image": status["runnerImage"]["image"],
        },
        {
            "name": "cgc-image-patches",
            "ok": bool(status["patch"]["applied"]),
            "details": status["patch"],
        },
    ]
    command_result = None
    if status["runnerImage"]["exists"] and not args.dry_run:
        _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
        render = cgc_compose_render(provider_settings, layouts)
        command_result = run_compose(
            render,
            ["run", "--rm", "--no-deps", "runner", "doctor"],
            cwd=layout.coordination_root,
            timeout=args.timeout,
        )
        checks.append({"name": "cgc-doctor-command", "ok": command_result["returncode"] == 0})
    elif args.dry_run:
        _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
        render = cgc_compose_render(provider_settings, layouts)
        command_result = {
            **compose_plan(
                render,
                ["run", "--rm", "--no-deps", "runner", "doctor"],
                cwd=layout.coordination_root,
            ),
            "compose": cgc_compose_summary(render),
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


def cgc_runtime_root_containment_check(layout: CgcRuntimeLayout) -> dict[str, Any]:
    """Return the CGC runtime-root containment safety check."""

    under_provider_root = layout.runtime_root.is_relative_to(layout.providers_root)
    under_coordination_root = layout.runtime_root.is_relative_to(layout.coordination_root)
    outside_source_repo = not layout.runtime_root.is_relative_to(layout.code_repo_root)
    return {
        "name": "runtime-root-contained",
        "ok": (under_provider_root or under_coordination_root) and outside_source_repo,
        "details": {
            "runtimeRoot": layout.runtime_root.as_posix(),
            "providersRoot": layout.providers_root.as_posix(),
            "coordinationRoot": layout.coordination_root.as_posix(),
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "underProviderRoot": under_provider_root,
            "underCoordinationRoot": under_coordination_root,
            "outsideSourceRepo": outside_source_repo,
        },
    }
