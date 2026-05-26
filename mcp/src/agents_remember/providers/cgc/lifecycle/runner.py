"""CodeGraphContext Docker runner image and command helpers."""

# ruff: noqa: F403
from __future__ import annotations

import argparse
from typing import Any

from agents_remember.providers.cgc.lifecycle.compose import (
    cgc_compose_render,
    cgc_compose_summary,
)
from agents_remember.providers.cgc.lifecycle.core import cgc_all_layouts_from_settings
from agents_remember.providers.context import *
from agents_remember.providers.lifecycle.compose_runtime import (
    compose_plan,
    provider_asset_path,
    provider_asset_text,
    run_compose,
)
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_container_running,
    docker_image_exists,
    docker_inspect_container,
    docker_repo_digest,
)
from agents_remember.providers.lifecycle.state_files import write_json


def cgc_runner_dockerfile() -> str:
    return provider_asset_text("docker", "codegraphcontext", "Dockerfile")


def cgc_runner_patch_script() -> str:
    return provider_asset_text("docker", "codegraphcontext", "patch_cgc.py")


def cgc_runner_image_build(args: argparse.Namespace, layout: Any) -> dict[str, Any]:
    _, provider_settings, layouts = cgc_all_layouts_from_settings(args)
    render = cgc_compose_render(provider_settings, layouts)
    command_args = ["build", "runner"]
    dockerfile = provider_asset_path("docker", "codegraphcontext", "Dockerfile")
    patch_script = provider_asset_path("docker", "codegraphcontext", "patch_cgc.py")
    if args.dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "image": layout.runner_image,
            "dockerfile": dockerfile.as_posix(),
            "patchScript": patch_script.as_posix(),
            "buildContext": dockerfile.parent.as_posix(),
            "compose": cgc_compose_summary(render),
            "command": compose_plan(render, command_args, cwd=layout.coordination_root),
        }
    if docker_image_exists(layout.runner_image, cwd=layout.coordination_root, timeout=args.timeout):
        return {"ok": True, "image": layout.runner_image, "alreadyExists": True}
    result = run_compose(render, command_args, cwd=layout.coordination_root, timeout=args.timeout)
    image_digest = docker_repo_digest(
        layout.runner_image,
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )
    write_json(layout.image_lock_file, {"image": layout.runner_image, "repoDigest": image_digest})
    return {
        "ok": result["returncode"] == 0,
        "image": layout.runner_image,
        "dockerfile": dockerfile.as_posix(),
        "patchScript": patch_script.as_posix(),
        "buildContext": dockerfile.parent.as_posix(),
        "compose": cgc_compose_summary(render),
        "command": result,
    }


def cgc_watcher_inspect(args: argparse.Namespace, layout: Any) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    return docker_inspect_container(
        layout.watcher_container_name,
        cwd=layout.coordination_root,
        timeout=args.timeout,
    )


def cgc_watcher_running(args: argparse.Namespace, layout: Any) -> bool:
    return docker_container_running(cgc_watcher_inspect(args, layout))


def cgc_runner_image_status(args: argparse.Namespace, layout: Any) -> dict[str, Any]:
    exists = (
        False
        if args.dry_run
        else docker_image_exists(
            layout.runner_image,
            cwd=layout.coordination_root,
            timeout=args.timeout,
        )
    )
    return {
        "image": layout.runner_image,
        "exists": exists,
        "imageLockFile": layout.image_lock_file.as_posix(),
        "buildRoot": layout.image_build_root.as_posix(),
    }
