"""Application entry point for MCP-owned runtime installation."""

from __future__ import annotations

from typing import Any

from agents_remember.install.runtime import (
    RuntimeInstallRequest,
    install_runtime_from_config,
    open_user_settings_notice,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.serving.daemon import ensure_running_dashboard, plan_running_dashboard
from agents_remember.serving.paseo.paseo_install import install_host
from agents_remember.serving.server_registry import server_inventory

__all__ = ["RuntimeInstallRequest", "open_user_settings_notice", "run_runtime_install"]


def run_runtime_install(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
) -> dict[str, Any]:
    result = install_runtime_from_config(config, request)
    host = install_host(config, request.dry_run)
    result["host"] = host
    result["ok"] = result["ok"] and host["ok"]
    dashboard = (
        plan_running_dashboard(config) if request.dry_run else ensure_running_dashboard(config)
    )
    if dashboard is not None:
        result["dashboard"] = {
            "action": dashboard.action,
            "detail": dashboard.detail,
            **(
                {"pid": dashboard.state.pid, "sourceDigest": dashboard.state.source_digest}
                if dashboard.state is not None
                else {}
            ),
        }
        result["ok"] = result["ok"] and dashboard.action not in {"failed", "lock-held"}
    result["runningServers"] = [
        server.model_dump(mode="json", exclude_none=True) for server in server_inventory(config)
    ]
    return result
