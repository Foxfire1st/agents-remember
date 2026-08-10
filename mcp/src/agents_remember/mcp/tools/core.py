"""Server, context, install, and skills payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.application.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.application.coordination_tools import resolve_context_tool
from agents_remember.application.runtime_install import RuntimeInstallRequest, run_runtime_install
from agents_remember.application.skill_tools import skills_install_tool
from agents_remember.application.task_ref import TaskRef
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.primitives.tool_reports import write_tool_report

from .. import SERVER_NAME, SERVER_VERSION
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
    include_freshness: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "context_packet",
        build_context_packet(
            config,
            ContextPacketRequest(
                repo_id=repo_id,
                include_providers=include_providers,
                include_drift=include_drift,
                include_freshness=include_freshness,
            ),
        ),
    )


def runtime_install_payload(
    config: McpRuntimeConfig,
    *,
    dry_run: bool = False,
    include_benchmarks: bool = False,
    install_provider_deps: bool = True,
    no_cache: bool = False,
) -> dict[str, Any]:
    full = run_runtime_install(
        config,
        RuntimeInstallRequest(
            dry_run=dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
            no_cache=no_cache,
        ),
    )
    report_path = write_tool_report(
        config.coordination_root,
        "runtime_install",
        full,
        label="dry-run" if dry_run else "install",
    )
    return _tool_payload(
        "runtime_install",
        compact_runtime_install_payload(full, report_path.as_posix()),
    )


def compact_runtime_install_payload(full: dict[str, Any], report_path: str) -> dict[str, Any]:
    """Keep the install outcome inline; the rebind runs (compose renders, docker
    argv, container transcripts — historically >50k chars) live in the report."""
    compact = {key: value for key, value in full.items() if key != "providerWatcherRebind"}
    messages = full.get("messages")
    if isinstance(messages, list) and len(messages) > 5:
        compact["messages"] = [*messages[:5], f"... {len(messages) - 5} more in the report"]
    rebind = full.get("providerWatcherRebind")
    if isinstance(rebind, dict):
        compact["providerWatcherRebind"] = {
            "attempted": rebind.get("attempted"),
            "ok": rebind.get("ok"),
            "phases": [
                {
                    "phase": run.get("phase"),
                    "action": run.get("action"),
                    "ok": run.get("ok"),
                }
                for run in rebind.get("runs", [])
                if isinstance(run, dict)
            ],
        }
    compact["reportPath"] = report_path
    return compact


def resolve_context_payload(
    config: McpRuntimeConfig,
    task: TaskRef,
    *,
    worktree_name: str | None = None,
    topology: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "resolve_context",
        resolve_context_tool(config, task, worktree_name=worktree_name, topology=topology),
    )


def skills_install_payload(
    config: McpRuntimeConfig,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "skills_install",
        skills_install_tool(
            config,
            dry_run=dry_run,
            overwrite=overwrite,
            archive_existing=archive_existing,
        ),
    )
