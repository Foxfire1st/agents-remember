"""Bounded CodeGraphContext query and visualizer commands."""

from __future__ import annotations

import argparse
from typing import Any

from agents_remember.providers.cgc.lifecycle.backend import cgc_backend_status
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
from agents_remember.providers.cgc.lifecycle.installation import cgc_status
from agents_remember.providers.context import (
    CgcRuntimeLayout,
    ContextProviderError,
    ensure_cgc_runtime_layout,
)
from agents_remember.providers.lifecycle.compose_runtime import compose_plan, run_compose
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


def cgc_run_command(
    args: argparse.Namespace, layout: CgcRuntimeLayout, native_args: list[str]
) -> tuple[dict[str, Any], Any, list[str]]:
    if cgc_uses_settings(args):
        _, provider_settings, layouts = cgc_project_layouts_from_settings(args, layout.repo_id)
    else:
        _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    command_args = ["run", "--rm", "--no-deps", "runner", *native_args]
    return compose_plan(render, command_args, cwd=layout.coordination_root), render, command_args


def cgc_run_dry_result(layout: CgcRuntimeLayout, command: dict[str, Any]) -> dict[str, Any]:
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
    # A one-shot `cgc run` (bundle import, graph queries) needs the FalkorDB backend, not the
    # watcher. Gating on the full provider status (which requires the watcher container running)
    # blocked the seed's `bundle import`: worktree watchers start last (OQ7), so at seed time the
    # watcher is missing, the import never ran, and the seed fell back to a full re-index. Gate on
    # backend readiness instead -- the backend is up by seed time, and queries (run with the
    # worktree fully up) are unaffected.
    status = cgc_backend_status(args)
    return None if status["ok"] else {**status, "action": "run", "ok": False}


def cgc_run(args: argparse.Namespace) -> dict[str, Any]:
    if cgc_uses_settings(args) and args.repo_id is None:
        raise ContextProviderError("cgc run requires --repo-id when using settings-backed roots")
    native_args = cgc_run_native_args(args)
    layout = cgc_layout_from_args(args)
    command, render, command_args = cgc_run_command(args, layout, native_args)
    if args.dry_run:
        return cgc_run_dry_result(layout, command)
    ensure_cgc_runtime_layout(layout)
    status_result = cgc_run_status_result(args)
    if status_result:
        return status_result
    result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    return {
        "provider": "codegraphcontext",
        "action": "run",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "compose": cgc_compose_summary(render),
        "command": result,
    }


def cgc_visualize_validate(args: argparse.Namespace) -> None:
    if cgc_uses_settings(args) and args.repo_id is None:
        raise ContextProviderError(
            "cgc visualize requires --repo-id when using settings-backed roots"
        )
    if args.port < 1 or args.port > 65535:
        raise ContextProviderError("cgc visualize requires --port between 1 and 65535")


def cgc_visualize_command(
    args: argparse.Namespace, layout: CgcRuntimeLayout
) -> tuple[dict[str, Any], Any, list[str]]:
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    command_args = [
        "run",
        "--rm",
        "-p",
        f"127.0.0.1:{args.port}:{args.port}",
        "runner",
        "visualize",
        "--repo",
        layout.container_code_repo_root,
        "--port",
        str(args.port),
    ]
    if args.context:
        command_args.extend(["--context", args.context])
    return compose_plan(render, command_args, cwd=layout.coordination_root), render, command_args


def cgc_visualize_dry_result(layout: CgcRuntimeLayout, command: dict[str, Any], url: str) -> dict[str, Any]:
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
    url = f"http://127.0.0.1:{args.port}"
    if not args.dry_run:
        require_durable_process_namespace("cgc visualize")
    command, render, command_args = cgc_visualize_command(args, layout)
    if args.dry_run:
        return cgc_visualize_dry_result(layout, command, url)
    ensure_cgc_runtime_layout(layout)
    status_result = cgc_visualize_status_result(args)
    if status_result:
        return status_result
    result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    return {
        "provider": "codegraphcontext",
        "action": "visualize",
        "ok": result["returncode"] == 0,
        "repoId": layout.repo_id,
        "url": url,
        "longRunning": True,
        "compose": cgc_compose_summary(render),
        "command": result,
    }
