"""Stdio MCP server wiring for Agents Remember."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import ConfigError, McpRuntimeConfig, load_config
from .tools import (
    cgc_callees_payload,
    cgc_callers_payload,
    cgc_complexity_payload,
    cgc_dependencies_payload,
    cgc_symbol_search_payload,
    cgc_visualize_payload,
    codex_benchmark_prepare_payload,
    codex_benchmark_run_payload,
    context_packet_payload,
    direct_closeout_apply_payload,
    direct_closeout_preview_payload,
    drift_check_payload,
    grepai_search_payload,
    grepai_trace_payload,
    memory_baseline_adopt_payload,
    memory_baseline_status_payload,
    memory_carryover_apply_payload,
    memory_carryover_plan_payload,
    memory_init_payload,
    memory_quality_check_payload,
    ping_payload,
    provider_diagnostics_payload,
    provider_status_payload,
    provider_watchers_payload,
    resolve_context_payload,
    route_index_refresh_payload,
    runtime_install_payload,
    server_info_payload,
    skills_install_payload,
    worktree_attach_payload,
    worktree_cleanup_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
    worktree_start_payload,
    worktree_status_payload,
)


def create_server(config: McpRuntimeConfig) -> Any:
    server = FastMCP("Agents Remember")

    @server.tool()
    def ping() -> dict[str, Any]:
        return ping_payload()

    @server.tool()
    def server_info() -> dict[str, Any]:
        return server_info_payload(config)

    @server.tool()
    def context_packet(
        repo_id: str,
        include_providers: bool = True,
        include_drift: bool = False,
    ) -> dict[str, Any]:
        return context_packet_payload(
            config,
            repo_id,
            include_providers=include_providers,
            include_drift=include_drift,
        )

    @server.tool()
    def runtime_install(
        dry_run: bool = True,
        include_benchmarks: bool = False,
        install_provider_deps: bool = True,
    ) -> dict[str, Any]:
        return runtime_install_payload(
            config,
            dry_run=dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
        )

    @server.tool()
    def resolve_context(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        worktree_name: str | None = None,
        topology: str | None = None,
    ) -> dict[str, Any]:
        return resolve_context_payload(
            config,
            repo_id,
            task_name=task_name,
            contract_path=contract_path,
            worktree_name=worktree_name,
            topology=topology,
        )

    @server.tool()
    def drift_check(repo_id: str, detail_limit: int = 50) -> dict[str, Any]:
        return drift_check_payload(config, repo_id, detail_limit=detail_limit)

    @server.tool()
    def memory_quality_check(
        repo_id: str,
        checks: list[str] | None = None,
        detail_limit: int = 50,
    ) -> dict[str, Any]:
        return memory_quality_check_payload(
            config,
            repo_id,
            checks=checks,
            detail_limit=detail_limit,
        )

    @server.tool()
    def route_index_refresh(repo_id: str, dry_run: bool = True) -> dict[str, Any]:
        return route_index_refresh_payload(config, repo_id, dry_run=dry_run)

    @server.tool()
    def memory_init(
        repo_id: str,
        dry_run: bool = True,
        initialize_git: bool = True,
    ) -> dict[str, Any]:
        return memory_init_payload(
            config,
            repo_id,
            dry_run=dry_run,
            initialize_git=initialize_git,
        )

    @server.tool()
    def skills_install(
        layout: str = "tree",
        dry_run: bool = True,
        overwrite: bool = False,
        archive_existing: bool = False,
    ) -> dict[str, Any]:
        return skills_install_payload(
            config,
            layout=layout,
            dry_run=dry_run,
            overwrite=overwrite,
            archive_existing=archive_existing,
        )

    @server.tool()
    def provider_status(detail_limit: int = 20) -> dict[str, Any]:
        return provider_status_payload(config, detail_limit=detail_limit)

    @server.tool()
    def provider_diagnostics(detail_limit: int = 20) -> dict[str, Any]:
        return provider_diagnostics_payload(config, detail_limit=detail_limit)

    @server.tool()
    def provider_watchers(action: str, dry_run: bool = True) -> dict[str, Any]:
        return provider_watchers_payload(config, action=action, dry_run=dry_run)

    @server.tool()
    def grepai_search(
        query: str,
        repo_ids: list[str] | None = None,
        all_repos: bool = True,
        limit: int = 10,
        output_format: str = "json",
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return grepai_search_payload(
            config,
            query,
            repo_ids=repo_ids,
            all_repos=all_repos,
            limit=limit,
            output_format=output_format,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def grepai_trace(
        trace_action: str,
        symbol: str,
        repo_ids: list[str] | None = None,
        all_repos: bool = True,
        depth: int | None = None,
        output_format: str = "json",
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return grepai_trace_payload(
            config,
            trace_action,
            symbol,
            repo_ids=repo_ids,
            all_repos=all_repos,
            depth=depth,
            output_format=output_format,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def cgc_symbol_search(
        repo_id: str,
        name: str,
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return cgc_symbol_search_payload(
            config,
            repo_id,
            name,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def cgc_callers(
        repo_id: str,
        function: str,
        file: str | None = None,
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return cgc_callers_payload(
            config,
            repo_id,
            function,
            file=file,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def cgc_callees(
        repo_id: str,
        function: str,
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return cgc_callees_payload(
            config,
            repo_id,
            function,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def cgc_dependencies(
        repo_id: str,
        module: str,
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return cgc_dependencies_payload(
            config,
            repo_id,
            module,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def cgc_complexity(
        repo_id: str,
        function: str | None = None,
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return cgc_complexity_payload(
            config,
            repo_id,
            function=function,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def cgc_visualize(
        repo_id: str,
        port: int = 8000,
        context: str | None = None,
        dry_run: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        return cgc_visualize_payload(
            config,
            repo_id,
            port=port,
            context=context,
            dry_run=dry_run,
            timeout=timeout,
        )

    @server.tool()
    def worktree_start(
        repo_id: str,
        task_name: str,
        worktree_name: str,
        workflow_kind: str = "light-task",
        source_branch: str | None = None,
        work_branch: str | None = None,
        memory_mode: str | None = None,
        memory_choice: str | None = None,
        skip_provider_setup: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        return worktree_start_payload(
            config,
            repo_id,
            task_name,
            worktree_name,
            workflow_kind=workflow_kind,
            source_branch=source_branch,
            work_branch=work_branch,
            memory_mode=memory_mode,
            memory_choice=memory_choice,
            skip_provider_setup=skip_provider_setup,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_attach(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
    ) -> dict[str, Any]:
        return worktree_attach_payload(
            config,
            repo_id,
            task_name=task_name,
            contract_path=contract_path,
        )

    @server.tool()
    def worktree_status(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
    ) -> dict[str, Any]:
        return worktree_status_payload(
            config,
            repo_id,
            task_name=task_name,
            contract_path=contract_path,
        )

    @server.tool()
    def worktree_closeout_preview(
        contract_path: str,
        code_commit_message: str,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
    ) -> dict[str, Any]:
        return worktree_closeout_preview_payload(
            config,
            contract_path,
            code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        )

    @server.tool()
    def worktree_closeout_apply(
        contract_path: str,
        intent_note: str,
        code_commit_message: str,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return worktree_closeout_apply_payload(
            config,
            contract_path,
            intent_note,
            code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def direct_closeout_preview(
        repo_id: str,
        task_name: str,
        code_commit_message: str,
        source_branch: str | None = None,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
    ) -> dict[str, Any]:
        return direct_closeout_preview_payload(
            config,
            repo_id,
            task_name,
            code_commit_message,
            source_branch=source_branch,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        )

    @server.tool()
    def direct_closeout_apply(
        repo_id: str,
        task_name: str,
        intent_note: str,
        code_commit_message: str,
        source_branch: str | None = None,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return direct_closeout_apply_payload(
            config,
            repo_id,
            task_name,
            intent_note,
            code_commit_message,
            source_branch=source_branch,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_integrate(
        contract_path: str,
        strategy: str = "ff-only",
        ledger_commit_message: str = "",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        return worktree_integrate_payload(
            config,
            contract_path,
            strategy=strategy,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_cleanup(contract_path: str, dry_run: bool = True) -> dict[str, Any]:
        return worktree_cleanup_payload(config, contract_path, dry_run=dry_run)

    @server.tool()
    def memory_baseline_status(repo_id: str) -> dict[str, Any]:
        return memory_baseline_status_payload(config, repo_id)

    @server.tool()
    def memory_baseline_adopt(
        repo_id: str,
        accept_drift: bool = False,
        source_branch: str | None = None,
        work_branch: str | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        return memory_baseline_adopt_payload(
            config,
            repo_id,
            accept_drift=accept_drift,
            source_branch=source_branch,
            work_branch=work_branch,
            dry_run=dry_run,
        )

    @server.tool()
    def memory_carryover_plan(
        repo_id: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        return memory_carryover_plan_payload(
            config,
            repo_id,
            source_memory,
            official_code_ref,
            source_code_ref,
            old_base,
            replace_existing=replace_existing,
        )

    @server.tool()
    def memory_carryover_apply(
        repo_id: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        intent_note: str,
        replace_existing: bool = False,
        include_review_required: list[str] | None = None,
        memory_commit_message: str = "Carry over landed branch memory",
        ledger_commit_message: str = "Record branch memory carryover",
    ) -> dict[str, Any]:
        return memory_carryover_apply_payload(
            config,
            repo_id,
            source_memory,
            official_code_ref,
            source_code_ref,
            old_base,
            intent_note,
            replace_existing=replace_existing,
            include_review_required=include_review_required,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        )

    @server.tool()
    def codex_benchmark_prepare(
        target: str = "all",
        case_id: str | None = None,
        benchmarks_root: str | None = None,
        dry_run: bool = True,
        force_clone: bool = False,
        skill_exposure_mode: str = "copy",
        provider_timeout: int = 1800,
    ) -> dict[str, Any]:
        return codex_benchmark_prepare_payload(
            config,
            target=target,
            case_id=case_id,
            benchmarks_root=benchmarks_root,
            dry_run=dry_run,
            force_clone=force_clone,
            skill_exposure_mode=skill_exposure_mode,
            provider_timeout=provider_timeout,
        )

    @server.tool()
    def codex_benchmark_run(
        target: str = "all",
        case_id: str | None = None,
        benchmarks_root: str | None = None,
        prompt: str | None = None,
        variant: str | None = None,
        repetitions: int | None = None,
        jobs: int | None = None,
        dry_run: bool = True,
        skip_prepare: bool = False,
        force_clone: bool = False,
        skill_exposure_mode: str = "copy",
        provider_timeout: int = 1800,
        codex_sandbox: str = "danger-full-access",
    ) -> dict[str, Any]:
        return codex_benchmark_run_payload(
            config,
            target=target,
            case_id=case_id,
            benchmarks_root=benchmarks_root,
            prompt=prompt,
            variant=variant,
            repetitions=repetitions,
            jobs=jobs,
            dry_run=dry_run,
            skip_prepare=skip_prepare,
            force_clone=force_clone,
            skill_exposure_mode=skill_exposure_mode,
            provider_timeout=provider_timeout,
            codex_sandbox=codex_sandbox,
        )

    return server


def run_server(config: McpRuntimeConfig) -> None:
    create_server(config).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agents Remember MCP server.")
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path to trusted MCP settings JSON.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))

    run_server(config)
    return 0
