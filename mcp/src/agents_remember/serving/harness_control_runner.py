"""Hosted terminal process that owns one adapter bridge and renders its transcript."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.serving.harness_control_adapter import (
    LaunchableHarnessProtocolAdapter,
    UnsupportedHarnessProtocolAdapter,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_factories import create_harness_protocol_adapter
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    LaunchSpec,
    PromptRequest,
)
from agents_remember.serving.harness_launch import (
    ResolvedLaunch,
    apply_launch_knobs,
    validate_launch_selection,
)
from agents_remember.serving.harness_terminal_surface import HarnessTerminalSurface


@dataclass(frozen=True)
class RunnerConfig:
    identity: ControlIdentity
    harness_id: str
    cwd: Path
    argv: tuple[str, ...]
    endpoint_root: Path
    session_commands: tuple[str, ...] = ()
    resolved_launch: ResolvedLaunch | None = None
    resume_thread_id: str | None = None


def control_runner_command(config: RunnerConfig) -> tuple[str, ...]:
    payload = {
        "identity": config.identity.to_json(),
        "harnessId": config.harness_id,
        "cwd": str(config.cwd),
        "argv": list(config.argv),
        "endpointRoot": str(config.endpoint_root),
        "sessionCommands": list(config.session_commands),
        "resolvedLaunch": (
            config.resolved_launch.to_json() if config.resolved_launch is not None else None
        ),
        "resumeThreadId": config.resume_thread_id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return (sys.executable, "-m", "agents_remember.serving.harness_control_runner", encoded)


def parse_runner_config(encoded: str) -> RunnerConfig:
    try:
        raw = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessControlError("hosted control runner config is malformed") from exc
    if not isinstance(raw, dict):
        raise HarnessControlError("hosted control runner config must be an object")
    identity = raw.get("identity")
    argv = raw.get("argv")
    session_commands = raw.get("sessionCommands", [])
    if (
        not isinstance(identity, dict)
        or not isinstance(argv, list)
        or not all(isinstance(part, str) and part for part in argv)
        or not isinstance(session_commands, list)
        or not all(isinstance(command, str) and command for command in session_commands)
    ):
        raise HarnessControlError("hosted control runner requires identity and argv")
    resolved_raw = raw.get("resolvedLaunch")
    resolved_launch = ResolvedLaunch.from_json(resolved_raw) if resolved_raw is not None else None
    resume_thread_id = raw.get("resumeThreadId")
    if resume_thread_id is not None and (
        not isinstance(resume_thread_id, str)
        or not resume_thread_id
        or resume_thread_id != resume_thread_id.strip()
    ):
        raise HarnessControlError(
            "hosted control runner resumeThreadId must be non-empty trimmed text or null"
        )
    config = RunnerConfig(
        identity=ControlIdentity.from_json(identity),
        harness_id=_required_text(raw, "harnessId"),
        cwd=Path(_required_text(raw, "cwd")),
        argv=tuple(argv),
        endpoint_root=Path(_required_text(raw, "endpointRoot")),
        session_commands=tuple(session_commands),
        resolved_launch=resolved_launch,
        resume_thread_id=resume_thread_id,
    )
    if resolved_launch is not None:
        if resolved_launch.harness_id != config.harness_id:
            raise HarnessControlError("runner resolved launch harness does not match harnessId")
        if resolved_launch.workspace != config.cwd:
            raise HarnessControlError("runner resolved launch workspace does not match cwd")
    return config


async def run_controlled_session(config: RunnerConfig) -> None:
    prepare_error: Exception | None = None
    try:
        adapter, launch = await _prepare_controlled_launch(config, env=os.environ)
    except Exception as exc:
        prepare_error = exc
        adapter = UnsupportedHarnessProtocolAdapter(config.harness_id)
        launch = None
    bridge = HarnessControlBridge(config.identity, adapter)
    endpoint = LocalControlEndpoint.for_session(config.endpoint_root, config.identity)
    server = HarnessControlServer(endpoint, bridge)
    await server.start()
    try:
        if prepare_error is not None:
            bridge.mark_failed(str(prepare_error))
        else:
            assert launch is not None
            try:
                await bridge.start(launch)
            except Exception as exc:
                bridge.mark_failed(str(exc))
        surface = HarnessTerminalSurface(bridge)
        if bridge.snapshot().control == "ready":
            await _submit_session_commands(bridge, config.session_commands)
        print(
            f"Agents Remember protocol session {config.harness_id}: {bridge.snapshot().control}",
            flush=True,
        )
        if bridge.snapshot().control == "failed":
            # Keep the failed endpoint addressable so readiness and daemon consumers can retrieve
            # the exact bridgeError. Exiting here would tear down tmux/socket before that evidence
            # could be observed, turning a precise launch refusal into a generic disconnect.
            print(
                f"[control] launch failed: {bridge.snapshot().raw.get('bridgeError', 'unknown error')}",
                flush=True,
            )
            await _read_terminal_input(surface)
            return
        render = asyncio.create_task(_render_updates(surface))
        terminal_input = asyncio.create_task(_read_terminal_input(surface))
        await asyncio.wait({render, terminal_input}, return_when=asyncio.FIRST_COMPLETED)
        render.cancel()
        terminal_input.cancel()
        await asyncio.gather(render, terminal_input, return_exceptions=True)
    finally:
        await server.close()
        await bridge.stop("forced")


async def _prepare_controlled_launch(
    config: RunnerConfig,
    *,
    env: Mapping[str, str],
) -> tuple[LaunchableHarnessProtocolAdapter, LaunchSpec]:
    """Discover, validate, then apply native knobs before the real harness process starts."""

    base = LaunchSpec(
        identity=config.identity,
        harness_id=config.harness_id,
        cwd=config.cwd,
        argv=adapter_argv(config.harness_id, config.argv),
        env=dict(env),
    )
    selection = config.resolved_launch
    if selection is None:
        return (
            create_harness_protocol_adapter(
                config.harness_id,
                env=env,
                resume_thread_id=config.resume_thread_id,
            ),
            base,
        )

    discovery_env = {
        **env,
        "AR_SPAWN_MODEL": selection.model_key,
        "AR_SPAWN_EFFORT": selection.effort,
    }
    discoverer = create_harness_protocol_adapter(config.harness_id, env=discovery_env)
    knobs = discoverer.launch_knobs(
        model_key=selection.model_key,
        effort=selection.effort,
    )
    # This is a pure preflight. Owned selector/config conflicts must refuse before the transient
    # discovery adapter is allowed to start a vendor process.
    launch = apply_launch_knobs(base, knobs)
    snapshot = await discoverer.discover(base)
    validate_launch_selection(selection, snapshot)
    adapter = create_harness_protocol_adapter(
        config.harness_id,
        env=env,
        resolved_launch=selection,
        launch_knobs=knobs,
        resume_thread_id=config.resume_thread_id,
    )
    return adapter, launch


async def _render_updates(surface: HarnessTerminalSurface) -> None:
    after_sequence = 0
    previous_state: tuple[str, str, str] | None = None
    async for snapshot in surface.bridge.subscribe():
        state = (snapshot.control, snapshot.activity, snapshot.acceptance)
        if state != previous_state:
            print(f"[control] {state[0]} activity={state[1]} acceptance={state[2]}", flush=True)
            previous_state = state
        entries = surface.bridge.transcript(after_sequence=after_sequence)
        for entry in entries:
            request = f" request={entry.request_id}" if entry.request_id else ""
            result = (
                f" result={entry.terminal_result.outcome}"
                if entry.terminal_result is not None
                else ""
            )
            print(
                f"[{entry.created_at}] {entry.role}{request}{result}: {_readable(entry.text)}",
                flush=True,
            )
            after_sequence = entry.sequence
        if snapshot.control in {"failed", "disconnected"}:
            return


async def _read_terminal_input(surface: HarnessTerminalSurface) -> None:
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        text = _committed_line(line)
        if not text:
            continue
        try:
            receipt = await surface.submit_terminal(text)
        except HarnessControlError as exc:
            print(f"[control] rejected: {exc}", flush=True)
            continue
        print(
            f"[control] submission {receipt.request_id} {receipt.acceptance}"
            + (f": {receipt.detail}" if receipt.detail else ""),
            flush=True,
        )


async def _submit_session_commands(bridge: HarnessControlBridge, commands: tuple[str, ...]) -> None:
    for index, command in enumerate(commands, start=1):
        now = datetime.now(UTC).isoformat()
        receipt = await bridge.submit(
            PromptRequest(
                request_id=f"{bridge.identity.ar_session_id}:session-command:{index}",
                source="durable",
                text=command,
                submitted_at=now,
            )
        )
        if receipt.acceptance not in {"immediate", "queued"}:
            print(
                f"[control] session command {index} {receipt.acceptance}"
                + (f": {receipt.detail}" if receipt.detail else ""),
                flush=True,
            )


def _committed_line(line: str) -> str:
    return line.replace("\x1b[200~", "").replace("\x1b[201~", "").rstrip("\r\n")


def adapter_argv(harness_id: str, argv: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize one registry argv for the native protocol boundary."""

    if harness_id != "codex":
        return argv
    # Keep every settings/user-supplied argument. The normalized adapter owns model and effort;
    # silently deleting an older ``--model`` or ``--config model_reasoning_effort=...`` here would
    # hide conflicting authority instead of letting ``apply_launch_knobs`` refuse it explicitly.
    return (argv[0], "app-server", *argv[1:])


def _readable(text: str) -> str:
    return "".join(character for character in text if character in "\n\t" or ord(character) >= 32)


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"hosted control runner requires non-empty {key}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run_controlled_session(parse_runner_config(args.config)))
    except KeyboardInterrupt:
        return 130
    except HarnessControlError as exc:
        print(f"Agents Remember control runner failed: {exc}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
