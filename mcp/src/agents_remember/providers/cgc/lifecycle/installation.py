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
    cgc_watcher_running,
)
from agents_remember.providers.context import (
    CGC_CGCIGNORE_PATCH_ID,
    CGC_DELETE_PATCH_ID,
    CGC_DISCOVERY_EXTENSIONS_PATCH_ID,
    CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID,
    CGC_VIZ_CLI_ROUTE_PATCH_ID,
    CGC_VIZ_REPO_QUERY_PATCH_ID,
    CGC_VIZ_SERVER_ROUTE_PATCH_ID,
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
    cleanup_cgc_runtime_artifacts,
    ensure_cgc_runtime_layout,
    find_cgc_cgcignore_module,
    find_cgc_cli_helpers_module,
    find_cgc_discovery_module,
    find_cgc_graph_builder_module,
    find_cgc_viz_server_module,
    find_cgc_writer_module,
    source_provider_artifacts,
    write_provider_state,
)
from agents_remember.providers.lifecycle.compose_runtime import compose_plan, run_compose
from agents_remember.providers.lifecycle.process_status import (
    process_namespace_status,
)
from agents_remember.providers.lifecycle.state_files import (
    read_json,
    write_json,
)


def cgc_install_commands(args: argparse.Namespace, layout: Any) -> tuple[Path, list[dict[str, Any]]]:
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    return layout.image_build_root, [
        compose_plan(render, ["build", "runner"], cwd=layout.coordination_root),
        compose_plan(render, ["run", "--rm", "runner", "doctor"], cwd=layout.coordination_root),
    ]


def cgc_install_dry_run_result(layout: Any, commands: list[list[str]]) -> dict[str, Any]:
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


def cgc_failed_install_result(layout: Any, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "install",
        "ok": False,
        "repoId": layout.repo_id,
        "commands": results,
    }


def cgc_install_backend(args: argparse.Namespace, layout: Any) -> dict[str, Any] | None:
    backend_result = cgc_backend_start(args) if cgc_uses_settings(args) else None
    if backend_result is not None and not backend_result.get("ok"):
        return {**backend_result, "action": "install", "ok": False, "repoId": layout.repo_id}
    return backend_result


def cgc_write_install_state(
    layout: Any,
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
    args: argparse.Namespace, layout: Any, commands: list[list[str]]
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


def cgc_status(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    artifacts = [path.as_posix() for path in source_provider_artifacts(layout.code_repo_root)]
    image = cgc_runner_image_status(args, layout)
    running = cgc_watcher_running(args, layout)
    patch = {"module": None, "applied": image["exists"], "error": None, "mode": "docker-image"}

    return {
        "provider": "codegraphcontext",
        "action": "status",
        "ok": image["exists"] and not artifacts,
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
        "sourceArtifacts": artifacts,
        "patch": patch,
        "process": {
            "pid": None,
            "alive": running,
            "mode": "docker-container-watch",
            "containerName": layout.watcher_container_name,
        },
        "processNamespace": process_namespace_status(),
    }


def cgc_empty_patch_status() -> dict[str, Any]:
    return {
        "module": None,
        "applied": False,
        "error": None,
        "patches": {},
    }


def cgc_status_patch(layout: Any, cgc_executable: Path) -> dict[str, Any]:
    patch = cgc_empty_patch_status()
    if not cgc_executable.exists():
        return patch
    try:
        return cgc_detected_patch_status(layout)
    except ContextProviderError as error:
        patch["error"] = str(error)
        return patch


def cgc_detected_patch_status(layout: Any) -> dict[str, Any]:
    cgcignore_module = find_cgc_cgcignore_module(layout.venv_root)
    writer_module = find_cgc_writer_module(layout.venv_root)
    graph_builder_module = find_cgc_graph_builder_module(layout.venv_root)
    discovery_module = find_cgc_discovery_module(layout.venv_root)
    viz_server_module = find_cgc_viz_server_module(layout.venv_root)
    cli_helpers_module = find_cgc_cli_helpers_module(layout.venv_root)
    patch_rows = {
        CGC_CGCIGNORE_PATCH_ID: (cgcignore_module, cgc_cgcignore_patch_applied(cgcignore_module)),
        CGC_DELETE_PATCH_ID: (writer_module, cgc_delete_patch_applied(writer_module)),
        CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID: (
            graph_builder_module,
            cgc_graph_builder_extensions_patch_applied(graph_builder_module),
        ),
        CGC_DISCOVERY_EXTENSIONS_PATCH_ID: (
            discovery_module,
            cgc_discovery_extensions_patch_applied(discovery_module),
        ),
        CGC_VIZ_REPO_QUERY_PATCH_ID: (
            viz_server_module,
            cgc_viz_repo_query_patch_applied(viz_server_module),
        ),
        CGC_VIZ_SERVER_ROUTE_PATCH_ID: (
            viz_server_module,
            cgc_viz_server_route_patch_applied(viz_server_module),
        ),
        CGC_VIZ_CLI_ROUTE_PATCH_ID: (
            cli_helpers_module,
            cgc_viz_cli_route_patch_applied(cli_helpers_module),
        ),
    }
    return {
        "module": cgcignore_module.as_posix(),
        "applied": all(applied for _, applied in patch_rows.values()),
        "error": None,
        "patches": {
            patch_id: {"module": module.as_posix(), "applied": applied}
            for patch_id, (module, applied) in patch_rows.items()
        },
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


def cgc_patch_targets(layout: Any) -> list[tuple[str, Path, Any, Any]]:
    cgcignore_module = find_cgc_cgcignore_module(layout.venv_root)
    writer_module = find_cgc_writer_module(layout.venv_root)
    graph_builder_module = find_cgc_graph_builder_module(layout.venv_root)
    discovery_module = find_cgc_discovery_module(layout.venv_root)
    viz_server_module = find_cgc_viz_server_module(layout.venv_root)
    cli_helpers_module = find_cgc_cli_helpers_module(layout.venv_root)
    return [
        (
            CGC_CGCIGNORE_PATCH_ID,
            cgcignore_module,
            cgc_cgcignore_patch_applied,
            apply_cgc_cgcignore_patch,
        ),
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


def cgc_apply_patch_target(
    args: argparse.Namespace,
    patch_id: str,
    module: Path,
    check_applied: Any,
    apply_func: Any,
) -> tuple[str, dict[str, Any]]:
    already_applied = check_applied(module)
    changed = cgc_patch_target_changed(args, module, already_applied, apply_func)
    applied = cgc_patch_target_applied(args, module, already_applied, check_applied)
    return patch_id, {
        "module": module.as_posix(),
        "alreadyApplied": already_applied,
        "applied": applied,
        "changed": changed,
        "dryRunWouldChange": changed if args.dry_run else False,
    }


def cgc_patch_target_changed(
    args: argparse.Namespace, module: Path, already_applied: bool, apply_func: Any
) -> bool:
    if args.dry_run:
        return not already_applied
    return False if already_applied else apply_func(module)


def cgc_patch_target_applied(
    args: argparse.Namespace, module: Path, already_applied: bool, check_applied: Any
) -> bool:
    return already_applied if args.dry_run else check_applied(module)


def cgc_patch_results(args: argparse.Namespace, layout: Any) -> dict[str, dict[str, Any]]:
    return dict(
        cgc_apply_patch_target(args, patch_id, module, check_applied, apply_func)
        for patch_id, module, check_applied, apply_func in cgc_patch_targets(layout)
    )


def cgc_patch_changed(patch_results: dict[str, dict[str, Any]]) -> bool:
    return any(result["changed"] for result in patch_results.values())


def cgc_patch_any_applied(patch_results: dict[str, dict[str, Any]]) -> bool:
    return any(result["alreadyApplied"] or result["changed"] for result in patch_results.values())


def cgc_patch_applied_ids(patch_results: dict[str, dict[str, Any]]) -> set[str]:
    return {
        patch_id
        for patch_id, result in patch_results.items()
        if result["applied"] or result["changed"]
    }


def cgc_update_patch_state(
    layout: Any,
    args: argparse.Namespace,
    patch_results: dict[str, dict[str, Any]],
) -> None:
    state = read_json(layout.state_file)
    existing = state.get("appliedPatches", [])
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "appliedPatches": sorted(set(existing) | cgc_patch_applied_ids(patch_results))
            if cgc_patch_any_applied(patch_results)
            else existing,
            "patchVerification": patch_results,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    if not args.dry_run:
        write_json(layout.state_file, state)


def cgc_patch(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    return {
        "provider": "codegraphcontext",
        "action": "patch",
        "ok": True,
        "dryRun": args.dry_run,
        "mode": "docker-image",
        "runnerImage": layout.runner_image,
        "changed": False,
    }


def cgc_doctor(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    status = cgc_status(args)
    checks: list[dict[str, Any]] = [
        {
            "name": "runtime-root-contained",
            "ok": layout.runtime_root.is_relative_to(layout.providers_root),
        },
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
            ["run", "--rm", "runner", "doctor"],
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
                ["run", "--rm", "runner", "doctor"],
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
