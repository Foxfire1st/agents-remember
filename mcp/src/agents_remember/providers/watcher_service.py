"""Configured provider-watcher lifecycle service shared by upper performers."""

from __future__ import annotations

from typing import Any

from agents_remember.mcp.config import DEFAULT_DOCKER_CONTROL_SECONDS, McpRuntimeConfig
from agents_remember.providers.current_state import write_current_provider_state
from agents_remember.providers.lifecycle_service import (
    ProviderLifecycleServiceConfig,
    run_watchers_lifecycle,
)
from agents_remember.providers.settings import write_lifecycle_settings


def run_configured_watchers(
    config: McpRuntimeConfig,
    action: str,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Run one watcher action against temporary settings derived from live config."""

    settings_path = write_lifecycle_settings(config)
    try:
        data = run_watchers_lifecycle(
            ProviderLifecycleServiceConfig(
                coordination_root=config.coordination_root,
                settings_path=settings_path,
                dry_run=dry_run,
                timeout=DEFAULT_DOCKER_CONTROL_SECONDS,
            ),
            action=action,
        )
        payload = {**data, "operation": "provider_watchers", "ok": bool(data.get("ok"))}
        if action == "status" and payload.get("provider") == "watchers":
            current_state = write_current_provider_state(config, payload)
            payload["currentStateFile"] = current_state["path"]
            payload["currentState"] = current_state["state"]
            payload["state"] = current_state["state"]["state"]
        return payload
    finally:
        settings_path.unlink(missing_ok=True)
