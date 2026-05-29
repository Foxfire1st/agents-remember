"""Server, context, install, and skills payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.controllers.coordination_tools import resolve_context_tool
from agents_remember.controllers.runtime_install import RuntimeInstallRequest, run_runtime_install
from agents_remember.controllers.skill_tools import skills_install_tool

from .. import SERVER_NAME, SERVER_VERSION
from ..config import McpRuntimeConfig
from .base import PUBLIC_TOOLS, RESERVED_TOOLS, TRANSPORT, _tool_payload


def ping_payload() -> dict[str, Any]:
    return _tool_payload(
        "ping",
        {
            "ok": True,
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "transport": TRANSPORT,
        },
    )


def server_info_payload(config: McpRuntimeConfig) -> dict[str, Any]:
    return _tool_payload(
        "server_info",
        {
            "ok": True,
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "transport": TRANSPORT,
            "configPath": config.config_path.as_posix(),
            "coordinationRoot": config.coordination_root.as_posix(),
            "workspaceRoot": config.workspace_root.as_posix(),
            "transcriptRoot": config.transcript_root.as_posix(),
            "harnessSkillRoot": (
                config.harness_skill_root.as_posix() if config.harness_skill_root else None
            ),
            "allowedRepoIds": list(config.allowed_repo_ids),
            "allowedProviderIds": list(config.allowed_provider_ids),
            "tools": list(PUBLIC_TOOLS),
            "reservedTools": list(RESERVED_TOOLS),
        },
    )


def context_packet_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    include_providers: bool = True,
    include_drift: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "context_packet",
        build_context_packet(
            config,
            ContextPacketRequest(
                repo_id=repo_id,
                include_providers=include_providers,
                include_drift=include_drift,
            ),
        ),
    )


def runtime_install_payload(
    config: McpRuntimeConfig,
    *,
    dry_run: bool = False,
    include_benchmarks: bool = False,
    install_provider_deps: bool = True,
) -> dict[str, Any]:
    return _tool_payload(
        "runtime_install",
        run_runtime_install(
            config,
            RuntimeInstallRequest(
                dry_run=dry_run,
                include_benchmarks=include_benchmarks,
                install_provider_deps=install_provider_deps,
            ),
        ),
    )


def resolve_context_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
    worktree_name: str | None = None,
    topology: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "resolve_context",
        resolve_context_tool(
            config,
            repo_id=repo_id,
            task_name=task_name,
            contract_path=contract_path,
            worktree_name=worktree_name,
            topology=topology,
        ),
    )


def skills_install_payload(
    config: McpRuntimeConfig,
    *,
    layout: str = "tree",
    dry_run: bool = False,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "skills_install",
        skills_install_tool(
            config,
            layout=layout,
            dry_run=dry_run,
            overwrite=overwrite,
            archive_existing=archive_existing,
        ),
    )
