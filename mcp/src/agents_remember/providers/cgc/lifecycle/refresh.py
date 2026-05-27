"""CodeGraphContext index refresh lifecycle."""

from __future__ import annotations

import argparse
import time
from typing import Any

from agents_remember.providers.cgc.lifecycle.backend import cgc_backend_start
from agents_remember.providers.cgc.lifecycle.compose import (
    cgc_compose_render,
    cgc_compose_summary,
)
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_layout_from_args,
    cgc_project_layouts_from_settings,
    cgc_uses_settings,
)
from agents_remember.providers.cgc.lifecycle.installation import cgc_doctor
from agents_remember.providers.cgc.lifecycle.process_control import (
    cgc_all_result,
    cgc_backend_all_error,
    cgc_parallel_layout_action_results,
    cgc_start_all,
)
from agents_remember.providers.lifecycle.compose_runtime import compose_plan, run_compose
from agents_remember.providers.lifecycle.state_files import read_json, write_json


def cgc_refresh_command(args: argparse.Namespace, layout: Any) -> dict[str, Any]:
    _, provider_settings, layouts = (
        cgc_project_layouts_from_settings(args, layout.repo_id)
        if cgc_uses_settings(args)
        else cgc_all_layouts_from_settings(args)
    )
    render = cgc_compose_render(provider_settings, layouts)
    command_args = [
        "run",
        "--rm",
        "--no-deps",
        "runner",
        "index",
        layout.code_repo_root.as_posix(),
        "--force",
    ]
    return compose_plan(render, command_args, cwd=layout.coordination_root)


def cgc_refresh_dry_result(layout: Any, command: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "refresh",
        "ok": True,
        "repoId": layout.repo_id,
        "dryRun": True,
        "command": command,
        "cwd": layout.runtime_root.as_posix(),
        "env": layout.env(),
    }


def cgc_refresh_backend(args: argparse.Namespace, layout: Any) -> dict[str, Any] | None:
    backend_result = cgc_backend_start(args) if cgc_uses_settings(args) else None
    if backend_result is not None and not backend_result.get("ok"):
        return {**backend_result, "action": "refresh", "ok": False, "repoId": layout.repo_id}
    return backend_result


def cgc_write_refresh_state(layout: Any, result: dict[str, Any]) -> None:
    state = read_json(layout.state_file)
    state.update(
        {
            "provider": "codegraphcontext",
            "repoId": layout.repo_id,
            "lastAction": "refresh",
            "lastRefresh": {
                "returncode": result["returncode"],
                "durationSeconds": result["durationSeconds"],
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
    )
    write_json(layout.state_file, state)


def cgc_refresh_preflight(
    args: argparse.Namespace, layout: Any, command: list[str]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if args.dry_run:
        return cgc_refresh_dry_result(layout, command), None
    backend_result = cgc_refresh_backend(args, layout)
    if backend_result is not None and not backend_result.get("ok"):
        return backend_result, backend_result
    doctor = cgc_doctor(args)
    if not doctor["ok"]:
        return {**doctor, "action": "refresh", "ok": False}, backend_result
    return None, backend_result


def cgc_refresh(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        return cgc_refresh_all(args)
    layout = cgc_layout_from_args(args)
    command = cgc_refresh_command(args, layout)
    early_result, backend_result = cgc_refresh_preflight(args, layout, command)
    if early_result:
        return early_result
    _, provider_settings, layouts = (
        cgc_project_layouts_from_settings(args, layout.repo_id)
        if cgc_uses_settings(args)
        else cgc_all_layouts_from_settings(args)
    )
    render = cgc_compose_render(provider_settings, layouts)
    result = run_compose(
        render,
        [
            "run",
            "--rm",
            "--no-deps",
            "runner",
            "index",
            layout.code_repo_root.as_posix(),
            "--force",
        ],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    cgc_write_refresh_state(layout, result)
    return {
        "provider": "codegraphcontext",
        "action": "refresh",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "command": result,
        "compose": cgc_compose_summary(render),
        "backend": backend_result,
    }


def cgc_refresh_with_started_watchers(args: argparse.Namespace) -> dict[str, Any]:
    layout = cgc_layout_from_args(args)
    _, provider_settings, layouts = cgc_project_layouts_from_settings(args, layout.repo_id)
    render = cgc_compose_render(provider_settings, layouts)
    result = run_compose(
        render,
        [
            "run",
            "--rm",
            "--no-deps",
            "runner",
            "index",
            layout.code_repo_root.as_posix(),
            "--force",
        ],
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    cgc_write_refresh_state(layout, result)
    return {
        "provider": "codegraphcontext",
        "action": "refresh",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "command": result,
        "compose": cgc_compose_summary(render),
    }


def cgc_refresh_all(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    watchers = cgc_start_all(args)
    backend = watchers.get("backend")
    if not watchers.get("ok"):
        error = cgc_backend_all_error(args, settings_path, backend, "refresh-all")
        if error:
            return error
        return {
            "provider": "codegraphcontext",
            "action": "refresh-all",
            "ok": False,
            "dryRun": args.dry_run,
            "settingsFile": settings_path.as_posix(),
            "backend": backend,
            "watchers": watchers,
            "results": [],
        }
    if args.dry_run:
        results = [
            cgc_refresh_dry_result(layout, cgc_refresh_command(args, layout))
            for layout in layouts
        ]
    else:
        results = cgc_parallel_layout_action_results(
            args,
            layouts,
            "refresh",
            cgc_refresh_with_started_watchers,
        )
    return cgc_all_result(
        args,
        settings_path=settings_path,
        backend=backend,
        action="refresh-all",
        results=results,
    ) | {"watchers": watchers, "parallel": True}
