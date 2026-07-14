"""Built-in protocol-adapter construction from settings-owned spawn state."""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.codex_app_server_session import CodexAppServerSettings
from agents_remember.serving.harness_control_adapter import (
    HarnessProtocolAdapter,
    UnsupportedHarnessProtocolAdapter,
)
from agents_remember.serving.harness_control_claude import ClaudeStreamJsonAdapter
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter

BUILTIN_PROTOCOL_HARNESSES = frozenset({"claude", "codex", "pi"})


def create_harness_protocol_adapter(
    harness_id: str, *, env: Mapping[str, str]
) -> HarnessProtocolAdapter:
    """Create a protocol-negotiating built-in adapter; custom ids stay explicitly unsupported."""

    if harness_id == "claude":
        return ClaudeStreamJsonAdapter()
    if harness_id == "codex":
        return CodexAppServerAdapter(
            CodexAppServerSettings(
                model=env.get("AR_SPAWN_MODEL") or None,
                reasoning_effort=env.get("AR_SPAWN_EFFORT") or "medium",
            )
        )
    if harness_id == "pi":
        return PiRpcAdapter()
    return UnsupportedHarnessProtocolAdapter(harness_id)
