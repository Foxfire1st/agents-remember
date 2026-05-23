"""Pure payload builders for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.controllers.runtime_install import (
    RuntimeInstallRequest,
    run_runtime_install,
)

from . import SERVER_NAME, SERVER_VERSION
from .config import McpRuntimeConfig

TRANSPORT = "stdio"
PUBLIC_TOOLS = ("ping", "server_info", "context_packet", "runtime_install")
RESERVED_TOOLS = ("provider_status",)


def ping_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "transport": TRANSPORT,
    }


def server_info_payload(config: McpRuntimeConfig) -> dict[str, Any]:
    return {
        "ok": True,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "transport": TRANSPORT,
        "configPath": config.config_path.as_posix(),
        "coordinationRoot": config.coordination_root.as_posix(),
        "workspaceRoot": config.workspace_root.as_posix(),
        "transcriptRoot": config.transcript_root.as_posix(),
        "allowedRepoIds": list(config.allowed_repo_ids),
        "allowedProviderIds": list(config.allowed_provider_ids),
        "tools": list(PUBLIC_TOOLS),
        "reservedTools": list(RESERVED_TOOLS),
    }


def context_packet_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    include_providers: bool = True,
    include_drift: bool = False,
) -> dict[str, Any]:
    return build_context_packet(
        config,
        ContextPacketRequest(
            repo_id=repo_id,
            include_providers=include_providers,
            include_drift=include_drift,
        ),
    )


def runtime_install_payload(
    config: McpRuntimeConfig,
    *,
    dry_run: bool = True,
    include_benchmarks: bool = False,
    install_provider_deps: bool = True,
) -> dict[str, Any]:
    return run_runtime_install(
        config,
        RuntimeInstallRequest(
            dry_run=dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
        ),
    )
