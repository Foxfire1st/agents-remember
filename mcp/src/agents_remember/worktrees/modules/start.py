from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.controlplane.task_publication_lock import task_publication_lock
from agents_remember.kernel.git_freshness import freshness_to_packet, read_branch_freshness
from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.leaf_doc import (
    LeafLifecycleRestampBlocked,
    plan_leaf_doc_lifecycle_restamp,
)
from agents_remember.tasks.store import write_task_docs
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
    require_parent_series_accepting_leaves,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
    require_matching_lifecycle_operation_location,
    reserve_new_lifecycle_operation_location,
    resume_new_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.leaf_refs import resolve_leaf_enclosure_contract_for_ref
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.context import resolve_context
from agents_remember.worktrees.modules.git import (
    ensure_worktree,
    longest_tracked_path_length,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    recovery_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.modules.startup.start_contract import (
    build_start_contract,
)
from agents_remember.worktrees.modules.startup.start_memory import (
    prepare_memory_for_start,
)
from agents_remember.worktrees.modules.startup.start_provider_preflight import (
    provider_enablement_state,
)
from agents_remember.worktrees.modules.startup.start_result import (
    StartedWorktreeState,
    started_result,
)
from agents_remember.worktrees.reopen import reopen_required_start_result
from agents_remember.worktrees.services import ProviderSetupRequestSpec, worktree_services
from agents_remember.worktrees.source_lineage import (
    lineage_block_payload,
    lineage_refusal,
    parent_source_lineage,
    source_lineage_for_contract,
)
from agents_remember.worktrees.start_progress import (
    StartBeat,
    StartingEnclosure,
    clear_start_progress,
    write_start_progress,
)
from agents_remember.worktrees.task_fact_publication import (
    contract_projection_scopes,
    publish_task_fact_mutation,
)
from agents_remember.worktrees.task_leaf_binding import (
    TaskLeafBindingError,
    require_current_start_task_binding,
)
from agents_remember.worktrees.task_resolver import resolve_leaf_enclosure_contract
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractError,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    load_contract,
)


@dataclass(frozen=True)
class ProviderStartPaths:
    target_coordination_root: Path
    source_coordination_root: Path
    source_repo_root: Path
    target_repo_root: Path
    source_memory_root: Path | None
    target_memory_root: Path | None
    provider_runtime_root: Path
    provider_settings_path: Path


def load_contract_from_args(args: WorktreeArgs) -> WorktreeContract:
    return load_contract(contract_path_from_args(args))


def contract_path_from_args(args: WorktreeArgs) -> Path:
    """Resolve the canonical enclosure path without requiring readable contract bytes."""

    if args.contract_path is not None:
        return args.contract_path
    context = resolve_context(args)
    if not args.task_name:
        raise RuntimeError("--task-name or --contract-path is required")
    if args.leaf_id:
        contract_path = resolve_leaf_enclosure_contract_for_ref(
            context.coordination_root,
            context.code_repository_name,
            args.task_name,
            leaf_id=args.leaf_id,
            parent_task=args.parent_task,
        )
    else:
        contract_path = resolve_leaf_enclosure_contract(
            context.coordination_root,
            context.code_repository_name,
            args.task_name,
            parent_task=args.parent_task,
        )
    if contract_path is None:
        raise RuntimeError("--task-name resolved no leaf enclosure; pass --leaf-id")
    return contract_path


def status_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract_path = contract_path_from_args(args)
    try:
        contract = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as exc:
        detail = "the canonical worktree contract is unreadable"
        missing = isinstance(exc, FileNotFoundError) or not contract_path.exists()
        return WorktreeCommandResult(
            2,
            {
                "state": "worktree-contract-unreadable",
                "status": "worktree-contract-unreadable",
                "summary": detail,
                "detail": detail,
                "contract_path": contract_path.as_posix(),
                "contractReadFailure": public_failure_evidence(
                    stage="contract-read",
                    side="contract",
                    name=contract_path.name,
                    error_type=type(exc).__name__,
                    observed={"state": "missing" if missing else "unreadable"},
                ),
            },
        )
    return WorktreeCommandResult(0, dict(status_payload(contract)))


def attach_result(args: WorktreeArgs) -> WorktreeCommandResult:
    contract = load_contract_from_args(args)
    try:
        require_matching_lifecycle_operation_location(contract)
    except LifecycleOperationLocationError as error:
        return _location_refusal(error)
    if contract.kind == "series":
        raise RuntimeError(
            "worktree_attach refused: an atomic integration branch is not a resumable workbench"
        )
    require_ordinary_worktree(contract, operation="worktree_attach")
    lineage = source_lineage_for_contract(contract)
    if lineage_refusal(lineage) is not None:
        assert lineage is not None
        return WorktreeCommandResult(
            2,
            {
                **status_payload(contract),
                **lineage_block_payload(lineage),
                "summary": "Attach refused before stale task context was resumed: "
                + lineage.summary,
            },
        )
    return WorktreeCommandResult(
        0, {"state": "attached", "attached": True, **status_payload(contract)}
    )


def _blocked_memory_start_result(
    context, args: WorktreeArgs, code_state: str, memory_state: dict[str, object]
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Code worktree is prepared, but external memory cannot be used until the developer selects a recovery path.",
            **recovery_guidance(
                "choose_memory_recovery",
                tool="worktree_start",
                args={
                    "repo_id": context.code_repository_name,
                    "task_name": args.task_name,
                    "worktree_name": args.worktree_name,
                    "workflow_kind": args.workflow_kind,
                },
                required_args=["memory_choice"],
            ),
            "code_worktree": code_state,
            "memory": memory_state,
        },
    )


def _contract_after_memory_start(
    contract: WorktreeContract, memory_state: dict[str, object]
) -> WorktreeContract:
    if contract.memory_mode == "external" and memory_state["state"] == "disabled":
        return amend_contract(
            replace(
                contract,
                memory_repo_path=None,
                memory_source_branch="",
                memory_work_branch="",
                memory_base_commit="",
                memory_worktree=None,
                ledger_path=None,
                memory_state="disabled",
            ),
            # Through the typed record, like every other vocabulary cell: `memory_state` above
            # is free text and `memory_mode` is not.
            ContractCells(memory_mode="disabled"),
        )
    reconciled_base = memory_state.get("reconciledMemoryBaseCommit")
    if isinstance(reconciled_base, str) and reconciled_base:
        return replace(contract, memory_base_commit=reconciled_base)
    return contract


def _blocked_provider_start_result(
    context,
    args: WorktreeArgs,
    code_state: str,
    memory_state: dict[str, object],
    provider_state: dict[str, object],
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": "blocked",
            "summary": "Worktree provider setup could not be prepared safely.",
            **recovery_guidance(
                "choose_provider_setup_recovery",
                tool="worktree_start",
                args={
                    "repo_id": context.code_repository_name,
                    "task_name": args.task_name,
                    "worktree_name": args.worktree_name,
                    "workflow_kind": args.workflow_kind,
                    "skip_provider_setup": True,
                },
            ),
            "code_worktree": code_state,
            "memory": memory_state,
            "providers": provider_state,
        },
    )


# Margin under the legacy Windows 260-char MAX_PATH so separators, suffixes
# (e.g. memory sidecar ".md"), and tooling scratch names still fit.
WINDOWS_MAX_PATH_BUDGET = 250


def _windows_long_paths_enabled() -> bool:
    if os.name != "nt":
        return True
    import winreg  # noqa: PLC0415 — Windows-only stdlib module

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(value)
    except OSError:
        return False


def long_path_block_payload(
    *,
    label: str,
    worktree_path: str,
    longest_tracked: int,
    budget: int = WINDOWS_MAX_PATH_BUDGET,
) -> dict[str, object] | None:
    """Pure decision: block payload when projected paths exceed the budget."""
    projected = len(worktree_path) + 1 + longest_tracked
    if projected <= budget:
        return None
    excess = projected - budget
    return {
        "state": "blocked",
        "summary": (
            f"Projected {label} worktree paths reach {projected} characters, but this "
            f"Windows host caps paths at 260 (LongPathsEnabled=0; budget {budget}). "
            "Files deeper in the tree would fail to check out or open mid-task."
        ),
        "projectedPathLength": projected,
        "pathBudget": budget,
        "longestTrackedPathLength": longest_tracked,
        "worktreePath": worktree_path,
        "remedies": [
            "Enable Windows long paths (admin): HKLM\\SYSTEM\\CurrentControlSet\\Control"
            "\\FileSystem\\LongPathsEnabled=1, then restart the MCP/harness processes.",
            f"Or choose a worktree name at least {excess} characters shorter.",
        ],
    }


def _long_path_preflight(contract: WorktreeContract) -> dict[str, object] | None:
    if _windows_long_paths_enabled():
        return None
    checks: list[tuple[str, Path, Path, str]] = [
        ("code", contract.code_repo_path, contract.code_worktree, contract.code_source_branch)
    ]
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path
        and contract.memory_worktree
    ):
        checks.append(
            (
                "memory",
                contract.memory_repo_path,
                contract.memory_worktree,
                contract.memory_source_branch or "HEAD",
            )
        )
    for label, repo, worktree, ref in checks:
        payload = long_path_block_payload(
            label=label,
            worktree_path=str(worktree),
            longest_tracked=longest_tracked_path_length(repo, ref),
        )
        if payload is not None:
            return payload
    return None


def _branch_freshness_findings(contract: WorktreeContract) -> list[dict[str, object]]:
    targets: list[tuple[str, Path, str]] = [
        ("code", contract.code_repo_path, contract.code_source_branch)
    ]
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and (contract.memory_repo_path / ".git").exists()
        and contract.memory_source_branch
    ):
        targets.append(("memory", contract.memory_repo_path, contract.memory_source_branch))
    return [
        {"side": side, **freshness_to_packet(read_branch_freshness(repo, branch))}
        for side, repo, branch in targets
    ]


def _stale_base_preflight(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object] | None:
    """Refuse to base a new worktree on a source branch behind its upstream (issue #54).

    Only `behind`/`diverged` block; `unknown` (offline) and `no-upstream` are reported
    by callers via worktree_status freshness, never blocked on. The only local override is
    `proceed-stale`; moving a protected source belongs to its landing plane.
    """
    if args.stale_base_choice == "proceed-stale":
        return None
    stale = [
        finding
        for finding in _branch_freshness_findings(contract)
        if finding["state"] in ("behind", "diverged")
    ]
    if not stale:
        return None
    return {
        "state": "blocked",
        "summary": "Source branches are behind their upstream; a worktree started now "
        "would base on stale code/memory and silently defeat the provider seed "
        "fast-path. Refresh the protected source through its repository landing plane, "
        "then retry, or explicitly choose proceed-stale.",
        **recovery_guidance(
            "choose_stale_base_recovery",
            tool="worktree_start",
            args={
                "repo_id": context.code_repository_name,
                "task_name": args.task_name,
                "worktree_name": args.worktree_name,
                "workflow_kind": args.workflow_kind,
            },
            required_args=["stale_base_choice"],
        ),
        "staleBases": stale,
        "retiredChoices": (
            ["fast-forward moves protected sources outside their landing plane"]
            if args.stale_base_choice == "fast-forward"
            else []
        ),
    }


def _starting_enclosure(contract: WorktreeContract, worktree_name: str) -> StartingEnclosure:
    """The contract's own front-matter facts, for a start that has not written one yet."""
    return StartingEnclosure(
        repo_name=contract.repo_name,
        task_name=contract.task_name,
        worktree_name=worktree_name,
        worktree_group=contract.worktree_group.as_posix(),
        memory_mode=contract.memory_mode,
        code_source_branch=contract.code_source_branch,
        code_base_commit=contract.code_base_commit,
        code_repo_path=contract.code_repo_path.as_posix(),
        code_worktree=contract.code_worktree.as_posix(),
    )


def _record_start_block(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
    beat: StartBeat,
) -> None:
    """Record a pre-contract start block (slice 5e §5.4) so the dashboard can see a start gated
    before its contract exists. Best-effort; skipped on dry runs."""
    if args.dry_run or args.worktree_name is None:
        return
    write_start_progress(
        context.coordination_root,
        _starting_enclosure(contract, args.worktree_name),
        beat,
    )


def _clear_start_block(context, contract: WorktreeContract, args: WorktreeArgs) -> None:
    """The contract now anchors the enclosure; drop the transient start-progress file."""
    if args.worktree_name is None:
        return
    clear_start_progress(context.coordination_root, contract.repo_name, args.worktree_name)


def _record_start_progress(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
    beat: StartBeat,
) -> None:
    """Record a happy-path pre-contract start beat (§9) so the Engine Room can observe the enclosure
    assembling rather than popping in at contract-write. Non-blocked (``blocked_reason`` stays None);
    best-effort; skipped on dry runs."""
    if args.dry_run or args.worktree_name is None:
        return
    write_start_progress(
        context.coordination_root,
        _starting_enclosure(contract, args.worktree_name),
        beat,
    )


def start_result(args: WorktreeArgs) -> WorktreeCommandResult:
    context = resolve_context(args)
    contract = build_start_contract(context, args)
    if isinstance(contract, WorktreeCommandResult):
        return contract
    existing_result = _existing_contract_result(context, contract, args)
    if existing_result is not None:
        return existing_result
    preflighted = _preflighted_contract(context, contract, args)
    if isinstance(preflighted, WorktreeCommandResult):
        return preflighted
    return _create_start_enclosure(context, preflighted, args)


def _existing_contract_result(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult | None:
    """Attach to a live contract at this path instead of recreating its worktrees.

    An abandoned contract is a tombstone and a reopened one (L11) is a reset: either
    way its worktrees/branches are gone, so start must recreate fresh rather than
    attach to a dead binding.
    """
    if not contract.contract_path.exists():
        return None
    existing = load_contract(contract.contract_path)
    if existing.cleanup in ("abandoned", "reopened"):
        return None
    return _active_existing_contract_result(context, existing, args)


def _active_existing_contract_result(
    context,
    existing: WorktreeContract,
    args: WorktreeArgs,
) -> WorktreeCommandResult:
    if existing.cleanup == "completed":
        return reopen_required_start_result(existing)
    location_refusal = _resume_existing_location(existing)
    if location_refusal is not None:
        return location_refusal
    require_ordinary_worktree(existing, operation="worktree_start")
    lineage = source_lineage_for_contract(existing)
    refusal = lineage_refusal(lineage)
    if refusal is not None:
        assert lineage is not None
        return WorktreeCommandResult(2, lineage_block_payload(lineage))
    if args.retry_provider_setup:
        return _retry_provider_setup_result(context, existing, args)
    return WorktreeCommandResult(
        0, {"state": "attached-existing-contract", **status_payload(existing)}
    )


def _resume_existing_location(
    contract: WorktreeContract,
) -> WorktreeCommandResult | None:
    try:
        require_matching_lifecycle_operation_location(contract)
        return None
    except LifecycleOperationLocationError as error:
        if error.status != "operation-location-publication-interrupted":
            return _location_refusal(error)
    try:
        resume_new_lifecycle_operation_location(
            contract,
            contract_text=contract_publication_text(
                contract.contract_path,
                contract,
            ),
        )
    except LifecycleOperationLocationError as error:
        return _location_refusal(error)
    return None


def _preflighted_contract(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeContract | WorktreeCommandResult:
    """Run the pre-creation preflights, returning the blocked result or the usable contract.

    The returned contract remains bound to the source tips from its ordinary build.
    """
    # A dry-run that is about to create a master's first leaf also plans the parent
    # integration contract and branch without publishing either. That virtual parent was
    # built from the protected source's current tip, so asking the ordinary lineage reader
    # to load its deliberately absent contract would turn preview non-mutation into a false
    # unavailable refusal. Only this in-process planned-parent case bypasses the filesystem
    # projection; existing parent contracts still fail closed through the normal reader.
    lineage_block = _parent_lineage_start_block(context, contract, args)
    if lineage_block is not None:
        return lineage_block
    stale_base_block = _stale_base_preflight(context, contract, args)
    if stale_base_block is not None:
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="stale-base-blocked",
                blocked_reason=str(stale_base_block.get("summary", "")),
            ),
        )
        return WorktreeCommandResult(2, stale_base_block)
    require_ordinary_worktree(contract, operation="worktree_start")
    long_path_block = _long_path_preflight(contract)
    if long_path_block is not None:
        return WorktreeCommandResult(2, long_path_block)
    return contract


def _parent_lineage_start_block(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult | None:
    parent_is_planned = (
        args.dry_run
        and bool(contract.parent_task_name)
        and contract.parent_contract_path is not None
        and not contract.parent_contract_path.exists()
    )
    lineage = None if parent_is_planned else parent_source_lineage(contract)
    refusal = lineage_refusal(lineage)
    if refusal is not None:
        assert lineage is not None
        block = lineage_block_payload(lineage)
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="source-lineage-blocked",
                blocked_reason=str(block.get("summary", "")),
            ),
        )
        return WorktreeCommandResult(2, block)
    return None


def _create_start_enclosure(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult:
    """Create the code worktree, prepare memory, write the contract, and set the providers up."""
    _record_start_progress(context, contract, args, StartBeat(phase="preflight"))
    prepared = _prepare_start_enclosure(context, contract, args)
    if isinstance(prepared, WorktreeCommandResult):
        return prepared
    contract = prepared.contract
    projection_effects: list[dict[str, object]] = []
    if not args.dry_run and contract.kind == "leaf" and contract.leaf_id and contract.lifecycle_id:
        prepared_stamp: dict[str, TaskDocument | None] = {"candidate": None}

        def validate_lifecycle_stamp() -> None:
            plan = plan_leaf_doc_lifecycle_restamp(
                contract.task_root,
                contract.leaf_id,
                contract.lifecycle_id,
            )
            if plan.blockers:
                raise LeafLifecycleRestampBlocked(plan)
            prepared_stamp["candidate"] = plan.candidate

        def lifecycle_stamp_scopes():
            candidate = prepared_stamp["candidate"]
            return (
                contract_projection_scopes(contract, (candidate,)) if candidate is not None else ()
            )

        def publish_lifecycle_stamp():
            candidate = prepared_stamp["candidate"]
            return write_task_docs(contract.task_root, [candidate]) if candidate is not None else []

        published = publish_task_fact_mutation(
            contract.coordination_root,
            contract.repo_name,
            validate=validate_lifecycle_stamp,
            projection_scopes=lifecycle_stamp_scopes,
            publication=publish_lifecycle_stamp,
        )
        projection_effects.extend(
            effect.model_dump(by_alias=True) for effect in published.projection_effects
        )
    provider_state = run_or_launch_provider_setup(
        context,
        contract,
        args,
        prepared.provider_plan,
    )
    if provider_state["state"] == "blocked":
        return _blocked_provider_start_result(
            context,
            args,
            prepared.code_state,
            prepared.memory_state,
            provider_state,
        )
    return started_result(
        contract,
        args,
        StartedWorktreeState(prepared.code_state, prepared.memory_state, provider_state),
        projection_effects=projection_effects,
    )


@dataclass(frozen=True)
class _PreparedStartEnclosure:
    contract: WorktreeContract
    code_state: str
    memory_state: dict[str, object]
    provider_plan: dict[str, object]


@dataclass(frozen=True)
class _StartEnclosurePlan:
    contract: WorktreeContract
    memory_preview: dict[str, object]
    provider_plan: dict[str, object]


def _prepare_start_enclosure(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> _PreparedStartEnclosure | WorktreeCommandResult:
    planned = _plan_start_enclosure(context, contract, args)
    if isinstance(planned, WorktreeCommandResult):
        return planned
    if args.dry_run:
        return _preview_start_enclosure(planned)
    return _materialize_start_enclosure(context, contract, args, planned)


def _plan_start_enclosure(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> _StartEnclosurePlan | WorktreeCommandResult:
    lineage_block = _parent_lineage_start_block(context, contract, args)
    if lineage_block is not None:
        return lineage_block
    require_parent_series_accepting_leaves(contract, operation="worktree_start")
    require_ordinary_worktree(contract, operation="worktree_start")
    memory_preview = prepare_memory_for_start(contract, replace(args, dry_run=True))
    if memory_preview["state"] == "blocked":
        return _blocked_memory_start_result(context, args, "not-created", memory_preview)
    planned_contract = _contract_after_memory_start(contract, memory_preview)
    provider_plan = plan_providers_for_start(context, planned_contract, args)
    if provider_plan["state"] == "blocked":
        return _blocked_provider_start_result(
            context,
            args,
            "not-created",
            memory_preview,
            provider_plan,
        )
    return _StartEnclosurePlan(planned_contract, memory_preview, provider_plan)


def _preview_start_enclosure(plan: _StartEnclosurePlan) -> _PreparedStartEnclosure:
    code_state = ensure_worktree(plan.contract, side="code", dry_run=True)
    return _PreparedStartEnclosure(
        plan.contract,
        code_state,
        plan.memory_preview,
        plan.provider_plan,
    )


def _materialize_start_enclosure(
    context,
    contract: WorktreeContract,
    args: WorktreeArgs,
    plan: _StartEnclosurePlan,
) -> _PreparedStartEnclosure | WorktreeCommandResult:
    planned_contract = plan.contract
    publication_text = contract_publication_text(
        planned_contract.contract_path,
        planned_contract,
    )
    try:
        # The short task CAS is the sole start-versus-discard serialization seam. It proves the
        # parent row still exists and reserves the exact address before code, memory, provider, or
        # task-lifecycle writes. The repository landing lock is intentionally absent here.
        with task_publication_lock(
            planned_contract.coordination_root,
            planned_contract.repo_name,
        ):
            require_current_start_task_binding(
                planned_contract.coordination_root,
                planned_contract.repo_name,
                planned_contract.task_root,
                planned_contract.leaf_id,
                task_name=args.task_name,
            )
            predecessor_contract = _restartable_start_predecessor(planned_contract)
            reserve_new_lifecycle_operation_location(
                planned_contract,
                contract_text=publication_text,
                predecessor_contract=predecessor_contract,
            )
    except TaskLeafBindingError as error:
        return _task_start_authority_refusal(error)
    except LifecycleOperationLocationError as error:
        return _location_refusal(error)
    code_state = ensure_worktree(contract, side="code", dry_run=args.dry_run)
    _record_start_progress(
        context,
        contract,
        args,
        StartBeat(phase="code-worktree", completed_phases=("preflight",)),
    )
    memory_state = prepare_memory_for_start(contract, args)
    if memory_state["state"] == "blocked":
        raw_choices = memory_state.get("choices")
        _record_start_block(
            context,
            contract,
            args,
            StartBeat(
                phase="memory-blocked",
                completed_phases=("preflight", "code-worktree"),
                choices=tuple(str(choice) for choice in raw_choices)
                if isinstance(raw_choices, list)
                else (),
                blocked_reason=str(memory_state.get("reason", "")),
            ),
        )
        return _blocked_memory_start_result(context, args, code_state, memory_state)
    materialized_contract = _contract_after_memory_start(contract, memory_state)
    if materialized_contract != planned_contract:
        return WorktreeCommandResult(
            2,
            {
                "state": "start-reservation-contract-conflict",
                "status": "start-reservation-contract-conflict",
                "summary": (
                    "memory preparation changed the exact contract after its start address was "
                    "reserved; recover this reserved start instead of creating another enclosure"
                ),
                "expectedContract": publication_text,
                "observedContract": contract_publication_text(
                    materialized_contract.contract_path,
                    materialized_contract,
                ),
                "nextAction": "recover-start-publication",
                "nextTool": "worktree_start",
                "nextArgs": {
                    "repo_id": planned_contract.repo_name,
                    "task_name": args.task_name,
                    "leaf_id": planned_contract.leaf_id,
                },
            },
        )
    try:
        resume_new_lifecycle_operation_location(
            planned_contract,
            contract_text=publication_text,
        )
    except LifecycleOperationLocationError as error:
        return _location_refusal(error)
    _clear_start_block(context, planned_contract, args)
    return _PreparedStartEnclosure(
        planned_contract,
        code_state,
        memory_state,
        plan.provider_plan,
    )


def _restartable_start_predecessor(
    planned_contract: WorktreeContract,
) -> WorktreeContract | None:
    """Read the exact tombstone that may authorize one successor locator generation."""

    if not planned_contract.contract_path.exists():
        return None
    existing = load_contract(planned_contract.contract_path)
    return existing if existing.cleanup in {"abandoned", "reopened"} else None


def _task_start_authority_refusal(error: TaskLeafBindingError) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": error.status,
            "status": error.status,
            "summary": error.detail,
            "detail": error.detail,
            "nextAction": "re-read-task-authority",
            "nextTool": "task_doc",
            "nextArgs": {"operation": "get"},
        },
    )


def _location_refusal(error: LifecycleOperationLocationError) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        {
            "state": error.status,
            "status": error.status,
            "summary": error.detail,
            "detail": error.detail,
            "expected": error.expected,
            "observed": error.observed,
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": error.detail,
        },
    )


def prepare_providers_for_start(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object]:
    """Preserve the facade while start_result writes the contract between both halves."""
    plan = plan_providers_for_start(context, contract, args)
    if plan["state"] != "enabled":
        return plan
    return run_or_launch_provider_setup(context, contract, args, plan)


def plan_providers_for_start(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> dict[str, object]:
    """Synchronous preflight: skip/enablement/settings checks, no execution.

    Config-level failures still block the start fast; only the long-running
    setup execution moves to the background launch.
    """
    skipped = _provider_setup_skip_state(args)
    if skipped:
        return skipped
    paths = _provider_start_paths(context, contract, args)
    provider_state = provider_enablement_state(
        paths.target_coordination_root,
        paths.provider_settings_path,
        target_memory_root=paths.target_memory_root,
    )
    if provider_state["state"] != "enabled":
        return provider_state
    return {**provider_state, "paths": paths}


def run_or_launch_provider_setup(
    context, contract: WorktreeContract, args: WorktreeArgs, plan: dict[str, object]
) -> dict[str, object]:
    """Dry runs stay synchronous; real setup launches on a background thread."""
    if plan["state"] != "enabled":
        return plan
    paths = plan["paths"]
    assert isinstance(paths, ProviderStartPaths)
    spec = _provider_setup_request(context, args, paths)
    request = worktree_services().provider_lifecycle.setup_request(spec=spec)
    if args.dry_run:
        payload = worktree_services().provider_lifecycle.run_setup(request)
        if not payload.get("ok"):
            return {
                "state": "blocked",
                "reason": "provider setup failed",
                "payload": payload,
            }
        state_file = _write_provider_state_file(contract, payload, dry_run=True)
        return {
            "state": "planned",
            "payload": payload,
            "provider_state_file": state_file.as_posix(),
        }
    setup_config = args.provider_setup_config
    cleanup = (
        paths.provider_settings_path
        if setup_config is not None and setup_config.unlink_settings_after_setup
        else None
    )
    return worktree_services().provider_lifecycle.launch_setup(
        request=request,
        contract=contract,
        write_state_file=lambda payload: _write_provider_state_file(
            contract, payload, dry_run=False
        ),
        settings_cleanup=cleanup,
    )


def _retry_provider_setup_result(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> WorktreeCommandResult:
    if worktree_services().provider_lifecycle.setup_running(contract):
        return WorktreeCommandResult(
            2,
            {
                "state": "blocked",
                "summary": (
                    "Provider setup is still running for this worktree (fresh "
                    "heartbeat); poll worktree_status instead of retrying."
                ),
                "providers": worktree_services().provider_lifecycle.setup_status(contract),
                **next_guidance(
                    "continue_work",
                    tool="worktree_status",
                    args=contract_next_args(contract),
                ),
            },
        )
    provider_plan = plan_providers_for_start(context, contract, args)
    if provider_plan["state"] == "blocked":
        return WorktreeCommandResult(
            2, {**status_payload(contract), "state": "blocked", "providers": provider_plan}
        )
    provider_state = run_or_launch_provider_setup(context, contract, args, provider_plan)
    return WorktreeCommandResult(
        0,
        {
            **status_payload(contract),
            "state": "provider-setup-retried",
            "providers": provider_state,
        },
    )


def _write_provider_state_file(
    contract: WorktreeContract,
    payload: dict[str, object],
    dry_run: bool,
) -> Path:
    state_file = contract.worktree_group / "provider-runtime" / "provider-state.json"
    if dry_run:
        return state_file
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(_provider_state_payload(contract, payload), indent=2) + "\n",
        encoding="utf-8",
    )
    return state_file


def _provider_state_payload(
    contract: WorktreeContract,
    payload: dict[str, object],
) -> dict[str, object]:
    isolated = payload.get("isolatedProviderSettings")
    return {
        "schema": "ar-worktree-provider-state/v1",
        "taskName": contract.task_name,
        "repoName": contract.repo_name,
        "worktreeGroup": contract.worktree_group.as_posix(),
        "codeWorktree": contract.code_worktree.as_posix(),
        "memoryWorktree": contract.memory_worktree.as_posix()
        if contract.memory_worktree is not None
        else None,
        "isolatedProviderSettings": isolated,
    }


def _provider_setup_skip_state(args: WorktreeArgs) -> dict[str, object] | None:
    if args.skip_provider_setup:
        return {"state": "skipped", "reason": "provider setup was skipped"}
    if getattr(args, "provider_setup_config", None) is None:
        return {
            "state": "skipped",
            "reason": "provider setup requires MCP-derived provider settings",
        }
    return None


def _provider_start_paths(
    context, contract: WorktreeContract, args: WorktreeArgs
) -> ProviderStartPaths:
    setup_config = args.provider_setup_config
    assert setup_config is not None
    return ProviderStartPaths(
        target_coordination_root=setup_config.coordination_root.resolve(),
        source_coordination_root=(
            setup_config.seed_source_coordination_root or context.coordination_root
        ).resolve(),
        source_repo_root=context.code_repository_root.resolve(),
        target_repo_root=contract.code_worktree.resolve(),
        source_memory_root=context.memory_root.resolve()
        if getattr(context, "memory_root", None) is not None
        else None,
        target_memory_root=_grepai_target_memory_root(contract),
        provider_runtime_root=(contract.worktree_group / "provider-runtime").resolve(),
        provider_settings_path=setup_config.settings_path.resolve(),
    )


def _grepai_target_memory_root(contract: WorktreeContract) -> Path | None:
    if contract.memory_mode == "external":
        return contract.memory_worktree.resolve() if contract.memory_worktree is not None else None
    if contract.memory_mode == "internal":
        return (contract.code_worktree / "ar-memory").resolve()
    return None


def _provider_setup_request(
    context,
    args: WorktreeArgs,
    paths: ProviderStartPaths,
):
    lifecycle = worktree_services().provider_lifecycle
    return ProviderSetupRequestSpec(
        action="prepare",
        coordination_root=paths.target_coordination_root,
        settings_path=lifecycle.settings_path(paths.provider_settings_path),
        timeout=getattr(args, "provider_timeout", 1800),
        dry_run=args.dry_run,
        skip_grepai=paths.target_memory_root is None,
        cgc_seed=lifecycle.cgc_seed_options(
            source_coordination_root=paths.source_coordination_root,
            repo_id=context.code_repository_name,
            source_repo_root=paths.source_repo_root,
            target_repo_root=paths.target_repo_root,
        ),
        cgc_isolated=lifecycle.isolated_cgc_options(runtime_root=paths.provider_runtime_root),
        grepai_seed=lifecycle.grepai_seed_options(
            source_coordination_root=paths.source_coordination_root,
            source_settings_path=paths.provider_settings_path,
            project_id=context.code_repository_name,
            target_memory_root=paths.target_memory_root,
        ),
        grepai_isolated=lifecycle.isolated_grepai_options(
            runtime_root=paths.provider_runtime_root,
            project_id=context.code_repository_name,
            target_memory_root=paths.target_memory_root,
            allow_missing_roots=args.dry_run,
        ),
    )
