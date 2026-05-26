"""CodeGraphContext Docker runner image and command helpers."""

# ruff: noqa: F403,F405
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from agents_remember.providers.context import *
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import (
    docker_command,
    docker_container_running,
    docker_image_exists,
    docker_inspect_container,
    docker_repo_digest,
)
from agents_remember.providers.lifecycle.state_files import write_json


def cgc_runner_dockerfile() -> str:
    requirements = " ".join(CGC_REQUIREMENTS)
    return f"""FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8
RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates git \\
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --upgrade pip \\
    && python -m pip install --no-cache-dir {requirements}
COPY patch_cgc.py /tmp/patch_cgc.py
RUN python /tmp/patch_cgc.py && rm /tmp/patch_cgc.py
ENTRYPOINT ["cgc"]
"""


def cgc_runner_patch_script() -> str:
    operations = [
        {
            "file": "core/cgcignore.py",
            "originals": [CGC_OLD_PATCHED_SNIPPET, CGC_ORIGINAL_SNIPPET],
            "patched": CGC_PATCHED_SNIPPET,
        },
        {
            "file": "tools/indexing/persistence/writer.py",
            "originals": [CGC_DELETE_PREFIX_ORIGINAL_SNIPPET],
            "patched": CGC_DELETE_PREFIX_PATCHED_SNIPPET,
        },
        {
            "file": "tools/indexing/persistence/writer.py",
            "originals": [CGC_DELETE_REL_ORIGINAL_SNIPPET],
            "patched": CGC_DELETE_REL_PATCHED_SNIPPET,
        },
        {
            "file": "tools/indexing/persistence/writer.py",
            "originals": [CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET],
            "patched": CGC_DELETE_CONTAINS_PATCHED_SNIPPET,
        },
        {
            "file": "tools/indexing/persistence/writer.py",
            "originals": [CGC_DELETE_NODE_ORIGINAL_SNIPPET],
            "patched": CGC_DELETE_NODE_PATCHED_SNIPPET,
        },
        {
            "file": "tools/graph_builder.py",
            "originals": [CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET],
            "patched": CGC_GRAPH_BUILDER_PARSER_PATCHED_SNIPPET,
        },
        {
            "file": "tools/graph_builder.py",
            "originals": [CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET],
            "patched": CGC_GRAPH_BUILDER_GENERIC_PATCHED_SNIPPET,
        },
        {
            "file": "tools/graph_builder.py",
            "originals": [CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET],
            "patched": CGC_GRAPH_BUILDER_PRESCAN_PATCHED_SNIPPET,
        },
        {
            "file": "tools/indexing/discovery.py",
            "originals": [CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET],
            "patched": CGC_DISCOVERY_GENERIC_PATCHED_SNIPPET,
        },
        {
            "file": "viz/server.py",
            "originals": [CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_REPO_QUERY_PATCHED_SNIPPET,
        },
        {
            "file": "viz/server.py",
            "originals": [CGC_VIZ_SERVER_RESPONSES_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_SERVER_RESPONSES_PATCHED_SNIPPET,
        },
        {
            "file": "viz/server.py",
            "originals": [CGC_VIZ_SERVER_GLOBAL_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_SERVER_GLOBAL_PATCHED_SNIPPET,
        },
        {
            "file": "viz/server.py",
            "originals": [CGC_VIZ_SERVER_FALLBACK_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_SERVER_FALLBACK_PATCHED_SNIPPET,
        },
        {
            "file": "viz/server.py",
            "originals": [CGC_VIZ_SERVER_RUN_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_SERVER_RUN_PATCHED_SNIPPET,
        },
        {
            "file": "cli/cli_helpers.py",
            "originals": [CGC_VIZ_CLI_URL_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_CLI_URL_PATCHED_SNIPPET,
        },
        {
            "file": "cli/cli_helpers.py",
            "originals": [CGC_VIZ_CLI_RUN_ORIGINAL_SNIPPET],
            "patched": CGC_VIZ_CLI_RUN_PATCHED_SNIPPET,
        },
    ]
    return f"""from __future__ import annotations

import pathlib
import sysconfig

base = pathlib.Path(sysconfig.get_paths()["purelib"]) / "codegraphcontext"
operations = {json.dumps(operations, indent=2)}

for operation in operations:
    path = base / operation["file"]
    text = path.read_text(encoding="utf-8")
    if operation["patched"] in text:
        continue
    for original in operation["originals"]:
        if original in text:
            break
    else:
        raise SystemExit(f"CodeGraphContext patch target did not match: {{operation['file']}}")
    patched = operation["patched"]
    if patched not in text:
        text = text.replace(original, patched, 1)
    path.write_text(text, encoding="utf-8")
"""


def cgc_runner_image_build(args: argparse.Namespace, layout: Any) -> dict[str, Any]:
    dockerfile = layout.image_build_root / "Dockerfile"
    patch_script = layout.image_build_root / "patch_cgc.py"
    command = [
        docker_command(),
        "build",
        "-t",
        layout.runner_image,
        layout.image_build_root.as_posix(),
    ]
    if args.dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "image": layout.runner_image,
            "dockerfile": dockerfile.as_posix(),
            "patchScript": patch_script.as_posix(),
            "command": command,
        }
    if docker_image_exists(layout.runner_image, cwd=layout.coordination_root, timeout=args.timeout):
        return {"ok": True, "image": layout.runner_image, "alreadyExists": True}
    layout.image_build_root.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(cgc_runner_dockerfile(), encoding="utf-8")
    patch_script.write_text(cgc_runner_patch_script(), encoding="utf-8")
    result = run_command(command, cwd=layout.coordination_root, timeout=args.timeout)
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
        "command": result,
    }


def cgc_container_env(layout: Any) -> list[str]:
    env = layout.env()
    env["FALKORDB_HOST"] = layout.backend_container_name
    return [f"{key}={value}" for key, value in sorted(env.items())]


def cgc_container_mount_args(layout: Any) -> list[str]:
    return [
        "-v",
        f"{layout.runtime_root.as_posix()}:{layout.runtime_root.as_posix()}",
        "-v",
        f"{layout.code_repo_root.as_posix()}:{layout.code_repo_root.as_posix()}:ro",
        "-w",
        layout.runtime_root.as_posix(),
    ]


def cgc_container_user_args() -> list[str]:
    if not hasattr(os, "getuid") or not hasattr(os, "getgid"):
        return []
    return ["--user", f"{os.getuid()}:{os.getgid()}"]


def cgc_container_env_args(layout: Any) -> list[str]:
    args: list[str] = []
    for env_value in cgc_container_env(layout):
        args.extend(["-e", env_value])
    return args


def cgc_docker_command(layout: Any, native_args: list[str], *, remove: bool = True) -> list[str]:
    command = [
        docker_command(),
        "run",
    ]
    if remove:
        command.append("--rm")
    command.extend(["--network", layout.network_name])
    command.extend(cgc_container_user_args())
    command.extend(cgc_container_mount_args(layout))
    command.extend(cgc_container_env_args(layout))
    command.append(layout.runner_image)
    command.extend(native_args)
    return command


def cgc_watcher_command(layout: Any) -> list[str]:
    command = [
        docker_command(),
        "run",
        "-d",
        "--name",
        layout.watcher_container_name,
        "--restart",
        "unless-stopped",
        "--network",
        layout.network_name,
    ]
    command.extend(cgc_container_user_args())
    command.extend(cgc_container_mount_args(layout))
    command.extend(cgc_container_env_args(layout))
    command.extend([layout.runner_image, "watch", layout.code_repo_root.as_posix()])
    return command


def cgc_visualize_docker_command(args: argparse.Namespace, layout: Any) -> list[str]:
    command = [
        docker_command(),
        "run",
        "--rm",
        "--network",
        layout.network_name,
        "-p",
        f"127.0.0.1:{args.port}:{args.port}",
    ]
    command.extend(cgc_container_user_args())
    command.extend(cgc_container_mount_args(layout))
    command.extend(cgc_container_env_args(layout))
    command.extend(
        [
            layout.runner_image,
            "visualize",
            "--repo",
            layout.code_repo_root.as_posix(),
            "--port",
            str(args.port),
        ]
    )
    if args.context:
        command.extend(["--context", args.context])
    return command


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
