"""Worktree lifecycle and direct-closeout payload builders."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers.worktree_tools import (
    direct_closeout_apply_tool,
    direct_closeout_preview_tool,
    worktree_attach_tool,
    worktree_cleanup_tool,
    worktree_closeout_apply_tool,
    worktree_closeout_preview_tool,
    worktree_integrate_tool,
    worktree_start_tool,
    worktree_status_tool,
)

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
    skip_provider_setup: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_start",
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
            skip_provider_setup=skip_provider_setup,
            dry_run=dry_run,
        ),
    )


def worktree_attach_payload(
    config: McpRuntimeConfig,
    repo_id: str,
    *,
    task_name: str | None = None,
    contract_path: str | None = None,
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_attach",
        worktree_attach_tool(
            config,
            repo_id=repo_id,
            task_name=task_name,
            contract_path=contract_path,
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
    return _tool_payload(
        "direct_closeout_preview",
        direct_closeout_preview_tool(
            config,
            repo_id=repo_id,
            task_name=task_name,
            source_branch=source_branch,
            code_commit_message=code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        ),
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
    return _tool_payload(
        "direct_closeout_apply",
        direct_closeout_apply_tool(
            config,
            repo_id=repo_id,
            task_name=task_name,
            source_branch=source_branch,
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
) -> dict[str, Any]:
    return _tool_payload(
        "worktree_cleanup",
        worktree_cleanup_tool(config, contract_path=contract_path, dry_run=dry_run),
    )
