"""CodeGraphContext watcher process start and stop lifecycle."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

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
from agents_remember.providers.cgc.lifecycle.runner import (
    cgc_watcher_running,
)
from agents_remember.providers.context import (
    CgcRuntimeLayout,
    ContextProviderError,
)
from agents_remember.providers.lifecycle.compose_runtime import (
    compose_plan,
    run_compose,
)
from agents_remember.providers.lifecycle.process_status import (
    require_durable_process_namespace,
)
from agents_remember.providers.lifecycle.state_files import read_json, write_json


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
