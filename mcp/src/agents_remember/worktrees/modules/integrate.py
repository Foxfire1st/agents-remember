from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.controlplane.enforcement import GateGuard, evaluate_gate
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.gate_policy import (
    GatePolicy,
)
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationRecord,
)
from agents_remember.worktrees.integration.atomic_series_landing import (
    AtomicLandingBlocked,
    require_atomic_landing_authority,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    integration_targets,
    require_ordinary_worktree,
    require_series_contract_authority,
)
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationRefRace,
    IntegrationSources,
    LandingAdmission,
    merge_integrated_commits,
    prepare_integration_ref_move,
    require_integrated_ledger_mapping,
)
from agents_remember.worktrees.integration.integration_resolution_handoff import (
    integration_resolution_required,
)
from agents_remember.worktrees.integration.master_review_gate import (
    blocked_integration_payload,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.git import (
    branch_commit,
    current_branch,
    head_commit,
    is_ancestor,
    require_clean,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.integration_preflight_results import (
    atomic_landing_blocked_result,
)
from agents_remember.worktrees.modules.integration_publication import (
    IntegratePreview,
    IntegrationPublication,
)
from agents_remember.worktrees.modules.landing_record import (
    LandedIntegration,
    record_landed_integration,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.series_closeout import (
    SeriesCheckpointRefs,
    atomic_series_ledger_prefix,
    capture_series_checkpoint_refs,
    publish_series_checkpoint_under_authority,
    publish_series_integration_under_authority,
    require_series_checkpoint_authority,
)
from agents_remember.worktrees.source_lineage import (
    lineage_block_payload,
    lineage_refusal,
    source_lineage_for_contract,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
)


def handover_gate_guard(
    gates: Mapping[str, GateRecord],
    *,
    task_name: str,
    parent_task_name: str,
    policy: GatePolicy,
) -> GateGuard:
    """The master-exit seam verdict for one integrating contract. Pure.

    The handover gate carries the MASTER identity: the manager raises it with
    ``enclosure=<master task name>`` on its own (worktree-less) lifecycle, while
    the master -> super integration runs on the orchestrator's integration
    worktree -- a different lifecycle -- so the fold must be cross-lifecycle
    (:meth:`GateStore.all_current`) and the address is the contract's master or
    series name, never ``contract.lifecycle_id``. Only gates whose ``enclosure``
    matches the contract's ``task_name`` or ``parent_task_name`` govern; the
    latest matching snapshot decides via :func:`evaluate_gate` (open or
    policy-invalid blocks). Gateless stays additive: with no matching gate the
    existing approval channel governs.
    """
    addresses = {name for name in (task_name, parent_task_name) if name}
    matching = {
        gate_id: gate
        for gate_id, gate in gates.items()
        if gate.kind == HANDOVER_GATE_KIND and gate.enclosure in addresses
    }
    return evaluate_gate(matching, kind=HANDOVER_GATE_KIND, policy=policy)


def unmatched_handover_gate_warning(
    gates: Mapping[str, GateRecord],
    *,
    task_name: str,
    parent_task_name: str,
) -> dict[str, object] | None:
    """The enclosure spelling-check for a gateless integrate. Pure.

    The seam address is an exact-string convention (``enclosure`` = master task
    name), so a mis-spelled address yields a gate :func:`handover_gate_guard`
    can never match -- gateless-permitted, silently. When NO gate in the fold
    addresses this contract but open master-handover-approval gates do exist,
    integration still proceeds (gateless stays additive -- another master's
    open gate is legitimate) and the result payload carries this warning, so a
    mis-addressed gate is loud at the exact moment it would have mattered.
    With a matching gate (any state) the address worked and other masters'
    in-flight gates are not worth a warning: ``None``.
    """
    addresses = {name for name in (task_name, parent_task_name) if name}
    handover_gates = [gate for gate in gates.values() if gate.kind == HANDOVER_GATE_KIND]
    if any(gate.enclosure in addresses for gate in handover_gates):
        return None
    unmatched = sorted(
        (gate for gate in handover_gates if gate.state == "open"),
        key=lambda gate: gate.id,
    )
    if not unmatched:
        return None
    return {
        "unmatched_open_gates": [
            {"gateId": gate.id, "enclosure": gate.enclosure} for gate in unmatched
        ],
        "note": (
            "open master-handover-approval gates exist but none address this master "
            "(task_name/parent_task_name); verify the enclosure spelling"
        ),
    }


def _unclosed_integration_refusal(contract: WorktreeContract) -> str:
    """The refusal an unclosed task meets, and the route an unfinished master can take instead.

    A leaf has exactly one way to be integrated and this is its prerequisite, so naming an
    alternative would repeat the defect this leaf fixed in the ledger refusal's remedy: a message
    that points at something the caller cannot run. A SERIES has a second verb --
    ``worktree_checkpoint_landing`` lands the accumulated line and leaves the master open -- so the
    operator who reaches for the obvious one is told about the one that works.
    """

    refusal = "integration requires closeout.status completed"
    if contract.kind == "series":
        return (
            f"{refusal}; a master that is still open can instead be landed with "
            "worktree_checkpoint_landing, which checkpoints it without closing it out"
        )
    return refusal


def validate_integrate_contract(contract: WorktreeContract) -> None:
    if contract.closeout_status != "completed":
        raise RuntimeError(_unclosed_integration_refusal(contract))
    if not contract.approved_for_commit:
        raise RuntimeError("integration requires approved closeout")
    if not contract.code_commit:
        raise RuntimeError("integration requires closeout code_commit")
    if contract.kind == "series":
        if (
            branch_commit(contract.code_repo_path, contract.code_work_branch)
            != contract.code_commit
        ):
            raise RuntimeError("atomic code ref does not match closeout code_commit")
    else:
        if not contract.code_worktree.exists():
            raise RuntimeError(f"code worktree does not exist: {contract.code_worktree}")
        if current_branch(contract.code_worktree) != contract.code_work_branch:
            raise RuntimeError(f"code worktree must have {contract.code_work_branch} checked out")
        require_clean(contract.code_worktree, "code worktree")
        if head_commit(contract.code_worktree) != contract.code_commit:
            raise RuntimeError("code worktree HEAD does not match closeout code_commit")
    if contract.memory_mode == "external":
        validate_integrate_memory_contract(contract)


def validate_integrate_memory_contract(contract: WorktreeContract) -> None:
    if contract.memory_repo_path is None or contract.ledger_path is None:
        raise RuntimeError("external-memory integration requires memory repo and ledger path")
    if not contract.memory_content_commit or not contract.ledger_commit:
        raise RuntimeError(
            "external-memory integration requires closeout memory_content_commit and ledger_commit"
        )
    if contract.kind == "series":
        if (
            branch_commit(contract.memory_repo_path, contract.memory_work_branch)
            != contract.ledger_commit
        ):
            raise RuntimeError("atomic memory ref does not match closeout ledger_commit")
        return
    if contract.memory_worktree is None:
        raise RuntimeError("external-memory leaf integration requires a memory worktree")
    if current_branch(contract.memory_worktree) != contract.memory_work_branch:
        raise RuntimeError(f"memory worktree must have {contract.memory_work_branch} checked out")
    require_clean(contract.memory_worktree, "memory worktree")
    if head_commit(contract.memory_worktree) != contract.ledger_commit:
        raise RuntimeError("memory worktree HEAD does not match closeout ledger_commit")


def _integration_lineage_block(
    contract: WorktreeContract, *, persist: bool
) -> WorktreeCommandResult | None:
    projection = source_lineage_for_contract(contract)
    refusal = lineage_refusal(projection)
    if refusal is None:
        return None
    assert projection is not None
    status, reason = refusal
    recovery = lineage_block_payload(projection)
    recovery.pop("state", None)
    recovery.pop("summary", None)
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            status,
            f"integration requires current transitive source lineage: {reason}",
            persist=persist,
            developer_decision_required=False,
            **recovery,
        ),
    )


def _integration_sources_moved_block(
    contract: WorktreeContract, sources: IntegrationSources
) -> WorktreeCommandResult | None:
    current_code = branch_commit(contract.code_repo_path, contract.code_source_branch)
    moved = current_code != sources.current_code_source
    current_memory = ""
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        current_memory = branch_commit(contract.memory_repo_path, contract.memory_source_branch)
        moved = moved or current_memory != sources.current_memory_source
    if not moved:
        return None
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            "source-moved-during-preflight",
            "integration source branches moved during transaction admission; retry from "
            "preflight so the combined candidate is bound to the new tips",
            developer_decision_required=False,
            nextOperation="request_integration_decision",
            nextTool="worktree_integrate",
            nextArgs={"contract_path": contract.contract_path.as_posix(), "dry_run": True},
        ),
    )


def _integration_source_state_block(
    contract: WorktreeContract, sources: IntegrationSources
) -> WorktreeCommandResult | None:
    """Re-prove transitive ancestry and the exact source-tip snapshot."""
    return _integration_sources_moved_block(contract, sources) or _integration_lineage_block(
        contract, persist=True
    )


def _integration_replay_requirements(contract: WorktreeContract) -> IntegrationSources:
    """The source state for the routes whose candidate is the contract's closeout cell."""

    return _replay_requirements(contract, contract.code_commit, contract.ledger_commit)


def _replay_requirements(
    contract: WorktreeContract,
    code_commit: str,
    ledger_commit: str,
) -> IntegrationSources:
    """One exact reading of both sources and their replay verdicts for one candidate.

    The candidate is a parameter, not a contract read: the routes differ in *where* their
    candidate comes from (the closeout cell for a finished task, the checkpoint's own live
    capture for an unfinished one) and not in how its ancestry is judged.
    """

    current_code_source = branch_commit(contract.code_repo_path, contract.code_source_branch)
    current_memory_source = ""
    code_replay_required = not is_ancestor(
        contract.code_repo_path, current_code_source, code_commit
    )
    memory_replay_required = False
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        current_memory_source = branch_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        memory_replay_required = not is_ancestor(
            contract.memory_repo_path, current_memory_source, ledger_commit
        )
    return IntegrationSources(
        current_code_source=current_code_source,
        current_memory_source=current_memory_source,
        code_replay_required=code_replay_required,
        memory_replay_required=memory_replay_required,
    )


def _blocked_non_ff_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
) -> WorktreeCommandResult:
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            "blocked-non-ff",
            "source branch moved; run worktree_sync for this contract, settle any retained "
            "code or memory conflict -- re-running the targeted test utility after code "
            "resolutions -- then re-run the closeout before retrying the integration",
            persist=not args.dry_run,
            code_replay_required=sources.code_replay_required,
            memory_replay_required=sources.memory_replay_required,
        ),
    )


def _dry_run_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    *,
    preview: IntegratePreview,
) -> WorktreeCommandResult:
    # The preview EVALUATES (never enforces) the seam guard, so the c-09-mandated
    # dry_run preflight cannot promise "would-integrate" and then have the real run
    # refuse with handover-gate-blocked. Nothing on this path persists a contract
    # mutation.
    summary = (
        "Dry run completed; integration preflight can proceed with the selected strategy."
        if preview.guard.permitted
        else "Dry run completed; the real run would refuse with handover-gate-blocked — "
        "decide the addressed master-handover-approval gate first."
    )
    payload: dict[str, object] = {
        "state": "would-integrate",
        **status_payload(contract),
        "summary": summary,
        **next_guidance(
            "request_integration_decision",
            tool="worktree_integrate",
            args=contract_next_args(
                contract,
                strategy=args.strategy,
                ledger_commit_message=args.ledger_commit_message,
                dry_run=False,
            ),
        ),
        "strategy": args.strategy,
        "code_replay_required": sources.code_replay_required,
        "memory_replay_required": sources.memory_replay_required,
        "handover_gate": {
            "permitted": preview.guard.permitted,
            "gateId": preview.guard.gate_id,
            "reason": preview.guard.reason,
        },
        "cleanup_reminder": (
            "On apply, the integration lands the refs; the code and memory worktrees are "
            "reclaimed when the task edge is finalized."
        ),
    }
    if preview.handover_warning is not None:
        payload["handover_gate_warning"] = preview.handover_warning
    return WorktreeCommandResult(0, payload)


@dataclass(frozen=True)
class CheckpointLanding:
    """Everything one checkpoint preflight proved: the captured refs and the source state.

    This value is the checkpoint's eligibility record, computed once and shared by the preview and
    the apply, so the two surfaces cannot disagree about *why* a partial landing is allowed. The ledger
    projection proof is not part of it: that one proof is owed by both routes and is evaluated at
    the preview/apply seam for both of them. What this value does own is the candidate the
    publication gate revalidates against the live tips before any ref moves.
    """

    refs: SeriesCheckpointRefs
    commits: IntegratedCommits
    sources: IntegrationSources

    def eligibility(self, contract: WorktreeContract) -> dict[str, object]:
        """The conditions this checkpoint satisfied, in the vocabulary the refusals use."""

        return {
            "codeCandidate": self.refs.code_commit,
            "memoryContentCandidate": self.refs.memory_content_commit,
            "ledgerCandidate": self.refs.ledger_commit,
            "ledgerMappingVerified": True,
            "codeWorkBranch": contract.code_work_branch,
            "codeSourceBranch": contract.code_source_branch,
            "codeSourceCommit": self.sources.current_code_source,
            "memoryWorkBranch": contract.memory_work_branch,
            "memorySourceBranch": contract.memory_source_branch,
            "memorySourceCommit": self.sources.current_memory_source,
            "codeReplayRequired": self.sources.code_replay_required,
            "memoryReplayRequired": self.sources.memory_replay_required,
            "closeoutRequired": False,
            "approvalRequired": True,
        }


def checkpoint_landing_eligibility(contract: WorktreeContract) -> CheckpointLanding:
    """Capture the checkpoint's own candidate and prove the refs it will land.

    This is the ONLY eligibility decision the checkpoint route makes, and the preview above and
    the apply below both read it from here. It replaces the ordinary route's closeout cells
    (``closeout_status``, ``approved_for_commit``, the recorded commit triple) -- the state this
    route exists to make reachable is exactly their absence -- with the checkpoint's own live
    capture. Every other condition is unchanged and is proven either here or on the shared path
    the two routes still walk: the series contract binding, the integration targets, the atomic
    landing authority, the source-lineage proof, the replay/ff source-state gate, the
    master-handover gate, and the compare-and-swap.

    The two ref-shape checks mirror the series arm of :func:`validate_integrate_contract`
    exactly; only the authoritative value differs, and it is the captured one rather than a
    stale contract cell.
    """

    if contract.kind != "series":
        raise RuntimeError(
            "checkpoint landing is defined only for an atomic series contract; a leaf lands "
            "through worktree_integrate"
        )
    require_series_checkpoint_authority(contract)
    refs = capture_series_checkpoint_refs(contract)
    if contract.memory_mode == "external":
        if not refs.memory_content_commit or not refs.ledger_commit:
            raise RuntimeError(
                "external-memory checkpoint landing requires the captured memory and ledger refs"
            )
        assert contract.memory_repo_path is not None
        if (
            branch_commit(contract.memory_repo_path, contract.memory_work_branch)
            != refs.ledger_commit
        ):
            raise RuntimeError("atomic memory ref does not match the captured checkpoint ledger")
    if branch_commit(contract.code_repo_path, contract.code_work_branch) != refs.code_commit:
        raise RuntimeError("atomic code ref does not match the captured checkpoint candidate")
    return CheckpointLanding(
        refs=refs,
        commits=IntegratedCommits(
            code=refs.code_commit,
            memory_content=refs.memory_content_commit,
            ledger=refs.ledger_commit,
        ),
        sources=_replay_requirements(contract, refs.code_commit, refs.ledger_commit),
    )


def _landing_admission(
    contract: WorktreeContract, *, checkpoint: CheckpointLanding | None
) -> LandingAdmission:
    """The ledger-admission facts for one route, derived in exactly one place.

    Both the earlier preview-side proof and the protected-boundary proof read this, so the two
    cannot disagree about which history form the route owes -- including which route may read the
    completion-census leaf-chain prefix at all.
    """

    return LandingAdmission(
        # The leaf-chain ledger prefix is a completion census, so it is read only for the route
        # that requires completion. An unfinished master has no finished chain to prefix against; its
        # ledger is proven as the projection of its own source instead.
        expected_series_ledger_prefix=(
            atomic_series_ledger_prefix(contract)
            if contract.kind == "series"
            and contract.memory_mode == "external"
            and checkpoint is None
            else ()
        ),
        checkpoint_candidate=None if checkpoint is None else checkpoint.commits,
    )


def _require_ledger_projection(
    contract: WorktreeContract,
    commits: IntegratedCommits,
    sources: IntegrationSources,
    admission: LandingAdmission,
) -> None:
    """Prove the ledger these commits land is its own projection, before any surface promises.

    This is the same proof the protected-ref transaction runs, on the same arguments, evaluated
    earlier so a dry run cannot promise a landing the apply will refuse. The external-memory guard
    is the transaction's: an internal-memory contract has no ledger to prove.

    COST: on the apply path the proof now runs twice -- here, and again at the protected boundary
    inside :func:`prepare_integration_ref_move`. The second read stays authoritative because it is
    re-taken under the transaction, immediately before the irreversible ref move; this one exists
    only so the preview refuses exactly what the apply refuses.
    """

    if contract.memory_mode != "external":
        return
    require_integrated_ledger_mapping(
        contract,
        commits,
        memory_source_commit=sources.current_memory_source,
        expected_series_prefix=admission.expected_series_ledger_prefix,
        checkpoint=admission.checkpoint_candidate is not None,
    )


def _route_commits(
    contract: WorktreeContract, checkpoint: CheckpointLanding | None
) -> IntegratedCommits:
    """The commits this route will land: the checkpoint's captured refs, or the closeout cells."""

    if checkpoint is not None:
        return checkpoint.commits
    return IntegratedCommits(
        code=contract.code_commit,
        memory_content=contract.memory_content_commit,
        ledger=contract.ledger_commit,
    )


def _checkpoint_dry_run_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    landing: CheckpointLanding,
    *,
    preview: IntegratePreview,
) -> WorktreeCommandResult:
    """The checkpoint's preview: the same eligibility the apply enforces, and nothing moved."""

    summary = (
        "Dry run completed; this unfinished master can be checkpointed with the selected strategy."
        if preview.guard.permitted
        else "Dry run completed; the real run would refuse with handover-gate-blocked — "
        "decide the addressed master-handover-approval gate first."
    )
    payload: dict[str, object] = {
        "state": "would-checkpoint",
        **status_payload(contract),
        "summary": summary,
        "eligibility": landing.eligibility(contract),
        **next_guidance(
            "request_integration_decision",
            tool="worktree_checkpoint_landing",
            args=contract_next_args(
                contract,
                strategy=args.strategy,
                ledger_commit_message=args.ledger_commit_message,
                dry_run=False,
            ),
        ),
        "strategy": args.strategy,
        "code_replay_required": landing.sources.code_replay_required,
        "memory_replay_required": landing.sources.memory_replay_required,
        "handover_gate": {
            "permitted": preview.guard.permitted,
            "gateId": preview.guard.gate_id,
            "reason": preview.guard.reason,
        },
        "cleanup_reminder": (
            "On apply, the checkpoint lands the refs and retires nothing: the master keeps its "
            "worktrees, its branches and its enclosure for the work that continues."
        ),
    }
    if preview.handover_warning is not None:
        payload["handover_gate_warning"] = preview.handover_warning
    return WorktreeCommandResult(0, payload)


def _integrated_code_commit(
    contract: WorktreeContract, current_code_source: str
) -> tuple[str, dict[str, object] | None]:
    integrated_code_commit = contract.code_commit
    if not is_ancestor(contract.code_repo_path, current_code_source, integrated_code_commit):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code source branch"
        )
    return integrated_code_commit, None


def _integrated_memory_commits(
    contract: WorktreeContract,
    current_memory_source: str,
) -> tuple[str, str, dict[str, object] | None]:
    integrated_memory_content_commit = contract.memory_content_commit
    integrated_ledger_commit = contract.ledger_commit
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        if not is_ancestor(
            contract.memory_repo_path, current_memory_source, integrated_ledger_commit
        ):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current memory source branch"
            )
    return integrated_memory_content_commit, integrated_ledger_commit, None


def _integrated_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    commits: IntegratedCommits,
    *,
    handover_warning: dict[str, object] | None,
) -> WorktreeCommandResult:
    record_landed_integration(
        contract,
        landed=LandedIntegration(
            strategy=args.strategy,
            code_commit=commits.code,
            memory_content_commit=commits.memory_content,
            ledger_commit=commits.ledger,
        ),
    )
    # Landing publishes the integration cell and stops there. Reclamation belongs to
    # ``lifecycle_finalize_task``, which runs the same terminal cleanup procedure and then
    # reconciles the leaf document and its master row; doing it here would complete the
    # enclosure before the edge that is supposed to finalize it could ever be reached.
    observed = load_contract(contract.contract_path)
    payload: dict[str, object] = {
        "state": "integrated",
        **status_payload(observed),
        "summary": (
            "Integration completed; the refs are landed and the code and memory worktrees "
            "are reclaimed when the task edge is finalized."
        ),
        "strategy": args.strategy,
        "integrated_code_commit": commits.code,
        "integrated_memory_content_commit": commits.memory_content,
        "integrated_ledger_commit": commits.ledger,
    }
    if handover_warning is not None:
        payload["handover_gate_warning"] = handover_warning
    return WorktreeCommandResult(0, payload)


def integrate_result(
    args: WorktreeArgs,
    current_contract: WorktreeContract,
) -> WorktreeCommandResult:
    report_operation_progress(args, "preflight", current_command="validate integration eligibility")
    if not args.approved and not args.dry_run:
        raise RuntimeError("integration requires explicit developer approval")
    assert args.contract_path is not None
    contract = current_contract
    if args.contract_path.resolve() != contract.contract_path.resolve():
        raise RuntimeError("integration contract path does not match the passed current contract")
    if contract.kind == "series":
        require_series_contract_authority(contract, operation="worktree_integrate")
    else:
        require_ordinary_worktree(contract, operation="worktree_integrate")
    integration_targets(contract)
    validate_integrate_contract(contract)
    # THE ONE INTEGRATION RULE, read from Git: the leaf's source branch must not
    # have moved since the candidate was verified. code_replay_required and
    # memory_replay_required are is_ancestor reads of the live source tip against
    # the candidate commit; a source that moved off that ancestry blocks the
    # integration in _blocked_non_ff_result.
    sources = _integration_replay_requirements(contract)
    return _continue_integration(contract, args, sources, None)


def checkpoint_landing_result(
    args: WorktreeArgs,
    current_contract: WorktreeContract,
) -> WorktreeCommandResult:
    """Land an unfinished atomic master's accumulated line into its super branch.

    :func:`integrate_result` closes a finished master; this lands an unfinished one without closing it. It shares the
    entire preflight and the ref move with that route -- the series contract binding, the atomic
    landing authority, the integration targets, the replay/ff source-state gate, the source-lineage
    proof and the master-handover gate all still run -- and differs in exactly two ways: it captures
    its own committed refs instead of reading a closeout cell it cannot have, and it records
    ``checkpointed`` instead of ``completed``.

    The closeout exemption is *only* the closeout cells. A master being landed before completion by definition has
    never closed out -- on LOCR that is the state the previous route made unreachable -- so
    demanding ``closeout_status == "completed"`` was demanding the outcome of an operation this one
    exists to make possible. The developer approval channel (``args.approved``), the ancestry proof
    and every ref guard are untouched.
    """

    report_operation_progress(args, "preflight", current_command="validate checkpoint eligibility")
    if not args.approved and not args.dry_run:
        raise RuntimeError("checkpoint landing requires explicit developer approval")
    assert args.contract_path is not None
    contract = current_contract
    if args.contract_path.resolve() != contract.contract_path.resolve():
        raise RuntimeError("checkpoint contract path does not match the passed current contract")
    require_series_contract_authority(contract, operation="worktree_checkpoint_landing")
    integration_targets(contract)
    landing = checkpoint_landing_eligibility(contract)
    # A checkpoint is still a landing, so it earns the same Git-read protection as a final one: the
    # recorded source branch must not have moved off the ancestry the candidate was verified at.
    return _continue_integration(contract, args, landing.sources, None, checkpoint=landing)


def _continue_integration(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    operation: LifecycleOperationRecord | None,
    *,
    checkpoint: CheckpointLanding | None = None,
) -> WorktreeCommandResult:
    if args.strategy == "ff-only" and sources.replay_required:
        return _blocked_non_ff_result(contract, args, sources)
    if args.strategy == "replay" and sources.replay_required:
        return integration_resolution_required(contract, args, sources, operation)
    lineage_block = _integration_lineage_block(contract, persist=not args.dry_run)
    if lineage_block is not None:
        return lineage_block
    return _handover_or_apply_integration(contract, args, sources, checkpoint=checkpoint)


HANDOVER_GATE_KIND = "master-handover-approval"


def _handover_or_apply_integration(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    *,
    checkpoint: CheckpointLanding | None = None,
) -> WorktreeCommandResult:
    # The master-exit seam consumer (mirror of the closeout gate): when a
    # master-handover-approval gate is addressed to this contract's master or
    # series (its `enclosure`), only a policy-valid approval lets the
    # integration proceed. The fold is cross-lifecycle because the raiser
    # (the manager) and the integrator anchor different lifecycles.
    # Gateless stays additive. The guard is EVALUATED for both runs — the
    # dry-run preview reports it instead of enforcing it — and the
    # unmatched-open-gate warning keeps a mis-addressed enclosure (an exact
    # string that would otherwise fail open) loud on the result payload.
    gate_store = GateStore(observer_logs_root(contract.coordination_root))
    gate_fold = gate_store.all_current()
    guard = handover_gate_guard(
        gate_fold,
        task_name=contract.task_name,
        parent_task_name=contract.parent_task_name,
        policy=args.gate_policy,
    )
    handover_warning = unmatched_handover_gate_warning(
        gate_fold,
        task_name=contract.task_name,
        parent_task_name=contract.parent_task_name,
    )
    if not args.dry_run and not guard.permitted:
        summary = (
            "Integration is blocked by the addressed master-handover-approval gate; "
            "inspect the structural gate and decide it before rerunning integration."
        )
        guidance = {
            "nextOperation": "review_handover_gate",
            "nextTool": "gate_list",
            "nextArgs": {},
        }
        return WorktreeCommandResult(
            2,
            {
                "state": "handover-gate-blocked",
                "gateId": guard.gate_id,
                "reason": guard.reason,
                **status_payload(contract),
                "summary": summary,
                **guidance,
                "nextStep": {"summary": summary, **guidance},
            },
        )

    # BOTH routes prove the ledger they are about to land before either surface promises a result,
    # so a dry run cannot say "would-integrate"/"would-checkpoint" and have the apply refuse on a
    # gate the preview never surfaced. It runs here -- after the replay/ff gate and the lineage
    # block -- so those keep returning their own payload and remedy rather than being replaced by a
    # ledger refusal, and the preflight source tip it reads is the one the preview promised.
    _require_ledger_projection(
        contract,
        _route_commits(contract, checkpoint),
        sources,
        _landing_admission(contract, checkpoint=checkpoint),
    )

    if args.dry_run:
        preview = IntegratePreview(
            guard=guard,
            handover_warning=handover_warning,
        )
        if checkpoint is not None:
            return _checkpoint_dry_run_result(contract, args, checkpoint, preview=preview)
        return _dry_run_result(contract, args, sources, preview=preview)

    return _apply_integration(
        contract,
        args,
        sources,
        handover_warning=handover_warning,
        checkpoint=checkpoint,
    )


def _apply_integration(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    *,
    handover_warning: dict[str, object] | None,
    checkpoint: CheckpointLanding | None = None,
) -> WorktreeCommandResult:
    """Land the code commit, then the memory commits, then merge both into their sources."""
    prepared = _prepare_integration_commits(contract, args, sources, checkpoint=checkpoint)
    if isinstance(prepared, WorktreeCommandResult):
        return prepared
    commits = prepared
    publication = IntegrationPublication(
        contract=contract,
        args=args,
        locked_args=args,
        sources=sources,
        commits=commits,
        handover_warning=handover_warning,
    )

    try:
        if contract.kind == "series" and checkpoint is not None:
            # The checkpoint's publication gate re-proves its captured refs against the live tips
            # before the one ref move, so the preview and the apply cannot admit different refs.
            return publish_series_checkpoint_under_authority(
                contract,
                lambda: _publish_integration_edge(
                    publication, "worktree_checkpoint_landing", checkpoint=checkpoint
                ),
                expected=checkpoint.refs,
            )
        if contract.kind == "series":
            # The two series routes differ in exactly one way: the final route proves the master is
            # a finished unit, the checkpoint route does not. Every ref-protecting authority is
            # identical on both.
            return publish_series_integration_under_authority(
                contract,
                lambda: _publish_integration_edge(publication, "worktree_integrate"),
            )
        return _publish_integration_edge(publication, "worktree_integrate")
    except AtomicLandingBlocked as error:
        return atomic_landing_blocked_result(contract, error)


def _publish_integration_edge(
    publication: IntegrationPublication,
    operation: str,
    *,
    checkpoint: CheckpointLanding | None = None,
) -> WorktreeCommandResult:
    """The one protected-ref move, told which operation it is performing.

    ``operation`` is required rather than inferred from ``checkpoint`` so every route has to name
    itself: a refusal on the repaired checkpoint route must not report ``worktree_integrate``, and a
    route added later cannot silently inherit the wrong name. Both literals are registered public
    tools, which is what the wire's ``nextTool`` validator requires.
    """

    current = load_contract(publication.contract.contract_path)
    if current != publication.contract:
        raise RuntimeError("integration contract changed before protected-ref movement")
    require_atomic_landing_authority(current)
    if current.kind == "series":
        require_series_contract_authority(current, operation=operation)
    else:
        require_ordinary_worktree(current, operation=operation)
    blocked = _integration_source_state_block(current, publication.sources)
    if blocked is not None:
        return blocked
    snapshot = prepare_integration_ref_move(
        current,
        publication.commits,
        publication.locked_args,
        publication.sources,
        admission=_landing_admission(current, checkpoint=checkpoint),
    )
    report_operation_progress(
        publication.args,
        "source-merge",
        current_command="compare-and-swap exact code and memory integration refs",
        irreversible_boundary=True,
        recovery_commits={
            "codeCommit": publication.commits.code,
            "memoryContentCommit": publication.commits.memory_content,
            "ledgerCommit": publication.commits.ledger,
        },
    )
    try:
        merge_integrated_commits(current, publication.commits, snapshot)
    except IntegrationRefRace as race:
        # A named ref moved between the exact read and the compare-and-swap. Git
        # reports what is true and the operator re-runs the integration -- the SAME
        # operation that was attempting it, which is why the name is the route's own
        # and not a fixed literal: telling a checkpoint operator to re-run
        # worktree_integrate would route them at the wrong tool for their master.
        return WorktreeCommandResult(
            2,
            {
                "state": "integration-ref-race",
                "summary": "a protected integration ref moved during the exact ref move",
                "detail": str(race),
                "nextTool": operation,
                "nextArgs": {"contract_path": current.contract_path.as_posix()},
            },
        )
    report_operation_progress(
        publication.locked_args,
        "contract-finalization",
        current_command="finalize integration contract edge",
    )
    if checkpoint is not None:
        return _checkpoint_result(current, publication.locked_args, publication.commits)
    return _integrated_result(
        current,
        publication.locked_args,
        publication.commits,
        handover_warning=publication.handover_warning,
    )


def _checkpoint_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    commits: IntegratedCommits,
) -> WorktreeCommandResult:
    """Record a non-final landing and reclaim nothing.

    The difference from :func:`_integrated_result` is the whole feature: this writes
    ``checkpointed`` rather than ``completed``, so the integration cell never claims a
    completion that has not happened and the master keeps its worktrees, its branches and its
    enclosure. Reclamation is not part of either landing route -- ``lifecycle_finalize_task``
    owns it, and an unfinished master is never finalized.
    """

    updated = record_landed_integration(
        contract,
        landed=LandedIntegration(
            strategy=args.strategy,
            code_commit=commits.code,
            memory_content_commit=commits.memory_content,
            ledger_commit=commits.ledger,
        ),
        checkpoint=True,
    )
    return WorktreeCommandResult(
        0,
        {
            "state": "checkpointed",
            **status_payload(updated),
            "summary": (
                "Checkpoint landing completed: this master's accumulated line landed into its "
                f"source branch via {args.strategy}, and the master stays open. Nothing was "
                "retired and no cleanup ran."
            ),
            "strategy": args.strategy,
            "integrated_code_commit": commits.code,
            "integrated_memory_content_commit": commits.memory_content,
            "integrated_ledger_commit": commits.ledger,
        },
    )


def _prepare_integration_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    *,
    checkpoint: CheckpointLanding | None = None,
) -> WorktreeCommandResult | IntegratedCommits:
    # A checkpoint already holds the exact commits it captured and proved at preflight, so it
    # lands those rather than re-deriving them from closeout cells it does not have.
    if checkpoint is not None:
        return checkpoint.commits
    return _prepare_fresh_integration_commits(contract, args, sources)


def _prepare_fresh_integration_commits(
    contract: WorktreeContract,
    _args: WorktreeArgs,
    sources: IntegrationSources,
) -> WorktreeCommandResult | IntegratedCommits:
    integrated_code_commit, blocked = _integrated_code_commit(contract, sources.current_code_source)
    if blocked is not None:
        return WorktreeCommandResult(2, blocked)
    blocked = _integration_source_state_block(contract, sources)
    if blocked is not None:
        return blocked
    integrated_memory_content_commit, integrated_ledger_commit, blocked = (
        _integrated_memory_commits(contract, sources.current_memory_source)
    )
    if blocked is not None:
        return WorktreeCommandResult(2, blocked)
    commits = IntegratedCommits(
        code=integrated_code_commit,
        memory_content=integrated_memory_content_commit,
        ledger=integrated_ledger_commit,
    )
    return commits
