"""Subprocess execution helpers for provider lifecycle commands."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.lifecycle.runtime_environment import subprocess_env


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
    timeout: int = 60,
    allow_timeout: bool = False,
) -> dict[str, Any]:
    merged_env = subprocess_env(env)
    started = time.monotonic()
    stdin_kwargs: dict[str, Any] = (
        {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
    )
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **stdin_kwargs,
        )
    except subprocess.TimeoutExpired as error:
        if not allow_timeout:
            raise
        return timeout_command_result(command, cwd, started, timeout, error)
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timedOut": False,
    }


def timeout_command_result(
    command: list[str],
    cwd: Path,
    started: float,
    timeout: int,
    error: subprocess.TimeoutExpired,
) -> dict[str, Any]:
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": None,
        "durationSeconds": round(time.monotonic() - started, 3),
        "stdout": timeout_stream_text(error.stdout),
        "stderr": timeout_stream_text(error.stderr),
        "timedOut": True,
        "timeoutSeconds": timeout,
    }


def timeout_stream_text(stream: str | bytes | None) -> str:
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream or ""


def run_foreground_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    merged_env = subprocess_env(env)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=merged_env,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return {
        "command": command,
        "cwd": cwd.as_posix(),
        "returncode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
    }


def popen_detached_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdout: Any = subprocess.DEVNULL,
    stderr: Any = subprocess.DEVNULL,
) -> subprocess.Popen[bytes]:
    """Start a long-running command without owning its lifetime."""

    merged_env = subprocess_env(env)
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "DETACHED_PROCESS", 0)
        popen_kwargs["creationflags"] |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=merged_env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        **popen_kwargs,
    )
