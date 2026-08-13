"""Application-owned startup operations for the MCP process."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, observer_root
from agents_remember.serving.control_plane_identity_migration import (
    migrate_control_plane_identity_logs,
)
from agents_remember.serving.daemon import maybe_autostart_dashboard

if TYPE_CHECKING:
    from agents_remember.kernel.primitives.runtime_config import (
        McpRuntimeConfig,
    )


def initialize_mcp_application(config: McpRuntimeConfig) -> None:
    """Install the process-wide application collaborators used by registered operations."""
    migrate_control_plane_identity_logs(
        config.coordination_root, include_agent_notifier_signals=False
    )
    install_ambient(AmbientLifecycle(EventStore(observer_root(config))))


def declare_mcp_process() -> None:
    """Declare trusted MCP execution before authority settings are loaded."""
    declare_process_role("mcp")


def prepare_mcp_process(config: McpRuntimeConfig) -> None:
    """Declare MCP store ownership, then start optional dashboard supervision."""
    declare_mcp_process()
    maybe_autostart_dashboard(config)
