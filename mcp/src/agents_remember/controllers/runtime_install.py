"""Controller for MCP-owned runtime installation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.install.runtime import install_runtime_from_config
from agents_remember.mcp.config import McpRuntimeConfig


@dataclass(frozen=True)
class RuntimeInstallRequest:
    dry_run: bool = True
    include_benchmarks: bool = False
    install_provider_deps: bool = True


def run_runtime_install(
    config: McpRuntimeConfig,
    request: RuntimeInstallRequest,
) -> dict[str, Any]:
    return install_runtime_from_config(
        config,
        dry_run=request.dry_run,
        include_benchmarks=request.include_benchmarks,
        install_provider_deps=request.install_provider_deps,
    )
