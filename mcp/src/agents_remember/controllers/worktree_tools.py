"""Controllers for worktree-backed MCP tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.controllers._guards import require_repo, require_within_coordination
from agents_remember.mcp.config import (
    DEFAULT_PROVIDER_SETUP_SECONDS,
    McpRuntimeConfig,
    RepositoryScope,
    reload_provider_authority,
)
from agents_remember.observer.ambient import AmbientLifecycle, ambient
from agents_remember.observer.events import now_iso
from agents_remember.observer.save_gate import coerce_save_decision
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.settings import write_lifecycle_settings
from agents_remember.serving.landing import land_seats_for_leaf
from agents_remember.serving.seat_events import log_landed_event
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.worktree_contract import load_contract


def worktree_start_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    task_name: str,
    worktree_name: str,
    leaf_id: str | None = None,
    parent_task: str | None = None,
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
    # Containment R1 (260707-HFX-L1): the on-disk authority file — not the boot
    # snapshot — decides whether provider setup may launch. An empty (or
    # unreadable: fail-closed) live providers map skips setup outright; the
    # worktree itself is still created. Launch runs on the LIVE providers map.
    authority = None if skip_provider_setup else reload_provider_authority(config)
    settings_path = (
        write_lifecycle_settings(authority.apply(config))
        if authority is not None and authority.providers and authority.error is None
        else None
    )
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
        leaf_id=leaf_id,
        parent_task=parent_task,
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
        if authority is not None and (
            authority.error is not None or (config.providers and not authority.providers)
        ):
            # Surface the veto so a stale-snapshot session sees WHY setup was
            # skipped instead of silently diverging from its boot config.
            veto: dict[str, Any] = {
                "source": str(authority.source_path),
                "bootSnapshotProviders": sorted(config.providers),
            }
            if authority.error is not None:
                veto["error"] = authority.error
            result["providersAuthority"] = veto
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
    enclosure = result.get("enclosure_path") or result.get("contract_path")
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
    enclosure = result.get("enclosure_path") or result.get("contract_path")
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
    leaf_id: str | None = None,
    parent_task: str | None = None,
    on_unsaved: str | None = None,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        leaf_id=leaf_id,
        parent_task=parent_task,
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
    leaf_id: str | None = None,
    parent_task: str | None = None,
) -> dict[str, Any]:
    repo = require_repo(config, repo_id)
    args = _worktree_namespace(
        config,
        repo,
        task_name=task_name,
        leaf_id=leaf_id,
        parent_task=parent_task,
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
    confined_contract = require_within_coordination(config, contract_path, "contract_path")
    args = git_worktree_manager.WorktreeArgs(
        contract_path=confined_contract,
        strategy=strategy,
        approved=not dry_run,
        ledger_commit_message=ledger_commit_message,
        dry_run=dry_run,
        # The configured policy MUST reach the seam guard (mirror of the closeout
        # path below): the dataclass default is all-human, which would refuse the
        # exact delegated approval the master-handover channel produces.
        gate_policy=config.orchestration.gate_policy,
    )
    result = _worktree_result("worktree_integrate", git_worktree_manager.integrate_result(args))
    if result["ok"] and not dry_run and config.retirement.auto_land_on_integration:
        result["autoLandedSeats"] = _auto_land_completed_seats(
            config,
            confined_contract,
            roles=frozenset({"worker", "reviewer"}),
            reason="leaf integrated into master",
            edge="leaf-integration",
        )
    return result


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
    result = _worktree_result("worktree_abandon", git_worktree_manager.abandon_result(args))
    # End the ambient lifecycle when it anchors the abandoned worktree — the owner-written
    # lifecycle.ended (L11). A lifecycle whose owner is gone (e.g. the server restarted) is
    # terminalized by the reader instead: the reducer projects `abandoned` from the
    # contract's cleanup field, honoring the store's single-writer invariant.
    if not dry_run and result.get("state") == "abandoned":
        _end_ambient_lifecycle_if_anchored(str(result.get("lifecycle_id") or ""))
    return result


def _end_ambient_lifecycle_if_anchored(lifecycle_id: str) -> None:
    amb = ambient()
    if not lifecycle_id or amb is None:
        return
    current = amb.current
    if current is not None and current.id == lifecycle_id:
        amb.end("abandoned")


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
    result = _worktree_result(
        "lifecycle_finalize_task", git_worktree_manager.finalize_result(args)
    )
    if result["ok"] and not dry_run and config.retirement.auto_land_on_finalize:
        result["autoLandedSeats"] = _auto_land_completed_seats(
            config,
            confined_contract,
            roles=frozenset({"manager", "reviewer"}),
            reason="master finalized into super",
            edge="master-finalization",
        )
    return result


def _auto_land_completed_seats(
    config: McpRuntimeConfig,
    contract_path: Path,
    *,
    roles: frozenset[str],
    reason: str,
    edge: str,
) -> list[str]:
    """The completion-edge auto-land hook: resolve the contract's qualified leaf key and archive seats.

    Best-effort, END TO END: an unreadable/already-archived contract, a catalog read/write failure,
    or any other failure in the landing body skips the auto-land rather than failing the completion
    edge itself -- landing is a cleanup courtesy, never a gate on the edge it rides. The whole body
    is guarded, not just ``load_contract``: ``land_seats_for_leaf``
    does catalog file I/O that can raise just as easily as a missing contract file, and a raise from
    there must never propagate out of an already-succeeded ``worktree_integrate`` /
    ``lifecycle_finalize_task`` call.
    """
    try:
        contract = load_contract(contract_path)
        leaf_key = f"{contract.repo_name}/{contract.task_root.name}/{contract.task_id}"
        catalog = TerminalCatalog(terminal_catalog_path(config.coordination_root))
        at = now_iso()
        landed = land_seats_for_leaf(
            catalog, leaf_key=leaf_key, roles=roles, reason=reason, edge=edge, at=at
        )
        for entry in landed:
            log_landed_event(config, entry)
        return [entry.id for entry in landed]
    except Exception:
        return []


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
        gate_policy=config.orchestration.gate_policy,
    )
    return _worktree_result(operation, git_worktree_manager.closeout_result(args))
