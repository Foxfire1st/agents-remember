"""Worktree lifecycle payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.worktree_tools import (
    worktree_abandon_tool,
    worktree_attach_tool,
    worktree_cleanup_tool,
    worktree_closeout_apply_tool,
    worktree_closeout_preview_tool,
    worktree_integrate_tool,
    worktree_start_tool,
    worktree_status_tool,
    worktree_sync_tool,
)
from agents_remember.providers.lifecycle.log_capture import summarize_command_logs

from ..config import McpRuntimeConfig
from .base import _tool_payload


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
    stale_base_choice: str | None = None,
    skip_provider_setup: bool = False,
    retry_provider_setup: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_start",
        summarize_command_logs(
            worktree_start_tool(
                config,
                repo_id=repo_id,
                task_name=task_name,
                worktree_name=worktree_name,
                workflow_kind=workflow_kind,
                source_branch=source_branch,
                work_branch=work_branch,
                memory_mode=memory_mode,
                memory_choice=memory_choice,
                stale_base_choice=stale_base_choice,
                skip_provider_setup=skip_provider_setup,
                retry_provider_setup=retry_provider_setup,
                dry_run=dry_run,
            )
        ),
    )


def worktree_sync_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    memory_sync_choice: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_sync",
        worktree_sync_tool(
            config,
            contract_path=contract_path,
            memory_sync_choice=memory_sync_choice,
            dry_run=dry_run,
        ),
    )


def worktree_attach_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
    on_unsaved: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_attach",
        worktree_attach_tool(
            config,
            repo_id=repo_id,
            task_name=task_name,
            contract_path=contract_path,
            on_unsaved=on_unsaved,
        ),
    )


def worktree_status_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_status",
        worktree_status_tool(
            config,
            repo_id=repo_id,
            task_name=task_name,
            contract_path=contract_path,
        ),
    )


def worktree_closeout_preview_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    code_commit_message: str,
    *,
    memory_commit_message: str = "",
    ledger_commit_message: str = "",
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_closeout_preview",
        worktree_closeout_preview_tool(
            config,
            contract_path=contract_path,
            code_commit_message=code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        ),
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
    return _tool_payload(
        "worktree_closeout_apply",
        worktree_closeout_apply_tool(
            config,
            contract_path=contract_path,
            intent_note=intent_note,
            code_commit_message=code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        ),
    )


def worktree_integrate_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    strategy: str = "ff-only",
    ledger_commit_message: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_integrate",
        worktree_integrate_tool(
            config,
            contract_path=contract_path,
            strategy=strategy,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        ),
    )


def worktree_cleanup_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_cleanup",
        worktree_cleanup_tool(
            config,
            contract_path=contract_path,
            dry_run=dry_run,
            teardown_providers=teardown_providers,
        ),
    )


def worktree_abandon_payload(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_abandon",
        worktree_abandon_tool(
            config,
            contract_path=contract_path,
            dry_run=dry_run,
            force=force,
        ),
    )
