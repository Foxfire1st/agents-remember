"""Bounded CodeGraphContext query and visualizer commands."""

from __future__ import annotations

import argparse
from typing import Any

from agents_remember.providers.cgc.lifecycle.core import cgc_layout_from_args, cgc_uses_settings
from agents_remember.providers.cgc.lifecycle.installation import cgc_status
from agents_remember.providers.context import ContextProviderError, ensure_cgc_runtime_layout
from agents_remember.providers.lifecycle.command_runner import run_command, run_foreground_command
from agents_remember.providers.lifecycle.process_status import require_durable_process_namespace


def cgc_stripped_native_args(args: argparse.Namespace) -> list[str]:
    native_args = list(getattr(args, "native_args", []) or [])
    if native_args and native_args[0] == "--":
        return native_args[1:]
    return native_args


def cgc_validate_run_native_args(native_args: list[str]) -> None:
    if not native_args:
        raise ContextProviderError("cgc run requires native CGC arguments after --")
    if native_args[0] == "visualize":
        raise ContextProviderError(
            "cgc run is for bounded native CGC commands; use cgc visualize for the visualizer server"
        )


def cgc_run_native_args(args: argparse.Namespace) -> list[str]:
    native_args = cgc_stripped_native_args(args)
    cgc_validate_run_native_args(native_args)
    return native_args


def cgc_run_dry_result(layout: Any, command: list[str]) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "run",
        "ok": True,
        "dryRun": True,
        "repoId": layout.repo_id,
        "command": command,
        "cwd": layout.runtime_root.as_posix(),
        "env": layout.env(),
    }


def cgc_run_status_result(args: argparse.Namespace) -> dict[str, Any] | None:
    status = cgc_status(args)
    return None if status["ok"] else {**status, "action": "run", "ok": False}


def cgc_run(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        raise ContextProviderError("cgc run requires --repo-id when using settings-backed roots")
    layout = cgc_layout_from_args(args)
    command = [layout.cgc_executable().as_posix(), *cgc_run_native_args(args)]
    if args.dry_run:
        return cgc_run_dry_result(layout, command)
    ensure_cgc_runtime_layout(layout)
    status_result = cgc_run_status_result(args)
    if status_result:
        return status_result
    result = run_command(command, cwd=layout.runtime_root, env=layout.env(), timeout=args.timeout)
    return {
        "provider": "codegraphcontext",
        "action": "run",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "command": result,
    }


def cgc_visualize_validate(args: argparse.Namespace) -> None:
    if cgc_uses_settings(args) and args.repo_id is None:
        raise ContextProviderError(
            "cgc visualize requires --repo-id when using settings-backed roots"
        )
    if args.port < 1 or args.port > 65535:
        raise ContextProviderError("cgc visualize requires --port between 1 and 65535")


def cgc_visualize_command(args: argparse.Namespace, layout: Any) -> list[str]:
    command = [
        layout.cgc_executable().as_posix(),
        "visualize",
        "--repo",
        layout.code_repo_root.as_posix(),
        "--port",
        str(args.port),
    ]
    if args.context:
        command.extend(["--context", args.context])
    return command


def cgc_visualize_dry_result(layout: Any, command: list[str], url: str) -> dict[str, Any]:
    return {
        "provider": "codegraphcontext",
        "action": "visualize",
        "ok": True,
        "dryRun": True,
        "repoId": layout.repo_id,
        "url": url,
        "longRunning": True,
        "command": command,
        "cwd": layout.runtime_root.as_posix(),
        "env": layout.env(),
    }


def cgc_visualize_status_result(args: argparse.Namespace) -> dict[str, Any] | None:
    status = cgc_status(args)
    return None if status["ok"] else {**status, "action": "visualize", "ok": False}


def cgc_visualize(args: argparse.Namespace) -> dict[str, Any]:
    cgc_visualize_validate(args)
    layout = cgc_layout_from_args(args)
    command = cgc_visualize_command(args, layout)
    url = f"http://127.0.0.1:{args.port}"
    if args.dry_run:
        return cgc_visualize_dry_result(layout, command, url)
    require_durable_process_namespace("cgc visualize")
    ensure_cgc_runtime_layout(layout)
    status_result = cgc_visualize_status_result(args)
    if status_result:
        return status_result
    result = run_foreground_command(command, cwd=layout.runtime_root, env=layout.env())
    return {
        "provider": "codegraphcontext",
        "action": "visualize",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "url": url,
        "longRunning": True,
        "command": result,
    }
