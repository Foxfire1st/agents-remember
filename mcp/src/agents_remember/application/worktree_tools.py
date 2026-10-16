"""Application entry points for worktree-backed MCP tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.application.review_external_git_movement import (
    external_git_movement_result_block,
)
from agents_remember.application.review_final_output_receipt import (
    attach_closeout_receipt,
    attach_integration_receipt,
    attach_prepared_selection,
)
from agents_remember.application.review_sync_rebinding import rebinding_result_block
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_status import project_contract_status
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.direct_execution_grants import (
    DirectGrantError,
    read_grant,
)
from agents_remember.kernel.primitives.runtime_config import (
    DEFAULT_PROVIDER_SETUP_SECONDS,
    McpRuntimeConfig,
    RepositoryScope,
    reload_provider_authority,
)
from agents_remember.models.certification.corrective import RedCatalogDisposition
from agents_remember.models.closeout.input import CloseoutCorrectedCall, EffectiveCloseoutInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import (
    GatePolicyRuleSnapshot,
    IntegrateStrategy,
    LifecycleOperationKind,
    LifecycleOperationProjection,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.worktree import MemorySyncChoice, SyncResolutionInput
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
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_controls import (
    LifecycleControlCommand,
    LifecycleControlError,
    control_operation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_read_decision import (
    lifecycle_journal_read_decision,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_request import (
    LifecycleControlRequestError,
    LifecycleControlRequestShape,
    validate_lifecycle_control_request,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
)
from agents_remember.worktrees.sync_transaction_state import observe_sync_operation
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
)

from .lifecycle.configured_contract_admission import (
    ConfiguredContractAccepted,
    ConfiguredContractRefused,
    admit_configured_contract,
    admit_configured_terminal_contract,
    execute_configured_contract_operation,
    project_configured_contract_refusal,
)
from .lifecycle.lifecycle_control_authority import (
    LifecycleCallerError,
    completed_disposition_authorized,
    require_completed_disposition_authority,
)
from .lifecycle.lifecycle_operation_location import (
    LifecycleOperationPublicAddress,
    configured_lifecycle_operation_location,
)
from .worktree_tool_requests import (
    DEFAULT_START_EXECUTION,
    DEFAULT_TASK_BASES,
    NO_TASK_DOCS,
    PREVIEW_ONLY,
    CloseoutApproval,
    CloseoutCommitMessages,
    FinalizeTaskDocs,
    LandedCommits,
    OperationControlRequest,
    StartExecution,
    TaskBases,
    TaskIdentity,
)

_START_ELIGIBILITY: dict[str, object] = {
    "requiresCheck": "not-performed",
    "detail": (
        "worktree_start does not read or verify the plan's Requires lines; the addressed leaf's "
        "declared dependencies are the owning seat's check, so confirm they are landed before "
        "building on this base"
    ),
}
"""What this tool did *not* check, stated where a caller reads the result.

D-17 measured the gap: a leaf previewed as ``would-start`` while a requirement its packet declares
as a dependency was unlanded, so a session that trusted the tool could create an enclosure whose
base lacks what the leaf builds on. Nothing in the leaf document carries the ``Requires`` set in a
machine-readable form (it lives in the plan document's prose), so the tool cannot verify it; what it
can do is stop implying that eligibility is part of what it checked. This notice is additive on
every start result and on the lifecycle refusal.
"""


def _direct_leaf_start_refusal(
    config: McpRuntimeConfig, identity: TaskIdentity
) -> dict[str, Any] | None:
    """Refuse ``worktree_start`` for a direct leaf: it gets no enclosure (MIK-R94 rule 12).

    ``None`` for series starts, for leaves that cannot be resolved, and for
    leaves without a grant. A grant that cannot be read refuses: a leaf is
    never taken as direct without a readable grant.
    """
    if not identity.leaf_id:
        return None
    task_root = (
        Path(config.coordination_root) / "tasks" / identity.repo_id / (identity.task_name or "")
    )
    ref = _started_leaf_ref(task_root, identity.repo_id, identity.task_name, identity.leaf_id)
    if ref is None:
        return None
    try:
        grant = read_grant(Path(config.coordination_root), ref)
    except DirectGrantError as error:
        grant = None
        unreadable = error.detail
    else:
        unreadable = ""
    if grant is None and not unreadable:
        return None
    if unreadable:
        summary = f"worktree_start for {ref.repository}:{ref.path} is refused: {unreadable}"
    elif grant is not None:
        summary = (
            f"worktree_start for {ref.repository}:{ref.path} is refused: the leaf is direct "
            f"({grant.describe()}). A direct leaf has no enclosure of its own; start its "
            "roles with role_start on the same leaf, which gives them the checkouts of "
            "the master's branch."
        )
    else:
        return None
    return {
        "ok": False,
        "state": "direct-leaf-no-enclosure",
        "summary": summary,
        "detail": summary,
        "eligibility": dict(_START_ELIGIBILITY),
    }


def _started_leaf_ref(
    task_root: Path, repo_id: str, task_name: str | None, leaf_id: str
) -> TaskDocumentRef | None:
    """The canonical reference of a leaf addressed by task name and leaf id."""
    if not task_name or not task_root.is_dir():
        return None
    for candidate in sorted(task_root.glob("*.json")):
        if candidate.name == "task.json":
            continue
        try:
            payload = json.loads(candidate.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("kind") == "subTask"
            and payload.get("id") == leaf_id
        ):
            return TaskDocumentRef(repository=repo_id, path=f"{task_name}/{candidate.name}")
    return None


def worktree_start_tool(
    config: McpRuntimeConfig,
    identity: TaskIdentity,
    *,
    bases: TaskBases = DEFAULT_TASK_BASES,
    execution: StartExecution = DEFAULT_START_EXECUTION,
) -> dict[str, Any]:
    repo = require_repo(config, identity.repo_id)
    direct_refusal = _direct_leaf_start_refusal(config, identity)
    if direct_refusal is not None:
        return direct_refusal
    # Stable contract/event scope identity; no session owns a worktree.
    lifecycle_id = new_ulid()
    # Containment R1 (260707-HFX-L1): the on-disk authority file — not the boot
    # snapshot — decides whether provider setup may launch. An empty (or
    # unreadable: fail-closed) live providers map skips setup outright; the
    # worktree itself is still created. Launch runs on the LIVE providers map.
    authority = None if execution.skip_provider_setup else reload_provider_authority(config)
    settings_path = (
        write_lifecycle_settings()
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
        result["eligibility"] = dict(_START_ELIGIBILITY)
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


def worktree_attach_tool(config: McpRuntimeConfig, task: TaskRef) -> dict[str, Any]:
    args = _task_ref_namespace(config, task)
    result = _worktree_result("worktree_attach", git_worktree_manager.attach_result(args))
    return result


def worktree_status_tool(
    config: McpRuntimeConfig,
    task: TaskRef,
    *,
    caller: DeclaredCaller | None = None,
) -> dict[str, Any]:
    args = _task_ref_namespace(config, task)
    result = _worktree_result("worktree_status", git_worktree_manager.status_result(args))
    contract_path = result.get("contract_path")
    if not isinstance(contract_path, str) or not contract_path:
        return result
    requested_path = Path(contract_path)
    sync_operation = None
    try:
        _, sync_location = configured_lifecycle_operation_location(config, requested_path)
        sync_operation = observe_sync_operation(
            sync_location.worktree_group,
            contract_path=requested_path,
        )
    except LifecycleOperationLocationError:
        pass
    if sync_operation is not None:
        result["syncOperation"] = sync_operation.model_dump(mode="json", exclude_none=True)
    return project_contract_status(config, result, requested_path, caller)


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
    memory_sync_choice: MemorySyncChoice | None = None,
    resolution: SyncResolutionInput | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    configured = admit_configured_contract(config, contract_path, operation="worktree_sync")
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(configured, operation="worktree_sync")
    args = git_worktree_manager.WorktreeArgs(
        contract_path=configured.contract_path,
        memory_sync_choice=memory_sync_choice,
        resolution_action=resolution.action if resolution is not None else None,
        dry_run=dry_run,
    )
    payload = _worktree_result("worktree_sync", git_worktree_manager.sync_result(args))
    # The sync has already finished its Git work and written its contract by this point, so the
    # rebinding measures what it resolved and never participates in the transaction's admission
    # (ICR-R22@v1): a sync that cannot be measured is returned unchanged, carrying the reason.
    return rebinding_result_block(configured.contract, payload)


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
    *,
    corrective_dispositions: tuple[RedCatalogDisposition, ...] = (),
) -> dict[str, Any]:
    del corrective_dispositions
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
    dry_run: bool = False,
) -> dict[str, Any]:
    """Land the task branches onto their source branches in this process.

    The one integration rule is the moved-parent refusal inside
    ``git_worktree_manager.integrate_result``: an is_ancestor comparison of the
    leaf's source ref against the candidate it was verified at. No door
    authority, publication intent, claim, journal or operation record
    participates.

    THE REFUSAL IS A RETURN VALUE, NOT AN EXCEPTION: a moved parent produces
    ``state == "blocked-non-ff"`` carrying "source branch moved" and routing to
    the documented ``worktree_sync`` remedy, where it previously raised
    ``RuntimeError("code integration source moved")``.
    """

    configured = admit_configured_contract(config, contract_path, operation="worktree_integrate")
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(
            configured,
            operation="worktree_integrate",
        )
    confined_contract = configured.contract_path
    args = git_worktree_manager.WorktreeArgs(
        contract_path=confined_contract,
        strategy=strategy,
        approved=not dry_run,
        dry_run=dry_run,
        # The configured policy MUST reach the seam guard (mirror of the closeout
        # path below): the dataclass default is all-human, which would refuse the
        # exact delegated approval the master-handover channel produces.
        gate_policy=config.orchestration.gate_policy,
    )
    result = _worktree_result(
        "worktree_integrate",
        git_worktree_manager.integrate_result(args, configured.contract),
    )
    if result.get("ok") and not dry_run:
        result = attach_integration_receipt(result, configured.contract)
    if result.get("ok"):
        # The raw-Git identity boundary is stated on this result too (ICR-R23@v1), for the landing
        # preview as well as the landing: an attached measurement, never a gate -- it refuses nothing
        # and the integration's own admission has already run by here.
        result = external_git_movement_result_block(configured.contract, result)
    return result


def worktree_checkpoint_landing_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    strategy: IntegrateStrategy = "ff-only",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Land an unfinished atomic master's accumulated line into its super branch.

    ``worktree_integrate`` closes a finished master: it proves the master's task document is
    ``Completed``, that every canonical leaf owns a landed enclosure, and that the master has
    closed out. A master being paused has none of those, so before this route a partial master
    could not land at all. This captures the master's own committed refs instead -- the live series
    code work branch tip and the live memory work branch tip -- proves the source ancestry,
    and lands exactly those. It shares the final route's entire preflight and ref move --
    the series contract binding, the atomic landing authority, the replay/ff source-state gate, the
    lineage proof, the master-handover gate and the compare-and-swap -- and requires the same
    explicit developer approval (``dry_run=False``). It records ``checkpointed`` rather than
    ``completed`` and retires nothing, so the master keeps its worktrees, its branches and its
    enclosure for the work that continues.
    """

    configured = admit_configured_contract(
        config, contract_path, operation="worktree_checkpoint_landing"
    )
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(
            configured,
            operation="worktree_checkpoint_landing",
        )
    args = git_worktree_manager.WorktreeArgs(
        contract_path=configured.contract_path,
        strategy=strategy,
        approved=not dry_run,
        dry_run=dry_run,
        gate_policy=config.orchestration.gate_policy,
    )
    return _worktree_result(
        "worktree_checkpoint_landing",
        git_worktree_manager.checkpoint_landing_result(args, configured.contract),
    )


def worktree_pause_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
) -> dict[str, Any]:
    """Stop an atomic master without publishing anything.

    This is the stop the developer asked for and the split exists to make possible. It
    releases the master's atomic-series activation selection -- so the master really does stop
    being the one exposing implementation work, rather than being marked as stopped -- and
    returns control to the developer with no continued-execution step. It moves no ref, creates
    no commit, lands nothing and writes no ledger row, and the master keeps its work branches,
    its worktrees, its enclosure and every unstarted leaf exactly as they were.

    ``worktree_checkpoint_landing`` is the separate, explicitly requested PUBLICATION that
    lands a partial master's accumulated line. It is not reachable from here: pausing a master
    and publishing one are two operations, and reaching for the stop must never publish.
    """

    configured = admit_configured_contract(config, contract_path, operation="worktree_pause")
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(configured, operation="worktree_pause")
    args = git_worktree_manager.WorktreeArgs(
        contract_path=configured.contract_path,
        gate_policy=config.orchestration.gate_policy,
    )
    return _worktree_result(
        "worktree_pause",
        git_worktree_manager.pause_result(args, configured.contract),
    )


def worktree_record_landing_tool(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    landed: LandedCommits,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Record a landing the remote already performed (the pull-request route).

    ``worktree_integrate`` moves refs locally; a pull request does not. This entry point shares
    that operation's one contract write (``modules.landing_record``) so the terminal
    ``integration`` cell has exactly one definition regardless of how the code landed. It refuses
    a commit that is not reachable from a landing target, so the cell cannot be set from a commit
    that landed nowhere.
    """

    configured = admit_configured_contract(
        config, contract_path, operation="worktree_record_landing"
    )
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(
            configured,
            operation="worktree_record_landing",
        )
    args = git_worktree_manager.WorktreeArgs(
        contract_path=configured.contract_path,
        approved=not dry_run,
        dry_run=dry_run,
        landed_code_commit=landed.code,
        landed_memory_content_commit=landed.memory_content,
        gate_policy=config.orchestration.gate_policy,
    )
    return _worktree_result(
        "worktree_record_landing",
        git_worktree_manager.record_landing_result(args),
    )


def worktree_operation_control_tool(
    config: McpRuntimeConfig,
    request: OperationControlRequest,
) -> dict[str, Any]:
    invalid = _operation_control_request_refusal(request)
    if invalid is not None:
        return invalid
    configured = admit_configured_contract(
        config, request.contract_path, operation="worktree_operation_control"
    )
    if isinstance(configured, ConfiguredContractRefused):
        return _configured_control_refusal(request, configured)
    caller_result = _resolve_control_caller(configured.contract, request)
    if isinstance(caller_result, dict):
        return caller_result
    caller, disposition_authorized = caller_result
    return _execute_operation_control(
        config,
        request,
        configured,
        caller,
        disposition_authorized=disposition_authorized,
    )


def _operation_control_request_refusal(
    request: OperationControlRequest,
) -> dict[str, Any] | None:
    try:
        validate_lifecycle_control_request(
            LifecycleControlRequestShape(
                action=request.action,
                expected_generation=request.expected_generation,
                intent_note=request.intent_note,
                commit_messages={
                    "code_commit_message": request.code_commit_message,
                    "memory_commit_message": request.memory_commit_message,
                },
                has_grade=request.grade is not None,
                has_admission=request.admission is not None,
            )
        )
    except LifecycleControlRequestError as error:
        return {
            "ok": False,
            "operation": "worktree_operation_control",
            "state": "refused",
            "status": error.status,
            "detail": error.detail,
            "expected": error.expected,
            "observed": error.observed,
            "nextAction": "correct-request",
        }
    return None


def _configured_control_refusal(
    request: OperationControlRequest,
    configured: ConfiguredContractRefused,
) -> dict[str, Any]:
    return project_configured_contract_refusal(
        configured,
        operation="worktree_operation_control",
        address=LifecycleOperationPublicAddress(
            "worktree_operation_control",
            request.operation_kind,
            request.expected_generation,
        ),
    )


def _resolve_control_caller(
    contract: WorktreeContract,
    request: OperationControlRequest,
) -> tuple[DeclaredCaller | None, bool] | dict[str, Any]:
    try:
        caller = request.caller
        if request.action in {"retire", "supersede"}:
            require_completed_disposition_authority(contract, caller)
        return caller, completed_disposition_authorized(contract, caller)
    except LifecycleCallerError as exc:
        return {
            "ok": False,
            "operation": "worktree_operation_control",
            "state": "refused",
            "status": exc.status,
            "detail": exc.detail,
            "expected": {},
            "observed": {},
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": exc.detail,
        }


def _execute_operation_control(
    config: McpRuntimeConfig,
    request: OperationControlRequest,
    configured: ConfiguredContractAccepted,
    caller: DeclaredCaller | None,
    *,
    disposition_authorized: bool,
) -> dict[str, Any]:
    confined = configured.contract_path
    resume_messages = (
        raw_closeout_messages(
            code=request.code_commit_message,
            memory=request.memory_commit_message,
        )
        if request.action == "resume"
        else None
    )
    contract = configured.contract
    try:
        execution = execute_configured_contract_operation(
            configured,
            lambda: control_operation(
                LifecycleControlCommand(
                    admitted_contract=contract,
                    admitted_location=configured.location,
                    configured_authority=config.config_path.as_posix(),
                    kind=request.operation_kind,
                    action=request.action,
                    expected_generation=request.expected_generation,
                    intent_note=request.intent_note,
                    dry_run=request.dry_run,
                    resume_messages=resume_messages,
                    resume_gate_policy=(
                        _gate_policy_snapshot(config) if request.action == "resume" else None
                    ),
                    corrective_dispositions=request.corrective_dispositions,
                    supersede_grade=request.grade,
                    supersede_admission=request.admission,
                    allow_completed_disposition=disposition_authorized,
                    caller=caller,
                )
            ),
        )
    except LifecycleControlError as exc:
        return {
            "ok": False,
            "operation": "worktree_operation_control",
            "state": "refused",
            "status": exc.status,
            "detail": exc.detail,
            **exc.response_fields(
                contract_path=confined.as_posix(),
                kind=request.operation_kind,
                generation=request.expected_generation,
                caller=caller,
            ),
        }
    except LifecycleOperationReadError as exc:
        return _journal_read_refusal(
            "worktree_operation_control",
            request.operation_kind,
            exc,
        )
    if isinstance(execution, ConfiguredContractRefused):
        return project_configured_contract_refusal(
            execution,
            operation="worktree_operation_control",
            address=LifecycleOperationPublicAddress(
                "worktree_operation_control",
                request.operation_kind,
                request.expected_generation,
            ),
        )
    return _operation_acknowledgement("worktree_operation_control", execution)


def _journal_read_refusal(
    operation: str,
    kind: LifecycleOperationKind,
    error: LifecycleOperationReadError,
) -> dict[str, Any]:
    decision = lifecycle_journal_read_decision(kind, error)
    payload = decision.payload()
    return {
        "ok": False,
        "operation": operation,
        "state": "refused",
        "status": decision.status,
        "detail": decision.detail,
        **{key: value for key, value in payload.items() if key != "state"},
    }


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
    configured = admit_configured_terminal_contract(
        config, contract_path, operation="worktree_cleanup"
    )
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(configured, operation="worktree_cleanup")
    args = git_worktree_manager.WorktreeArgs(
        contract_path=configured.contract_path,
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
    configured = admit_configured_terminal_contract(
        config,
        contract_path,
        # Abandon admits a contract whose only disagreement with the configured
        # authority is the removed mode (MIK-R50 R3b) and writes its terminal
        # state. Everything else about abandon is unchanged.
        allow_disabled_memory=True,
        operation="worktree_abandon",
    )
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(configured, operation="worktree_abandon")
    args = git_worktree_manager.WorktreeArgs(
        contract_path=configured.contract_path,
        approved=not dry_run,
        dry_run=dry_run,
        force=force,
    )
    result = _worktree_result("worktree_abandon", git_worktree_manager.abandon_result(args))
    return result


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
    return result


def _worktree_namespace(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    **kwargs: Any,
) -> git_worktree_manager.WorktreeArgs:
    values: dict[str, Any] = {
        "code_repository_name": repo.repo_id,
        "certification_profile": repo.certification_profile,
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
    messages: CloseoutCommitMessages,
    approval: CloseoutApproval,
) -> dict[str, Any]:
    configured = admit_configured_contract(
        config,
        contract_path,
        require_candidate_identity=False,
        operation=operation,
    )
    address = LifecycleOperationPublicAddress(operation, "closeout")
    if isinstance(configured, ConfiguredContractRefused):
        return project_configured_contract_refusal(
            configured,
            operation=operation,
            address=address,
        )
    confined_contract = configured.contract_path
    corrected_arguments = corrected_closeout_arguments(confined_contract.as_posix())
    if operation == "worktree_closeout_apply":
        corrected_arguments.update(intent_note="<developer intent>", dry_run=True)
    try:
        effective_input = _normalize_worktree_closeout(
            configured.contract,
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
    result = git_worktree_manager.closeout_result(args, configured.contract)
    payload = _worktree_result(operation, result)
    # The raw-Git identity boundary is stated beside the receipt on both closeout results (ICR-R23@v1):
    # the preview says what the leaf's declared identities look like right now, and the applied result
    # says it about the work it just committed. It is an attached statement and never a gate -- the
    # closeout door's own source-lineage checks remain the only checks on the transaction, and this
    # block returns the payload unchanged when it cannot measure.
    if operation == "worktree_closeout_preview":
        return external_git_movement_result_block(
            configured.contract, attach_prepared_selection(payload, configured.contract)
        )
    return external_git_movement_result_block(
        configured.contract, attach_closeout_receipt(payload, configured.contract)
    )


def _normalize_worktree_closeout(
    contract: WorktreeContract,
    messages: CloseoutCommitMessages,
    *,
    tool_name: str,
    corrected_arguments: dict[str, object],
) -> EffectiveCloseoutInput:
    plan = resolve_closeout_plan(
        contract,
        route="worktree",
        candidate=capture_closeout_candidate(contract),
    )
    return normalize_closeout_input(
        contract,
        raw_closeout_messages(code=messages.code, memory=messages.memory),
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
        "detail": "closeout input is invalid; use the corrected call",
        **error.response_fields(),
    }
