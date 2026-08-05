"""Application entry point for MCP-owned runtime installation."""

from __future__ import annotations

from typing import Any

from agents_remember.install.runtime import RuntimeInstallRequest, install_runtime_from_config
from agents_remember.mcp.config import McpRuntimeConfig

__all__ = ["RuntimeInstallRequest", "run_runtime_install"]


def run_runtime_install(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
) -> dict[str, Any]:
    return install_runtime_from_config(config, request)
