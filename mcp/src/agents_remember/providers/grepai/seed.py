"""GrepAI workflow-local database clone support."""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.providers.grepai.context.layout import (
    grepai_runtime_layout_from_provider_settings,
)
from agents_remember.providers.grepai.lifecycle.core import grepai_backend_settings
from agents_remember.providers.lifecycle.docker_runtime import docker_command
from agents_remember.providers.setup_common import (
    context_providers,
    load_settings,
    run_lifecycle,
    settings_path,
    stable_provider_id,
)

GREPAI_PROVIDER_ID = "grepai-memory"


@dataclass(frozen=True)
class GrepaiSeedOptions:
    source_coordination_root: Path | None = None
    source_settings_path: Path | None = None
    project_id: str | None = None
    target_memory_root: Path | None = None


@dataclass(frozen=True)
class GrepaiCloneContext:
    project_id: str
    source_coordination_root: Path
    target_coordination_root: Path
    source_container: str
    target_container: str
    source_database: str
    target_database: str
    source_user: str
    target_user: str
    source_password: str
    target_password: str
    source_settings_path: Path
    target_settings_path: Path


def grepai_extra_args(args: Any) -> list[str]:
    path = (
        getattr(args, "grepai_from_settings", None)
        or getattr(args, "provider_from_settings", None)
        or getattr(args, "from_settings", None)
    )
    return ["--from-settings", path.as_posix()] if path is not None else []


def grepai_seed_source_settings_path(
    args: Any,
    source_coordination_root: Path,
    target_coordination_root: Path,
) -> Path | None:
    explicit = getattr(args, "grepai_seed_source_from_settings", None)
    if explicit is not None:
        return explicit
    if source_coordination_root.resolve() == target_coordination_root.resolve():
        return getattr(args, "from_settings", None)
    return None


def grepai_seed_source_extra_args(
    args: Any,
    source_coordination_root: Path,
    target_coordination_root: Path,
) -> list[str]:
    path = grepai_seed_source_settings_path(args, source_coordination_root, target_coordination_root)
    return ["--from-settings", path.as_posix()] if path is not None else []


def grepai_clone_bundle(args: Any, target_settings: dict[str, Any]) -> dict[str, Any]:
    context = _resolve_clone_context(args, target_settings)
    if not isinstance(context, GrepaiCloneContext):
        return context
    source_start = _source_backend_start(args, context)
    target_start = _target_backend_start(args, context)
    if not source_start.get("ok"):
        return {"ok": False, "stage": "source-backend-start", "command": source_start}
    if not target_start.get("ok"):
        return {"ok": False, "stage": "target-backend-start", "command": target_start}
    clone = _clone_database(args, context)
    if not clone.get("ok"):
        return {"ok": False, "stage": "database-clone", "command": clone}
    return _clone_success_payload(context, source_start, target_start, clone)


def _resolve_clone_context(args: Any, target_settings: dict[str, Any]) -> GrepaiCloneContext | dict[str, Any]:
    source_coordination_root = args.grepai_seed_source_coordination_root
    if source_coordination_root is None:
        return _clone_skip("no GrepAI seed source coordination root configured")
    target_settings_path = (
        getattr(args, "grepai_from_settings", None)
        or getattr(args, "provider_from_settings", None)
        or getattr(args, "from_settings", None)
    )
    if target_settings_path is None:
        return _clone_skip("no GrepAI target settings configured")
    project_id = getattr(args, "grepai_seed_project_id", None)
    if not project_id:
        return _clone_skip("no GrepAI seed project id configured")

    source_settings_path = grepai_seed_source_settings_path(
        args, source_coordination_root, args.coordination_root
    )
    source_settings = load_settings(source_coordination_root, source_settings_path)
    if source_settings is None:
        return _clone_skip(
            f"source settings missing: {settings_path(source_coordination_root, source_settings_path)}"
        )
    source_provider = _grepai_provider(source_settings)
    target_provider = _grepai_provider(target_settings)
    if source_provider is None:
        return _clone_skip("source grepai-memory provider is not configured")
    if target_provider is None:
        return _clone_skip("target grepai-memory provider is not configured")
    return _clone_context_from_providers(
        args,
        source_coordination_root=source_coordination_root,
        source_settings_path=settings_path(source_coordination_root, source_settings_path),
        target_settings_path=target_settings_path,
        project_id=stable_provider_id(project_id),
        source_provider=source_provider,
        target_provider=target_provider,
    )


def _grepai_provider(settings: dict[str, Any]) -> dict[str, Any] | None:
    provider = context_providers(settings).get(GREPAI_PROVIDER_ID)
    return provider if isinstance(provider, dict) and provider.get("enabled") is True else None


def _clone_context_from_providers(
    args: Any,
    *,
    source_coordination_root: Path,
    source_settings_path: Path,
    target_settings_path: Path,
    project_id: str,
    source_provider: dict[str, Any],
    target_provider: dict[str, Any],
) -> GrepaiCloneContext | dict[str, Any]:
    source_layout = grepai_runtime_layout_from_provider_settings(
        coordination_root=source_coordination_root,
        provider_settings=source_provider,
    )
    target_layout = grepai_runtime_layout_from_provider_settings(
        coordination_root=args.coordination_root,
        provider_settings=target_provider,
    )
    source_backend = grepai_backend_settings(source_provider, source_layout)
    target_backend = grepai_backend_settings(target_provider, target_layout)
    if source_backend["containerName"] == target_backend["containerName"]:
        return _clone_skip("source and target GrepAI backend containers are the same")
    return GrepaiCloneContext(
        project_id=project_id,
        source_coordination_root=source_coordination_root,
        target_coordination_root=args.coordination_root,
        source_container=source_backend["containerName"],
        target_container=target_backend["containerName"],
        source_database=source_backend["postgresDatabase"],
        target_database=target_backend["postgresDatabase"],
        source_user=source_backend["postgresUser"],
        target_user=target_backend["postgresUser"],
        source_password=source_backend["postgresPassword"],
        target_password=target_backend["postgresPassword"],
        source_settings_path=source_settings_path,
        target_settings_path=target_settings_path,
    )


def _source_backend_start(args: Any, context: GrepaiCloneContext) -> dict[str, Any]:
    return run_lifecycle(
        context.source_coordination_root,
        "grepai",
        "backend-start",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=grepai_seed_source_extra_args(
            args, context.source_coordination_root, context.target_coordination_root
        ),
    )


def _target_backend_start(args: Any, context: GrepaiCloneContext) -> dict[str, Any]:
    return run_lifecycle(
        context.target_coordination_root,
        "grepai",
        "backend-start",
        timeout=args.timeout,
        dry_run=args.dry_run,
        extra_args=grepai_extra_args(args),
    )


def _clone_database(args: Any, context: GrepaiCloneContext) -> dict[str, Any]:
    commands = {
        "dump": _dump_command(context, dry_run=args.dry_run),
        "restore": _restore_command(context, dry_run=args.dry_run),
    }
    if args.dry_run:
        return {"ok": True, "dryRun": True, "commands": commands}
    started = time.monotonic()
    with tempfile.NamedTemporaryFile(prefix="agents-remember-grepai-", suffix=".sql") as dump_file:
        dump = subprocess.run(
            commands["dump"],
            cwd=context.target_coordination_root,
            stdout=dump_file,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=args.timeout,
            check=False,
        )
        if dump.returncode != 0:
            return _command_result("dump", dump, started, commands)
        dump_file.flush()
        dump_file.seek(0)
        restore = subprocess.run(
            commands["restore"],
            cwd=context.target_coordination_root,
            stdin=dump_file,
            capture_output=True,
            timeout=args.timeout,
            check=False,
        )
        if restore.returncode != 0:
            return _command_result("restore", restore, started, commands)
    return {
        "ok": True,
        "durationSeconds": round(time.monotonic() - started, 3),
        "commands": commands,
    }


def _docker_executable(*, dry_run: bool) -> str:
    return "docker" if dry_run else docker_command()


def _dump_command(context: GrepaiCloneContext, *, dry_run: bool) -> list[str]:
    return [
        _docker_executable(dry_run=dry_run),
        "exec",
        "-e",
        f"PGPASSWORD={context.source_password}",
        context.source_container,
        "pg_dump",
        "-U",
        context.source_user,
        "-d",
        context.source_database,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
    ]


def _restore_command(context: GrepaiCloneContext, *, dry_run: bool) -> list[str]:
    return [
        _docker_executable(dry_run=dry_run),
        "exec",
        "-i",
        "-e",
        f"PGPASSWORD={context.target_password}",
        context.target_container,
        "psql",
        "-U",
        context.target_user,
        "-d",
        context.target_database,
        "-v",
        "ON_ERROR_STOP=1",
    ]


def _command_result(
    stage: str,
    completed: subprocess.CompletedProcess[bytes],
    started: float,
    commands: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": stage,
        "returncode": completed.returncode,
        "stdout": completed.stdout.decode("utf-8", errors="replace")
        if completed.stdout
        else "",
        "stderr": completed.stderr.decode("utf-8", errors="replace")
        if completed.stderr
        else "",
        "durationSeconds": round(time.monotonic() - started, 3),
        "commands": commands,
    }


def _clone_success_payload(
    context: GrepaiCloneContext,
    source_start: dict[str, Any],
    target_start: dict[str, Any],
    clone: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "seeded": True,
        "strategy": "database-clone-active-project-reconcile",
        "projectId": context.project_id,
        "sourceCoordinationRoot": context.source_coordination_root.as_posix(),
        "targetCoordinationRoot": context.target_coordination_root.as_posix(),
        "sourceSettingsFile": context.source_settings_path.as_posix(),
        "targetSettingsFile": context.target_settings_path.as_posix(),
        "sourceContainer": context.source_container,
        "targetContainer": context.target_container,
        "sourceBackend": source_start,
        "targetBackend": target_start,
        "clone": clone,
    }


def _clone_skip(reason: str) -> dict[str, Any]:
    return {"ok": False, "skipped": True, "reason": reason}
