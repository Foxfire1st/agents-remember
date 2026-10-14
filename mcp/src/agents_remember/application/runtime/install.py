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
from agents_remember.serving.paseo.paseo_install import install_host

__all__ = ["RuntimeInstallRequest", "open_user_settings_notice", "run_runtime_install"]


def run_runtime_install(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
) -> dict[str, Any]:
    result = install_runtime_from_config(config, request)
    host = install_host(config, request.dry_run)
    result["host"] = host
    result["ok"] = result["ok"] and host["ok"]
    return result
