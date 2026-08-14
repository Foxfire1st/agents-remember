"""Provider control tools: readiness, diagnostics, and watcher actions."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from ..tools import (
    provider_diagnostics_payload,
    provider_status_payload,
    provider_watchers_payload,
)


def register_provider_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def provider_status(detail_limit: int = 20) -> dict[str, Any]:
        """Compact provider readiness summary (per-provider state ready/degraded/stopped, watcher
        up, indexing state). Read-only. Returns noProviders when none are enabled in settings."""
        return provider_status_payload(config, detail_limit=detail_limit)

    @server.tool()
    def provider_diagnostics(detail_limit: int = 20) -> dict[str, Any]:
        """Raw provider-native diagnostic detail (container states, ports, backend/embedder health,
        ping output). Read-only. Use when provider_status reports degraded and you need the cause."""
        return provider_diagnostics_payload(config, detail_limit=detail_limit)

    @server.tool()
    def provider_watchers(action: str, dry_run: bool = False) -> dict[str, Any]:
        """Control provider watchers. action: 'start' (recreate containers from current compose and
        begin indexing), 'restart' (stop then start; watchers come back up and pick up changes via
        their incremental scan WITHOUT rebuilding indexes -- use this to wake a stale watcher),
        'stop'/'shutdown-all', 'invalidate-indexes' (DELETE and rebuild every index from scratch:
        full re-embed + full graph re-index, slow and CPU-heavy), or 'status'. Mutating except
        status. Indexing runs in the watcher and is never time-capped. Preview with dry_run=true."""
        return provider_watchers_payload(config, action=action, dry_run=dry_run)
