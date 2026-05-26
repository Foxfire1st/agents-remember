"""CodeGraphContext index refresh lifecycle."""

from __future__ import annotations

import argparse
import time
from typing import Any

from agents_remember.providers.cgc.lifecycle.backend import cgc_backend_start
from agents_remember.providers.cgc.lifecycle.core import (
    cgc_all_layouts_from_settings,
    cgc_layout_from_args,
    cgc_uses_settings,
)
from agents_remember.providers.cgc.lifecycle.installation import cgc_doctor
from agents_remember.providers.cgc.lifecycle.process_control import (
    cgc_all_result,
    cgc_backend_all_error,
    cgc_layout_action_results,
)
from agents_remember.providers.cgc.lifecycle.runner import cgc_docker_command
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.state_files import read_json, write_json


def cgc_refresh_command(layout: Any) -> list[str]:
    return cgc_docker_command(layout, ["index", layout.code_repo_root.as_posix(), "--force"])


def cgc_refresh_dry_result(layout: Any, command: list[str]) -> dict[str, Any]:
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
    command = cgc_refresh_command(layout)
    early_result, backend_result = cgc_refresh_preflight(args, layout, command)
    if early_result:
        return early_result
    result = run_command(command, cwd=layout.coordination_root, timeout=args.timeout)
    cgc_write_refresh_state(layout, result)
    return {
        "provider": "codegraphcontext",
        "action": "refresh",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "command": result,
        "backend": backend_result,
    }


def cgc_refresh_all(args: argparse.Namespace) -> dict[str, Any]:
    settings_path, _, layouts = cgc_all_layouts_from_settings(args)
    backend = cgc_backend_start(args)
    error = cgc_backend_all_error(args, settings_path, backend, "refresh-all")
    if error:
        return error
    return cgc_all_result(
        args,
        settings_path=settings_path,
        backend=backend,
        action="refresh-all",
        results=cgc_layout_action_results(args, layouts, "refresh", cgc_refresh),
    )
