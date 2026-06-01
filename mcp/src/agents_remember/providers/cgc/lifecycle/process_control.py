"""CodeGraphContext watcher process start and stop lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.lifecycle.backend import cgc_backend_start
from agents_remember.providers.cgc.lifecycle.compose import (
    cgc_compose_render,
    cgc_compose_summary,
    cgc_watcher_service_name,
)
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_layout_from_args,
    cgc_scoped_args,
    cgc_uses_settings,
)
from agents_remember.providers.cgc.lifecycle.installation import cgc_doctor
from agents_remember.providers.cgc.lifecycle.runner import (
    cgc_watcher_running,
)
from agents_remember.providers.context import (
    CgcRuntimeLayout,
    ContextProviderError,
    ensure_cgc_runtime_layout,
    write_provider_state,
)
from agents_remember.providers.lifecycle.compose_runtime import (
    compose_plan,
    remove_unmanaged_compose_container,
    run_compose,
)
from agents_remember.providers.lifecycle.process_status import (
    require_durable_process_namespace,
)
from agents_remember.providers.lifecycle.state_files import read_json, write_json


def cgc_start_dry_run_result(args: argparse.Namespace, layout: CgcRuntimeLayout) -> dict[str, Any]:
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    command_args = ["up", "-d", cgc_watcher_service_name(layout)]
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": True,
        "repoId": layout.repo_id,
        "dryRun": True,
        "command": compose_plan(render, command_args, cwd=layout.coordination_root),
        "compose": cgc_compose_summary(render),
        "cwd": layout.watch_cwd.as_posix(),
        "env": layout.env(),
    }


def cgc_start_backend(args: argparse.Namespace, layout: CgcRuntimeLayout) -> dict[str, Any] | None:
    backend_result = cgc_backend_start(args) if cgc_uses_settings(args) else None
    if backend_result is not None and not backend_result.get("ok"):
        return {**backend_result, "action": "start", "ok": False, "repoId": layout.repo_id}
    return backend_result


def cgc_backend_result_port(
    backend_result: dict[str, Any] | None, service: str
) -> int | str | None:
    if not backend_result:
        return None
    return backend_result.get("ports", {}).get(service, {}).get("hostPort")


def cgc_start_compose_render(args: argparse.Namespace, backend_result: dict[str, Any] | None):
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    return cgc_compose_render(
        provider_settings,
        layouts,
        falkordb_port=cgc_backend_result_port(backend_result, "falkordb"),
        browser_port=cgc_backend_result_port(backend_result, "browser"),
    )


def cgc_start_watch_process(args: argparse.Namespace, layout: CgcRuntimeLayout, render: Any) -> dict[str, Any]:
    watch_log = layout.watch_log_file
    watch_log.parent.mkdir(parents=True, exist_ok=True)
    return run_compose(
        render,
        ["up", "-d", cgc_watcher_service_name(layout)],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )


def cgc_start_all_watch_process(
    args: argparse.Namespace,
    layouts: list[CgcRuntimeLayout],
    render: Any,
) -> dict[str, Any]:
    for layout in layouts:
        layout.watch_log_file.parent.mkdir(parents=True, exist_ok=True)
    return run_compose(
        render,
        ["up", "-d", *(cgc_watcher_service_name(layout) for layout in layouts)],
        cwd=layouts[0].coordination_root,
        timeout=args.timeout,
    )


def cgc_write_start_state(layout: CgcRuntimeLayout, result: dict[str, Any]) -> None:
    write_provider_state(
        layout,
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "process": {
                "pid": None,
                "containerName": layout.watcher_container_name,
                "logFile": layout.watch_log_file.as_posix(),
                "mode": "docker-container-watch",
                "returncode": result["returncode"],
            },
            "lastAction": "start",
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def cgc_start_result(
    layout: CgcRuntimeLayout,
    *,
    process: dict[str, Any],
    render: Any,
    migration: dict[str, Any] | None,
    backend_result: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": process["returncode"] == 0,
        "repoId": layout.repo_id,
        "containerName": layout.watcher_container_name,
        "logFile": layout.watch_log_file.as_posix(),
        "compose": cgc_compose_summary(render),
        "migration": migration,
        "command": process,
        "backend": backend_result,
    }


def cgc_start_preflight(
    args: argparse.Namespace, layout: CgcRuntimeLayout
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if args.dry_run:
        return cgc_start_dry_run_result(args, layout), None
    require_durable_process_namespace("cgc start")
    ensure_cgc_runtime_layout(layout)
    backend_result = cgc_start_backend(args, layout)
    if backend_result is not None and not backend_result.get("ok"):
        return backend_result, backend_result
    return None, backend_result


def cgc_start(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_start_all(args)
    layout = cgc_layout_from_args(args)
    early_result, backend_result = cgc_start_preflight(args, layout)
    if early_result:
        return early_result
    doctor = cgc_doctor(args)
    if not doctor["ok"]:
        return {**doctor, "action": "start", "ok": False, "repoId": layout.repo_id}
    render = cgc_start_compose_render(args, backend_result)
    migration = remove_unmanaged_compose_container(
        layout.watcher_container_name,
        project_name=render.project_name,
        cwd=layout.coordination_root,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    process = cgc_start_watch_process(args, layout, render)
    cgc_write_start_state(layout, process)
    return cgc_start_result(
        layout,
        process=process,
        render=render,
        migration=migration,
        backend_result=backend_result,
    )


def cgc_layout_action_results(
    args: argparse.Namespace,
    layouts: list[CgcRuntimeLayout],
    action: str,
    handler: Any,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for layout in layouts:
        scoped = cgc_scoped_args(args, layout.repo_id, action)
        try:
            result = handler(scoped)
        except (
            ContextProviderError,
            subprocess.TimeoutExpired,
            OSError,
            json.JSONDecodeError,
        ) as error:
            result = {
                "provider": "codegraphcontext",
                "action": action,
                "ok": False,
                "repoId": layout.repo_id,
                "error": str(error),
            }
        results.append(result)
    return results


def cgc_layout_action_result(
    args: argparse.Namespace,
    layout: CgcRuntimeLayout,
    action: str,
    handler: Any,
) -> dict[str, Any]:
    scoped = cgc_scoped_args(args, layout.repo_id, action)
    try:
        return handler(scoped)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        return {
            "provider": "codegraphcontext",
            "action": action,
            "ok": False,
            "repoId": layout.repo_id,
            "error": str(error),
        }


DEFAULT_CGC_INDEX_CONCURRENCY = 2


def cgc_index_concurrency(layout_count: int) -> int:
    """How many repos to (re)index at once across a multi-repo workspace.

    Each cgc indexer self-throttles to ~10 in-flight FalkorDB queries (asyncio.Semaphore in
    codegraphcontext) and uses up to ~10 parser threads. Reindexing every repo at once -- the
    previous ``max_workers=len(layouts)`` -- therefore both pegs the CPU and overruns the
    shared FalkorDB query queue on a workspace with many repos. Cap the fan-in to a small
    default so a large workspace degrades to "slower" instead of breaking; override with the
    AR_CGC_INDEX_CONCURRENCY env var on bigger machines. Returns at least 1, never more than
    the number of repos.
    """
    cap = DEFAULT_CGC_INDEX_CONCURRENCY
    override = os.environ.get("AR_CGC_INDEX_CONCURRENCY")
    if override:
        try:
            cap = int(override)
        except ValueError:
            cap = DEFAULT_CGC_INDEX_CONCURRENCY
    return max(1, min(layout_count, cap))


def cgc_parallel_layout_action_results(
    args: argparse.Namespace,
    layouts: list[CgcRuntimeLayout],
    action: str,
    handler: Any,
) -> list[dict[str, Any]]:
    if not layouts:
        return []
    results: list[dict[str, Any] | None] = [None] * len(layouts)
    workers = cgc_index_concurrency(len(layouts))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"cgc-{action}") as pool:
        futures = {
            pool.submit(cgc_layout_action_result, args, layout, action, handler): index
            for index, layout in enumerate(layouts)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return [result for result in results if result is not None]


def cgc_backend_all_error(
    args: argparse.Namespace,
    settings_path: Path,
    backend: dict[str, Any] | None,
    action: str,
) -> dict[str, Any] | None:
    if backend is None or backend.get("ok"):
        return None
    return {
        "provider": "codegraphcontext",
        "action": action,
        "ok": False,
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "backend": backend,
    }


def cgc_all_result(
    args: argparse.Namespace,
    *,
    settings_path: Path,
    backend: dict[str, Any] | None,
    action: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    data = {
        "provider": "codegraphcontext",
        "action": action,
        "ok": all(result.get("ok") for result in results),
        "dryRun": args.dry_run,
        "settingsFile": settings_path.as_posix(),
        "count": len(results),
        "results": results,
    }
    if backend is not None:
        data["backend"] = backend
    return data


def cgc_start_all_migrations(
    args: argparse.Namespace,
    layouts: list[CgcRuntimeLayout],
    render: Any,
) -> dict[str, dict[str, Any] | None]:
    return {
        layout.repo_id: remove_unmanaged_compose_container(
            layout.watcher_container_name,
            project_name=render.project_name,
            cwd=layout.coordination_root,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
        for layout in layouts
    }


def cgc_start_all_dry_results(
    layouts: list[CgcRuntimeLayout],
    render: Any,
    migrations: dict[str, dict[str, Any] | None],
    backend: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    command_args = ["up", "-d", *(cgc_watcher_service_name(layout) for layout in layouts)]
    command = compose_plan(render, command_args, cwd=layouts[0].coordination_root)
    results = [
        {
            "provider": "codegraphcontext",
            "action": "start",
            "ok": True,
            "repoId": layout.repo_id,
            "dryRun": True,
            "containerName": layout.watcher_container_name,
            "logFile": layout.watch_log_file.as_posix(),
            "command": command,
            "compose": cgc_compose_summary(render),
            "migration": migrations.get(layout.repo_id),
            "backend": backend,
        }
        for layout in layouts
    ]
    return command, results


def _cgc_start_all_live(
    args: argparse.Namespace,
    layouts: list[CgcRuntimeLayout],
    render: Any,
    migrations: dict[str, Any],
    backend: dict[str, Any],
) -> tuple[Any, list[dict[str, Any]]]:
    command = cgc_start_all_watch_process(args, layouts, render)
    results = [
        cgc_start_result(
            layout,
            process=command,
            render=render,
            migration=migrations.get(layout.repo_id),
            backend_result=backend,
        )
        for layout in layouts
    ]
    for layout in layouts:
        cgc_write_start_state(layout, command)
    return command, results


def cgc_start_all(args: argparse.Namespace) -> dict[str, Any]:
    if args.repo_id is not None:
        raise ContextProviderError(
            "cgc start-all does not accept --repo-id; use cgc start for one root"
        )
    if not args.dry_run:
        require_durable_process_namespace("cgc start-all")
    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    backend = cgc_backend_start(args)
    error = cgc_backend_all_error(args, settings_path, backend, "start-all")
    if error:
        return error
    if not args.dry_run:
        for layout in layouts:
            ensure_cgc_runtime_layout(layout)
    scoped = cgc_scoped_args(args, layouts[0].repo_id, "start")
    doctor = None if args.dry_run else cgc_doctor(scoped)
    if doctor is not None and not doctor["ok"]:
        return {**doctor, "action": "start-all", "ok": False, "repoId": None}
    render = cgc_start_compose_render(args, backend)
    migrations = cgc_start_all_migrations(args, layouts, render)
    if args.dry_run:
        command, results = cgc_start_all_dry_results(layouts, render, migrations, backend)
    else:
        command, results = _cgc_start_all_live(args, layouts, render, migrations, backend)
    result = cgc_all_result(
        args,
        settings_path=settings_path,
        backend=backend,
        action="start-all",
        results=results,
    )
    result["command"] = command
    result["compose"] = cgc_compose_summary(render)
    result["doctor"] = doctor
    result["parallel"] = True
    return result


def cgc_stop_dry_run_result(args: argparse.Namespace, layout: CgcRuntimeLayout) -> dict[str, Any]:
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    command_args = ["rm", "-sf", cgc_watcher_service_name(layout)]
    return {
        "provider": "codegraphcontext",
        "action": "stop",
        "ok": True,
        "repoId": layout.repo_id,
        "dryRun": True,
        "command": compose_plan(render, command_args, cwd=layout.coordination_root),
        "compose": cgc_compose_summary(render),
    }


def cgc_mark_stopped(layout: CgcRuntimeLayout, result: dict[str, Any]) -> None:
    state = read_json(layout.state_file)
    state["process"] = {
        "pid": None,
        "alive": False,
        "containerName": layout.watcher_container_name,
        "mode": "docker-container-watch",
        "returncode": result["returncode"],
        "stoppedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state["lastAction"] = "stop"
    write_json(layout.state_file, state)


def cgc_stop_preflight(args: argparse.Namespace, layout: CgcRuntimeLayout) -> dict[str, Any] | None:
    if not args.dry_run:
        require_durable_process_namespace("cgc stop")
    if args.dry_run:
        return cgc_stop_dry_run_result(args, layout)
    if not cgc_watcher_running(args, layout):
        return {
            "provider": "codegraphcontext",
            "action": "stop",
            "ok": True,
            "repoId": layout.repo_id,
            "message": "watcher container is not running",
            "containerName": layout.watcher_container_name,
        }
    return None


def cgc_stop(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_stop_all(args)
    layout = cgc_layout_from_args(args)
    result = cgc_stop_preflight(args, layout)
    if result:
        return result
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    command_result = run_compose(
        render,
        ["rm", "-sf", cgc_watcher_service_name(layout)],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    cgc_mark_stopped(layout, command_result)
    return {
        "provider": "codegraphcontext",
        "action": "stop",
        "ok": command_result["returncode"] == 0,
        "repoId": layout.repo_id,
        "containerName": layout.watcher_container_name,
        "compose": cgc_compose_summary(render),
        "command": command_result,
    }


def cgc_stop_all(args: argparse.Namespace) -> dict[str, Any]:
    if args.repo_id is not None:
        raise ContextProviderError(
            f"cgc {args.action} does not accept --repo-id; use cgc stop for one root"
        )
    if not args.dry_run:
        require_durable_process_namespace(f"cgc {args.action}")
    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    return cgc_all_result(
        args,
        settings_path=settings_path,
        backend=None,
        action=args.action if args.action in {"stop-all", "shutdown-all"} else "stop-all",
        results=cgc_layout_action_results(args, layouts, "stop", cgc_stop),
    )
