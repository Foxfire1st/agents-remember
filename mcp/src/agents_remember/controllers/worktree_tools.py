"""Controllers for worktree-backed MCP tools."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope, path_is_relative_to
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
