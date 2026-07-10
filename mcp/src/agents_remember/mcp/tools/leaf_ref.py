"""MCP payload helpers for leaf-ref validation refusals."""

from __future__ import annotations

from typing import Any

from agents_remember.worktrees.leaf_refs import LeafRefResolutionError

from .base import _tool_payload


def leaf_ref_refusal_payload(
    operation: str,
    leaf_key: str,
    error: LeafRefResolutionError,
    *,
    kind: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "operation": operation,
        "status": error.status,
        "session": "",
        "leafKey": leaf_key,
        "detail": str(error),
    }
    if operation == "spawn_agent_session":
        payload["kind"] = kind if kind in ("harness", "terminal") else None
    return _tool_payload(operation, payload)
