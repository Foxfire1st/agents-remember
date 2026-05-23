"""Pure payload builders for Agents Remember MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.context_packet import ContextPacketRequest, build_context_packet
from agents_remember.controllers.runtime_install import (
    RuntimeInstallRequest,
    run_runtime_install,
)
from agents_remember.controllers.skill_tools import (
    benchmark_prepare_tool,
    benchmark_run_tool,
    cgc_query_tool,
    cgc_visualize_tool,
    direct_closeout_apply_tool,
    direct_closeout_preview_tool,
    drift_check_tool,
    grepai_search_tool,
    grepai_trace_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_carryover_apply_tool,
    memory_carryover_plan_tool,
    memory_init_tool,
    provider_status_tool,
    provider_watchers_tool,
    resolve_context_tool,
    route_index_refresh_tool,
    skills_install_tool,
    worktree_attach_tool,
    worktree_cleanup_tool,
    worktree_closeout_apply_tool,
    worktree_closeout_preview_tool,
    worktree_integrate_tool,
    worktree_start_tool,
    worktree_status_tool,
)

from . import SERVER_NAME, SERVER_VERSION
from .config import McpRuntimeConfig

TRANSPORT = "stdio"
PUBLIC_TOOLS = (
    "ping",
    "server_info",
    "context_packet",
    "runtime_install",
    "resolve_context",
    "drift_check",
    "route_index_refresh",
    "memory_init",
    "skills_install",
    "provider_status",
    "grepai_search",
    "grepai_trace",
    "cgc_query",
    "provider_watchers",
    "cgc_visualize",
    "worktree_start",
    "worktree_attach",
    "worktree_status",
    "worktree_closeout_preview",
    "worktree_closeout_apply",
    "direct_closeout_preview",
    "direct_closeout_apply",
    "worktree_integrate",
    "worktree_cleanup",
    "memory_baseline_status",
    "memory_baseline_adopt",
    "memory_carryover_plan",
    "memory_carryover_apply",
    "benchmark_prepare",
    "benchmark_run",
)
RESERVED_TOOLS: tuple[str, ...] = ()


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


def resolve_context_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
    worktree_name: str | None = None,
    topology: str | None = None,
) -> dict[str, Any]:
    return resolve_context_tool(
        config,
        repo_id=repo_id,
        task_name=task_name,
        contract_path=contract_path,
        worktree_name=worktree_name,
        topology=topology,
    )


def drift_check_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    detail_limit: int = 50,
) -> dict[str, Any]:
    return drift_check_tool(config, repo_id=repo_id, detail_limit=detail_limit)


def route_index_refresh_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    return route_index_refresh_tool(config, repo_id=repo_id, dry_run=dry_run)


def memory_init_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    dry_run: bool = True,
    initialize_git: bool = True,
) -> dict[str, Any]:
    return memory_init_tool(
        config,
        repo_id=repo_id,
        dry_run=dry_run,
        initialize_git=initialize_git,
    )


def skills_install_payload(
    install_root: str,
    *,
    layout: str = "tree",
    dry_run: bool = True,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    return skills_install_tool(
        install_root=install_root,
        layout=layout,
        dry_run=dry_run,
        overwrite=overwrite,
        archive_existing=archive_existing,
    )


def provider_status_payload(config: McpRuntimeConfig, *, detail_limit: int = 20) -> dict[str, Any]:
    return provider_status_tool(config, detail_limit=detail_limit)


def provider_watchers_payload(
    config: McpRuntimeConfig,
    *,
    action: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    return provider_watchers_tool(config, action=action, dry_run=dry_run)


def grepai_search_payload(
    config: McpRuntimeConfig,
    query: str,
    *,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return grepai_search_tool(config, query=query, dry_run=dry_run, timeout=timeout)


def grepai_trace_payload(
    config: McpRuntimeConfig,
    query: str,
    *,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return grepai_trace_tool(config, query=query, dry_run=dry_run, timeout=timeout)


def cgc_query_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    query_type: str,
    *,
    arguments: list[str] | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return cgc_query_tool(
        config,
        repo_id=repo_id,
        query_type=query_type,
        arguments=arguments,
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_visualize_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    port: int = 8000,
    context: str | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return cgc_visualize_tool(
        config,
        repo_id=repo_id,
        port=port,
        context=context,
        dry_run=dry_run,
        timeout=timeout,
    )


def worktree_start_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    task_name: str,
    worktree_name: str,
    *,
    workflow_kind: str = "light-task",
    source_branch: str | None = None,
    work_branch: str | None = None,
    memory_mode: str | None = None,
    memory_choice: str | None = None,
    skip_provider_setup: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    return worktree_start_tool(
        config,
        repo_id=repo_id,
        task_name=task_name,
        worktree_name=worktree_name,
        workflow_kind=workflow_kind,
        source_branch=source_branch,
        work_branch=work_branch,
        memory_mode=memory_mode,
        memory_choice=memory_choice,
        skip_provider_setup=skip_provider_setup,
        dry_run=dry_run,
    )


def worktree_attach_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    return worktree_attach_tool(
        config,
        repo_id=repo_id,
        task_name=task_name,
        contract_path=contract_path,
    )


def worktree_status_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    return worktree_status_tool(
        config,
        repo_id=repo_id,
        task_name=task_name,
        contract_path=contract_path,
    )


def worktree_closeout_preview_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    code_commit_message: str,
    *,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
) -> dict[str, Any]:
    return worktree_closeout_preview_tool(
        config,
        contract_path=contract_path,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
    )


def worktree_closeout_apply_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    intent_note: str,
    code_commit_message: str,
    *,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    return worktree_closeout_apply_tool(
        config,
        contract_path=contract_path,
        intent_note=intent_note,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
    )


def direct_closeout_preview_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    task_name: str,
    code_commit_message: str,
    *,
    source_branch: str | None = None,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
) -> dict[str, Any]:
    return direct_closeout_preview_tool(
        config,
        repo_id=repo_id,
        task_name=task_name,
        source_branch=source_branch,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
    )


def direct_closeout_apply_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    task_name: str,
    intent_note: str,
    code_commit_message: str,
    *,
    source_branch: str | None = None,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    return direct_closeout_apply_tool(
        config,
        repo_id=repo_id,
        task_name=task_name,
        source_branch=source_branch,
        intent_note=intent_note,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
    )


def worktree_integrate_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    strategy: str = "ff-only",
    ledger_commit_message: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    return worktree_integrate_tool(
        config,
        contract_path=contract_path,
        strategy=strategy,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
    )


def worktree_cleanup_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    return worktree_cleanup_tool(config, contract_path=contract_path, dry_run=dry_run)


def memory_baseline_status_payload(config: McpRuntimeConfig, repo_id: str) -> dict[str, Any]:
    return memory_baseline_status_tool(config, repo_id=repo_id)


def memory_baseline_adopt_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    accept_drift: bool = False,
    source_branch: str | None = None,
    work_branch: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    return memory_baseline_adopt_tool(
        config,
        repo_id=repo_id,
        accept_drift=accept_drift,
        source_branch=source_branch,
        work_branch=work_branch,
        dry_run=dry_run,
    )


def memory_carryover_plan_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    source_memory: str,
    official_code_ref: str,
    source_code_ref: str,
    old_base: str,
    *,
    replace_existing: bool = False,
) -> dict[str, Any]:
    return memory_carryover_plan_tool(
        config,
        repo_id=repo_id,
        source_memory=source_memory,
        official_code_ref=official_code_ref,
        source_code_ref=source_code_ref,
        old_base=old_base,
        replace_existing=replace_existing,
    )


def memory_carryover_apply_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    source_memory: str,
    official_code_ref: str,
    source_code_ref: str,
    old_base: str,
    intent_note: str,
    *,
    replace_existing: bool = False,
    include_review_required: list[str] | None = None,
    memory_commit_message: str = "Carry over landed branch memory",
    ledger_commit_message: str = "Record branch memory carryover",
) -> dict[str, Any]:
    return memory_carryover_apply_tool(
        config,
        repo_id=repo_id,
        source_memory=source_memory,
        official_code_ref=official_code_ref,
        source_code_ref=source_code_ref,
        old_base=old_base,
        intent_note=intent_note,
        replace_existing=replace_existing,
        include_review_required=include_review_required,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
    )


def benchmark_prepare_payload(
    config: McpRuntimeConfig,
    *,
    target: str = "all",
    case_id: str | None = None,
    benchmarks_root: str | None = None,
    dry_run: bool = True,
    force_clone: bool = False,
    skill_exposure_mode: str = "copy",
    provider_timeout: int = 1800,
) -> dict[str, Any]:
    return benchmark_prepare_tool(
        config,
        target=target,
        case_id=case_id,
        benchmarks_root=benchmarks_root,
        dry_run=dry_run,
        force_clone=force_clone,
        skill_exposure_mode=skill_exposure_mode,
        provider_timeout=provider_timeout,
    )


def benchmark_run_payload(
    config: McpRuntimeConfig,
    *,
    target: str = "all",
    case_id: str | None = None,
    benchmarks_root: str | None = None,
    prompt: str | None = None,
    variant: str | None = None,
    repetitions: int | None = None,
    codex_bin: str = "codex",
    jobs: int | None = None,
    dry_run: bool = True,
    skip_prepare: bool = False,
    force_clone: bool = False,
    skill_exposure_mode: str = "copy",
    provider_timeout: int = 1800,
) -> dict[str, Any]:
    return benchmark_run_tool(
        config,
        target=target,
        case_id=case_id,
        benchmarks_root=benchmarks_root,
        prompt=prompt,
        variant=variant,
        repetitions=repetitions,
        codex_bin=codex_bin,
        jobs=jobs,
        dry_run=dry_run,
        skip_prepare=skip_prepare,
        force_clone=force_clone,
        skill_exposure_mode=skill_exposure_mode,
        provider_timeout=provider_timeout,
    )
