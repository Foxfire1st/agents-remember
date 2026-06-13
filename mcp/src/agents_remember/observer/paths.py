"""Resolution of the observer store root -- the one read/write path abstraction.

Design §2.3 / North-Star #5: every reader and the writer resolve
``logs/observer`` here, so a future synced coordination store is a swap at one
site, not a refactor. Kept deliberately dependency-light (no reducer/snapshot
imports) so the write side can resolve the root without pulling in the read-side
machinery -- and so a *producer* (the memory_quality drift run) can resolve the
drift-snapshot path without importing the observer's read machinery either.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents_remember.mcp.config import McpRuntimeConfig

# The drift snapshot (slice 3b, b1): the memory_quality drift run persists what it
# already computes as a durable JSON snapshot here, and the reducer reads it cheaply
# with a staleness age -- the same pattern the provider ``current.json`` snapshot uses
# (no git in the read edge). The schema + dir live here, the one leaf both the
# producer and the reader import, so the on-disk contract never drifts between them.
DRIFT_SNAPSHOT_SCHEMA = "ar-drift-snapshot/v1"


def observer_logs_root(coordination_root: Path) -> Path:
    """The ``logs/observer`` root under a coordination root (the shared base)."""
    return coordination_root / "logs" / "observer"


def observer_root(config: McpRuntimeConfig) -> Path:
    """The ``logs/observer`` root under the coordination root."""
    return observer_logs_root(config.coordination_root)


def drift_snapshot_dir(coordination_root: Path) -> Path:
    """Where drift JSON snapshots live: ``logs/observer/drift``."""
    return observer_logs_root(coordination_root) / "drift"
