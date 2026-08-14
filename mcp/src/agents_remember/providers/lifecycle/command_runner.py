"""Subprocess execution helpers for provider lifecycle commands."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from agents_remember.providers.lifecycle.runtime_environment import subprocess_env

UNLIMITED_TIMEOUT = 0


def run_command(
    command: list[str],
    *,
    cwd: Path,
    stdin_text: str | None = None,
    timeout: int = 60,
    allow_timeout: bool = False,
) -> dict[str, Any]:
    # Provider commands always run under the sanitized provider environment;
    # no caller has ever supplied its own, so there is no env override here.
    merged_env = subprocess_env(None)
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
            timeout=(None if timeout <= 0 else timeout),
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
