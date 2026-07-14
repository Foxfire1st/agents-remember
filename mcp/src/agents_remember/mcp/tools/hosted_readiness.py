"""Payload builder for exact hosted-session readiness."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from agents_remember.serving.hosted_readiness import (
    MAX_HOSTED_READINESS_WAIT_SECONDS,
    HostedReadinessHost,
    hosted_session_readiness,
)
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path

from .base import _tool_payload

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig


def hosted_session_readiness_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    wait_seconds: float = 0.0,
    catalog: TerminalCatalog | None = None,
    host: HostedReadinessHost | None = None,
) -> dict[str, Any]:
    """Run one read-only predicate wait for the exact catalog session id."""

    if (
        not math.isfinite(wait_seconds)
        or not 0.0 <= wait_seconds <= MAX_HOSTED_READINESS_WAIT_SECONDS
    ):
        raise ValueError(
            "wait_seconds must be finite and between 0 and "
            f"{MAX_HOSTED_READINESS_WAIT_SECONDS:g} seconds"
        )
    resolved_catalog = catalog or TerminalCatalog(terminal_catalog_path(config.coordination_root))
    result = hosted_session_readiness(
        resolved_catalog,
        host or TerminalHost(),
        session_id=session_id,
        wait_seconds=wait_seconds,
    )
    entry = result.entry
    return _tool_payload(
        "hosted_session_readiness",
        {
            "ok": result.status == "ready",
            "operation": "hosted_session_readiness",
            "status": result.status,
            "session": result.session_id,
            "harness": entry.harness if entry is not None else None,
            "tmuxName": entry.tmux_name if entry is not None else None,
            "controlState": entry.control_state if entry is not None else None,
            "activity": entry.control_activity if entry is not None else None,
            "acceptance": entry.control_acceptance if entry is not None else None,
            "vendorSessionId": (
                entry.control_vendor_session_id if entry is not None else None
            ),
            "pendingInteraction": (
                entry.control_pending_interaction if entry is not None else None
            ),
            "detail": result.detail,
        },
    )
