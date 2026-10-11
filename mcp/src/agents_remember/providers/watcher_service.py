"""Configured provider-watcher lifecycle service shared by upper performers."""

from __future__ import annotations

from typing import Any

from agents_remember.kernel.primitives.runtime_config import (
    DEFAULT_DOCKER_CONTROL_SECONDS,
    McpRuntimeConfig,
)
from agents_remember.providers.current_state import build_current_provider_state
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

    settings_path = write_lifecycle_settings()
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
            # Status files none (rule 6, and the tool's own description): the answer
            # carries the aggregated state with no snapshot file behind it. Lifecycle
            # actions and the serving projection's explicit refresh still persist.
            aggregated = build_current_provider_state(config, payload)
            payload["currentStateFile"] = None
            payload["currentState"] = aggregated
            payload["state"] = aggregated["state"]
        return payload
    finally:
        settings_path.unlink(missing_ok=True)
