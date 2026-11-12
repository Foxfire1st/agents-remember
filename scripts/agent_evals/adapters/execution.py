"""Configured native commands, with sandbox-bound proof before launch.

The measurement owner supplies the native invocation and binding resolver. This
module invents no harness flags and writes no harness configuration. A probe
uses the same command factory and environment as the actor's start.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.errors import AuthorityError
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel


@dataclass(frozen=True)
class LaunchProfile:
    harness: Literal["codex", "hermes", "claude"]
    model: str
    mode: str | None
    reasoning: str | None = None
    service_tier: str | None = None
    auto_accept: bool = False


CODEX_ACTOR = LaunchProfile("codex", "gpt-6.1-sol", "auto-review", "high", "priority")
HERMES_ACTOR = LaunchProfile("hermes", "opencode-go:deepseek-v4.1-flash", "accept_edits")
HERMES_KIMI_ACTOR = LaunchProfile("hermes", "opencode-go:kimi-k3.1-pro", "accept_edits")
HERMES_GPT_ACTOR = LaunchProfile("hermes", "openai-codex:gpt-6.1-sol-900k", "accept_edits", "high")
ACTOR_PROFILES = (CODEX_ACTOR, HERMES_ACTOR, HERMES_KIMI_ACTOR, HERMES_GPT_ACTOR)
OPUS_JUDGE = LaunchProfile("claude", "claude-opus-5-5", None)
CODEX_JUDGE = LaunchProfile("codex", "gpt-6.1-sol", None, "high", "priority")


def actor_profile(model: str) -> LaunchProfile:
    """Resolve exact admitted selectors; the Hermes alias keeps its DeepSeek seat."""
    if model == "hermes-kimi":
        return HERMES_KIMI_ACTOR
    if model == "hermes-gpt":
        return HERMES_GPT_ACTOR
    for profile in ACTOR_PROFILES:
        if model in (profile.harness, profile.model):
            return profile
    raise AuthorityError("trial model must name the admitted Codex or Hermes profile")


@dataclass(frozen=True)
class NativeBinding:
    """Observed by an owner-provided native resolver, never actor self-report."""

    project_root: Path
    sandbox_home: Path
    settings_path: Path
    tool_build: str
    servers: tuple[str, ...]
    profile: LaunchProfile


@dataclass(frozen=True)
class NativeInvocation:
    argv: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]
    run_root: Path
    binding: NativeBinding
    timeout_seconds: float = 600
    max_output_bytes: int = 16 * 1024 * 1024


@dataclass(frozen=True)
class NativeExecution:
    returncode: int | None
    stdout_path: Path
    stderr_path: Path
    unknowns: tuple[str, ...]


def preflight(
    invocation: NativeInvocation, resolver: Callable[[NativeInvocation], NativeBinding]
) -> None:
    root = invocation.run_root.resolve(strict=True)
    binding = invocation.binding
    for path in (invocation.cwd, binding.project_root, binding.sandbox_home, binding.settings_path):
        relative = os.path.relpath(path.absolute(), root)
        confine_non_symlink_rel(root, relative)
    if invocation.cwd.resolve() != binding.project_root.resolve():
        raise AuthorityError("native cwd differs from bound project")
    if binding.profile in (OPUS_JUDGE, CODEX_JUDGE) and binding.servers:
        raise AuthorityError("rubric judges admit no tool servers")
    if not binding.tool_build or not invocation.argv or not Path(invocation.argv[0]).is_absolute():
        raise AuthorityError("native executable must be absolute and tool build must be bound")
    if invocation.timeout_seconds <= 0 or invocation.max_output_bytes <= 0:
        raise AuthorityError("native capture limits must be positive")
    if resolver(invocation) != binding:
        raise AuthorityError("observed native binding differs from requested project/build/profile")


def execute_native(
    invocation: NativeInvocation,
    *,
    resolver: Callable[[NativeInvocation], NativeBinding],
    prompt: str,
) -> NativeExecution:
    preflight(invocation, resolver)
    stdout_path = invocation.run_root / "native.stdout"
    stderr_path = invocation.run_root / "native.stderr"
    unknowns: list[str] = []
    stdin_path = invocation.run_root / "native.stdin"
    with stdin_path.open("x", encoding="utf-8") as stream:
        stream.write(prompt)
    with (
        stdin_path.open("rb") as stdin,
        stdout_path.open("xb") as stdout,
        stderr_path.open("xb") as stderr,
    ):
        process = subprocess.Popen(
            invocation.argv,
            cwd=invocation.cwd,
            env=dict(invocation.environment),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            assert process.stdout is not None and process.stderr is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, stdout)
                selector.register(process.stderr, selectors.EVENT_READ, stderr)
                deadline = time.monotonic() + invocation.timeout_seconds
                sizes = {stdout: 0, stderr: 0}
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        unknowns.append("native process timed out; process group reclaimed")
                        break
                    exceeded = False
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        sink = key.data
                        available = invocation.max_output_bytes - sizes[sink]
                        sink.write(chunk[:available])
                        sizes[sink] += min(len(chunk), available)
                        if len(chunk) > available:
                            unknowns.append("native output limit exceeded; capture incomplete")
                            exceeded = True
                            break
                    if exceeded:
                        break
        finally:
            # Descendants inheriting the capture FDs must not outlive the native run.
            _kill_group(process)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    return NativeExecution(process.returncode, stdout_path, stderr_path, tuple(unknowns))


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def binding_probe_payload(binding: NativeBinding) -> str:
    """A resolver hook may use this schema; it must observe its native settings."""
    return json.dumps(
        {
            "project_root": str(binding.project_root),
            "sandbox_home": str(binding.sandbox_home),
            "settings_path": str(binding.settings_path),
            "tool_build": binding.tool_build,
            "servers": list(binding.servers),
            "model": binding.profile.model,
            "mode": binding.profile.mode,
            "reasoning": binding.profile.reasoning,
            "service_tier": binding.profile.service_tier,
            "auto_accept": binding.profile.auto_accept,
        },
        sort_keys=True,
    )
