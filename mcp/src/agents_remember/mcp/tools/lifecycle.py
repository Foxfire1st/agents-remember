"""Payload builders for the ``lifecycle_*`` signal tools.

Each builder drives the process-singleton ambient lifecycle (``require_ambient``)
and returns the modeled response through ``_tool_payload`` -- so a lifecycle
signal is itself an attributed tool call that the choke point tags like any
other (``lifecycle_start`` emits ``lifecycle.started`` then its own
``tool.completed``; ``lifecycle_end`` emits ``lifecycle.ended`` and, having
cleared the ambient, no trailing ``tool.completed``).
"""

from __future__ import annotations

from typing import Any

from agents_remember.observer.ambient import build_ask, require_ambient
from agents_remember.observer.lifecycle_state import LifecycleState, coerce_phase

from .base import _tool_payload


def _state_fields(state: LifecycleState) -> dict[str, Any]:
    return {"lifecycleId": state.id, "state": state.state, "phase": state.phase}


def lifecycle_start_payload() -> dict[str, Any]:
    state = require_ambient().start()
    return _tool_payload(
        "lifecycle_start",
        {
            "ok": True,
            "operation": "lifecycle_start",
            **_state_fields(state),
            "fleeting": state.fleeting,
        },
    )


def lifecycle_block_payload(
    *,
    kind: str | None = None,
    prompt: str | None = None,
    options: list[str] | None = None,
) -> dict[str, Any]:
    state = require_ambient().block(kind=kind, prompt=prompt, options=options)
    payload: dict[str, Any] = {
        "ok": True,
        "operation": "lifecycle_block",
        **_state_fields(state),
    }
    ask = build_ask(kind, prompt, options)
    if ask is not None:
        payload["ask"] = ask
    return _tool_payload("lifecycle_block", payload)


def lifecycle_resume_payload() -> dict[str, Any]:
    state = require_ambient().resume()
    return _tool_payload(
        "lifecycle_resume",
        {"ok": True, "operation": "lifecycle_resume", **_state_fields(state)},
    )


def lifecycle_end_payload(outcome: str) -> dict[str, Any]:
    state = require_ambient().end(outcome)
    return _tool_payload(
        "lifecycle_end",
        {"ok": True, "operation": "lifecycle_end", **_state_fields(state)},
    )


def lifecycle_phase_payload(phase: str) -> dict[str, Any]:
    state = require_ambient().phase(coerce_phase(phase))
    return _tool_payload(
        "lifecycle_phase",
        {"ok": True, "operation": "lifecycle_phase", **_state_fields(state)},
    )


def switch_lifecycle_payload() -> dict[str, Any]:
    """Transition the current lifecycle away and adopt a fresh one.

    Slice scope: the create-new path. The contract-resolved worktree target and
    the save-gate promote path arrive with the worktree-attach wiring; the model
    never handles the target id.
    """
    state = require_ambient().switch()
    return _tool_payload(
        "switch_lifecycle",
        {
            "ok": True,
            "operation": "switch_lifecycle",
            **_state_fields(state),
            "fleeting": state.fleeting,
        },
    )
