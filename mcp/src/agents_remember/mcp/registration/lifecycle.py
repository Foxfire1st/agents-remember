"""Session-lifecycle tools: the state and phase signals an agent sends about itself."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from ..tools import (
    lifecycle_end_payload,
    lifecycle_phase_payload,
    lifecycle_resume_payload,
    lifecycle_start_payload,
    lifecycle_turn_end_notification_payload,
    switch_lifecycle_payload,
)


def register_lifecycle_tools(server: FastMCP, _config: McpRuntimeConfig) -> None:
    # These payloads act on the process-wide ambient lifecycle, not on resolved settings, so
    # the config goes unused -- the registrar still takes it to keep the one signature every
    # module in this package is called with.
    @server.tool()
    def lifecycle_start() -> dict[str, Any]:
        """Begin a new session lifecycle and become its running owner. Guarded: rejected
        (with a reminder naming the active lifecycle) when one is already active in this
        session -- end or switch it first. Takes no identifier; the server mints and tracks
        the id. Signal this once governance is confirmed at the trust checkpoint."""
        return lifecycle_start_payload()

    @server.tool()
    def lifecycle_resume() -> dict[str, Any]:
        """Resume the active lifecycle from blocked back to running once the gate or question
        that blocked it is resolved."""
        return lifecycle_resume_payload()

    @server.tool()
    def lifecycle_turn_end_notification(summary: str) -> dict[str, Any]:
        """Notify the developer the turn is complete and stop -- no wait, no gate; the next AR
        tool next turn resumes automatically."""
        return lifecycle_turn_end_notification_payload(summary)

    @server.tool()
    def lifecycle_end(outcome: str) -> dict[str, Any]:
        """End the active lifecycle. outcome is 'completed' (the human declared done) or
        'abandoned' (otherwise). Clears the session's active lifecycle."""
        return lifecycle_end_payload(outcome)

    @server.tool()
    def switch_lifecycle(on_unsaved: str | None = None) -> dict[str, Any]:
        """Leave the current lifecycle and begin a fresh one. A persistent lifecycle is paused;
        leaving an unsaved fleeting one needs on_unsaved='save' (promote) or 'discard' (abandon).
        The model never handles ids; resuming an existing lifecycle is worktree_attach."""
        return switch_lifecycle_payload(on_unsaved)

    @server.tool()
    def lifecycle_phase(phase: str) -> dict[str, Any]:
        """Move the active lifecycle along its phase axis (orthogonal to state): one of
        request | trust-checkpoint | reframe-research | decide | build | close."""
        return lifecycle_phase_payload(phase)
