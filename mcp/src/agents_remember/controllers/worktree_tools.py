"""Controllers for worktree-backed MCP tools."""

from __future__ import annotations

from typing import Any

from agents_remember.controllers._guards import require_repo, require_within_coordination
from agents_remember.mcp.config import (
    DEFAULT_PROVIDER_SETUP_SECONDS,
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.observer.ambient import AmbientLifecycle, ambient
from agents_remember.observer.save_gate import coerce_save_decision
from agents_remember.observer.ulid import new_ulid
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
    amb = ambient()
    # worktree_start promotes the active lifecycle to persistent (design §1.3); with
    # no active lifecycle, mint a fresh anchor so the contract always carries one.
    lifecycle_id = amb.current.id if amb is not None and amb.current is not None else new_ulid()
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
        lifecycle_id=lifecycle_id,
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
        _attribute_start(amb, result, repo_id)
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


def _attribute_start(amb: AmbientLifecycle | None, result: dict[str, Any], repo_id: str) -> None:
    """Promote the active lifecycle into the freshly started worktree (design §1.3).

    Only a clean ``started`` result attributes: the contract now anchors the
    lifecycle. An active lifecycle is promoted in place; with none active the
    minted contract id is adopted as a fresh persistent lifecycle. Re-attaching to
    an existing contract is ``worktree_attach``'s job, not start's.
    """
    if amb is None or result.get("state") != "started":
        return
    enclosure = result.get("contract_path")
    if not isinstance(enclosure, str):
        return
    if amb.current is not None:
        amb.promote(enclosure=enclosure, repo_id=repo_id, scope=repo_id)
        return
    lifecycle_id = result.get("lifecycle_id")
    if isinstance(lifecycle_id, str) and lifecycle_id:
        amb.attach(lifecycle_id, enclosure=enclosure, repo_id=repo_id)


def _attribute_attach(
    amb: AmbientLifecycle | None, result: dict[str, Any], repo_id: str, on_unsaved: str | None
) -> None:
    """Resume the contract's lifecycle on ``worktree_attach`` (design §1.3 table).

    ``attach`` adopts when none is active, no-ops on the same id, auto-pauses a
    persistent current, and routes an unsaved fleeting through the save gate --
    raising ``SaveGateRequired`` when ``on_unsaved`` was not supplied, so unsaved
    work is never dropped silently (the read-only attach already returned).
    """
    if amb is None:
        return
    lifecycle_id = result.get("lifecycle_id")
    enclosure = result.get("contract_path")
    if not isinstance(lifecycle_id, str) or not lifecycle_id or not isinstance(enclosure, str):
        return
    decision = coerce_save_decision(on_unsaved) if on_unsaved else None
    amb.attach(lifecycle_id, enclosure=enclosure, repo_id=repo_id, on_unsaved=decision)


def worktree_attach_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str | None = None,
    contract_path: str | None = None,
    on_unsaved: str | None = None,
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
    result = _worktree_result("worktree_attach", git_worktree_manager.attach_result(args))
    _attribute_attach(ambient(), result, repo_id, on_unsaved)
    return result


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


def worktree_sync_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    memory_sync_choice: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    args = git_worktree_manager.WorktreeArgs(
        contract_path=require_within_coordination(config, contract_path, "contract_path"),
        memory_sync_choice=memory_sync_choice,
        dry_run=dry_run,
    )
    return _worktree_result("worktree_sync", git_worktree_manager.sync_result(args))


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


def lifecycle_finalize_task_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    task_doc_path: str | None = None,
    master_doc_path: str | None = None,
    subtask_number: str = "",
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict[str, Any]:
    confined_contract = require_within_coordination(config, contract_path, "contract_path")
    args = git_worktree_manager.FinalizeArgs(
        contract_path=confined_contract,
        task_doc_path=require_within_coordination(config, task_doc_path, "task_doc_path")
        if task_doc_path
        else None,
        master_doc_path=require_within_coordination(config, master_doc_path, "master_doc_path")
        if master_doc_path
        else None,
        subtask_number=subtask_number,
        dry_run=dry_run,
        teardown_providers=teardown_providers,
    )
    return _worktree_result("lifecycle_finalize_task", git_worktree_manager.finalize_result(args))


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
