"""Built-in protocol-adapter construction from settings-owned spawn state."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import HarnessControlError
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.codex_app_server_session import (
    CodexAppServerSettings,
    codex_launch_knobs,
)
from agents_remember.serving.harness_capabilities import LaunchKnobs
from agents_remember.serving.harness_control_adapter import (
    LaunchableHarnessProtocolAdapter,
    UnsupportedHarnessProtocolAdapter,
)
from agents_remember.serving.harness_control_claude import (
    ClaudeStreamJsonAdapter,
    claude_launch_knobs,
)
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.pi_rpc_protocol import pi_launch_knobs

BUILTIN_PROTOCOL_HARNESSES = frozenset({"claude", "codex", "pi"})

_LAUNCH_KNOBS = {
    "claude": claude_launch_knobs,
    "codex": codex_launch_knobs,
    "pi": pi_launch_knobs,
}


def harness_launch_knobs(harness_id: str, *, model_key: str, effort: str | None) -> LaunchKnobs:
    """How one harness spells a model/effort selection at launch, before any adapter exists.

    Answered from the harness id alone because none of the three implementations reads adapter
    state; a custom id has no native launch vocabulary and is refused rather than defaulted.
    """

    knobs = _LAUNCH_KNOBS.get(harness_id)
    if knobs is None:
        raise HarnessControlError(f"no protocol adapter is registered for {harness_id!r}")
    return knobs(model_key=model_key, effort=effort)


def create_harness_protocol_adapter(
    harness_id: str,
    *,
    env: Mapping[str, str],
    resolved_launch: ResolvedLaunch | None = None,
    launch_knobs: LaunchKnobs | None = None,
    resume_thread_id: str | None = None,
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
    if resume_thread_id is not None:
        if harness_id != "codex":
            raise HarnessControlError("resume_thread_id is only supported for the codex harness")
        if not resume_thread_id or resume_thread_id != resume_thread_id.strip():
            raise HarnessControlError("resume_thread_id must be non-empty with no outer whitespace")

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
                resume_thread_id=resume_thread_id,
                config=dict(launch_knobs.session_config) if launch_knobs is not None else {},
            )
        )
    if harness_id == "pi":
        return PiRpcAdapter(expected_launch=resolved_launch)
    return UnsupportedHarnessProtocolAdapter(harness_id)
