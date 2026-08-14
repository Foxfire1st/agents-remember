"""GrepAI workflow-local database clone support."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from agents_remember.providers.grepai.context.layout import (
    grepai_runtime_layout_from_provider_settings,
)
from agents_remember.providers.grepai.lifecycle.core import grepai_backend_settings
from agents_remember.providers.lifecycle.docker_runtime import docker_command
from agents_remember.providers.setup_common import (
    LifecycleCommand,
    load_settings,
    provider_settings,
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
    path = grepai_seed_source_settings_path(
        args, source_coordination_root, target_coordination_root
    )
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


def _resolve_clone_context(
    args: Any, target_settings: dict[str, Any]
) -> GrepaiCloneContext | dict[str, Any]:
    inputs = _clone_inputs(args, target_settings)
    if isinstance(inputs, dict):
        return inputs
    source_settings_path = grepai_seed_source_settings_path(
        args, inputs.source_coordination_root, args.coordination_root
    )
    source_settings = load_settings(source_settings_path)
    if source_settings is None:
        return _clone_skip(f"source settings missing: {settings_path(source_settings_path)}")
    source_provider = _grepai_provider(source_settings)
    target_provider = _grepai_provider(target_settings)
    if source_provider is None:
        return _clone_skip("source grepai-memory provider is not configured")
    if target_provider is None:
        return _clone_skip("target grepai-memory provider is not configured")
    return _clone_context_from_providers(
        _GrepaiCloneEnd(
            coordination_root=inputs.source_coordination_root,
            settings_path=settings_path(source_settings_path),
            provider=source_provider,
        ),
        _GrepaiCloneEnd(
            coordination_root=args.coordination_root,
            settings_path=inputs.target_settings_path,
            provider=target_provider,
        ),
        project_id=stable_provider_id(inputs.project_id),
    )


class _CloneInputs(NamedTuple):
    """The caller-supplied coordinates of a clone, once each is known to be present."""

    source_coordination_root: Path
    target_settings_path: Path
    project_id: str


def _clone_inputs(args: Any, target_settings: dict[str, Any]) -> _CloneInputs | dict[str, Any]:
    """The clone's coordinates, or the skip payload naming the first one that is missing."""
    # Benchmarks are hermetic: a benchmark-scoped target must never clone from another
    # stack. This is what stopped the benchmark from starting the live workspace backend
    # as a clone source and cascading a full re-embed across main + every worktree.
    target_cfg = provider_settings(target_settings, GREPAI_PROVIDER_ID)
    target_instance = target_cfg.get("instance") if isinstance(target_cfg, dict) else None
    if isinstance(target_instance, dict) and target_instance.get("scope") == "benchmark":
        return _clone_skip("benchmark grepai-memory is hermetic; seeding/cloning is disabled")
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
    return _CloneInputs(source_coordination_root, target_settings_path, project_id)


def _grepai_provider(settings: dict[str, Any]) -> dict[str, Any] | None:
    # Distinct from the other provider extractors: seed clone requires the
    # source/target provider to be explicitly enabled, not merely present.
    provider = provider_settings(settings, GREPAI_PROVIDER_ID)
    return provider if provider is not None and provider.get("enabled") is True else None


@dataclass(frozen=True)
class _GrepaiCloneEnd:
    """One end of a GrepAI seed clone: a coordination root and its provider entry.

    Source and target are symmetric — each is a coordination root, the settings
    file that describes it, and the enabled grepai-memory provider found there.
    Naming the end makes the clone read as source → target instead of six
    interleaved parameters.
    """

    coordination_root: Path
    settings_path: Path
    provider: dict[str, Any]


def _clone_context_from_providers(
    source: _GrepaiCloneEnd,
    target: _GrepaiCloneEnd,
    *,
    project_id: str,
) -> GrepaiCloneContext | dict[str, Any]:
    source_layout = grepai_runtime_layout_from_provider_settings(
        coordination_root=source.coordination_root,
        provider_settings=source.provider,
    )
    target_layout = grepai_runtime_layout_from_provider_settings(
        coordination_root=target.coordination_root,
        provider_settings=target.provider,
    )
    source_backend = grepai_backend_settings(source.provider, source_layout)
    target_backend = grepai_backend_settings(target.provider, target_layout)
    if source_backend["containerName"] == target_backend["containerName"]:
        return _clone_skip("source and target GrepAI backend containers are the same")
    return GrepaiCloneContext(
        project_id=project_id,
        source_coordination_root=source.coordination_root,
        target_coordination_root=target.coordination_root,
        source_container=source_backend["containerName"],
        target_container=target_backend["containerName"],
        source_database=source_backend["postgresDatabase"],
        target_database=target_backend["postgresDatabase"],
        source_user=source_backend["postgresUser"],
        target_user=target_backend["postgresUser"],
        source_password=source_backend["postgresPassword"],
        target_password=target_backend["postgresPassword"],
        source_settings_path=source.settings_path,
        target_settings_path=target.settings_path,
    )


def _source_backend_start(args: Any, context: GrepaiCloneContext) -> dict[str, Any]:
    return run_lifecycle(
        context.source_coordination_root,
        LifecycleCommand(
            provider="grepai",
            action="backend-start",
            extra_args=tuple(
                grepai_seed_source_extra_args(
                    args, context.source_coordination_root, context.target_coordination_root
                )
            ),
        ),
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


def _target_backend_start(args: Any, context: GrepaiCloneContext) -> dict[str, Any]:
    return run_lifecycle(
        context.target_coordination_root,
        LifecycleCommand(
            provider="grepai",
            action="backend-start",
            extra_args=tuple(grepai_extra_args(args)),
        ),
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


# A wedge's signature is silence, not duration: the clone deliberately has no
# total-time cap (it scales with index size by design — moving index data is
# what makes rapid worktree provider deployment viable), but zero progress for
# this long means a wedged docker exec and gets killed with a structured,
# phase-named result instead of hanging worktree_start forever.
GREPAI_CLONE_STALL_SECONDS = 300
GREPAI_CLONE_POLL_SECONDS = 10.0


def _clone_database(args: Any, context: GrepaiCloneContext) -> dict[str, Any]:
    commands = {
        "dump": _dump_command(context, dry_run=args.dry_run),
        "restore": _restore_command(context, dry_run=args.dry_run),
    }
    if args.dry_run:
        return {"ok": True, "dryRun": True, "commands": commands}
    started = time.monotonic()
    stall_seconds = int(getattr(args, "seed_stall_seconds", 0) or 0) or GREPAI_CLONE_STALL_SECONDS
    with tempfile.NamedTemporaryFile(prefix="agents-remember-grepai-", suffix=".sql") as dump_file:
        dump = _run_with_stall_watchdog(
            commands["dump"],
            _StallWatchdog(
                progress=lambda: os.fstat(dump_file.fileno()).st_size,
                stall_seconds=stall_seconds,
            ),
            cwd=context.target_coordination_root,
            stdout=dump_file,
            stdin=subprocess.DEVNULL,
        )
        if dump is None:
            return _clone_stall_result("dump", started, stall_seconds, commands)
        if dump.returncode != 0:
            return _command_result("dump", dump, started, commands)
        dump_file.flush()
        dump_file.seek(0)
        restore = _run_with_stall_watchdog(
            commands["restore"],
            _StallWatchdog(
                progress=lambda: _target_database_size(context),
                stall_seconds=stall_seconds,
            ),
            cwd=context.target_coordination_root,
            stdout=subprocess.PIPE,
            stdin=dump_file,
        )
        if restore is None:
            return _clone_stall_result("restore", started, stall_seconds, commands)
        if restore.returncode != 0:
            return _command_result("restore", restore, started, commands)
    return {
        "ok": True,
        "durationSeconds": round(time.monotonic() - started, 3),
        "commands": commands,
    }


@dataclass(frozen=True)
class _StallWatchdog:
    """The no-progress detector that guards an uncapped clone command.

    A wedge's signature is silence, not duration, so the three settings are one
    rule: how to sample progress, how often to sample it, and how long zero
    movement is allowed before the child is killed.
    """

    progress: Any
    stall_seconds: int
    poll_seconds: float = GREPAI_CLONE_POLL_SECONDS


def _run_with_stall_watchdog(
    command: list[str],
    watchdog: _StallWatchdog,
    *,
    cwd: Path,
    stdout: Any,
    stdin: Any,
) -> subprocess.CompletedProcess[str] | None:
    """Run a command without a total-time cap, killing it only after
    ``stall_seconds`` of zero progress. Returns None when killed for stalling.

    stderr goes to a temp file (not a pipe) so an unread pipe buffer can never
    deadlock the child while the watchdog waits.

    ``Popen`` is entered as a context manager because a ``stdout=PIPE`` caller gets a
    read end this function owns: both exits below (the stall kill and the normal return)
    leave the ``Popen`` unreferenced, so without ``__exit__`` closing it the pipe is
    finalised by GC instead of by the code that opened it."""
    with (
        tempfile.TemporaryFile() as stderr_file,
        subprocess.Popen(
            command,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr_file,
            stdin=stdin,
        ) as process,
    ):
        last_progress = watchdog.progress()
        last_change = time.monotonic()
        while True:
            try:
                returncode = process.wait(timeout=watchdog.poll_seconds)
                break
            except subprocess.TimeoutExpired:
                current = watchdog.progress()
                if current != last_progress:
                    last_progress = current
                    last_change = time.monotonic()
                elif time.monotonic() - last_change >= watchdog.stall_seconds:
                    process.kill()
                    process.wait()
                    return None
        captured_stdout = b""
        if stdout == subprocess.PIPE and process.stdout is not None:
            captured_stdout = process.stdout.read()
        stderr_file.seek(0)
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=captured_stdout.decode("utf-8", errors="replace"),
            stderr=stderr_file.read().decode("utf-8", errors="replace"),
        )


def _target_database_size(context: GrepaiCloneContext) -> int:
    result = subprocess.run(
        [
            docker_command(),
            "exec",
            "-e",
            f"PGPASSWORD={context.target_password}",
            context.target_container,
            "psql",
            "-U",
            context.target_user,
            "-d",
            context.target_database,
            "-tAc",
            f"SELECT pg_database_size('{context.target_database}');",
        ],
        cwd=context.target_coordination_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return -1


def _clone_stall_result(
    phase: str, started: float, stall_seconds: int, commands: dict[str, list[str]]
) -> dict[str, Any]:
    return {
        "ok": False,
        "phase": phase,
        "stalled": True,
        "stallSeconds": stall_seconds,
        "durationSeconds": round(time.monotonic() - started, 3),
        "error": (
            f"grepai database clone {phase} made no progress for {stall_seconds}s "
            "and was killed (a working clone of any size keeps progressing). "
            "Inspect the source/target postgres containers for a wedged docker exec."
        ),
        "commands": commands,
    }


def _stream_text(stream: str | bytes | None) -> str:
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream or ""


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
    completed: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes],
    started: float,
    commands: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "ok": False,
        "stage": stage,
        "returncode": completed.returncode,
        "stdout": _stream_text(completed.stdout),
        "stderr": _stream_text(completed.stderr),
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
    # An intentional skip (no source memory configured, source == target backend,
    # etc.) is not a failed phase -- mirror CGC's benign skips, which report ok:True.
    return {"ok": True, "skipped": True, "reason": reason}
