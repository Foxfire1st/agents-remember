from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, cast

from agents_remember.controlplane.enforcement import (
    CLOSEOUT_GATE_KIND,
    CloseoutGuard,
    evaluate_closeout_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.observer.events import now_iso
from agents_remember.worktrees.closeout_queue_lifecycle import (
    certify_queue_candidate_closeout,
    claim_queue_candidate_for_closeout,
)
from agents_remember.worktrees.closeout_recovery import (
    MemoryCloseoutOutcome,
    accepted_code_commit,
    prove_closeout_recovery_commits,
    resume_external_commits,
)
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.closeout_memory_quality import (
    combine_memory_quality,
    run_memory_quality_phase,
)
from agents_remember.worktrees.modules.closeout_staged_quality import (
    gate_staged_code as _gate_staged_code,
)
from agents_remember.worktrees.modules.code_quality_gate import (
    QualityGatePlan,
    code_quality_gate_preview,
    requires_integrated_acceptance,
    requires_strict_code_quality,
)
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    changed_worktree_paths,
    commit_date,
    commit_if_dirty,
    committed_changed_paths,
    head_commit,
    is_ancestor,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    recovery_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import (
    PATH_SAMPLE_LIMIT,
    EntityFingerprintRefreshPlan,
    OnboardingRefreshPlan,
    RouteOverviewBodyClassification,
    RouteOverviewRefreshPlan,
    SidecarBodyClassification,
    VerifiedChange,
    WorktreeCommandResult,
)
from agents_remember.worktrees.modules.onboarding import (
    classify_route_overview_updates,
    classify_sidecar_updates,
    contract_memory_verified_commit,
    entity_fingerprint_refresh_plan,
    onboarding_refresh_plan,
    refresh_entity_fingerprints_for_context,
    refresh_onboarding_metadata,
    refresh_route_indexes_for_context,
    refresh_route_overview_metadata_for_context,
    route_index_refresh_plan_for_context,
    route_overview_metadata_refresh_plan,
    validate_onboarding_refresh_plan,
    validate_route_overview_refresh_plan,
)
from agents_remember.worktrees.route_review import (
    code_candidate_tree,
    code_change_present,
    require_current_route_review,
)
from agents_remember.worktrees.services import worktree_services
from agents_remember.worktrees.source_lineage import require_current_source_lineage
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    amend_contract,
    load_contract,
    write_contract,
)


def closeout_changed_paths(contract) -> dict[str, list[str]]:
    """Closeout worklist: working-tree changes plus the unverified committed range.

    ``committed`` covers commits the task transports (merges, pre-committed
    slices) that no previous closeout verified; ``working`` keeps the strict
    dirty-tree tier. A path in both tiers counts as working.
    """
    working = changed_worktree_paths(contract.code_worktree)
    committed = committed_changed_paths(
        contract.code_worktree, contract.code_base_commit, contract.code_commit
    )
    committed_only = sorted(set(committed) - set(working))
    return {
        "all": sorted({*working, *committed_only}),
        "working": working,
        "committed": committed_only,
    }


def _quality_gate_executor(contract) -> str:
    settings = load_agentic_settings(contract.coordination_root, repo_root=contract.code_repo_path)
    return settings.quality_gate.executor


def _bounded_paths(paths: list[str]) -> dict[str, object]:
    """Count plus capped sample so committed-range lists never flood the payload."""
    return {"count": len(paths), "sample": paths[:PATH_SAMPLE_LIMIT]}


def _bounded_refresh_plan_view(plan: OnboardingRefreshPlan) -> dict[str, object]:
    """Payload view of the sidecar plan: blockers stay full, scaling lists bounded."""
    return {
        "required": _bounded_paths([item["source_path"] for item in plan["required"]]),
        "missing": plan["missing"],
        "unsupported": plan["unsupported"],
        "unonboarded": _bounded_paths(plan["unonboarded"]),
    }


def _bounded_classification_view(
    classification: SidecarBodyClassification,
) -> dict[str, object]:
    return {
        "stale": _bounded_paths(classification["stale"]),
        "untraced": _bounded_paths(classification["untraced"]),
        "attested_no_impact": _bounded_paths(classification["attested_no_impact"]),
    }


def _refresh_plans_have_work(
    metadata_refresh: OnboardingRefreshPlan,
    entity_refresh: EntityFingerprintRefreshPlan,
    route_overview_refresh: RouteOverviewRefreshPlan,
    route_index_refresh: dict[str, Any],
) -> bool:
    """True when any onboarding/entity/route refresh would change memory content."""
    return (
        bool(metadata_refresh["required"])
        or bool(entity_refresh["required"])
        or bool(route_overview_refresh["required"])
        or route_index_refresh["written"] > 0
    )


def _completed_integration_source_heads(contract, base: str, integrated: str) -> set[str]:
    expected = {base}
    if contract.integration_status == "completed" and integrated:
        expected.add(integrated)
    return expected


def _format_expected_heads(expected: set[str]) -> str:
    return ", ".join(sorted(expected))


def _commit_missing_from_source(repo, commit: str, source_branch: str) -> bool:
    return bool(commit) and not is_ancestor(repo, commit, source_branch)


def _preview_integration_reopen(
    contract,
    *,
    code_dirty: bool,
    memory_would_commit: bool,
) -> dict[str, object]:
    if contract.integration_status != "completed":
        return {"would_reopen": False, "reason": "integration is not completed"}
    code_head = head_commit(contract.code_worktree)
    code_unlanded = code_dirty or (
        code_head != contract.code_commit
        and _commit_missing_from_source(
            contract.code_repo_path, code_head, contract.code_source_branch
        )
    )
    would_reopen = code_unlanded or memory_would_commit
    return {
        "would_reopen": would_reopen,
        "code_would_reopen": code_unlanded,
        "memory_would_reopen": memory_would_commit,
        "reason": "completed integration would be reopened after closeout"
        if would_reopen
        else "no new unlanded code or memory content is expected",
    }


def _completed_integration_reopen(
    contract,
    *,
    code_commit: str,
    memory_content_commit: str,
    ledger_commit: str,
) -> dict[str, object]:
    if contract.integration_status != "completed":
        return {"reopened": False, "reason": "integration is not completed"}
    code_changed = code_commit != contract.code_commit
    code_unlanded = code_changed and _commit_missing_from_source(
        contract.code_repo_path, code_commit, contract.code_source_branch
    )
    memory_content_changed = (
        contract.memory_mode == "external"
        and bool(memory_content_commit)
        and memory_content_commit != contract.memory_content_commit
    )
    memory_unlanded = False
    if memory_content_changed and contract.memory_repo_path is not None:
        memory_unlanded = _commit_missing_from_source(
            contract.memory_repo_path, ledger_commit, contract.memory_source_branch
        )
    reopened = code_unlanded or memory_unlanded
    return {
        "reopened": reopened,
        "code_unlanded": code_unlanded,
        "memory_unlanded": memory_unlanded,
        "previous_code_commit": contract.code_commit,
        "previous_memory_content_commit": contract.memory_content_commit,
        "previous_ledger_commit": contract.ledger_commit,
        "reason": "new closeout commit is not on the recorded source branch"
        if reopened
        else "no new unlanded code or memory content commit",
    }


@dataclass(frozen=True)
class _MemoryRefreshPreview:
    """What external-memory closeout would refresh, and what the body gates make of it.

    Six values computed by one step and consumed by one caller. They are grouped rather
    than returned as a tuple because the caller reads them by name, and grouped rather
    than left inline because the six ``contract.memory_mode == "external"`` conditionals
    that produce them were 66 of ``closeout_preview_payload``'s 153 lines.
    """

    metadata: OnboardingRefreshPlan
    entities: EntityFingerprintRefreshPlan
    route_overviews: RouteOverviewRefreshPlan
    route_indexes: dict[str, Any]
    sidecar_body_gate: SidecarBodyClassification
    route_overview_body_gate: RouteOverviewBodyClassification


def _memory_refresh_preview(contract, worklist: dict[str, list[str]]) -> _MemoryRefreshPreview:
    """Plan the external-memory refresh and classify it, without touching anything.

    Every field is the same conditional: plan it for real when the task carries external
    memory, and answer with the empty plan when it does not -- internal-memory closeout has
    no onboarding tree to refresh.
    """
    changed_paths = worklist["all"]
    metadata_refresh: OnboardingRefreshPlan = (
        onboarding_refresh_plan(contract, changed_paths, working_paths=worklist["working"])
        if contract.memory_mode == "external"
        else {
            "required": [],
            "missing": [],
            "unsupported": [],
            "unonboarded": [],
        }
    )
    entity_refresh: EntityFingerprintRefreshPlan = (
        entity_fingerprint_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "unsupported": [],
        }
    )
    route_overview_refresh: RouteOverviewRefreshPlan = (
        route_overview_metadata_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "missing_metadata": [],
        }
    )
    route_index_refresh: dict[str, Any] = (
        route_index_refresh_plan_for_context(_closeout_contract_context(contract))
        if contract.memory_mode == "external"
        else {
            "routes": 0,
            "written": 0,
            "unchanged": 0,
            "indexes": [],
        }
    )
    sidecar_body_gate: SidecarBodyClassification = (
        classify_sidecar_updates(
            _closeout_contract_context(contract),
            metadata_refresh,
            memory_tree=contract.memory_worktree,
            memory_verified_commit=contract_memory_verified_commit(contract),
        )
        if contract.memory_mode == "external"
        else {
            "stale": [],
            "untraced": [],
            "attested_no_impact": [],
        }
    )
    route_overview_body_gate: RouteOverviewBodyClassification = (
        classify_route_overview_updates(
            _closeout_contract_context(contract),
            route_overview_refresh,
            changed_paths,
            memory_tree=contract.memory_worktree,
            memory_verified_commit=contract_memory_verified_commit(contract),
        )
        if contract.memory_mode == "external"
        else {
            "stale": [],
            "untraced": [],
            "attested_no_impact": [],
            "stamped_without_body_review": [],
        }
    )
    return _MemoryRefreshPreview(
        metadata=metadata_refresh,
        entities=entity_refresh,
        route_overviews=route_overview_refresh,
        route_indexes=route_index_refresh,
        sidecar_body_gate=sidecar_body_gate,
        route_overview_body_gate=route_overview_body_gate,
    )


def _proposed_commits(
    contract,
    args: WorktreeArgs,
    code_dirty: bool,
    memory_would_commit: bool,
    code_quality_gate: dict[str, Any],
) -> dict[str, object]:
    """The three commits the preview is asking approval for, and what each is gated on.

    ``ledger_message`` is derived here because this is its only reader: the preview's
    default text names the two commits the ledger will map once they exist.
    """
    ledger_message = (
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: <code_commit> -> <memory_commit>"
    )
    return {
        "code": {
            "would_commit": code_dirty,
            "message": args.code_commit_message,
            "worktree": contract.code_worktree.as_posix(),
            "strict_code_quality_before_commit": bool(code_quality_gate["required"]),
        },
        "memory": {
            "would_commit": memory_would_commit,
            "message": args.memory_commit_message,
            "worktree": contract.memory_worktree.as_posix() if contract.memory_worktree else "",
            "metadata_refresh_after_code_commit": contract.memory_mode == "external",
            "entity_fingerprint_refresh_after_code_commit": contract.memory_mode == "external",
            "route_refresh_after_code_commit": contract.memory_mode == "external",
            "memory_quality_check_before_commit": contract.memory_mode == "external",
        },
        "ledger": {
            "would_update": contract.memory_mode == "external",
            "message": ledger_message,
            "path": contract.ledger_path.as_posix() if contract.ledger_path else "",
        },
    }


def closeout_preview_payload(contract, args: WorktreeArgs) -> dict[str, object]:
    """Answer what closeout would do, having done none of it."""
    _refuse_series_code_commit(contract)
    code_dirty = worktree_dirty(contract.code_worktree)
    code_changed = code_change_present(contract)
    route_review = require_current_route_review(contract)
    memory_dirty = contract.memory_mode == "external" and worktree_dirty(contract.memory_worktree)
    worklist = closeout_changed_paths(contract)
    changed_paths = worklist["all"]
    refresh = _memory_refresh_preview(contract, worklist)
    memory_would_commit = memory_dirty or _refresh_plans_have_work(
        refresh.metadata, refresh.entities, refresh.route_overviews, refresh.route_indexes
    )
    code_quality_gate = _closeout_quality_gate_preview(contract, code_would_commit=code_changed)
    return {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": (
            "Closeout preview only; no commits were created. For external memory, the "
            "working-tree memory-quality preflight runs before staging or any code-quality "
            "subprocess, so a structurally invalid entity catalog or broken citation aborts "
            "before Pyright or pytest. The staging "
            "step and its two refusals belong only to the leaf change-set-scoped quality "
            "gate: when a leaf would commit and this checkout carries the quality wrapper, closeout "
            "refuses a non-task checkout or unresolved conflicts; otherwise it stages the "
            "whole task worktree, runs its configured fast hook once, restages any hook edits, "
            "and runs the leaf's targeted contract over exactly what it will commit. The code "
            "commit bypasses hooks so nothing restarts after the wrapper's pytest-final phase. "
            "Series/master closeout does not rerun acceptance. The full wrapper runs once "
            "per master at the Dagger-container master "
            "integration gate through the pinned Dagger graph. After the code commit, "
            "external-memory closeout refreshes "
            "onboarding and entity metadata plus route overviews and indexes, reruns memory "
            "quality without the preflight's temporary base provenance, and only then commits "
            "memory and ledger. A refusal commits nothing; a refused code gate may leave the "
            "disposable task worktree staged because its retry resets and restages it."
        ),
        **recovery_guidance(
            "request_commit_approval",
            tool="worktree_closeout_apply",
            args=contract_next_args(
                contract,
                code_commit_message=args.code_commit_message,
                memory_commit_message=args.memory_commit_message,
                ledger_commit_message=args.ledger_commit_message,
            ),
            required_args=["intent_note"],
        ),
        "commit_approval_required": True,
        "route_review": route_review,
        "approval_question": "Approve creating the code, memory, and ledger commits with these messages?",
        "closeout_order": [
            "run-working-tree-memory-quality-preflight-before-code-quality",
            "refuse-if-gate-would-run-and-code-checkout-is-not-the-tasks-own-worktree",
            "refuse-if-gate-would-run-and-code-worktree-has-unresolved-merge-conflicts",
            "reset-and-stage-whole-task-worktree-if-gate-would-run",
            "run-configured-pre-commit-hook-once-and-restage-hook-edits",
            "run-strict-code-quality-over-that-staged-content",
            "commit-exactly-certified-code-index-without-rerunning-hooks",
            "refresh-onboarding-metadata-and-entity-fingerprints",
            "refresh-route-overview-metadata-and-indexes",
            "run-post-refresh-memory-quality-check",
            "commit-memory-content",
            "update-ledger",
            "commit-ledger",
            "update-contract",
        ],
        "changed_code_paths": _bounded_paths(changed_paths),
        "changed_code_paths_committed": _bounded_paths(worklist["committed"]),
        "onboarding_metadata_refresh": _bounded_refresh_plan_view(refresh.metadata),
        "sidecar_body_gate": _bounded_classification_view(refresh.sidecar_body_gate),
        "sidecars_attested_no_impact": _bounded_paths(
            refresh.sidecar_body_gate["attested_no_impact"]
        ),
        "entity_fingerprint_refresh": refresh.entities,
        "route_overview_metadata_refresh": refresh.route_overviews,
        "route_overview_body_gate": refresh.route_overview_body_gate,
        "route_overviews_attested_no_impact": refresh.route_overview_body_gate[
            "attested_no_impact"
        ],
        "route_index_refresh": refresh.route_indexes,
        "code_quality_gate": code_quality_gate,
        "integration_reopen": _preview_integration_reopen(
            contract, code_dirty=code_dirty, memory_would_commit=memory_would_commit
        ),
        "closeout_gate": _closeout_gate_payload(_closeout_gate_guard(contract, args)),
        "proposed_commits": _proposed_commits(
            contract, args, code_dirty, memory_would_commit, code_quality_gate
        ),
    }


def _validate_closeout_source_heads(contract) -> None:
    current_code_source = head_commit(contract.code_repo_path, contract.code_source_branch)
    expected_code_heads = _completed_integration_source_heads(
        contract, contract.code_base_commit, contract.integrated_code_commit
    )
    if current_code_source not in expected_code_heads:
        raise RuntimeError(
            "code source branch moved since task start: "
            f"{contract.code_source_branch} is {current_code_source}, "
            f"expected {_format_expected_heads(expected_code_heads)}"
        )
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_base_commit
    ):
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        expected_memory_heads = _completed_integration_source_heads(
            contract, contract.memory_base_commit, contract.integrated_ledger_commit
        )
        if current_memory_source not in expected_memory_heads:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, "
                f"expected {_format_expected_heads(expected_memory_heads)}"
            )


def _validate_closeout_source_state(contract) -> None:
    """Prove immediate source heads and the full super -> master -> leaf chain."""
    _validate_closeout_source_heads(contract)
    require_current_source_lineage(contract, operation="closeout")


def _closeout_approval_note(args: WorktreeArgs) -> str:
    if not args.approved:
        raise RuntimeError("closeout requires --approved after explicit commit approval")
    approval_note = args.approval_note.replace("\n", " ").strip()
    if not approval_note:
        raise RuntimeError(
            "closeout requires --approval-note describing the developer's explicit commit approval"
        )
    return approval_note


def _closeout_gate_guard(contract, args: WorktreeArgs) -> CloseoutGuard | None:
    """The lifecycle's closeout-gate verdict, or ``None`` when the lifecycle is gateless.

    Reads the same gate log the dashboard writes -- the observer root under the
    contract's coordination root, keyed by ``contract.lifecycle_id``. A pure read;
    whether an unsatisfied verdict raises is the caller's choice, so the preview can
    surface the verdict without failing.
    """
    if not contract.lifecycle_id:
        return None
    store = GateStore(observer_logs_root(contract.coordination_root))
    return evaluate_closeout_gate(
        store.current(contract.lifecycle_id),
        policy=args.gate_policy,
        operation_key=args.operation_key or None,
    )


def _refuse_unsatisfied_closeout_gate(contract, args: WorktreeArgs) -> None:
    """Refuse early, on a pure read, when the gate is visibly unsatisfied. DECIDES NOTHING.

    Server-side gate enforcement (slice 6b): a dashboard-opened ``closeout-approval`` gate is
    binding; a gateless lifecycle falls back to the chat commit gate (``--approved`` + note),
    unchanged. The agent cannot satisfy the gate itself: its own ``gate_decide`` is
    ``decidedBy="model"``, which :func:`evaluate_closeout_gate` rejects.

    THIS IS NOT THE ENFORCEMENT ANY MORE, and reading it as such is the check-then-act mistake
    leaf 260731-EFA-L5 R2 was called in to remove. The approval is consumed by
    :func:`_claim_closeout_gate`, under the gate log's lock, immediately before the first
    irreversible act. This call exists only so an unapproved closeout is refused BEFORE it stages
    the worktree and spends a minute in the strict code-quality gate.

    It is safe to keep precisely because it can only DENY. Its read is unlocked and therefore
    already stale by the time it returns, but a stale read here has exactly two outcomes: it
    refuses a gate that has since been approved (the operator reruns, and nothing was consumed),
    or it permits and the claim re-evaluates the same policy under the lock and refuses there. It
    can never be the reason an approval is spent, because it never writes.
    """
    guard = _closeout_gate_guard(contract, args)
    if guard is not None and not guard.permitted:
        raise RuntimeError(f"closeout blocked by gate enforcement: {guard.reason}")


def _claim_closeout_gate(contract, args: WorktreeArgs) -> CloseoutGuard | None:
    """Spend the lifecycle's closeout approval, atomically, BEFORE anything irreversible happens.

    Two properties, and both are the point (leaf 260731-EFA-L5 R2/R3):

    ATOMIC. :meth:`GateStore.claim_approval` folds the log, applies the policy and appends the
    ``applied`` snapshot inside one held lock, so two closeouts racing this line resolve to exactly
    one spend. The old shape checked here and marked applied ~100 lines later, with every commit in
    between; two real processes and a 0.4s body were enough to have both permitted and two
    ``applied`` snapshots on disk.

    CLAIMED BEFORE THE SPEND, WHICH IS A DELIBERATE SEMANTIC CHANGE: an approval now authorises ONE
    ATTEMPT, not one success. A closeout that dies after this line -- a crashed process, a failed
    memory quality gate, a git error, an ENOSPC -- leaves the approval consumed, and the next
    closeout needs a fresh gate. ``controlplane/enforcement.py`` already words the remedy: "was
    already applied; open a fresh gate for a new mutation".

    That is the correct trade and the alternative is not a milder version of it. Marking applied at
    the END means the marker is attempted only once the code commit, the memory commit, the ledger
    commit and the contract rewrite have all happened -- so every way that write can fail to land
    (the process dies; the append raises) leaves a live approval sitting on top of an unknown
    amount of completed, irreversible work. Both were reproduced. Fail-closed costs a re-approval
    after a failure the operator can see; fail-open silently hands the next closeout an approval
    the human granted for work that is already done.

    A two-phase claim (a ``claimed`` state, finalised to ``applied`` on success and released back
    to ``approved`` on a clean failure) was considered and rejected: the release is exactly the
    step that cannot be guaranteed -- it is the same write, at the same late position, with the
    same failure modes -- so it would need a reaper to age a stuck ``claimed`` gate back to
    spendable, which re-opens this window on a timer and cannot tell a died-mid-commit closeout
    from a died-before-commit one.

    The call site is one statement above the first irreversible act. Everything upstream of it --
    source-head validation, the onboarding/route plans, the mixed reset and staging, the strict
    code-quality gate -- either only reads or only touches the index of the task's own disposable
    worktree, so a refusal there changes nothing and must not cost the developer their approval.
    ``mcp/tests/test_gate_replay_window.py`` pins both halves: the gate is already ``applied`` by
    the time ``commit_if_dirty`` runs, and a gate failure leaves it ``approved``.
    """
    if not contract.lifecycle_id:
        return None
    store = GateStore(observer_logs_root(contract.coordination_root))
    guard = store.claim_approval(
        contract.lifecycle_id,
        kind=CLOSEOUT_GATE_KIND,
        now=now_iso(),
        policy=args.gate_policy,
        operation_key=args.operation_key or None,
    )
    if not guard.permitted:
        raise RuntimeError(f"closeout blocked by gate enforcement: {guard.reason}")
    return guard


def _closeout_gate_payload(guard: CloseoutGuard | None) -> dict[str, object]:
    """How the preview/apply response reports gate enforcement to the commit-approval relay."""
    if guard is None:
        return {"enforced": False, "reason": "gateless lifecycle; chat commit approval governs"}
    return {
        "enforced": True,
        "permitted": guard.permitted,
        "gateId": guard.gate_id,
        "reason": guard.reason,
    }


def _closeout_contract_context(contract):
    context = contract_context(contract)
    return replace(context, code_repository_root=contract.code_worktree)


def _resumed_external_outcome(
    contract,
    args: WorktreeArgs,
    code_commit: str,
) -> MemoryCloseoutOutcome | None:
    recovery_memory_commit = (
        args.recovery_commits.memoryContentCommit if args.recovery_commits is not None else ""
    )
    if not recovery_memory_commit:
        return None
    memory_commit, ledger_commit = resume_external_commits(
        contract,
        args,
        code_commit=code_commit,
        memory_commit=recovery_memory_commit,
    )
    return MemoryCloseoutOutcome(
        memory_commit=memory_commit,
        ledger_commit=ledger_commit,
    )


def _external_closeout_commits(
    contract,
    args: WorktreeArgs,
    change: VerifiedChange,
    memory_quality_before_refresh: dict[str, Any],
) -> MemoryCloseoutOutcome:
    if contract.memory_worktree is None or contract.ledger_path is None:
        raise RuntimeError("external-memory closeout requires memory worktree and ledger path")
    code_commit = change.commit
    resuming = args.approval_claimed or args.recovery_commits is not None
    recovered = _resumed_external_outcome(contract, args, code_commit)
    if recovered is not None:
        return recovered
    context = _closeout_contract_context(contract)
    report_operation_progress(
        args, "memory-refresh", current_command="refresh onboarding and route metadata"
    )
    refreshed_onboarding = refresh_onboarding_metadata(contract, change)
    refreshed_route_overviews = refresh_route_overview_metadata_for_context(
        context,
        change,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, change.changed_paths)
    route_index_refresh = refresh_route_indexes_for_context(context)
    _, after_checks = worktree_services().memory_quality.check_groups()
    memory_quality_after_refresh = run_memory_quality_phase(context, after_checks)
    memory_quality = combine_memory_quality(
        memory_quality_before_refresh, memory_quality_after_refresh
    )
    memory_content_dirty = worktree_dirty(contract.memory_worktree)
    ledger = load_ledger(contract.ledger_path)
    existing_mapping = find_mapping(ledger, code_commit)
    report_operation_progress(
        args, "memory-commit", current_command="commit verified external memory"
    )
    if memory_content_dirty:
        memory_commit = commit_if_dirty(contract.memory_worktree, args.memory_commit_message)
    elif existing_mapping is not None:
        memory_commit = existing_mapping.memory_commit
        if not is_ancestor(
            contract.memory_worktree,
            memory_commit,
            head_commit(contract.memory_worktree),
        ):
            raise RuntimeError(
                "closeout recovery ledger mapping names memory content that is not reachable "
                "from the current memory worktree"
            )
    else:
        memory_head = head_commit(contract.memory_worktree)
        memory_commit = memory_head if resuming else contract.memory_content_commit or memory_head
    report_operation_progress(
        args,
        "memory-commit",
        current_command="external memory commit recorded for recovery",
        recovery_commits={
            "codeCommit": code_commit,
            "memoryContentCommit": memory_commit,
            "ledgerCommit": "",
        },
    )
    if existing_mapping is not None and existing_mapping.memory_commit == memory_commit:
        ledger_commit = head_commit(contract.memory_worktree)
    else:
        report_operation_progress(
            args, "ledger-commit", current_command="commit code-to-memory ledger mapping"
        )
        write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
        require_git(contract.memory_worktree, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(
            contract.memory_worktree,
            args.ledger_commit_message
            or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}",
        )
    report_operation_progress(
        args,
        "ledger-commit",
        current_command="external ledger commit recorded for recovery",
        recovery_commits={
            "codeCommit": code_commit,
            "memoryContentCommit": memory_commit,
            "ledgerCommit": ledger_commit,
        },
    )
    return MemoryCloseoutOutcome(
        memory_commit=memory_commit,
        ledger_commit=ledger_commit,
        refreshed_onboarding=refreshed_onboarding,
        refreshed_entities=refreshed_entities,
        refreshed_route_overviews=refreshed_route_overviews,
        route_index_refresh=route_index_refresh,
        memory_quality=memory_quality,
    )


@dataclass(frozen=True)
class _CloseoutAttestations:
    """What the onboarding body gates found, read before anything is committed.

    Validating rather than merely planning: ``validate_onboarding_refresh_plan`` and
    ``validate_route_overview_refresh_plan`` raise on a plan closeout must not proceed
    with, so this step is also the last refusal before the approval is claimed.
    """

    attested_sidecars: list[str] = field(default_factory=list)
    attested_overviews: list[str] = field(default_factory=list)
    stamped_overviews: list[str] = field(default_factory=list)
    unonboarded_paths: list[str] = field(default_factory=list)


def _closeout_attestations(contract, worklist: dict[str, list[str]]) -> _CloseoutAttestations:
    """Validate and classify the onboarding refresh for a closeout that is about to run."""
    if contract.memory_mode != "external":
        return _CloseoutAttestations()
    changed_paths = worklist["all"]
    sidecar_plan = validate_onboarding_refresh_plan(
        contract, changed_paths, working_paths=worklist["working"]
    )
    attested_sidecars = classify_sidecar_updates(
        contract_context(contract),
        sidecar_plan,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )["attested_no_impact"]
    overview_plan = validate_route_overview_refresh_plan(contract, changed_paths)
    overview_gate = classify_route_overview_updates(
        contract_context(contract),
        overview_plan,
        changed_paths,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )
    return _CloseoutAttestations(
        attested_sidecars=attested_sidecars,
        attested_overviews=overview_gate["attested_no_impact"],
        stamped_overviews=overview_gate["stamped_without_body_review"],
        unonboarded_paths=sidecar_plan["unonboarded"],
    )


def _amended_closeout_contract(
    contract,
    approval_note: str,
    code_commit: str,
    memory: MemoryCloseoutOutcome,
    reopened: bool,
):
    """The contract as closeout leaves it: approved, committed, and possibly reopened.

    ``reopened`` means the closeout produced a commit that is not on the recorded source
    branch, so every integration cell it had earned is cleared and cleanup goes back to
    pending -- the task is not integrated any more, whatever it said a moment ago.
    """
    return amend_contract(
        replace(
            contract,
            approved_for_commit=True,
            commit_approval_note=approval_note,
            code_commit=code_commit,
            memory_content_commit=memory.memory_commit,
            ledger_commit=memory.ledger_commit,
            integration_strategy="" if reopened else contract.integration_strategy,
            integrated_code_commit="" if reopened else contract.integrated_code_commit,
            integrated_memory_content_commit=""
            if reopened
            else contract.integrated_memory_content_commit,
            integrated_ledger_commit="" if reopened else contract.integrated_ledger_commit,
        ),
        # The vocabulary cells go through the typed record; `replace` above carries only the
        # free-text commits and notes, which have no vocabulary to check them against.
        ContractCells(
            human_review_status="approved",
            closeout_status="completed",
            integration_status="not-started" if reopened else contract.integration_status,
            cleanup="pending" if reopened else contract.cleanup,
        ),
    )


def _memory_quality_before_refresh(contract) -> dict[str, Any]:
    """Run the external-memory citation preflight before the expensive code gate."""
    if contract.memory_mode != "external":
        return {}
    before_checks, _ = worktree_services().memory_quality.check_groups()
    return run_memory_quality_phase(
        _closeout_contract_context(contract),
        before_checks,
        unstamped_code_commit=contract.code_base_commit,
    )


@dataclass(frozen=True)
class _CloseoutResultFacts:
    code_commit: str
    memory: MemoryCloseoutOutcome
    attestations: _CloseoutAttestations
    code_quality_gate: dict[str, Any]
    integration_reopen: dict[str, Any]
    gate_guard: CloseoutGuard | None


def _recover_closeout_finalization(contract, args: WorktreeArgs) -> WorktreeCommandResult | None:
    """Finalize an already-committed detached closeout exactly once."""
    commits = args.recovery_commits
    if commits is None or (
        contract.memory_mode == "external"
        and (not commits.memoryContentCommit or not commits.ledgerCommit)
    ):
        return None
    memory = prove_closeout_recovery_commits(contract, commits)
    if contract.closeout_status == "completed":
        if (
            contract.code_commit != commits.codeCommit
            or contract.memory_content_commit != commits.memoryContentCommit
            or contract.ledger_commit != commits.ledgerCommit
        ):
            raise RuntimeError(
                "completed closeout contract does not match its recorded recovery commits"
            )
        return WorktreeCommandResult(
            0,
            {"state": "already-closed", "recovered": True, **status_payload(contract)},
        )
    approval_note = _closeout_approval_note(args)
    integration_reopen = _completed_integration_reopen(
        contract,
        code_commit=commits.codeCommit,
        memory_content_commit=commits.memoryContentCommit,
        ledger_commit=commits.ledgerCommit,
    )
    updated = _amended_closeout_contract(
        contract,
        approval_note,
        commits.codeCommit,
        memory,
        bool(integration_reopen["reopened"]),
    )
    write_contract(contract.contract_path, updated)
    payload = _closed_result_payload(
        updated,
        _CloseoutResultFacts(
            code_commit=commits.codeCommit,
            memory=memory,
            attestations=_CloseoutAttestations(),
            code_quality_gate={
                "status": "recovered-contract-finalization",
                "passed": True,
                "reason": "the exact accepted commit set was proven from Git and the ledger",
            },
            integration_reopen=integration_reopen,
            gate_guard=_closeout_gate_guard(contract, args),
        ),
    )
    payload["recovered"] = True
    return WorktreeCommandResult(0, payload)


def _closed_result_payload(updated, facts: _CloseoutResultFacts) -> dict[str, Any]:
    """Build the completed-closeout response after all durable writes finish."""
    memory = facts.memory
    attestations = facts.attestations
    return {
        "state": "closed",
        **status_payload(updated),
        "summary": "Closeout completed; integrate the task branches back into their source branches.",
        "code_commit": facts.code_commit,
        "memory_content_commit": memory.memory_commit,
        "ledger_commit": memory.ledger_commit,
        "refreshed_onboarding": _bounded_paths(
            [item["source_path"] for item in memory.refreshed_onboarding]
        ),
        "sidecars_attested_no_impact": _bounded_paths(attestations.attested_sidecars),
        "unonboarded_changed_paths": _bounded_paths(attestations.unonboarded_paths),
        "refreshed_entities": memory.refreshed_entities,
        "refreshed_route_overviews": memory.refreshed_route_overviews,
        "route_overviews_attested_no_impact": attestations.attested_overviews,
        "route_overviews_stamped_without_body_review": attestations.stamped_overviews,
        "route_index_refresh": memory.route_index_refresh,
        "memory_quality": memory.memory_quality,
        "code_quality_gate": facts.code_quality_gate,
        "integration_reopen": facts.integration_reopen,
        "closeout_gate": _closeout_gate_payload(facts.gate_guard),
    }


def _closeout_quality_preflight(
    contract, args: WorktreeArgs, *, code_would_commit: bool
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Run the reversible memory and code gates before approval is consumed."""
    code_quality_gate = _closeout_quality_gate_preview(
        contract, code_would_commit=code_would_commit
    )
    report_operation_progress(
        args, "memory-preflight", current_command="run pre-refresh memory quality"
    )
    memory_quality = _memory_quality_before_refresh(contract)
    strict_required = contract.kind == "leaf" and requires_strict_code_quality(
        contract.code_worktree,
        code_would_commit=code_would_commit,
        required_when_missing=requires_integrated_acceptance(contract.repo_name),
    )
    if strict_required:
        report_operation_progress(
            args, "quality", current_command="run targeted leaf quality contract"
        )
        code_quality_gate = _gate_staged_code(
            contract.code_worktree,
            worktree_group=contract.worktree_group,
            diff_base=contract.code_base_commit,
            executor=_quality_gate_executor(contract),
            candidate_tree=args.candidate_tree,
        )
    return code_quality_gate, memory_quality, strict_required


def _closeout_quality_gate_preview(contract, *, code_would_commit: bool) -> dict[str, object]:
    if contract.kind != "leaf":
        return {
            "required": False,
            "status": "not-required-master-altitude",
            "command": "",
            "reason": (
                "series/master closeout records landed commits without rerunning acceptance; "
                "the master integration owns the single full acceptance"
            ),
        }
    return code_quality_gate_preview(
        contract.code_worktree,
        code_would_commit=code_would_commit,
        diff_base=contract.code_base_commit,
        plan=QualityGatePlan(mode="targeted", executor=_quality_gate_executor(contract)),
        required_when_missing=requires_integrated_acceptance(contract.repo_name),
    )


def _refuse_series_code_commit(contract) -> None:
    if contract.kind != "leaf" and worktree_dirty(contract.code_worktree):
        raise RuntimeError(
            "series/master closeout cannot create a code commit; land code through a leaf "
            "before recertifying the series"
        )


def _revalidate_reviewed_candidate(
    contract, route_review: dict[str, Any], accepted_candidate_tree: str
) -> None:
    """Re-prove source lineage and review identity at the last reversible boundary."""
    _validate_closeout_source_state(contract)
    if code_candidate_tree(contract) != accepted_candidate_tree:
        raise RuntimeError(
            "closeout candidate changed after quality; restart from the current candidate"
        )
    if not route_review.get("required", False):
        return
    current_review = require_current_route_review(contract)
    if route_review.get("candidateTree") != current_review.get("candidateTree"):
        raise RuntimeError(
            "closeout candidate changed after route review and quality; rerun independent review"
        )


@dataclass(frozen=True)
class _CloseoutCommitPhase:
    code_commit: str
    memory: MemoryCloseoutOutcome
    integration_reopen: dict[str, Any]
    gate_guard: CloseoutGuard | None


def _closeout_commit_phase(
    contract,
    args: WorktreeArgs,
    *,
    worklist: dict[str, list[str]],
    memory_quality_before_refresh: dict[str, Any],
    strict_code_quality_required: bool,
) -> _CloseoutCommitPhase:
    resuming = args.approval_claimed or args.recovery_commits is not None
    report_operation_progress(
        args,
        "approval-claim",
        current_command="resume claimed closeout" if resuming else "claim closeout approval",
        irreversible_boundary=True,
        approval_claimed=resuming,
    )
    gate_guard = (
        _closeout_gate_guard(contract, args) if resuming else _claim_closeout_gate(contract, args)
    )
    report_operation_progress(
        args,
        "approval-claim",
        current_command="closeout approval claimed",
        approval_claimed=True,
    )
    report_operation_progress(args, "code-commit", current_command="commit verified code")
    code_commit = accepted_code_commit(
        contract,
        args,
        strict_code_quality_required=strict_code_quality_required,
        resuming=resuming,
    )
    code_commit_date = commit_date(contract.code_worktree, code_commit)
    memory = MemoryCloseoutOutcome()
    if contract.memory_mode == "external":
        memory = _external_closeout_commits(
            contract,
            args,
            VerifiedChange(
                commit=code_commit,
                commit_date=code_commit_date,
                changed_paths=worklist["all"],
                working_paths=worklist["working"],
            ),
            memory_quality_before_refresh,
        )
    integration_reopen = _completed_integration_reopen(
        contract,
        code_commit=code_commit,
        memory_content_commit=memory.memory_commit,
        ledger_commit=memory.ledger_commit,
    )
    return _CloseoutCommitPhase(code_commit, memory, integration_reopen, gate_guard)


def closeout_result(args: WorktreeArgs) -> WorktreeCommandResult:
    """Run closeout for real, in the order the preview promised.

    Nothing moved across the claim on line "THE CLAIM" below, and nothing may: the ordering
    it enforces is the whole point of 260731-EFA-L5 R3.
    """
    assert args.contract_path is not None
    report_operation_progress(args, "preflight", current_command="validate closeout eligibility")
    contract = load_contract(args.contract_path)
    recovered = _recover_closeout_finalization(contract, args)
    if recovered is not None:
        certify_queue_candidate_closeout(load_contract(args.contract_path), args.operation_key)
        return recovered
    _validate_closeout_source_state(contract)
    _refuse_series_code_commit(contract)
    if args.dry_run:
        return WorktreeCommandResult(0, closeout_preview_payload(contract, args))
    if args.operation_key and not args.candidate_tree:
        raise RuntimeError("closeout operation is missing its accepted candidate tree")
    if not args.candidate_tree:
        args = replace(args, candidate_tree=code_candidate_tree(contract))
    route_review = require_current_route_review(contract)
    approval_note = _closeout_approval_note(args)
    resuming = args.approval_claimed or args.recovery_commits is not None
    if not resuming:
        _refuse_unsatisfied_closeout_gate(contract, args)
    worklist = closeout_changed_paths(contract)
    code_would_commit = code_change_present(contract)
    if resuming:
        attestations = _CloseoutAttestations()
        code_quality_gate = {
            "status": "recovered-post-claim",
            "passed": True,
            "reason": "the accepted candidate resumes after its durable approval claim",
        }
        memory_quality_before_refresh = {}
        strict_code_quality_required = contract.kind == "leaf" and requires_strict_code_quality(
            contract.code_worktree,
            code_would_commit=code_would_commit,
            required_when_missing=requires_integrated_acceptance(contract.repo_name),
        )
    else:
        attestations = _closeout_attestations(contract, worklist)
        code_quality_gate, memory_quality_before_refresh, strict_code_quality_required = (
            _closeout_quality_preflight(contract, args, code_would_commit=code_would_commit)
        )
    accepted_candidate_tree = cast(str, args.candidate_tree)
    _revalidate_reviewed_candidate(contract, route_review, accepted_candidate_tree)
    claim_queue_candidate_for_closeout(contract, args.operation_key)
    committed = _closeout_commit_phase(
        contract,
        args,
        worklist=worklist,
        memory_quality_before_refresh=memory_quality_before_refresh,
        strict_code_quality_required=strict_code_quality_required,
    )
    updated = _amended_closeout_contract(
        contract,
        approval_note,
        committed.code_commit,
        committed.memory,
        bool(committed.integration_reopen["reopened"]),
    )
    report_operation_progress(
        args,
        "contract-finalization",
        current_command="finalize closeout contract edge",
        recovery_commits={
            "codeCommit": committed.code_commit,
            "memoryContentCommit": committed.memory.memory_commit,
            "ledgerCommit": committed.memory.ledger_commit,
        },
    )
    write_contract(contract.contract_path, updated)
    certify_queue_candidate_closeout(updated, args.operation_key)
    return WorktreeCommandResult(
        0,
        _closed_result_payload(
            updated,
            _CloseoutResultFacts(
                code_commit=committed.code_commit,
                memory=committed.memory,
                attestations=attestations,
                code_quality_gate=code_quality_gate,
                integration_reopen=committed.integration_reopen,
                gate_guard=committed.gate_guard,
            ),
        ),
    )
