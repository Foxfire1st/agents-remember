"""Application entry points for worktree-backed MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agents_remember.application.completion_cleanup import auto_complete_seats
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.primitives.runtime_config import (
    DEFAULT_PROVIDER_SETUP_SECONDS,
    McpRuntimeConfig,
    RepositoryScope,
    reload_provider_authority,
)
from agents_remember.models.closeout_input import CloseoutCorrectedCall, EffectiveCloseoutInput
from agents_remember.models.lifecycles.operation import (
    GatePolicyRuleSnapshot,
    IntegrateOperationInput,
    IntegrateStrategy,
    LifecycleOperationKind,
    LifecycleOperationProjection,
)
from agents_remember.models.lifecycles.responses import TerminalState
from agents_remember.observer.ambient import AmbientLifecycle, ambient
from agents_remember.observer.save_gate import coerce_save_decision
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.lifecycle.log_capture import summarize_command_logs
from agents_remember.providers.settings import write_lifecycle_settings
from agents_remember.worktrees import git_worktree_manager
from agents_remember.worktrees.closeout_input import (
    CloseoutInputError,
    capture_closeout_candidate,
    corrected_closeout_arguments,
    normalize_closeout_input,
    raw_closeout_messages,
    resolve_closeout_plan,
)
from agents_remember.worktrees.integration.closeout_operation_admission import (
    CloseoutOperationAdmission,
)
from agents_remember.worktrees.integration.lifecycle_operations import (
    cancel_operation,
    latest_operation_projection,
    observe_operation,
    require_configured_contract_repositories,
    start_or_observe_closeout_operation,
    start_or_observe_operation,
)
from agents_remember.worktrees.worktree_contract import load_contract


@dataclass(frozen=True)
class TaskIdentity:
    """The identity a task is created under.

    ``worktree_name`` is the on-disk directory the code worktree gets;
    ``leaf_id``/``parent_task`` place the task in the task tree; ``workflow_kind``
    is the document format its contract follows ('light-task' or 'chat-task').
    """

    repo_id: str
    task_name: str
    worktree_name: str
    leaf_id: str | None = None
    parent_task: str | None = None
    workflow_kind: str = "light-task"


@dataclass(frozen=True)
class TaskBases:
    """What a started task is based on, and the answers that clear a refused base.

    A start cuts a code work branch from a source branch and opens memory alongside
    it under ``memory_mode``. When a preflight refuses -- a source branch behind or
    diverged from its remote, or an undecided memory setup -- the caller may explicitly
    choose ``proceed-stale`` or ``disabled-memory``. Protected source repair belongs to a
    separate landing/recovery operation.
    """

    source_branch: str | None = None
    work_branch: str | None = None
    memory_mode: str | None = None
    memory_choice: str | None = None
    stale_base_choice: str | None = None


@dataclass(frozen=True)
class StartExecution:
    """How the start itself runs: as a preview or for real, and what happens to the
    background provider setup -- skipped outright, or relaunched for a contract whose
    earlier setup failed or went stale."""

    dry_run: bool = False
    skip_provider_setup: bool = False
    retry_provider_setup: bool = False


DEFAULT_TASK_BASES = TaskBases()
"""Repo-default branches, repo-default memory topology, no recovery choice made."""

DEFAULT_START_EXECUTION = StartExecution()
"""A real start with background provider setup launched normally."""


def worktree_start_tool(
    config: McpRuntimeConfig,
    identity: TaskIdentity,
    *,
    bases: TaskBases = DEFAULT_TASK_BASES,
    execution: StartExecution = DEFAULT_START_EXECUTION,
) -> dict[str, Any]:
    repo = require_repo(config, identity.repo_id)
    amb = ambient()
    if amb is not None and amb.current is not None and not amb.current.fleeting:
        return {
            "ok": False,
            "state": "lifecycle-switch-required",
            "summary": (
                "worktree_start refuses to repoint the active persistent lifecycle; switch "
                "away from it to a fresh fleeting lifecycle, then retry worktree_start"
            ),
            "nextOperation": "switch_task_lifecycle",
            "nextTool": "switch_lifecycle",
            "nextArgs": {},
            "nextStep": {
                "summary": "Switch to a fresh fleeting lifecycle, then retry worktree_start.",
                "nextOperation": "switch_task_lifecycle",
                "nextTool": "switch_lifecycle",
                "nextArgs": {},
            },
        }
    # worktree_start promotes the active lifecycle to persistent (design §1.3); with
    # no active lifecycle, mint a fresh anchor so the contract always carries one.
    lifecycle_id = amb.current.id if amb is not None and amb.current is not None else new_ulid()
    # Containment R1 (260707-HFX-L1): the on-disk authority file — not the boot
    # snapshot — decides whether provider setup may launch. An empty (or
    # unreadable: fail-closed) live providers map skips setup outright; the
    # worktree itself is still created. Launch runs on the LIVE providers map.
    authority = None if execution.skip_provider_setup else reload_provider_authority(config)
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
            # The temp settings file must outlive this entry point's call: the
            # background setup thread reads it and owns the unlink (GitHub #53).
            unlink_settings_after_setup=True,
        )
    )
    args = _worktree_namespace(
        config,
        repo,
        task_name=identity.task_name,
        leaf_id=identity.leaf_id,
        parent_task=identity.parent_task,
        worktree_name=identity.worktree_name,
        lifecycle_id=lifecycle_id,
        workflow_kind=identity.workflow_kind,
        source_branch=bases.source_branch,
        work_branch=bases.work_branch,
        memory_mode=bases.memory_mode,
        memory_choice=bases.memory_choice,
        stale_base_choice=bases.stale_base_choice,
        custom_instruction=None,
        skip_provider_setup=execution.skip_provider_setup,
        retry_provider_setup=execution.retry_provider_setup,
        provider_setup_config=provider_setup_config,
        # Setup-flow bound: the documented timeoutCaps.providerSetupSeconds cap
        # (matching runtime_install), not the docker-control default — the seed
        # export of a large graph is legitimate setup work (GitHub #53/#58).
        provider_timeout=config.timeout_caps.get(
            "providerSetupSeconds", DEFAULT_PROVIDER_SETUP_SECONDS
        ),
        dry_run=execution.dry_run,
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
        _attribute_start(amb, result, identity.repo_id)
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


def summarized_worktree_start_tool(
    config: McpRuntimeConfig,
    identity: TaskIdentity,
    *,
    bases: TaskBases = DEFAULT_TASK_BASES,
    execution: StartExecution = DEFAULT_START_EXECUTION,
) -> dict[str, Any]:
    """Run worktree start and apply command-log reporting policy before transport."""
    return summarize_command_logs(
        worktree_start_tool(config, identity, bases=bases, execution=execution)
    )


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
    if amb is None or result.get("state") != "attached":
        return
    lifecycle_id = result.get("lifecycle_id")
    enclosure = result.get("enclosure_path") or result.get("contract_path")
    if not isinstance(lifecycle_id, str) or not lifecycle_id or not isinstance(enclosure, str):
        return
    decision = coerce_save_decision(on_unsaved) if on_unsaved else None
    amb.attach(lifecycle_id, enclosure=enclosure, repo_id=repo_id, on_unsaved=decision)


def worktree_attach_tool(
    config: McpRuntimeConfig,
    task: TaskRef,
    *,
    on_unsaved: str | None = None,
) -> dict[str, Any]:
    args = _task_ref_namespace(config, task)
    result = _worktree_result("worktree_attach", git_worktree_manager.attach_result(args))
    _attribute_attach(ambient(), result, task.repo_id, on_unsaved)
    return result


def worktree_status_tool(config: McpRuntimeConfig, task: TaskRef) -> dict[str, Any]:
    args = _task_ref_namespace(config, task)
    result = _worktree_result("worktree_status", git_worktree_manager.status_result(args))
    contract_path = result.get("contract_path")
    if isinstance(contract_path, str) and contract_path:
        operation = latest_operation_projection(Path(contract_path))
        if operation is not None:
            result["lifecycleOperation"] = operation.model_dump(mode="json", exclude_none=True)
    return result


def _task_ref_namespace(
    config: McpRuntimeConfig, task: TaskRef
) -> git_worktree_manager.WorktreeArgs:
    """Resolve a caller's task reference into the domain's worktree arguments."""
    return _worktree_namespace(
        config,
        require_repo(config, task.repo_id),
        task_name=task.task_name,
        leaf_id=task.leaf_id,
        parent_task=task.parent_task,
        contract_path=require_within_coordination(config, task.contract_path, "contract_path")
        if task.contract_path
        else None,
    )


def worktree_sync_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    memory_sync_choice: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    confined_contract = _configured_contract_path(config, contract_path)
    args = git_worktree_manager.WorktreeArgs(
        contract_path=confined_contract,
        memory_sync_choice=memory_sync_choice,
        dry_run=dry_run,
    )
    return _worktree_result("worktree_sync", git_worktree_manager.sync_result(args))


@dataclass(frozen=True)
class CloseoutCommitMessages:
    """Raw public observations; contract resolution decides which legs are enabled."""

    code: str | None = None
    memory: str | None = None
    ledger: str | None = None


@dataclass(frozen=True)
class CloseoutApproval:
    """Whether a closeout actually commits, and the note recording why it may.

    A preview is the unapproved form -- ``dry_run`` with no note. An apply carries the
    developer's intent note, which the seam guard records as the approval.
    """

    intent_note: str = ""
    dry_run: bool = False


PREVIEW_ONLY = CloseoutApproval(dry_run=True)
"""The preview form: nothing is committed and no approval is claimed."""


def worktree_closeout_preview_tool(
    config: McpRuntimeConfig,
    contract_path: str,
    messages: CloseoutCommitMessages,
) -> dict[str, Any]:
    return _worktree_closeout(
        config,
        operation="worktree_closeout_preview",
        contract_path=contract_path,
        messages=messages,
        approval=PREVIEW_ONLY,
    )


def worktree_closeout_apply_tool(
    config: McpRuntimeConfig,
    contract_path: str,
    messages: CloseoutCommitMessages,
    approval: CloseoutApproval,
) -> dict[str, Any]:
    if not approval.dry_run:
        confined = _configured_contract_path(config, contract_path)
        corrected_arguments = corrected_closeout_arguments(
            confined.as_posix(), intent_note="<developer intent>"
        )
        try:
            operation = start_or_observe_closeout_operation(
                CloseoutOperationAdmission(
                    config_path=config.config_path.as_posix(),
                    contract_path=confined,
                    messages=raw_closeout_messages(
                        code=messages.code,
                        memory=messages.memory,
                        ledger=messages.ledger,
                    ),
                    approval_note=approval.intent_note,
                    gate_policy=_gate_policy_snapshot(config),
                    corrected_call=CloseoutCorrectedCall(
                        tool="worktree_closeout_apply",
                        arguments=corrected_arguments,
                    ),
                ),
            )
        except CloseoutInputError as exc:
            return _closeout_input_refusal("worktree_closeout_apply", exc)
        return _operation_acknowledgement("worktree_closeout_apply", operation)
    return _worktree_closeout(
        config,
        operation="worktree_closeout_apply",
        contract_path=contract_path,
        messages=messages,
        approval=approval,
    )


def worktree_integrate_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    strategy: IntegrateStrategy = "ff-only",
    ledger_commit_message: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Start/observe only the exact already-selected task operation.

    Queue role projection assigns this leaf action to the owning manager. This
    generic task-addressed boundary does not make scheduling decisions: it cannot
    select or substitute a candidate, and the worker revalidates the selected
    durable queue record immediately before moving source history.
    """

    confined_contract = require_within_coordination(config, contract_path, "contract_path")
    if dry_run:
        _configured_contract_path(config, contract_path)
    if not dry_run:
        operation = start_or_observe_operation(
            IntegrateOperationInput(
                configPath=config.config_path.as_posix(),
                contractPath=confined_contract.as_posix(),
                strategy=strategy,
                ledgerCommitMessage=ledger_commit_message,
                gatePolicy=_gate_policy_snapshot(config),
                autoCompleteSeats=config.retirement.auto_land_on_integration,
            )
        )
        return _operation_acknowledgement("worktree_integrate", operation)
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
        result.update(
            auto_complete_seats(
                config,
                confined_contract,
                reason="auto-close: leaf integrated into master",
                edge="leaf-integration",
            )
        )
    return result


def worktree_operation_cancel_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    operation_kind: str,
    intent_note: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    if operation_kind not in {"closeout", "integrate"}:
        raise ValueError("operation_kind must be 'closeout' or 'integrate'")
    if not intent_note.replace("\n", " ").strip():
        raise ValueError("operation cancellation requires a non-empty intent_note")
    confined = _configured_contract_path(config, contract_path)
    kind = cast(LifecycleOperationKind, operation_kind)
    projection = observe_operation(confined, kind) if dry_run else cancel_operation(confined, kind)
    if projection is None:
        raise RuntimeError(f"no {operation_kind} operation exists for this task")
    return _operation_acknowledgement("worktree_operation_cancel", projection)


def _gate_policy_snapshot(config: McpRuntimeConfig) -> list[GatePolicyRuleSnapshot]:
    return [
        GatePolicyRuleSnapshot(
            kind=rule.kind,
            delegatedRole=rule.delegated_role,
            requireReviewerVerdict=rule.require_reviewer_verdict,
        )
        for rule in config.orchestration.gate_policy.rules
    ]


def _operation_acknowledgement(
    operation: str, projection: LifecycleOperationProjection
) -> dict[str, Any]:
    payload = projection.model_dump(mode="json", exclude_none=True)
    return {
        "ok": True,
        "operation": operation,
        "state": payload["status"],
        "summary": (
            f"{payload['kind']} is {payload['status']}; poll worktree_status for task-bound "
            "progress. No job or process identifier is required."
        ),
        "pollTool": "worktree_status",
        "lifecycleOperation": payload,
    }


def worktree_cleanup_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict[str, Any]:
    confined_contract = _configured_contract_path(config, contract_path)
    args = git_worktree_manager.WorktreeArgs(
        contract_path=confined_contract,
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
    confined_contract = _configured_contract_path(config, contract_path)
    args = git_worktree_manager.WorktreeArgs(
        contract_path=confined_contract,
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
        end_ambient_lifecycle_if_anchored(
            str(result.get("lifecycle_id") or ""), outcome="abandoned"
        )
    return result


def end_ambient_lifecycle_if_anchored(lifecycle_id: str, *, outcome: TerminalState) -> None:
    """End the process-owned ambient when one task transition retires its anchor."""
    amb = ambient()
    if not lifecycle_id or amb is None:
        return
    current = amb.current
    if current is not None and current.id == lifecycle_id:
        amb.end(outcome)


@dataclass(frozen=True)
class FinalizeTaskDocs:
    """The documents a finalize ticks off: the leaf's own task document, the master
    document that tracks it, and the master subtask row number this leaf occupies."""

    task_doc_path: str | None = None
    master_doc_path: str | None = None
    subtask_number: str = ""


NO_TASK_DOCS = FinalizeTaskDocs()
"""Finalize a contract that carries no task documents to tick."""


def lifecycle_finalize_task_tool(
    config: McpRuntimeConfig,
    contract_path: str,
    *,
    docs: FinalizeTaskDocs = NO_TASK_DOCS,
    dry_run: bool = False,
    teardown_providers: bool = True,
) -> dict[str, Any]:
    confined_contract = require_within_coordination(config, contract_path, "contract_path")
    args = git_worktree_manager.FinalizeArgs(
        contract_path=confined_contract,
        task_doc_path=require_within_coordination(config, docs.task_doc_path, "task_doc_path")
        if docs.task_doc_path
        else None,
        master_doc_path=require_within_coordination(config, docs.master_doc_path, "master_doc_path")
        if docs.master_doc_path
        else None,
        subtask_number=docs.subtask_number,
        dry_run=dry_run,
        teardown_providers=teardown_providers,
    )
    result = _worktree_result("lifecycle_finalize_task", git_worktree_manager.finalize_result(args))
    if result["ok"] and not dry_run and config.retirement.auto_land_on_finalize:
        result.update(
            auto_complete_seats(
                config,
                confined_contract,
                reason="auto-close: master finalized into super",
                edge="master-finalization",
            )
        )
    return result


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


def _configured_contract_path(config: McpRuntimeConfig, contract_path: str) -> Path:
    """Confine a task contract and bind its Git identities to configured authority."""

    confined = require_within_coordination(config, contract_path, "contract_path")
    require_configured_contract_repositories(
        load_contract(confined),
        config.config_path.as_posix(),
    )
    return confined


def _worktree_closeout(
    config: McpRuntimeConfig,
    *,
    operation: str,
    contract_path: str,
    messages: CloseoutCommitMessages,
    approval: CloseoutApproval,
) -> dict[str, Any]:
    confined_contract = _configured_contract_path(config, contract_path)
    corrected_arguments = corrected_closeout_arguments(confined_contract.as_posix())
    if operation == "worktree_closeout_apply":
        corrected_arguments.update(intent_note="<developer intent>", dry_run=True)
    try:
        effective_input = _normalize_worktree_closeout(
            confined_contract,
            messages,
            tool_name=operation,
            corrected_arguments=corrected_arguments,
        )
    except CloseoutInputError as exc:
        return _closeout_input_refusal(operation, exc)
    args = git_worktree_manager.WorktreeArgs(
        contract_path=confined_contract,
        closeout_input=effective_input,
        approval_note=approval.intent_note,
        approved=not approval.dry_run,
        dry_run=approval.dry_run,
        gate_policy=config.orchestration.gate_policy,
    )
    return _worktree_result(operation, git_worktree_manager.closeout_result(args))


def _normalize_worktree_closeout(
    contract_path: Path,
    messages: CloseoutCommitMessages,
    *,
    tool_name: str,
    corrected_arguments: dict[str, object],
) -> EffectiveCloseoutInput:
    contract = load_contract(contract_path)
    plan = resolve_closeout_plan(
        contract,
        route="worktree",
        candidate=capture_closeout_candidate(contract),
    )
    return normalize_closeout_input(
        contract,
        raw_closeout_messages(code=messages.code, memory=messages.memory, ledger=messages.ledger),
        route="worktree",
        corrected_call=CloseoutCorrectedCall(
            tool=tool_name,
            arguments=corrected_arguments,
        ),
        resolved_plan=plan,
    )


def _closeout_input_refusal(operation: str, error: CloseoutInputError) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "state": "refused",
        "status": error.status,
        "detail": str(error),
        **error.response_fields(),
    }
