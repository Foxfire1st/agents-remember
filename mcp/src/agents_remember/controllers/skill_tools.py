"""Controllers for skill-facing MCP parity tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.benchmarks import runner as benchmark_runner
from agents_remember.drift.summary import run_drift_summary
from agents_remember.install.runtime import source_root_from_package
from agents_remember.install.skills import install_skills
from agents_remember.kernel.coordination_context_resolver import (
    context_to_dict,
    resolve_coordination_context,
)
from agents_remember.kernel.memory_init import initialize_memory
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.mcp.command_capture import run_package_main
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope, path_is_relative_to
from agents_remember.memory import baseline, carryover
from agents_remember.providers import provider_lifecycle
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
        contract_path=_coord_path(config, contract_path, "contract_path") if contract_path else repo.contract_path,
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
    *,
    install_root: str,
    layout: str = "tree",
    dry_run: bool = True,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    return install_skills(
        install_root=Path(install_root).resolve(),
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
        "providers": provider_status_packet(config, include_providers=True, detail_limit=detail_limit),
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
    return _provider_main(
        config,
        operation="grepai_search",
        provider="grepai",
        action="run",
        dry_run=dry_run,
        timeout=timeout,
        extra=["--lifecycle-json", "--", "search", query],
    )


def grepai_trace_tool(
    config: McpRuntimeConfig,
    *,
    query: str,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    return _provider_main(
        config,
        operation="grepai_trace",
        provider="grepai",
        action="run",
        dry_run=dry_run,
        timeout=timeout,
        extra=["--lifecycle-json", "--", "trace", query],
    )


def cgc_query_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    query_type: str,
    arguments: list[str] | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
) -> dict[str, Any]:
    _repo(config, repo_id)
    native_args = [query_type, *(arguments or [])]
    return _provider_main(
        config,
        operation="cgc_query",
        provider="cgc",
        action="run",
        repo_id=repo_id,
        dry_run=dry_run,
        timeout=timeout,
        extra=["--lifecycle-json", "--", *native_args],
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
    extra = ["--port", str(port)]
    if context:
        extra.extend(["--context", context])
    return _provider_main(
        config,
        operation="cgc_visualize",
        provider="cgc",
        action="visualize",
        repo_id=repo_id,
        dry_run=dry_run,
        timeout=timeout,
        extra=extra,
    )


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
    argv = [
        "start",
        *_repo_common_argv(config, repo),
        "--task-name",
        task_name,
        "--worktree-name",
        worktree_name,
        "--workflow-kind",
        workflow_kind,
    ]
    _append_option(argv, "--source-branch", source_branch)
    _append_option(argv, "--work-branch", work_branch)
    _append_option(argv, "--memory-mode", memory_mode)
    _append_option(argv, "--memory-choice", memory_choice)
    settings_path = None
    if skip_provider_setup:
        argv.append("--skip-provider-setup")
    else:
        settings_path = write_lifecycle_settings(config)
        argv.extend(
            [
                "--provider-from-settings",
                settings_path.as_posix(),
                "--provider-timeout",
                str(config.timeout_caps.get("providerSeconds", 120)),
            ]
        )
    if dry_run:
        argv.append("--dry-run")
    try:
        return _worktree_main("worktree_start", argv)
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
    argv = ["attach", *_repo_common_argv(config, repo)]
    _append_option(argv, "--task-name", task_name)
    _append_option(argv, "--contract-path", _coord_path_text(config, contract_path, "contract_path"))
    return _worktree_main("worktree_attach", argv)


def worktree_status_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    argv = ["status", *_repo_common_argv(config, repo)]
    _append_option(argv, "--task-name", task_name)
    _append_option(argv, "--contract-path", _coord_path_text(config, contract_path, "contract_path"))
    return _worktree_main("worktree_status", argv)


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
    argv = [
        "integrate",
        "--contract-path",
        _coord_path(config, contract_path, "contract_path").as_posix(),
        "--strategy",
        strategy,
    ]
    if not dry_run:
        argv.append("--approved")
    _append_option(argv, "--ledger-commit-message", ledger_commit_message)
    if dry_run:
        argv.append("--dry-run")
    return _worktree_main("worktree_integrate", argv)


def worktree_cleanup_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    argv = ["cleanup", "--contract-path", _coord_path(config, contract_path, "contract_path").as_posix()]
    if not dry_run:
        argv.append("--approved")
    if dry_run:
        argv.append("--dry-run")
    return _worktree_main("worktree_cleanup", argv)


def memory_baseline_status_tool(config: McpRuntimeConfig, *, repo_id: str) -> dict[str, Any]:
    repo = _repo(config, repo_id)
    argv = ["status", *_baseline_common_argv(config, repo)]
    return run_package_main(operation="memory_baseline_status", main=baseline.main, argv=argv)


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
    argv = ["adopt", *_baseline_common_argv(config, repo)]
    if accept_drift:
        argv.append("--accept-drift")
    _append_option(argv, "--source-branch", source_branch)
    _append_option(argv, "--work-branch", work_branch)
    if dry_run:
        argv.append("--dry-run")
    return run_package_main(operation="memory_baseline_adopt", main=baseline.main, argv=argv)


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
    argv = _carryover_argv(
        config,
        repo_id=repo_id,
        source_memory=source_memory,
        official_code_ref=official_code_ref,
        source_code_ref=source_code_ref,
        old_base=old_base,
        replace_existing=replace_existing,
    )
    return run_package_main(operation="memory_carryover_plan", main=carryover.main, argv=["plan", *argv])


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
    argv = [
        "apply",
        *_carryover_argv(
            config,
            repo_id=repo_id,
            source_memory=source_memory,
            official_code_ref=official_code_ref,
            source_code_ref=source_code_ref,
            old_base=old_base,
            replace_existing=replace_existing,
        ),
        "--approved",
        "--approval-note",
        intent_note,
        "--memory-commit-message",
        memory_commit_message,
        "--ledger-commit-message",
        ledger_commit_message,
    ]
    for source_path in include_review_required or []:
        argv.extend(["--include-review-required", source_path])
    return run_package_main(operation="memory_carryover_apply", main=carryover.main, argv=argv)


def benchmark_prepare_tool(
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
    argv = [
        "--benchmarks-root",
        _benchmark_root(config, benchmarks_root).as_posix(),
        "prepare",
        target,
    ]
    _append_positional(argv, case_id)
    argv.extend(["--skill-exposure-mode", skill_exposure_mode, "--provider-timeout", str(provider_timeout)])
    if force_clone:
        argv.append("--force-clone")
    if dry_run:
        argv.append("--dry-run")
    return run_package_main(operation="benchmark_prepare", main=benchmark_runner.main, argv=argv)


def benchmark_run_tool(
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
    argv = ["--benchmarks-root", _benchmark_root(config, benchmarks_root).as_posix(), "run", target]
    _append_positional(argv, case_id)
    _append_option(argv, "--prompt", prompt)
    _append_option(argv, "--variant", variant)
    if repetitions is not None:
        argv.extend(["--repetitions", str(repetitions)])
    argv.extend(["--codex-bin", codex_bin, "--skill-exposure-mode", skill_exposure_mode, "--provider-timeout", str(provider_timeout)])
    if jobs is not None:
        argv.extend(["--jobs", str(jobs)])
    if dry_run:
        argv.append("--dry-run")
    if skip_prepare:
        argv.append("--skip-prepare")
    if force_clone:
        argv.append("--force-clone")
    return run_package_main(operation="benchmark_run", main=benchmark_runner.main, argv=argv)


def _repo(config: McpRuntimeConfig, repo_id: str) -> RepositoryScope:
    try:
        return config.repositories[repo_id]
    except KeyError as error:
        allowed = ", ".join(config.allowed_repo_ids) or "<none>"
        raise ValueError(f"repo_id {repo_id!r} is not allowed by MCP settings; allowed: {allowed}") from error


def _coord_path(config: McpRuntimeConfig, value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = config.coordination_root / path
    path = path.resolve()
    if not path_is_relative_to(path, config.coordination_root):
        raise ValueError(f"{label} must stay inside coordination_root")
    return path


def _coord_path_text(config: McpRuntimeConfig, value: str | None, label: str) -> str | None:
    if not value:
        return None
    return _coord_path(config, value, label).as_posix()


def _repo_common_argv(config: McpRuntimeConfig, repo: RepositoryScope) -> list[str]:
    return [
        "--code-repository-name",
        repo.repo_id,
        "--workspace-root",
        config.workspace_root.as_posix(),
        "--coordination-root",
        config.coordination_root.as_posix(),
        "--code-repository-root",
        repo.path.as_posix(),
    ]


def _baseline_common_argv(config: McpRuntimeConfig, repo: RepositoryScope) -> list[str]:
    return [
        "--code-repository-name",
        repo.repo_id,
        "--workspace-root",
        config.workspace_root.as_posix(),
        "--coordination-root",
        config.coordination_root.as_posix(),
        "--code-repository-root",
        repo.path.as_posix(),
        "--topology",
        "external",
    ]


def _provider_watchers_once(config: McpRuntimeConfig, action: str, *, dry_run: bool) -> dict[str, Any]:
    settings_path = write_lifecycle_settings(config)
    try:
        argv = [
            "watchers",
            action,
            "--coordination-root",
            config.coordination_root.as_posix(),
            "--from-settings",
            settings_path.as_posix(),
            "--timeout",
            str(config.timeout_caps.get("providerSeconds", 120)),
            "--json",
        ]
        if dry_run:
            argv.append("--dry-run")
        return run_package_main(
            operation="provider_watchers",
            main=provider_lifecycle.main,
            argv=argv,
        )
    finally:
        settings_path.unlink(missing_ok=True)


def _provider_refresh(config: McpRuntimeConfig, *, dry_run: bool) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if "grepai-memory" in config.providers:
        steps.append(
            _provider_main(
                config,
                operation="provider_watchers",
                provider="grepai",
                action="refresh",
                dry_run=dry_run,
            )
        )
    if "codegraphcontext-code" in config.providers:
        steps.append(
            _provider_main(
                config,
                operation="provider_watchers",
                provider="cgc",
                action="refresh-all",
                dry_run=dry_run,
            )
        )
    return {
        "ok": all(step.get("ok") for step in steps),
        "operation": "provider_watchers",
        "action": "refresh",
        "steps": steps,
    }


def _provider_main(
    config: McpRuntimeConfig,
    *,
    operation: str,
    provider: str,
    action: str,
    repo_id: str | None = None,
    dry_run: bool = True,
    timeout: int | None = None,
    extra: list[str] | None = None,
) -> dict[str, Any]:
    settings_path = write_lifecycle_settings(config)
    try:
        argv = [
            provider,
            action,
            "--coordination-root",
            config.coordination_root.as_posix(),
            "--from-settings",
            settings_path.as_posix(),
            "--timeout",
            str(timeout or config.timeout_caps.get("providerSeconds", 120)),
            "--json",
        ]
        if repo_id:
            argv.extend(["--repo-id", repo_id])
        if dry_run:
            argv.append("--dry-run")
        argv.extend(extra or [])
        return run_package_main(operation=operation, main=provider_lifecycle.main, argv=argv)
    finally:
        settings_path.unlink(missing_ok=True)


def _worktree_main(operation: str, argv: list[str]) -> dict[str, Any]:
    return run_package_main(operation=operation, main=git_worktree_manager.main, argv=argv)


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
    argv = [
        "closeout",
        "--contract-path",
        _coord_path(config, contract_path, "contract_path").as_posix(),
        "--code-commit-message",
        code_commit_message,
    ]
    _append_option(argv, "--memory-commit-message", memory_commit_message)
    _append_option(argv, "--ledger-commit-message", ledger_commit_message)
    if intent_note:
        argv.extend(["--approval-note", intent_note])
    if not dry_run:
        argv.append("--approved")
    if dry_run:
        argv.append("--dry-run")
    return _worktree_main(operation, argv)


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
    argv = [
        "direct-closeout",
        *_repo_common_argv(config, repo),
        "--task-name",
        task_name,
        "--code-commit-message",
        code_commit_message,
    ]
    _append_option(argv, "--source-branch", source_branch)
    _append_option(argv, "--memory-commit-message", memory_commit_message)
    _append_option(argv, "--ledger-commit-message", ledger_commit_message)
    if intent_note:
        argv.extend(["--approval-note", intent_note])
    if not dry_run:
        argv.append("--approved")
    if dry_run:
        argv.append("--dry-run")
    return _worktree_main(operation, argv)


def _carryover_argv(
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
    argv = [
        "--code-repository-root",
        repo.path.as_posix(),
        "--official-code-ref",
        official_code_ref,
        "--source-code-ref",
        source_code_ref,
        "--old-base",
        old_base,
        "--official-memory",
        repo.memory_root.as_posix(),
        "--source-memory",
        source_memory_path.as_posix(),
        "--code-repository-name",
        repo.repo_id,
    ]
    if replace_existing:
        argv.append("--replace-existing")
    return argv


def _benchmark_root(config: McpRuntimeConfig, value: str | None) -> Path:
    if value:
        return _coord_path(config, value, "benchmarks_root")
    coordinator_benchmarks = config.coordination_root / "benchmarks"
    if (coordinator_benchmarks / "cases").is_dir():
        return coordinator_benchmarks
    return source_root_from_package() / "benchmarks"


def _append_option(argv: list[str], flag: str, value: str | None) -> None:
    if value:
        argv.extend([flag, value])


def _append_positional(argv: list[str], value: str | None) -> None:
    if value:
        argv.append(value)
