"""Application-owned startup operations for the MCP process."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.controlplane.durable_store import declare_process_role
from agents_remember.models.core import ServingBuildPayload
from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, observer_root
from agents_remember.serving.build_info import process_serving_build
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


def mcp_serving_build_payload() -> ServingBuildPayload:
    """Return the application-owned boot identity exposed by the MCP adapter."""

    return process_serving_build().payload()


def measuring_build_stamp() -> dict[str, object]:
    """Which build produced a measurement, as wire JSON (D-33).

    A memory-quality or citation count is produced by ONE build, and the build that answers an MCP
    tool call need not be the candidate being measured: ``server_info`` reported
    ``servingBuild.commit f0313143`` while the leaf under curation carried newer code, so a reader
    of a checklist could not tell a candidate-ruler count from a serving-build-ruler count. This
    stamp is what makes the ruler nameable, and it costs nothing per call -- ``process_serving_build``
    resolves the identity once per process, so this is a dict copy rather than a probe. ``dirty``
    rides along so an uncommitted serving tree reads as such instead of being taken for the commit
    it names.
    """

    return {
        "servingBuild": process_serving_build().payload().model_dump(mode="json", exclude_none=True)
    }


def declare_mcp_process() -> None:
    """Declare trusted MCP execution before authority settings are loaded."""
    declare_process_role("mcp")


def prepare_mcp_process(config: McpRuntimeConfig) -> None:
    """Declare MCP store ownership, then start optional dashboard supervision."""
    declare_mcp_process()
    maybe_autostart_dashboard(config)
