"""Bounded JSON-line subprocess transport for Claude Code stream-json sessions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from agents_remember.errors import HarnessAdapterDisconnectedError, HarnessControlError
from agents_remember.serving.harness_control_models import ShutdownMode

MAX_CLAUDE_FRAME_BYTES = 1024 * 1024
PROCESS_SHUTDOWN_TIMEOUT_SECONDS = 5.0
VERSION_PROBE_TIMEOUT_SECONDS = 10.0


class ClaudeStreamTransport(Protocol):
    """The process/framing seam used by the Claude adapter and its fake protocol tests."""

    @property
    def returncode(self) -> int | None: ...

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> None: ...

    async def read_frame(self) -> dict[str, object] | None: ...

    async def write_frame(self, frame: Mapping[str, object]) -> None: ...

    async def stop(self, mode: ShutdownMode) -> None: ...


class ClaudeSubprocessTransport:
    """Own one Claude subprocess without retaining stderr or environment secrets."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None

    async def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> None:
        if self._process is not None:
            raise HarnessControlError("Claude stream transport is already started")
        try:
            self._process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=dict(env) if env else None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_CLAUDE_FRAME_BYTES,
            )
        except OSError as exc:
            raise HarnessControlError(f"could not launch Claude Code: {exc}") from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def read_frame(self) -> dict[str, object] | None:
        process = self._require_process()
        if process.stdout is None:
            raise HarnessControlError("Claude Code stdout is unavailable")
        try:
            line = await process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise HarnessControlError(
                f"Claude Code emitted a frame larger than {MAX_CLAUDE_FRAME_BYTES} bytes"
            ) from exc
        if not line:
            return None
        if len(line) > MAX_CLAUDE_FRAME_BYTES:
            raise HarnessControlError(
                f"Claude Code emitted a frame larger than {MAX_CLAUDE_FRAME_BYTES} bytes"
            )
        try:
            decoded = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessControlError("Claude Code emitted invalid stream-json framing") from exc
        if not isinstance(decoded, dict):
            raise HarnessControlError("Claude Code stream-json frame must be an object")
        return decoded

    async def write_frame(self, frame: Mapping[str, object]) -> None:
        process = self._require_process()
        stdin = process.stdin
        if stdin is None or stdin.is_closing():
            raise HarnessAdapterDisconnectedError(
                "Claude Code stdin closed before the message was written",
                may_have_sent=False,
            )
        payload = (
            json.dumps(dict(frame), separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode()
        if len(payload) > MAX_CLAUDE_FRAME_BYTES:
            raise HarnessControlError(
                f"Claude Code input frame exceeds {MAX_CLAUDE_FRAME_BYTES} bytes"
            )
        try:
            stdin.write(payload)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise HarnessAdapterDisconnectedError(
                "Claude Code disconnected while accepting a message",
                may_have_sent=True,
            ) from exc

    async def stop(self, mode: ShutdownMode) -> None:
        process = self._process
        if process is None:
            return
        if mode == "forced":
            if process.returncode is None:
                process.kill()
        elif process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=PROCESS_SHUTDOWN_TIMEOUT_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()
        await self._finish_stderr_task()

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise HarnessControlError("Claude stream transport is not started")
        return self._process

    async def _drain_stderr(self) -> None:
        process = self._require_process()
        if process.stderr is None:
            return
        while await process.stderr.read(64 * 1024):
            pass

    async def _finish_stderr_task(self) -> None:
        if self._stderr_task is None:
            return
        await asyncio.gather(self._stderr_task, return_exceptions=True)


async def probe_claude_version(
    executable: str,
    cwd: Path,
    env: Mapping[str, str],
) -> str:
    """Return bounded ``claude --version`` output without exposing stderr or environment data."""

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--version",
            cwd=cwd,
            env=dict(env) if env else None,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=4096,
        )
    except OSError as exc:
        raise HarnessControlError(f"could not probe Claude Code version: {exc}") from exc
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=VERSION_PROBE_TIMEOUT_SECONDS
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise HarnessControlError("Claude Code version probe timed out") from exc
    if process.returncode != 0:
        raise HarnessControlError(
            f"Claude Code version probe exited with status {process.returncode}"
        )
    if len(stdout) > 4096:
        raise HarnessControlError("Claude Code version output exceeded 4096 bytes")
    return stdout.decode("utf-8", errors="strict").strip()
