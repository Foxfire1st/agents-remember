"""Built-in protocol-adapter construction from settings-owned spawn state."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import HarnessControlError
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.codex_app_server_session import CodexAppServerSettings
from agents_remember.serving.harness_capabilities import LaunchKnobs
from agents_remember.serving.harness_control_adapter import (
    LaunchableHarnessProtocolAdapter,
    UnsupportedHarnessProtocolAdapter,
)
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter

BUILTIN_PROTOCOL_HARNESSES = frozenset({"claude", "codex", "pi"})


def create_harness_protocol_adapter(
    harness_id: str,
    *,
    env: Mapping[str, str],
    resolved_launch: ResolvedLaunch | None = None,
    launch_knobs: LaunchKnobs | None = None,
) -> LaunchableHarnessProtocolAdapter:
    """Create a protocol-negotiating built-in adapter; custom ids stay explicitly unsupported."""

    # Runtime environment stays on LaunchSpec. In particular, AR_SPAWN_* provenance must never
    # become roleless adapter selection authority here.
    del env
    if resolved_launch is not None and resolved_launch.harness_id != harness_id:
        raise HarnessControlError(
            "resolved launch harness does not match the adapter factory harness"
        )
    if resolved_launch is not None and launch_knobs is None:
        raise HarnessControlError("resolved launch requires adapter-produced launch knobs")

    if harness_id == "claude":
        return ClaudeStreamJsonAdapter(expected_launch=resolved_launch)
    if harness_id == "codex":
        # Until L4 supplies a typed per-session selection, a roleless/dashboard open follows the
        # native catalog defaults. Ambient AR_SPAWN_* values are provenance, not authority.
        model = resolved_launch.model_key if resolved_launch is not None else None
        effort = resolved_launch.effort if resolved_launch is not None else None
        return CodexAppServerAdapter(
            CodexAppServerSettings(
                model=model,
                reasoning_effort=effort,
                config=dict(launch_knobs.session_config) if launch_knobs is not None else {},
            )
        )
    if harness_id == "pi":
        return PiRpcAdapter(expected_launch=resolved_launch)
    return UnsupportedHarnessProtocolAdapter(harness_id)
