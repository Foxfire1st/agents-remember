"""Application operations for the ``lifecycle_*`` signals.

Each builder drives the process-singleton ambient lifecycle (``require_ambient``)
and returns the modeled response through ``_result``. The six model-invoked signal
tools are removed (MIK-R112); only the internal compatibility builder remains.
"""

from __future__ import annotations

from typing import Any

from agents_remember.observer.ambient import build_ask, require_ambient
from agents_remember.observer.lifecycle_state import LifecycleState


def _result(_tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the raw use-case result for the MCP adapter to finalize."""
    return payload


def _state_fields(state: LifecycleState) -> dict[str, Any]:
    return {"lifecycleId": state.id, "state": state.state, "phase": state.phase}


def lifecycle_block_tool(
    *,
    kind: str | None = None,
    prompt: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    """Lower-level compatibility builder; public agent gates use ``lifecycle_gate``."""
    state = require_ambient().block(kind=kind, prompt=prompt, options=options)
    payload: dict[str, Any] = {
        "ok": True,
        "operation": "lifecycle_block",
        **_state_fields(state),
    }
    ask = build_ask(kind, prompt, options)
    if ask is not None:
        payload["ask"] = ask
    return _result("lifecycle_block", payload)
