"""Application boundary for the direct landing operation."""

from __future__ import annotations

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.worktrees.direct_landing import (
    DirectLandingError,
    DirectLandingRequest,
    direct_landing,
)


def direct_landing_tool(
    config: McpRuntimeConfig,
    request: DirectLandingRequest,
) -> dict[str, object]:
    """Run one branch-addressed direct landing (policy-gated, atomic)."""
    try:
        return direct_landing(config, request)
    except DirectLandingError as exc:
        return {
            "ok": False,
            "operation": "direct_landing",
            "state": "refused",
            "status": exc.status,
            "detail": str(exc),
        }


__all__ = [
    "DirectLandingError",
    "DirectLandingRequest",
    "direct_landing_tool",
]
