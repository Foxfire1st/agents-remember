"""Controllers for worktree-backed MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers._guards import require_repo, require_within_coordination
from agents_remember.mcp.config import (
    DEFAULT_PROVIDER_SETUP_SECONDS,
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.providers.settings import write_lifecycle_settings
from agents_remember.worktrees import git_worktree_manager


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
    stale_base_choice: str | None = None,
    skip_provider_setup: bool = False,
    retry_provider_setup: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    settings_path = None if skip_provider_setup else write_lifecycle_settings(config)
    provider_setup_config = (
        None
        if settings_path is None
        else git_worktree_manager.WorktreeProviderSetupConfig(
            coordination_root=config.coordination_root,
            settings_path=settings_path,
            seed_source_coordination_root=config.coordination_root,
            # The temp settings file must outlive this controller call: the
            # background setup thread reads it and owns the unlink (GitHub #53).
            unlink_settings_after_setup=True,
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
        stale_base_choice=stale_base_choice,
        custom_instruction=None,
        skip_provider_setup=skip_provider_setup,
        retry_provider_setup=retry_provider_setup,
        provider_setup_config=provider_setup_config,
        # Setup-flow bound: the documented timeoutCaps.providerSetupSeconds cap
        # (matching runtime_install), not the docker-control default — the seed
        # export of a large graph is legitimate setup work (GitHub #53/#58).
        provider_timeout=config.timeout_caps.get(
            "providerSetupSeconds", DEFAULT_PROVIDER_SETUP_SECONDS
        ),
        dry_run=dry_run,
    )
    result: dict[str, Any] | None = None
    try:
        result = _worktree_result("worktree_start", git_worktree_manager.start_result(args))
        return result
    finally:
        if settings_path is not None and not _settings_owned_by_background(result):
            settings_path.unlink(missing_ok=True)


def _settings_owned_by_background(result: dict[str, Any] | None) -> bool:
    """True when a launched background setup took over the temp settings file."""
    if result is None:
        return False
    providers = result.get("providers")
    return isinstance(providers, dict) and providers.get("state") == "starting"


def worktree_attach_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        contract_path=require_within_coordination(config, contract_path, "contract_path")
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
    repo = require_repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        contract_path=require_within_coordination(config, contract_path, "contract_path")
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
    dry_run: bool = False,
) -> dict[str, Any]:
    args = git_worktree_manager.WorktreeArgs(
        contract_path=require_within_coordination(config, contract_path, "contract_path"),
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
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict[str, Any]:
    args = git_worktree_manager.WorktreeArgs(
        contract_path=require_within_coordination(config, contract_path, "contract_path"),
        approved=not dry_run,
        dry_run=dry_run,
        teardown_providers=teardown_providers,
    )
    return _worktree_result("worktree_cleanup", git_worktree_manager.cleanup_result(args))


def worktree_abandon_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    args = git_worktree_manager.WorktreeArgs(
        contract_path=require_within_coordination(config, contract_path, "contract_path"),
        approved=not dry_run,
        dry_run=dry_run,
        force=force,
    )
    return _worktree_result("worktree_abandon", git_worktree_manager.abandon_result(args))


def _worktree_namespace(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    **kwargs: Any,
) -> git_worktree_manager.WorktreeArgs:
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
    return git_worktree_manager.WorktreeArgs(**values)


def _worktree_result(
    operation: str, result: git_worktree_manager.WorktreeCommandResult
) -> dict[str, Any]:
    return {**result.payload, "ok": result.returncode == 0, "operation": operation}


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
    args = git_worktree_manager.WorktreeArgs(
        contract_path=require_within_coordination(config, contract_path, "contract_path"),
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
    repo = require_repo(config, repo_id)
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
