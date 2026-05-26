"""CodeGraphContext watcher process start and stop lifecycle."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.lifecycle.backend import cgc_backend_start
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_layout_from_args,
    cgc_scoped_args,
    cgc_uses_settings,
)
from agents_remember.providers.cgc.lifecycle.installation import cgc_doctor
from agents_remember.providers.context import (
    ContextProviderError,
    ensure_cgc_runtime_layout,
    write_provider_state,
)
from agents_remember.providers.lifecycle.command_runner import popen_detached_command
from agents_remember.providers.lifecycle.process_status import (
    process_alive,
    process_cmdline,
    require_durable_process_namespace,
)
from agents_remember.providers.lifecycle.state_files import read_json, write_json


def cgc_start_dry_run_result(layout: Any) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": True,
        "repoId": layout.repo_id,
        "dryRun": True,
        "command": [layout.cgc_executable().as_posix(), "watch", layout.code_repo_root.as_posix()],
        "cwd": layout.watch_cwd.as_posix(),
        "env": layout.env(),
    }


def cgc_start_backend(args: argparse.Namespace, layout: Any) -> dict[str, Any] | None:
    backend_result = cgc_backend_start(args) if cgc_uses_settings(args) else None
    if backend_result is not None and not backend_result.get("ok"):
        return {**backend_result, "action": "start", "ok": False, "repoId": layout.repo_id}
    return backend_result


def cgc_running_process_result(
    layout: Any, backend_result: dict[str, Any] | None
) -> dict[str, Any] | None:
    process_state = read_json(layout.state_file).get("process", {})
    if not isinstance(process_state, dict):
        return None
    pid = process_state.get("pid")
    if not isinstance(pid, int) or not process_alive(pid):
        return None
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": True,
        "repoId": layout.repo_id,
        "alreadyRunning": True,
        "pid": pid,
        "logFile": process_state.get("logFile", layout.watch_log_file.as_posix()),
        "backend": backend_result,
    }


def cgc_start_watch_process(layout: Any) -> Any:
    watch_log = layout.watch_log_file
    watch_log.parent.mkdir(parents=True, exist_ok=True)
    with watch_log.open("ab") as log_handle:
        return popen_detached_command(
            [layout.cgc_executable().as_posix(), "watch", layout.code_repo_root.as_posix()],
            cwd=layout.watch_cwd,
            env=layout.env(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )


def cgc_write_start_state(layout: Any, process: Any) -> None:
    write_provider_state(
        layout,
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "codeRepoRoot": layout.code_repo_root.as_posix(),
            "runtimeRoot": layout.runtime_root.as_posix(),
            "process": {
                "pid": process.pid,
                "logFile": layout.watch_log_file.as_posix(),
                "mode": "watch",
            },
            "lastAction": "start",
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def cgc_start_preflight(
    args: argparse.Namespace, layout: Any
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if args.dry_run:
        return cgc_start_dry_run_result(layout), None
    require_durable_process_namespace("cgc start")
    ensure_cgc_runtime_layout(layout)
    backend_result = cgc_start_backend(args, layout)
    if backend_result is not None and not backend_result.get("ok"):
        return backend_result, backend_result
    return cgc_running_process_result(layout, backend_result), backend_result


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
    process = cgc_start_watch_process(layout)
    cgc_write_start_state(layout, process)
    return {
        "provider": "codegraphcontext",
        "action": "start",
        "ok": True,
        "repoId": layout.repo_id,
        "pid": process.pid,
        "logFile": layout.watch_log_file.as_posix(),
        "backend": backend_result,
    }


def cgc_layout_action_results(
    args: argparse.Namespace,
    layouts: list[Any],
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


def cgc_backend_all_error(
    args: argparse.Namespace,
    settings_path: Path,
    backend: dict[str, Any],
    action: str,
) -> dict[str, Any] | None:
    if backend.get("ok"):
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
    return cgc_all_result(
        args,
        settings_path=settings_path,
        backend=backend,
        action="start-all",
        results=cgc_layout_action_results(args, layouts, "start", cgc_start),
    )


def cgc_stop_pid_state(layout: Any) -> tuple[dict[str, Any], int | None, dict[str, Any] | None]:
    state = read_json(layout.state_file)
    process_state = state.get("process", {})
    if not isinstance(process_state, dict):
        process_state = {}
    pid = process_state.get("pid")
    if isinstance(pid, int):
        return state, pid, None
    return state, None, {
        "provider": "codegraphcontext",
        "action": "stop",
        "ok": True,
        "repoId": layout.repo_id,
        "message": "no managed pid",
    }


def cgc_validate_stop_pid(layout: Any, pid: int) -> dict[str, Any] | None:
    cmdline = process_cmdline(pid)
    if not cmdline or "cgc" in cmdline or "codegraphcontext" in cmdline.lower():
        return None
    return {
        "provider": "codegraphcontext",
        "action": "stop",
        "ok": False,
        "repoId": layout.repo_id,
        "message": "managed pid no longer looks like a CGC process",
        "pid": pid,
        "cmdline": cmdline,
    }


def cgc_stop_dry_run_result(layout: Any, pid: int) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "stop",
        "ok": True,
        "repoId": layout.repo_id,
        "dryRun": True,
        "pid": pid,
    }


def cgc_mark_stopped(layout: Any, state: dict[str, Any], pid: int) -> None:
    if process_alive(pid):
        os.kill(pid, signal.SIGTERM)
    state["process"] = {
        "pid": pid,
        "alive": False,
        "stoppedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    state["lastAction"] = "stop"
    write_json(layout.state_file, state)


def cgc_stop_preflight(
    args: argparse.Namespace, layout: Any
) -> tuple[dict[str, Any], int | None, dict[str, Any] | None]:
    if not args.dry_run:
        require_durable_process_namespace("cgc stop")
    state, pid, result = cgc_stop_pid_state(layout)
    return cgc_stop_pid_preflight(args, layout, state, pid, result)


def cgc_stop_pid_preflight(
    args: argparse.Namespace,
    layout: Any,
    state: dict[str, Any],
    pid: int | None,
    result: dict[str, Any] | None,
) -> tuple[dict[str, Any], int | None, dict[str, Any] | None]:
    if result or pid is None:
        return state, pid, result
    pid_error = cgc_validate_stop_pid(layout, pid)
    if pid_error or not args.dry_run:
        return state, pid, pid_error
    return state, pid, cgc_stop_dry_run_result(layout, pid)


def cgc_stop(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_stop_all(args)
    layout = cgc_layout_from_args(args)
    state, pid, result = cgc_stop_preflight(args, layout)
    if result:
        return result
    assert pid is not None
    cgc_mark_stopped(layout, state, pid)
    return {
        "provider": "codegraphcontext",
        "action": "stop",
        "ok": True,
        "repoId": layout.repo_id,
        "pid": pid,
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
