"""Application operations for the ``lifecycle_*`` signals.

Each builder drives the process-singleton ambient lifecycle (``require_ambient``)
and returns the modeled response through ``_result`` -- so a lifecycle
signal is itself an attributed tool call that the choke point tags like any
other (``lifecycle_start`` emits ``lifecycle.started`` then its own
``tool.completed``; ``lifecycle_end`` emits ``lifecycle.ended`` and, having
cleared the ambient, no trailing ``tool.completed``).
"""

from __future__ import annotations

from typing import Any

from agents_remember.application.next_step import FRONT_HALF_RUNDOWN
from agents_remember.observer.ambient import build_ask, require_ambient
from agents_remember.observer.lifecycle_state import LifecycleState, coerce_phase
from agents_remember.observer.save_gate import coerce_save_decision


def _result(_tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the raw use-case result for the MCP adapter to finalize."""
    return payload


def _state_fields(state: LifecycleState) -> dict[str, Any]:
    return {"lifecycleId": state.id, "state": state.state, "phase": state.phase}


def lifecycle_start_tool() -> dict[str, Any]:
    state = require_ambient().start()
    return _result(
        "lifecycle_start",
        {
            "ok": True,
            "operation": "lifecycle_start",
            **_state_fields(state),
            "fleeting": state.fleeting,
            # Task 27: the one-time, non-linear front-half roadmap. The per-tool
            # nextStep chain only begins once the worktree exists.
            "frontHalfRundown": list(FRONT_HALF_RUNDOWN),
        },
    )


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


def lifecycle_resume_tool() -> dict[str, Any]:
    state = require_ambient().resume()
    return _result(
        "lifecycle_resume",
        {"ok": True, "operation": "lifecycle_resume", **_state_fields(state)},
    )


def lifecycle_turn_end_notification_tool(summary: str) -> dict[str, Any]:
    """NOTIFY-AND-CONTINUE turn end (leaf-28): declare the turn complete and stop.

    No gate, no wait -- ``await_developer`` flips the lifecycle to
    ``awaiting-developer``; the next AR tool call auto-resumes it at the
    application tool-response choke point. This tool itself does NOT self-dismiss (the
    choke point guards on the tool name) so its own response still reports the
    awaiting-developer state.
    """
    state = require_ambient().await_developer(summary=summary)
    return _result(
        "lifecycle_turn_end_notification",
        {
            "ok": True,
            "operation": "lifecycle_turn_end_notification",
            **_state_fields(state),
            "summary": summary,
        },
    )


def lifecycle_end_tool(outcome: str) -> dict[str, Any]:
    state = require_ambient().end(outcome)
    return _result(
        "lifecycle_end",
        {"ok": True, "operation": "lifecycle_end", **_state_fields(state)},
    )


def lifecycle_phase_tool(phase: str) -> dict[str, Any]:
    state = require_ambient().phase(coerce_phase(phase))
    return _result(
        "lifecycle_phase",
        {"ok": True, "operation": "lifecycle_phase", **_state_fields(state)},
    )


def switch_lifecycle_tool(on_unsaved: str | None = None) -> dict[str, Any]:
    """Leave the current lifecycle and adopt a fresh one (no target).

    Leaving a *fleeting* lifecycle routes through the save gate: ``on_unsaved``
    must be ``save`` (promote it) or ``discard`` (abandon it), and omitting it
    raises ``SaveGateRequired`` so unsaved work is never dropped silently.
    Adopting an *existing* lifecycle is contract-resolved through
    ``worktree_attach``; the model never handles ids here.
    """
    decision = coerce_save_decision(on_unsaved) if on_unsaved else None
    state = require_ambient().switch(on_unsaved=decision)
    return _result(
        "switch_lifecycle",
        {
            "ok": True,
            "operation": "switch_lifecycle",
            **_state_fields(state),
            "fleeting": state.fleeting,
        },
    )
