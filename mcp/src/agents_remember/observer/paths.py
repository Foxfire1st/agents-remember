"""Resolution of the observer store root -- the one read/write path abstraction.

Design §2.3 / North-Star #5: every reader and the writer resolve
``logs/observer`` here, so a future synced coordination store is a swap at one
site, not a refactor. Kept deliberately dependency-light (no reducer/snapshot
imports) so the write side can resolve the root without pulling in the read-side
machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from agents_remember.mcp.config import McpRuntimeConfig


def observer_root(config: McpRuntimeConfig) -> Path:
    """The ``logs/observer`` root under the coordination root."""
    return config.coordination_root / "logs" / "observer"
