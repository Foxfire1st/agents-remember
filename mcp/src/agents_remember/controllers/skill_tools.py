"""Controllers for skill-facing MCP parity tools."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.install.runtime import source_root_from_package
from agents_remember.install.skills import install_skills
from agents_remember.kernel.coordination_context_resolver import (
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope, path_is_relative_to
from agents_remember.memory import baseline, carryover
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.providers import lifecycle_service
from agents_remember.providers.settings import write_lifecycle_settings
from agents_remember.providers.status import provider_status_packet
from agents_remember.worktrees import git_worktree_manager


def resolve_context_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
    worktree_name: str | None = None,
    topology: str | None = None,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        requested_topology=topology,
        coordination_root=config.coordination_root,
        code_repository_root=repo.path,
        task_name=task_name,
        worktree_name=worktree_name,
        contract_path=_coord_path(config, contract_path, "contract_path")
        if contract_path
        else repo.contract_path,
    )
    return {
        "ok": True,
        "operation": "resolve_context",
        "repoId": repo.repo_id,
        "context": context_to_dict(context),
    }


def drift_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    detail_limit: int = 50,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        coordination_root=config.coordination_root,
        code_repository_root=repo.path,
        onboarding_root=(repo.memory_root / "onboarding") if repo.memory_root else None,
        contract_path=repo.contract_path,
    )
    packet = run_drift_summary(
        code_repository_root=repo.path,
        context=context,
        detail_limit=detail_limit,
    )
    return {"ok": packet.get("status") == "checked", "operation": "drift_check", **packet}


def memory_quality_check_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    checks: list[str] | None = None,
    detail_limit: int = 50,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    onboarding_root = repo.memory_root / "onboarding"
    context = resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        coordination_root=config.coordination_root,
        code_repository_root=repo.path,
        onboarding_root=onboarding_root,
        contract_path=repo.contract_path,
    )
    payload = run_memory_quality_check(
        onboarding_root,
        checks=checks,
        drift_context=DriftCheckContext(
            code_repository_root=repo.path,
            context=context,
            detail_limit=detail_limit,
        ),
    )
    return {
        "operation": "memory_quality_check",
        "repoId": repo.repo_id,
        "onboardingRoot": onboarding_root.as_posix(),
        **payload,
    }


def route_index_refresh_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    result = build_route_indexes(
        code_root=repo.path,
        onboarding_root=repo.memory_root / "onboarding",
        repository=repo.repo_id,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "operation": "route_index_refresh",
        "repoId": repo.repo_id,
        "dryRun": dry_run,
        **result.to_dict(),
    }


def memory_init_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    dry_run: bool = True,
    initialize_git: bool = True,
) -> dict[str, Any]:
    return initialize_memory(
        config,
        repo_id=repo_id,
        dry_run=dry_run,
        initialize_git=initialize_git,
    )


def skills_install_tool(
    config: McpRuntimeConfig,
    *,
    layout: str = "tree",
    dry_run: bool = True,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    if config.harness_skill_root is None:
        raise ValueError(
            "MCP settings must live under <registration-root>/mcp/ or define "
            "harnessSkillRoot before skills_install can run"
        )
    return install_skills(
        install_root=config.harness_skill_root,
        layout=layout,
        dry_run=dry_run,
        overwrite=overwrite,
        archive_existing=archive_existing,
    )


def provider_status_tool(
    config: McpRuntimeConfig,
    *,
    detail_limit: int = 20,
) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": "provider_status",
        "providers": provider_status_packet(
            config, include_providers=True, detail_limit=detail_limit
        ),
    }


def provider_watchers_tool(
    config: McpRuntimeConfig,
    *,
    action: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    if action not in {"status", "start", "stop", "restart", "refresh", "shutdown-all"}:
        raise ValueError("action must be status, start, stop, restart, refresh, or shutdown-all")
    if action == "restart":
        return {
            "ok": True,
            "operation": "provider_watchers",
            "action": "restart",
            "steps": [
                _provider_watchers_once(config, "stop", dry_run=dry_run),
                _provider_watchers_once(config, "start", dry_run=dry_run),
            ],
        }
    if action == "refresh":
        return _provider_refresh(config, dry_run=dry_run)
    return _provider_watchers_once(config, action, dry_run=dry_run)


def grepai_search_tool(
    config: McpRuntimeConfig,
    *,
    query: str,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _provider_lifecycle_result(
        config,
        operation="grepai_search",
        dry_run=dry_run,
        timeout=timeout,
        run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
            service_config,
            action="run",
            native_args=["search", query],
        ),
    )


def grepai_trace_tool(
    config: McpRuntimeConfig,
    *,
    query: str,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _provider_lifecycle_result(
        config,
        operation="grepai_trace",
        dry_run=dry_run,
        timeout=timeout,
        run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
            service_config,
            action="run",
            native_args=["trace", query],
        ),
    )


def cgc_symbol_search_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    name: str,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_symbol_search",
        repo_id=repo_id,
        native_args=["find", "name", _required_text(name, "name")],
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_callers_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    file: str | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    native_args = ["analyze", "callers", _required_text(function, "function")]
    if file:
        native_args.extend(["--file", _required_text(file, "file")])
    return _cgc_run_tool(
        config,
        operation="cgc_callers",
        repo_id=repo_id,
        native_args=native_args,
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_callees_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_callees",
        repo_id=repo_id,
        native_args=["analyze", "calls", _required_text(function, "function")],
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_dependencies_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    module: str,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _cgc_run_tool(
        config,
        operation="cgc_dependencies",
        repo_id=repo_id,
        native_args=["analyze", "dependencies", _required_text(module, "module")],
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_complexity_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    function: str | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    native_args = ["analyze", "complexity"]
    if function:
        native_args.append(_required_text(function, "function"))
    return _cgc_run_tool(
        config,
        operation="cgc_complexity",
        repo_id=repo_id,
        native_args=native_args,
        dry_run=dry_run,
        timeout=timeout,
    )


def cgc_visualize_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    port: int = 8000,
    context: str | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    _repo(config, repo_id)
    return _provider_lifecycle_result(
        config,
        operation="cgc_visualize",
        dry_run=dry_run,
        timeout=timeout,
        run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
            service_config,
            action="visualize",
            repo_id=repo_id,
            port=port,
            context=context,
        ),
    )


def _cgc_run_tool(
    config: McpRuntimeConfig,
    *,
    operation: str,
    repo_id: str,
    native_args: list[str],
    dry_run: bool,
    timeout: int | None,
) -> dict[str, Any]:
    _repo(config, repo_id)
    return _provider_lifecycle_result(
        config,
        operation=operation,
        dry_run=dry_run,
        timeout=timeout,
        run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
            service_config,
            action="run",
            repo_id=repo_id,
            native_args=native_args,
        ),
    )


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def worktree_start_tool(
    config: McpRuntimeConfig,
    *,
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
    repo = _repo(config, repo_id)
    settings_path = None if skip_provider_setup else write_lifecycle_settings(config)
    provider_setup_config = (
        None
        if settings_path is None
        else git_worktree_manager.WorktreeProviderSetupConfig(
            coordination_root=config.coordination_root,
            settings_path=settings_path,
            seed_source_coordination_root=config.coordination_root,
        )
    )
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        worktree_name=worktree_name,
        workflow_kind=workflow_kind,
        source_branch=source_branch,
        work_branch=work_branch,
        memory_mode=memory_mode,
        memory_choice=memory_choice,
        custom_instruction=None,
        skip_provider_setup=skip_provider_setup,
        provider_setup_config=provider_setup_config,
        provider_timeout=config.timeout_caps.get("providerSeconds", 120),
        dry_run=dry_run,
    )
    try:
        return _worktree_result("worktree_start", git_worktree_manager.start_result(args))
    finally:
        if settings_path is not None:
            settings_path.unlink(missing_ok=True)


def worktree_attach_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        contract_path=_coord_path(config, contract_path, "contract_path")
        if contract_path
        else None,
    )
    return _worktree_result("worktree_attach", git_worktree_manager.attach_result(args))


def worktree_status_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        contract_path=_coord_path(config, contract_path, "contract_path")
        if contract_path
        else None,
    )
    return _worktree_result("worktree_status", git_worktree_manager.status_result(args))


def worktree_closeout_preview_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    code_commit_message: str,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
) -> dict[str, Any]:
    return _worktree_closeout(
        config,
        operation="worktree_closeout_preview",
        contract_path=contract_path,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        dry_run=True,
        intent_note="",
    )


def worktree_closeout_apply_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    intent_note: str,
    code_commit_message: str,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    return _worktree_closeout(
        config,
        operation="worktree_closeout_apply",
        contract_path=contract_path,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
        intent_note=intent_note,
    )


def direct_closeout_preview_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str,
    code_commit_message: str,
    source_branch: str | None = None,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
) -> dict[str, Any]:
    return _direct_closeout(
        config,
        operation="direct_closeout_preview",
        repo_id=repo_id,
        task_name=task_name,
        source_branch=source_branch,
        intent_note="",
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        dry_run=True,
    )


def direct_closeout_apply_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str,
    intent_note: str,
    code_commit_message: str,
    source_branch: str | None = None,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    return _direct_closeout(
        config,
        operation="direct_closeout_apply",
        repo_id=repo_id,
        task_name=task_name,
        source_branch=source_branch,
        intent_note=intent_note,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
    )


def worktree_integrate_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    strategy: str = "ff-only",
    ledger_commit_message: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    args = argparse.Namespace(
        contract_path=_coord_path(config, contract_path, "contract_path"),
        strategy=strategy,
        approved=not dry_run,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
    )
    return _worktree_result("worktree_integrate", git_worktree_manager.integrate_result(args))


def worktree_cleanup_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    args = argparse.Namespace(
        contract_path=_coord_path(config, contract_path, "contract_path"),
        approved=not dry_run,
        dry_run=dry_run,
    )
    return _worktree_result("worktree_cleanup", git_worktree_manager.cleanup_result(args))


def memory_baseline_status_tool(config: McpRuntimeConfig, *, repo_id: str) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    payload = baseline.baseline_status(_baseline_request(config, repo))
    return {"ok": True, "operation": "memory_baseline_status", **payload}


def memory_baseline_adopt_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    accept_drift: bool = False,
    source_branch: str | None = None,
    work_branch: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    returncode, payload = baseline.baseline_adopt(
        _baseline_request(config, repo),
        accept_drift=accept_drift,
        source_branch=source_branch,
        work_branch=work_branch,
        dry_run=dry_run,
    )
    return {"ok": returncode == 0, "operation": "memory_baseline_adopt", **payload}


def memory_carryover_plan_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    source_memory: str,
    official_code_ref: str,
    source_code_ref: str,
    old_base: str,
    replace_existing: bool = False,
) -> dict[str, Any]:
    request = _carryover_request(
        config,
        repo_id=repo_id,
        source_memory=source_memory,
        official_code_ref=official_code_ref,
        source_code_ref=source_code_ref,
        old_base=old_base,
        replace_existing=replace_existing,
    )
    payload = carryover.build_plan_for_request(request)
    return {"ok": True, "operation": "memory_carryover_plan", **payload}


def memory_carryover_apply_tool(
    config: McpRuntimeConfig,
    *,
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
    payload = carryover.apply_carryover_for_request(
        _carryover_request(
            config,
            repo_id=repo_id,
            source_memory=source_memory,
            official_code_ref=official_code_ref,
            source_code_ref=source_code_ref,
            old_base=old_base,
            replace_existing=replace_existing,
        ),
        intent_note=intent_note,
        include_review_required=include_review_required,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
    )
    return {"ok": True, "operation": "memory_carryover_apply", **payload}


def codex_benchmark_prepare_tool(
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
    return benchmark_runner.prepare_benchmarks(
        benchmark_runner.BenchmarkPrepareRequest(
            benchmarks_root=_benchmark_root(config, benchmarks_root),
            target=target,
            case_id=case_id,
            dry_run=dry_run,
            skill_exposure_mode=skill_exposure_mode,
            force_clone=force_clone,
            provider_timeout=provider_timeout,
        )
    )


def codex_benchmark_run_tool(
    config: McpRuntimeConfig,
    *,
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
    codex_sandbox: str = benchmark_runner.CODEX_BENCHMARK_SANDBOX,
) -> dict[str, Any]:
    try:
        codex_executable = benchmark_runner.resolve_codex_executable()
    except benchmark_runner.CodexExecutableNotFound as error:
        return {
            "ok": False,
            "operation": "codex_benchmark_run",
            "error": str(error),
            "codexExecutionPolicy": benchmark_runner.codex_execution_policy(
                codex_sandbox=codex_sandbox,
            ),
            "executable": benchmark_runner.CODEX_EXECUTABLE_NAME,
            "resolution": benchmark_runner.CODEX_EXECUTABLE_RESOLUTION,
            "recoveryAction": (
                "Install the Codex CLI or ensure `codex` is on the MCP server process PATH."
            ),
        }

    result = benchmark_runner.run_codex_benchmark(
        benchmark_runner.BenchmarkRunRequest(
            benchmarks_root=_benchmark_root(config, benchmarks_root),
            target=target,
            case_id=case_id,
            prompt=prompt,
            variant=variant,
            repetitions=repetitions,
            jobs=jobs,
            dry_run=dry_run,
            skip_prepare=skip_prepare,
            skill_exposure_mode=skill_exposure_mode,
            force_clone=force_clone,
            provider_timeout=provider_timeout,
            codex_sandbox=codex_sandbox,
        )
    )
    result["codexExecutable"] = codex_executable
    result["codexResolution"] = "PATH"
    return result


def _repo(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    try:
        return config.repositories[repo_id]
    except KeyError as error:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ValueError(
            f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}"
        ) from error


def _coord_path(config: McpRuntimeConfig, value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = config.coordination_root / path
    path = path.resolve()
    if not path_is_relative_to(path, config.coordination_root):
        raise ValueError(f"{label} must stay inside coordination_root")
    return path


def _worktree_namespace(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    **kwargs: Any,
) -> argparse.Namespace:
    values: dict[str, Any] = {
        "code_repository_name": repo.repo_id,
        "workspace_root": config.workspace_root,
        "coordination_root": config.coordination_root,
        "code_repository_root": repo.path,
        "topology": None,
        "contract_path": None,
        "task_name": None,
    }
    values.update(kwargs)
    return argparse.Namespace(**values)


def _worktree_result(
    operation: str, result: git_worktree_manager.WorktreeCommandResult
) -> dict[str, Any]:
    return {"ok": result.returncode == 0, "operation": operation, **result.payload}


def _baseline_request(config: McpRuntimeConfig, repo: RepositoryScope) -> baseline.BaselineRequest:
    return baseline.BaselineRequest(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        code_repository_root=repo.path,
        coordination_root=config.coordination_root,
        topology="external",
    )


def _provider_watchers_once(
    config: McpRuntimeConfig, action: str, *, dry_run: bool
) -> dict[str, Any]:
    return _provider_lifecycle_result(
        config,
        operation="provider_watchers",
        dry_run=dry_run,
        timeout=None,
        run=lambda service_config: lifecycle_service.run_watchers_lifecycle(
            service_config,
            action=action,
        ),
    )


def _provider_refresh(config: McpRuntimeConfig, *, dry_run: bool) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if "grepai-memory" in config.providers:
        steps.append(
            _provider_lifecycle_result(
                config,
                operation="provider_watchers",
                dry_run=dry_run,
                timeout=None,
                run=lambda service_config: lifecycle_service.run_grepai_lifecycle(
                    service_config,
                    action="refresh",
                ),
            )
        )
    if "codegraphcontext-code" in config.providers:
        steps.append(
            _provider_lifecycle_result(
                config,
                operation="provider_watchers",
                dry_run=dry_run,
                timeout=None,
                run=lambda service_config: lifecycle_service.run_cgc_lifecycle(
                    service_config,
                    action="refresh-all",
                ),
            )
        )
    return {
        "ok": all(step.get("ok") for step in steps),
        "operation": "provider_watchers",
        "action": "refresh",
        "steps": steps,
    }


def _provider_lifecycle_result(
    config: McpRuntimeConfig,
    *,
    operation: str,
    dry_run: bool = True,
    timeout: int | None = None,
    run: Any,
) -> dict[str, Any]:
    settings_path = write_lifecycle_settings(config)
    try:
        service_config = lifecycle_service.ProviderLifecycleServiceConfig(
            coordination_root=config.coordination_root,
            settings_path=settings_path,
            dry_run=dry_run,
            timeout=timeout or config.timeout_caps.get("providerSeconds", 120),
        )
        data = run(service_config)
        return {"operation": operation, "ok": bool(data.get("ok")), **data}
    finally:
        settings_path.unlink(missing_ok=True)


def _worktree_closeout(
    config: McpRuntimeConfig,
    *,
    operation: str,
    contract_path: str,
    code_commit_message: str,
    memory_commit_message: str,
    ledger_commit_message: str,
    dry_run: bool,
    intent_note: str,
) -> dict[str, Any]:
    args = argparse.Namespace(
        contract_path=_coord_path(config, contract_path, "contract_path"),
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        approval_note=intent_note,
        approved=not dry_run,
        dry_run=dry_run,
    )
    return _worktree_result(operation, git_worktree_manager.closeout_result(args))


def _direct_closeout(
    config: McpRuntimeConfig,
    *,
    operation: str,
    repo_id: str,
    task_name: str,
    source_branch: str | None,
    intent_note: str,
    code_commit_message: str,
    memory_commit_message: str,
    ledger_commit_message: str,
    dry_run: bool,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        source_branch=source_branch,
        code_commit_message=code_commit_message,
        memory_commit_message=memory_commit_message,
        ledger_commit_message=ledger_commit_message,
        approval_note=intent_note,
        approved=not dry_run,
        dry_run=dry_run,
    )
    return _worktree_result(operation, git_worktree_manager.direct_closeout_result(args))


def _carryover_request(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    source_memory: str,
    official_code_ref: str,
    source_code_ref: str,
    old_base: str,
    replace_existing: bool,
) -> list[str]:
    repo = _repo(config, repo_id)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    source_memory_path = _coord_path(config, source_memory, "source_memory")
    return carryover.CarryoverRequest(
        code_repository_root=repo.path,
        official_code_ref=official_code_ref,
        source_code_ref=source_code_ref,
        old_base=old_base,
        official_memory=repo.memory_root,
        source_memory=source_memory_path,
        code_repository_name=repo.repo_id,
        replace_existing=replace_existing,
    )


def _benchmark_root(config: McpRuntimeConfig, value: str | None) -> Path:
    if value:
        return _coord_path(config, value, "benchmarks_root")
    coordinator_benchmarks = config.coordination_root / "benchmarks"
    if (coordinator_benchmarks / "cases").is_dir():
        return coordinator_benchmarks
    return source_root_from_package() / "benchmarks"
