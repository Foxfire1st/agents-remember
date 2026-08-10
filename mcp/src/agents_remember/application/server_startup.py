"""Application-owned startup operations for the MCP process."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, observer_root
from agents_remember.serving.daemon import maybe_autostart_dashboard

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


def initialize_mcp_application(config: McpRuntimeConfig) -> None:
    """Install the process-wide application collaborators used by registered operations."""
    install_ambient(AmbientLifecycle(EventStore(observer_root(config))))


def prepare_mcp_process(config: McpRuntimeConfig) -> None:
    """Declare MCP store ownership, then start optional dashboard supervision."""
    declare_process_role("mcp")
    maybe_autostart_dashboard(config)
