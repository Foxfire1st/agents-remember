from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from agents_remember.controlplane.enforcement import GateGuard, evaluate_gate
from agents_remember.controlplane.records import GateRecord
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.primitives.gate_policy import (
    GatePolicy,
)
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.code_quality_gate import (
    GATE_FULL,
    QualityGatePlan,
    QualityGateTarget,
    code_quality_gate_preview,
    requires_integrated_acceptance,
    requires_strict_code_quality,
    run_strict_code_quality_gate,
)
from agents_remember.worktrees.modules.git import (
    branch_exists,
    commit_if_dirty,
    current_branch,
    head_commit,
    is_ancestor,
    require_clean,
    require_git,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.source_lineage import (
    lineage_block_payload,
    lineage_refusal,
    source_lineage_for_contract,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)

HANDOVER_GATE_KIND = "master-handover-approval"


def quality_gate_mode(contract: WorktreeContract) -> str:
    """Return the only accepting integration mode: full, at master altitude."""
    if contract.kind == "leaf":
        raise ValueError("leaf integration reuses the exact leaf-closeout acceptance")
    return GATE_FULL


def _quality_gate_settings(contract: WorktreeContract):
    settings = load_agentic_settings(contract.coordination_root, repo_root=contract.code_repo_path)
    return settings.quality_gate


def _quality_gate_memory_cap(contract: WorktreeContract) -> int | None:
    return _quality_gate_settings(contract).memory_cap_bytes


def _quality_gate_preview(contract: WorktreeContract) -> dict[str, object]:
    if contract.kind == "leaf":
        return {
            "required": False,
            "status": "certified-at-leaf-closeout",
            "command": "",
            "mode": "targeted",
            "reason": (
                "leaf integration lands the exact commit certified once at leaf closeout; "
                "integration does not rerun acceptance"
            ),
        }
    mode = quality_gate_mode(contract)
    settings = _quality_gate_settings(contract)
    memory_cap_bytes = _quality_gate_memory_cap(contract) if mode == GATE_FULL else None
    return code_quality_gate_preview(
        contract.code_worktree,
        code_would_commit=True,
        diff_base=contract.code_base_commit,
        plan=QualityGatePlan(
            mode=mode,
            memory_cap_bytes=memory_cap_bytes,
            executor=settings.executor,
        ),
        required_when_missing=requires_integrated_acceptance(contract.repo_name),
    )


@dataclass(frozen=True)
class IntegratePreview:
    """The evaluated seam guard and the planned altitude-routed quality gate."""

    guard: GateGuard
    handover_warning: dict[str, object] | None
    quality_gate: dict[str, object]


def integration_branch(contract: WorktreeContract) -> str:
    return f"{contract.memory_work_branch}-integration"


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


def blocked_integration_payload(
    contract: WorktreeContract, state: str, reason: str, persist: bool = True, **extra: object
) -> dict[str, object]:
    blocked = amend_contract(contract, ContractCells(integration_status="blocked"))
    if persist:
        write_contract(blocked.contract_path, blocked)
    next_step: dict[str, object] = {"summary": reason}
    for key in ("nextOperation", "nextTool", "nextArgs"):
        if key in extra:
            next_step[key] = extra[key]
    return {
        "state": state,
        **status_payload(blocked),
        "reason": reason,
        "summary": reason,
        "developer_decision_required": True,
        "nextStep": next_step,
        **extra,
    }


def validate_integrate_contract(contract: WorktreeContract) -> None:
    if contract.closeout_status != "completed":
        raise RuntimeError("integration requires closeout.status completed")
    if not contract.approved_for_commit:
        raise RuntimeError("integration requires approved closeout")
    if not contract.code_commit:
        raise RuntimeError("integration requires closeout code_commit")
    if not contract.code_worktree.exists():
        raise RuntimeError(f"code worktree does not exist: {contract.code_worktree}")
    if current_branch(contract.code_repo_path) != contract.code_source_branch:
        raise RuntimeError(f"code source repo must have {contract.code_source_branch} checked out")
    if current_branch(contract.code_worktree) != contract.code_work_branch:
        raise RuntimeError(f"code worktree must have {contract.code_work_branch} checked out")
    require_clean(contract.code_repo_path, "code source repo")
    require_clean(contract.code_worktree, "code worktree")
    if head_commit(contract.code_worktree) != contract.code_commit:
        raise RuntimeError("code worktree HEAD does not match closeout code_commit")
    if contract.memory_mode == "external":
        validate_integrate_memory_contract(contract)


def validate_integrate_memory_contract(contract: WorktreeContract) -> None:
    if (
        contract.memory_repo_path is None
        or contract.memory_worktree is None
        or contract.ledger_path is None
    ):
        raise RuntimeError(
            "external-memory integration requires memory repo, worktree, and ledger path"
        )
    if not contract.memory_content_commit or not contract.ledger_commit:
        raise RuntimeError(
            "external-memory integration requires closeout memory_content_commit and ledger_commit"
        )
    if current_branch(contract.memory_repo_path) != contract.memory_source_branch:
        raise RuntimeError(
            f"memory source repo must have {contract.memory_source_branch} checked out"
        )
    if current_branch(contract.memory_worktree) != contract.memory_work_branch:
        raise RuntimeError(f"memory worktree must have {contract.memory_work_branch} checked out")
    require_clean(contract.memory_repo_path, "memory source repo")
    require_clean(contract.memory_worktree, "memory worktree")
    if head_commit(contract.memory_worktree) != contract.ledger_commit:
        raise RuntimeError("memory worktree HEAD does not match closeout ledger_commit")


def replay_code_if_needed(
    contract: WorktreeContract, current_code_source: str
) -> tuple[str, dict[str, object] | None]:
    if is_ancestor(contract.code_repo_path, current_code_source, contract.code_commit):
        return contract.code_commit, None
    result = run_git(contract.code_worktree, ["rebase", contract.code_source_branch])
    if result.returncode != 0:
        return "", blocked_integration_payload(
            contract,
            "blocked-code-conflict",
            "code replay conflicted; resolve with the developer before moving main",
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            conflict_scope="code",
        )
    return head_commit(contract.code_worktree), None


def replay_memory_content(
    contract: WorktreeContract,
    integrated_code_commit: str,
    ledger_message: str,
) -> tuple[str, str, dict[str, object] | None]:
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    assert contract.ledger_path is not None
    scratch_branch = integration_branch(contract)
    if branch_exists(contract.memory_repo_path, scratch_branch):
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-existing-integration-branch",
                f"memory integration branch already exists: {scratch_branch}",
                conflict_scope="memory",
                branch=scratch_branch,
            ),
        )
    result = run_git(
        contract.memory_worktree, ["checkout", "-b", scratch_branch, contract.memory_content_commit]
    )
    if result.returncode != 0:
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-memory-replay",
                "could not create memory integration branch",
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                conflict_scope="memory",
            ),
        )
    result = run_git(
        contract.memory_worktree,
        ["rebase", "--onto", contract.memory_source_branch, contract.memory_base_commit],
    )
    if result.returncode != 0:
        return (
            "",
            "",
            blocked_integration_payload(
                contract,
                "blocked-memory-conflict",
                "memory replay conflicted; resolve with the developer before moving memory main",
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                conflict_scope="memory",
                branch=scratch_branch,
            ),
        )
    integrated_memory_content_commit = head_commit(contract.memory_worktree)
    ledger = load_ledger(contract.ledger_path)
    write_ledger(
        contract.ledger_path,
        prepend_mapping(ledger, integrated_code_commit, integrated_memory_content_commit),
    )
    require_git(contract.memory_worktree, ["add", "memory.md"])
    integrated_ledger_commit = commit_if_dirty(contract.memory_worktree, ledger_message)
    return integrated_memory_content_commit, integrated_ledger_commit, None


@dataclass(frozen=True)
class IntegrationSources:
    """Where each side's source branch stands at the moment integration starts.

    Its current head, and whether that head has already moved past the commit closeout
    landed -- which is exactly what makes a fast-forward impossible and ``--strategy replay``
    necessary. Head and verdict are read in the same breath per side and every consumer
    needs both, so they are one reading of the two source branches, not four values.
    """

    current_code_source: str
    current_memory_source: str
    code_replay_required: bool
    memory_replay_required: bool

    @property
    def replay_required(self) -> bool:
        return self.code_replay_required or self.memory_replay_required


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
    current_code = head_commit(contract.code_repo_path, contract.code_source_branch)
    moved = current_code != sources.current_code_source
    current_memory = ""
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        current_memory = head_commit(contract.memory_repo_path, contract.memory_source_branch)
        moved = moved or current_memory != sources.current_memory_source
    if not moved:
        return None
    return WorktreeCommandResult(
        2,
        blocked_integration_payload(
            contract,
            "source-moved-during-quality",
            "integration source branches moved while the quality gate ran; retry from "
            "preflight so the combined candidate is certified against the new tips",
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
    return _integration_lineage_block(contract, persist=True) or _integration_sources_moved_block(
        contract, sources
    )


def _integration_replay_requirements(contract: WorktreeContract) -> IntegrationSources:
    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    current_memory_source = ""
    code_replay_required = not is_ancestor(
        contract.code_repo_path, current_code_source, contract.code_commit
    )
    memory_replay_required = False
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        memory_replay_required = not is_ancestor(
            contract.memory_repo_path, current_memory_source, contract.ledger_commit
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
            "source branch moved; rerun with --strategy replay after reviewing parallel changes",
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
        "quality_gate": preview.quality_gate,
        "cleanup_question": "After successful integration, ask whether to remove the code and memory worktrees plus merged local task branches.",
    }
    if preview.handover_warning is not None:
        payload["handover_gate_warning"] = preview.handover_warning
    return WorktreeCommandResult(0, payload)


def _integrated_code_commit(
    contract: WorktreeContract, args: WorktreeArgs, current_code_source: str
) -> tuple[str, dict[str, object] | None]:
    integrated_code_commit = contract.code_commit
    if args.strategy == "replay":
        integrated_code_commit, blocked = replay_code_if_needed(contract, current_code_source)
        if blocked is not None:
            return "", blocked
    if not is_ancestor(contract.code_repo_path, current_code_source, integrated_code_commit):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code source branch"
        )
    return integrated_code_commit, None


def _memory_needs_new_ledger(
    contract: WorktreeContract,
    args: WorktreeArgs,
    current_memory_source: str,
    integrated_code_commit: str,
) -> bool:
    assert contract.memory_repo_path is not None
    return args.strategy == "replay" and (
        integrated_code_commit != contract.code_commit
        or not is_ancestor(contract.memory_repo_path, current_memory_source, contract.ledger_commit)
    )


def _integrated_memory_commits(
    contract: WorktreeContract,
    args: WorktreeArgs,
    current_memory_source: str,
    integrated_code_commit: str,
) -> tuple[str, str, dict[str, object] | None]:
    integrated_memory_content_commit = contract.memory_content_commit
    integrated_ledger_commit = contract.ledger_commit
    if contract.memory_mode == "external":
        assert contract.memory_repo_path is not None
        if _memory_needs_new_ledger(contract, args, current_memory_source, integrated_code_commit):
            integrated_memory_content_commit, integrated_ledger_commit, blocked = (
                replay_memory_content(
                    contract,
                    integrated_code_commit,
                    args.ledger_commit_message
                    or f"[{contract.task_id}] Integration ledger sync: {integrated_code_commit} -> {contract.memory_content_commit}",
                )
            )
            if blocked is not None:
                return "", "", blocked
        if not is_ancestor(
            contract.memory_repo_path, current_memory_source, integrated_ledger_commit
        ):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current memory source branch"
            )
    return integrated_memory_content_commit, integrated_ledger_commit, None


@dataclass(frozen=True)
class IntegratedCommits:
    """The three commits one integration lands: the code commit, the memory content commit,
    and the ledger commit that maps them. Every step past the replay decision -- the merge,
    the contract rewrite, the result payload -- consumes all three or none."""

    code: str
    memory_content: str
    ledger: str


def _merge_integrated_commits(contract: WorktreeContract, commits: IntegratedCommits) -> None:
    integrated_code_commit = commits.code
    integrated_ledger_commit = commits.ledger
    integrated_memory_content_commit = commits.memory_content
    external = contract.memory_mode == "external"
    # Pre-validate that BOTH fast-forwards are possible before mutating either
    # branch, so a memory-side problem cannot leave the code branch advanced
    # while memory stays behind (a half-integrated state).
    code_head_before = head_commit(contract.code_repo_path)
    if not is_ancestor(contract.code_repo_path, code_head_before, integrated_code_commit):
        raise RuntimeError(
            "integrated code commit is not a fast-forward from the current code branch"
        )
    memory_head_before = ""
    if external:
        assert contract.memory_repo_path is not None
        memory_head_before = head_commit(contract.memory_repo_path)
        if not is_ancestor(contract.memory_repo_path, memory_head_before, integrated_ledger_commit):
            raise RuntimeError(
                "integrated memory ledger commit is not a fast-forward from the current memory branch"
            )

    require_git(contract.code_repo_path, ["merge", "--ff-only", integrated_code_commit])
    if not external:
        return
    assert contract.memory_repo_path is not None
    try:
        require_git(contract.memory_repo_path, ["merge", "--ff-only", integrated_ledger_commit])
        ledger = load_ledger(contract.memory_repo_path / "memory.md")
        mapping = find_mapping(ledger, integrated_code_commit)
        if mapping is None or mapping.memory_commit != integrated_memory_content_commit:
            raise RuntimeError(
                "integrated memory ledger does not map landed code commit to landed memory content commit"
            )
    except Exception:
        # The memory side failed after the code branch advanced. Roll both
        # branches back to their pre-merge heads (best-effort) so integration is
        # all-or-nothing, then re-raise the original failure.
        run_git(contract.code_repo_path, ["reset", "--hard", code_head_before])
        run_git(contract.memory_repo_path, ["reset", "--hard", memory_head_before])
        raise


def _integrated_result(
    contract: WorktreeContract,
    args: WorktreeArgs,
    commits: IntegratedCommits,
    *,
    handover_warning: dict[str, object] | None,
    quality_gate: dict[str, object],
) -> WorktreeCommandResult:
    updated = amend_contract(
        replace(
            contract,
            integration_strategy=args.strategy,
            integrated_code_commit=commits.code,
            integrated_memory_content_commit=commits.memory_content,
            integrated_ledger_commit=commits.ledger,
        ),
        ContractCells(integration_status="completed", cleanup="pending"),
    )
    write_contract(contract.contract_path, updated)
    payload: dict[str, object] = {
        "state": "integrated",
        **status_payload(updated),
        "summary": "Integration completed; ask the developer whether to clean up worktrees and merged local branches.",
        "strategy": args.strategy,
        "integrated_code_commit": commits.code,
        "integrated_memory_content_commit": commits.memory_content,
        "integrated_ledger_commit": commits.ledger,
        "quality_gate": quality_gate,
        "cleanup_question": "Integration completed. Remove the code and memory worktrees plus merged local task branches now?",
    }
    if handover_warning is not None:
        payload["handover_gate_warning"] = handover_warning
    return WorktreeCommandResult(0, payload)


def _recovery_sources_landed(
    contract: WorktreeContract,
    commits: LifecycleOperationRecoveryCommits,
) -> bool:
    """Return whether the exact source pair landed; reject a torn landing."""
    code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    code_landed = code_source == commits.codeCommit
    if contract.memory_mode == "external":
        if contract.memory_repo_path is None:
            raise RuntimeError("external-memory integration recovery requires a memory repo")
        memory_source = head_commit(
            contract.memory_repo_path,
            contract.memory_source_branch,
        )
        memory_landed = memory_source == commits.ledgerCommit
        if not code_landed and not memory_landed:
            return False
        if not code_landed or not memory_landed:
            raise RuntimeError(
                "integration contract-finalization recovery requires manual reconciliation: "
                f"code source is {code_source} (expected {commits.codeCommit}); memory source "
                f"is {memory_source} (expected {commits.ledgerCommit})"
            )
        return True
    if not code_landed:
        return False
    if commits.memoryContentCommit or commits.ledgerCommit:
        raise RuntimeError(
            "integration contract-finalization recovery recorded external-memory commits "
            "for an internal-memory contract"
        )
    return True


def _prove_external_memory_recovery(
    contract: WorktreeContract,
    commits: LifecycleOperationRecoveryCommits,
) -> None:
    """Prove the task memory head, landed mapping, and content ancestry."""
    assert contract.memory_repo_path is not None
    assert contract.memory_worktree is not None
    require_clean(contract.memory_repo_path, "recovering integration memory source")
    require_clean(contract.memory_worktree, "recovering integration memory worktree")
    task_memory_head = head_commit(contract.memory_worktree)
    if task_memory_head != commits.ledgerCommit:
        raise RuntimeError(
            "integration contract-finalization recovery requires manual reconciliation: "
            f"recorded ledger commit {commits.ledgerCommit}, found task memory HEAD "
            f"{task_memory_head}"
        )
    mapping = find_mapping(
        load_ledger(contract.memory_repo_path / "memory.md"),
        commits.codeCommit,
    )
    if mapping is None or mapping.memory_commit != commits.memoryContentCommit:
        found = "missing" if mapping is None else mapping.memory_commit
        raise RuntimeError(
            "integration contract-finalization recovery requires manual reconciliation: "
            f"landed ledger mapping is {found}, expected {commits.memoryContentCommit}"
        )
    if not is_ancestor(
        contract.memory_repo_path,
        commits.memoryContentCommit,
        commits.ledgerCommit,
    ):
        raise RuntimeError(
            "integration contract-finalization recovery requires manual reconciliation: "
            "recorded memory content is not reachable from the recorded ledger commit"
        )


def _prove_integration_recovery_commits(
    contract: WorktreeContract,
    commits: LifecycleOperationRecoveryCommits,
) -> IntegratedCommits | None:
    """Prove a wholly landed source pair, or permit an untouched retry."""
    if not _recovery_sources_landed(contract, commits):
        return None

    require_clean(contract.code_repo_path, "recovering integration code source")
    require_clean(contract.code_worktree, "recovering integration code worktree")
    task_code_head = head_commit(contract.code_worktree)
    if task_code_head != commits.codeCommit:
        raise RuntimeError(
            "integration contract-finalization recovery requires manual reconciliation: "
            f"recorded code commit {commits.codeCommit}, found task HEAD {task_code_head}"
        )
    if contract.memory_mode == "external":
        _prove_external_memory_recovery(contract, commits)
    return IntegratedCommits(
        code=commits.codeCommit,
        memory_content=commits.memoryContentCommit,
        ledger=commits.ledgerCommit,
    )


def _recover_integration_finalization(
    contract: WorktreeContract,
    args: WorktreeArgs,
) -> WorktreeCommandResult | None:
    commits = args.recovery_commits
    if commits is None:
        return None
    proven = _prove_integration_recovery_commits(contract, commits)
    if proven is None:
        return None
    if contract.integration_status == "completed":
        if (
            contract.integrated_code_commit != commits.codeCommit
            or contract.integrated_memory_content_commit != commits.memoryContentCommit
            or contract.integrated_ledger_commit != commits.ledgerCommit
        ):
            raise RuntimeError(
                "completed integration contract does not match its recorded recovery commits"
            )
        return WorktreeCommandResult(
            0,
            {"state": "already-integrated", "recovered": True, **status_payload(contract)},
        )
    result = _integrated_result(
        contract,
        args,
        proven,
        handover_warning=None,
        quality_gate={
            "status": "recovered-contract-finalization",
            "passed": True,
            "reason": "the exact accepted commit set was proven from Git and the ledger",
        },
    )
    result.payload["recovered"] = True
    return result


def integrate_result(args: WorktreeArgs) -> WorktreeCommandResult:
    report_operation_progress(args, "preflight", current_command="validate integration eligibility")
    if not args.approved and not args.dry_run:
        raise RuntimeError("integration requires --approved after human review")
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    recovered = _recover_integration_finalization(contract, args)
    completed = recovered
    if completed is None and contract.integration_status == "completed":
        completed = WorktreeCommandResult(
            0,
            {"state": "already-integrated", **status_payload(contract)},
        )
    if completed is not None:
        return completed
    validate_integrate_contract(contract)
    lineage_block = _integration_lineage_block(contract, persist=not args.dry_run)
    if lineage_block is not None:
        return lineage_block
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

    sources = _integration_replay_requirements(contract)
    if args.strategy == "ff-only" and sources.replay_required:
        return _blocked_non_ff_result(contract, args, sources)
    if args.dry_run:
        return _dry_run_result(
            contract,
            args,
            sources,
            preview=IntegratePreview(
                guard=guard,
                handover_warning=handover_warning,
                quality_gate=_quality_gate_preview(contract),
            ),
        )

    return _apply_integration(
        contract,
        args,
        sources,
        handover_warning=handover_warning,
    )


def _apply_integration(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
    *,
    handover_warning: dict[str, object] | None,
) -> WorktreeCommandResult:
    """Land the code commit, then the memory commits, then merge both into their sources."""
    replay_head = (
        head_commit(contract.code_worktree)
        if args.strategy == "replay" and sources.code_replay_required
        else ""
    )
    memory_replay_state = _memory_replay_state(contract, args, sources)
    source_merged = False
    try:
        report_operation_progress(
            args, "integration-replay", current_command="resolve integration replay commits"
        )
        integrated_code_commit, blocked = _integrated_code_commit(
            contract, args, sources.current_code_source
        )
        if blocked is not None:
            return WorktreeCommandResult(2, blocked)
        report_operation_progress(
            args, "integration-quality", current_command="run altitude-routed quality contract"
        )
        quality_gate, blocked = _run_integration_quality_gate(contract)
        if blocked is not None:
            return WorktreeCommandResult(2, blocked)
        # The master-altitude full gate can run for minutes. Re-prove both the transitive
        # chain and the exact source tips before memory replay or source merge so its
        # certified code cannot go stale. The same proof is cheap and harmless for a leaf.
        blocked = _integration_source_state_block(contract, sources)
        if blocked is not None:
            return blocked
        integrated_memory_content_commit, integrated_ledger_commit, blocked = (
            _integrated_memory_commits(
                contract, args, sources.current_memory_source, integrated_code_commit
            )
        )
        if blocked is not None:
            return WorktreeCommandResult(2, blocked)
        commits = IntegratedCommits(
            code=integrated_code_commit,
            memory_content=integrated_memory_content_commit,
            ledger=integrated_ledger_commit,
        )
        blocked = _integration_source_state_block(contract, sources)
        if blocked is not None:
            return blocked
        report_operation_progress(
            args,
            "source-merge",
            current_command="fast-forward code and memory source branches",
            irreversible_boundary=True,
            recovery_commits={
                "codeCommit": commits.code,
                "memoryContentCommit": commits.memory_content,
                "ledgerCommit": commits.ledger,
            },
        )
        _merge_integrated_commits(contract, commits)
        source_merged = True
        report_operation_progress(
            args, "contract-finalization", current_command="finalize integration contract edge"
        )
        return _integrated_result(
            contract,
            args,
            commits,
            handover_warning=handover_warning,
            quality_gate=quality_gate,
        )
    finally:
        if replay_head and not source_merged:
            _restore_replayed_code_worktree(contract, replay_head)
        if memory_replay_state is not None and not source_merged:
            _restore_replayed_memory_worktree(contract, memory_replay_state)


def _restore_replayed_code_worktree(contract: WorktreeContract, original_head: str) -> None:
    """Restore a reversible replay when integration stops before the source merge."""
    if head_commit(contract.code_worktree) == original_head:
        return
    run_git(contract.code_worktree, ["rebase", "--abort"])
    reset = run_git(contract.code_worktree, ["reset", "--hard", original_head])
    if reset.returncode != 0:
        raise RuntimeError(
            "integration could not restore the task worktree after a reversible replay: "
            f"{reset.stderr.strip()}"
        )


@dataclass(frozen=True)
class _MemoryReplayState:
    branch: str
    head: str
    scratch_branch: str
    scratch_existed: bool


def _memory_replay_state(
    contract: WorktreeContract,
    args: WorktreeArgs,
    sources: IntegrationSources,
) -> _MemoryReplayState | None:
    if (
        args.strategy != "replay"
        or not (sources.code_replay_required or sources.memory_replay_required)
        or contract.memory_worktree is None
        or contract.memory_repo_path is None
    ):
        return None
    scratch = integration_branch(contract)
    return _MemoryReplayState(
        branch=current_branch(contract.memory_worktree),
        head=head_commit(contract.memory_worktree),
        scratch_branch=scratch,
        scratch_existed=branch_exists(contract.memory_repo_path, scratch),
    )


def _restore_replayed_memory_worktree(
    contract: WorktreeContract,
    original: _MemoryReplayState,
) -> None:
    assert contract.memory_worktree is not None
    assert contract.memory_repo_path is not None
    worktree = contract.memory_worktree
    run_git(worktree, ["rebase", "--abort"])
    if current_branch(worktree) != original.branch:
        checkout = run_git(worktree, ["checkout", "-f", original.branch])
        if checkout.returncode != 0:
            raise RuntimeError(
                "integration could not restore the memory task branch after replay: "
                f"{checkout.stderr.strip()}"
            )
    reset = run_git(worktree, ["reset", "--hard", original.head])
    if reset.returncode != 0:
        raise RuntimeError(
            "integration could not restore the memory task worktree after replay: "
            f"{reset.stderr.strip()}"
        )
    if not original.scratch_existed and branch_exists(
        contract.memory_repo_path, original.scratch_branch
    ):
        deleted = run_git(
            contract.memory_repo_path,
            ["branch", "-D", original.scratch_branch],
        )
        if deleted.returncode != 0:
            raise RuntimeError(
                "integration could not remove the reversible memory replay branch: "
                f"{deleted.stderr.strip()}"
            )


def _run_integration_quality_gate(
    contract: WorktreeContract,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Run the one integration-owned acceptance: full, at master altitude.

    Leaf integration reuses the exact targeted acceptance already bound to its closeout
    commit. Master integration runs the full wrapper once through the pinned Dagger
    executor inside the integration step itself. An explicit cap remains available.
    """
    if contract.kind == "leaf":
        return _quality_gate_preview(contract), None
    if not requires_strict_code_quality(
        contract.code_worktree,
        code_would_commit=True,
        required_when_missing=requires_integrated_acceptance(contract.repo_name),
    ):
        return _quality_gate_preview(contract), None
    mode = quality_gate_mode(contract)
    settings = _quality_gate_settings(contract)
    memory_cap_bytes = _quality_gate_memory_cap(contract) if mode == GATE_FULL else None
    try:
        gate = run_strict_code_quality_gate(
            QualityGateTarget(
                code_worktree=contract.code_worktree,
                worktree_group=contract.worktree_group,
            ),
            diff_base=contract.code_base_commit,
            plan=QualityGatePlan(
                mode=mode,
                memory_cap_bytes=memory_cap_bytes,
                executor=settings.executor,
            ),
            invocation="master-integration" if mode == GATE_FULL else "leaf-integration",
        )
    except RuntimeError as error:
        return {}, blocked_integration_payload(
            contract,
            "blocked-quality-gate",
            f"integration refused by the quality gate: {error}",
        )
    return gate, None
