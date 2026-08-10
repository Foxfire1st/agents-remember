from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agents_remember.controlplane.enforcement import (
    CLOSEOUT_GATE_KIND,
    CloseoutGuard,
    evaluate_closeout_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.observer.events import now_iso
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.code_quality_gate import (
    QualityGatePlan,
    code_quality_gate_preview,
    requires_strict_code_quality,
    run_strict_code_quality_gate,
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
from agents_remember.worktrees.services import worktree_services
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
    code_dirty = worktree_dirty(contract.code_worktree)
    memory_dirty = contract.memory_mode == "external" and worktree_dirty(contract.memory_worktree)
    worklist = closeout_changed_paths(contract)
    changed_paths = worklist["all"]
    refresh = _memory_refresh_preview(contract, worklist)
    memory_would_commit = memory_dirty or _refresh_plans_have_work(
        refresh.metadata, refresh.entities, refresh.route_overviews, refresh.route_indexes
    )
    code_quality_gate = code_quality_gate_preview(
        contract.code_worktree,
        code_would_commit=code_dirty,
        diff_base=contract.code_base_commit,
        plan=QualityGatePlan(mode="targeted"),
    )
    return {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": (
            "Closeout preview only; no commits were created. For external memory, the "
            "working-tree memory-quality preflight runs before staging or any code-quality "
            "subprocess, so a broken citation aborts before Pyright or pytest. The staging "
            "step and its two refusals belong to the leaf change-set-scoped quality gate: "
            "when code would commit and this checkout carries the quality wrapper, closeout "
            "refuses a non-task checkout or unresolved conflicts; otherwise it stages the "
            "whole task worktree and runs the leaf's targeted contract over exactly what it "
            "will commit. The full wrapper runs once per master at the memory-capped master "
            "integration gate. After the code commit, external-memory closeout refreshes "
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
        "approval_question": "Approve creating the code, memory, and ledger commits with these messages?",
        "closeout_order": [
            "run-working-tree-memory-quality-preflight-before-code-quality",
            "refuse-if-gate-would-run-and-code-checkout-is-not-the-tasks-own-worktree",
            "refuse-if-gate-would-run-and-code-worktree-has-unresolved-merge-conflicts",
            "reset-and-stage-whole-task-worktree-if-gate-would-run",
            "run-strict-code-quality-over-that-staged-content",
            "commit-code",
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
    return evaluate_closeout_gate(store.current(contract.lifecycle_id), policy=args.gate_policy)


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


def _format_memory_quality_finding(finding: dict[str, Any]) -> str:
    path = str(finding.get("path") or finding.get("sourceFile") or "")
    code = str(finding.get("code") or finding.get("check") or "memory_quality")
    message = str(finding.get("message") or "")
    return f"{code}{f' at {path}' if path else ''}: {message}"


def _memory_quality_failure_message(result: dict[str, Any]) -> str:
    finding_count = int(result.get("findingCount", 0))
    findings = result.get("findings", [])
    sample: list[str] = []
    if isinstance(findings, list):
        sample = [
            _format_memory_quality_finding(finding)
            for finding in findings[:5]
            if isinstance(finding, dict)
        ]
    details = "; ".join(sample)
    if details:
        details = f" Findings: {details}"
    return (
        "external-memory closeout requires a clean memory_quality_check before memory commit; "
        f"findingCount={finding_count}.{details} Fix memory/onboarding issues, rerun "
        "memory_quality_check, then rerun closeout."
    )


def _run_memory_quality_phase(
    context,
    checks: tuple[str, ...],
    *,
    unstamped_code_commit: str | None = None,
) -> dict[str, Any]:
    result = worktree_services().memory_quality.run_check(
        context.onboarding_root,
        checks=checks,
        drift_context=worktree_services().memory_quality.drift_context(
            code_repository_root=context.code_repository_root,
            context=context,
            detail_limit=50,
            unstamped_code_commit=unstamped_code_commit,
        ),
    )
    if not result.get("ok", False):
        raise RuntimeError(_memory_quality_failure_message(result))
    return result


def _combined_memory_quality(
    before_refresh: dict[str, Any], after_refresh: dict[str, Any]
) -> dict[str, Any]:
    """One official closeout gate, reported after both temporal phases pass.

    The before phase may be empty: claim evidence is only comparable once the commit it must
    be compared against exists, so phases with no declared checks contribute no result at all.
    """
    before_checks, after_checks = worktree_services().memory_quality.check_groups()
    report_only_sample = [
        *before_refresh.get("reportOnlySample", []),
        *after_refresh["reportOnlySample"],
    ][:50]
    return {
        "ok": True,
        "checks": {**before_refresh.get("checks", {}), **after_refresh["checks"]},
        "findingCount": before_refresh.get("findingCount", 0) + after_refresh["findingCount"],
        "findings": [*before_refresh.get("findings", []), *after_refresh["findings"]],
        "reportOnlyFindingCount": (
            before_refresh.get("reportOnlyFindingCount", 0)
            + after_refresh["reportOnlyFindingCount"]
        ),
        "reportOnlySample": report_only_sample,
        "reportOnlySampleCount": len(report_only_sample),
        "closeoutPhases": {
            "beforeMetadataRefresh": list(before_checks),
            "afterMetadataRefresh": list(after_checks),
        },
    }


@dataclass(frozen=True)
class _MemoryCloseoutOutcome:
    """What external-memory closeout committed and refreshed.

    The defaults are what internal-memory closeout produces: no memory commit, no ledger
    commit, nothing refreshed. That is why the caller can build one unconditionally and
    replace it only when the task carries external memory, instead of initialising seven
    separate names above an ``if`` and hoping every arm assigns all of them.
    """

    memory_commit: str = ""
    ledger_commit: str = ""
    refreshed_onboarding: list[dict[str, str]] = field(default_factory=list)
    refreshed_entities: list[dict[str, object]] = field(default_factory=list)
    refreshed_route_overviews: list[dict[str, str]] = field(default_factory=list)
    route_index_refresh: dict[str, object] = field(default_factory=dict)
    memory_quality: dict[str, object] = field(default_factory=dict)


def _external_closeout_commits(
    contract,
    args: WorktreeArgs,
    change: VerifiedChange,
    memory_quality_before_refresh: dict[str, Any],
) -> _MemoryCloseoutOutcome:
    if contract.memory_worktree is None or contract.ledger_path is None:
        raise RuntimeError("external-memory closeout requires memory worktree and ledger path")
    code_commit = change.commit
    context = _closeout_contract_context(contract)
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
    memory_quality_after_refresh = _run_memory_quality_phase(context, after_checks)
    memory_quality = _combined_memory_quality(
        memory_quality_before_refresh, memory_quality_after_refresh
    )
    memory_content_dirty = worktree_dirty(contract.memory_worktree)
    memory_commit = (
        commit_if_dirty(contract.memory_worktree, args.memory_commit_message)
        if memory_content_dirty
        else contract.memory_content_commit or head_commit(contract.memory_worktree)
    )
    ledger = load_ledger(contract.ledger_path)
    existing_mapping = find_mapping(ledger, code_commit)
    if existing_mapping is not None and existing_mapping.memory_commit == memory_commit:
        ledger_commit = contract.ledger_commit or head_commit(contract.memory_worktree)
    else:
        write_ledger(contract.ledger_path, prepend_mapping(ledger, code_commit, memory_commit))
        require_git(contract.memory_worktree, ["add", "memory.md"])
        ledger_commit = commit_if_dirty(
            contract.memory_worktree,
            args.ledger_commit_message
            or f"[{contract.task_id}] Ledger sync: {code_commit} -> {memory_commit}",
        )
    return _MemoryCloseoutOutcome(
        memory_commit=memory_commit,
        ledger_commit=ledger_commit,
        refreshed_onboarding=refreshed_onboarding,
        refreshed_entities=refreshed_entities,
        refreshed_route_overviews=refreshed_route_overviews,
        route_index_refresh=route_index_refresh,
        memory_quality=memory_quality,
    )


def _refuse_outside_a_linked_worktree(code_worktree: Path) -> None:
    """Refuse to stage anywhere except a task's own throwaway worktree.

    Staging is safe here because of what this checkout *is*, not because of what closeout
    is careful to do with it. A leaf's ``code_worktree`` is created by ``worktree_start``
    and destroyed by ``lifecycle_finalize_task``; it is agent scratch space with no human
    in it, so leaving it fully staged costs nobody anything -- ``commit_if_dirty`` was
    going to run ``git add -A`` over it a moment later regardless. In a checkout somebody
    works in, the same ``add -A`` overwrites a ``git add -p`` selection, stages files
    deliberately left out, and resolves an in-progress merge to whatever is on disk.

    That is not hypothetical: :func:`default_series_contract` sets
    ``code_worktree=code.repo_path`` for a ``kind: "series"`` contract, which is the
    primary checkout itself. Nothing else stops such a contract reaching
    ``worktree_closeout_apply``, so this is the guard that does.

    The test is git's own definition of a linked worktree -- ``--git-dir`` differs from
    ``--git-common-dir`` -- rather than the contract's ``kind``, and the difference matters.
    ``kind`` is a label sitting next to the path; this constrains the path that is about to
    be written. A leaf contract whose ``code_worktree`` had been pointed at the primary
    checkout would pass a ``kind`` check and still stage in somebody's working repository,
    and a series contract that genuinely pointed at a disposable worktree would be refused
    by one for no reason. Checking the property that makes staging safe is both necessary
    and sufficient; checking the label is neither.
    """
    git_dir, common_dir = require_git(
        code_worktree, ["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"]
    ).splitlines()
    if git_dir == common_dir:
        raise RuntimeError(
            "closeout refuses to run the strict code-quality gate here: it stages the whole code"
            f" checkout before the gate, and {code_worktree} is not a task worktree -- git"
            f" reports its git dir as {git_dir}, which is the repository's own, so this is a"
            " checkout a person works in. Staging it would overwrite a partial 'git add -p'"
            " selection, stage files deliberately held back, and resolve any merge in progress"
            " to whatever is on disk. Nothing was staged and nothing was committed. Closeout"
            " stages only the disposable worktree a task was started with; run it against the"
            " leaf contract whose code_worktree is that worktree. A series or master contract"
            " records the repository path itself and is not closed out this way."
        )


def _refuse_conflicted_worktree(code_worktree: Path) -> None:
    """Refuse, before staging anything, when the checkout has unresolved conflicts.

    ``git add -A`` over an unmerged index does not fail: it *resolves* every conflict by
    taking whatever the working tree holds, which is the file with the ``<<<<<<<`` markers
    still in it, and closeout then commits that. Closeout did exactly that before this
    check existed, so this refusal is a behaviour change worth naming rather than a guard
    against something that could not happen.

    It replaces the plumbing message the reader would otherwise be shown -- a ruff syntax
    error inside a conflict marker, or nothing at all when the conflicted file is not
    Python -- with the state git is actually in and what to do about it.
    """
    conflicted = require_git(code_worktree, ["diff", "--name-only", "--diff-filter=U"]).splitlines()
    if conflicted:
        raise RuntimeError(
            "closeout cannot stage the code worktree for the strict code-quality gate: the"
            f" index has {len(conflicted)} unmerged path(s), so a merge, rebase, cherry-pick or"
            " revert is still in progress with its conflicts unresolved"
            f" ({', '.join(conflicted[:PATH_SAMPLE_LIMIT])})."
            " Nothing was staged and nothing was committed. Resolve the conflicts, stage the"
            " resolutions, then rerun closeout -- staging a conflicted worktree would commit the"
            " conflict markers themselves."
        )


def _gate_staged_code(code_worktree: Path, *, diff_base: str) -> dict[str, object]:
    """Reset and stage the task worktree, then run the targeted leaf gate over exactly what it commits.

    Every rail of the gate reads the index. ``derive_scope`` lists what ruff and pyright
    are given with ``git ls-files``; ``diff_coverage`` diffs the base against the tracked
    tree, which is likewise blind to a file git has never been told about. Closeout commits
    with ``git add -A``, so until it staged first, any file the task *created* -- as opposed
    to edited -- went into the commit without a single rail of the gate reading a line of
    it, and the gate reported green having never seen it. Leaf 3's ``abc7cbcc`` shipped four
    files that way. The index cut both ways, too: a path the task deleted stayed in
    ``ls-files`` until the deletion was staged, so ruff was handed a file that no longer
    existed and took an ``E902`` for it.

    Staging first is what makes the gate's scope and the commit's content one set, by
    construction rather than by a second enumeration that has to be kept in step. The
    alternative -- widening ``derive_scope`` to ``--cached --others --exclude-standard`` --
    would redefine the pre-commit tier, where staged content is precisely the point, and
    could not reach the coverage floor at all, since an untracked file has no diff against
    any base.

    The mixed reset is what makes a retry mean the same thing as a first run. ``add -A``
    on its own does not: git applies ignore rules only to files it does not already track
    or have staged, so a path staged by a refused attempt stays staged even after the retry
    adds it to ``.gitignore``, and the commit carries it. That is this leaf's own history --
    a ``.dmypy.json`` a type checker had dropped in the worktree was staged by a refused
    attempt, ignored on the retry, and committed anyway. Resetting first means each run
    recomputes the index from the working tree under the ignore rules in force *now*,
    instead of inheriting whatever the last attempt happened to leave behind. It costs
    nothing to do: ``--mixed`` is index-only, so the tree the gate is about to certify is
    byte-for-byte what the task left on disk.

    The reset goes after both refusals, not before either of them. Ahead of the first it
    would inflict the exact damage that refusal exists to prevent -- a mixed reset in a
    checkout somebody works in discards their ``git add -p`` selection, and that refusal
    promises nothing in the checkout was touched. Ahead of the second it would disarm it
    silently: ``git reset`` drops the unmerged index entries and removes ``MERGE_HEAD``,
    so ``diff --diff-filter=U`` would report nothing, the conflict refusal would never fire
    again, and ``add -A`` would go on to stage the ``<<<<<<<`` markers it was added to keep
    out of a commit. Reset-then-add is one step and belongs wholly downstream of both
    checks; nothing in it may run until they have both passed.

    There is no rollback here and none is wanted. The staging is not undone if the gate
    refuses, because this worktree is the task's own disposable checkout
    (:func:`_refuse_outside_a_linked_worktree` is what makes that true rather than assumed),
    nobody is holding a partial staging in it, and the reset means the next attempt does not
    inherit it in any case. A previous attempt saved the index file aside and copied it
    back, and that machinery is gone rather than fixed: it could not survive
    ``core.splitIndex`` (the saved pointer outlives the ``sharedindex.<sha>`` that
    ``add -A`` expires, leaving ``status`` exiting 128), it could not survive ``SIGTERM``,
    which is how an MCP server actually dies, and every guarantee it tried to offer was
    about a person who is never in this checkout.
    """
    _refuse_outside_a_linked_worktree(code_worktree)
    _refuse_conflicted_worktree(code_worktree)
    require_git(code_worktree, ["reset", "--mixed", "--quiet", "HEAD"])
    require_git(code_worktree, ["add", "-A"])
    return run_strict_code_quality_gate(
        code_worktree, diff_base=diff_base, plan=QualityGatePlan(mode="targeted")
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
    memory: _MemoryCloseoutOutcome,
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


def closeout_result(args: WorktreeArgs) -> WorktreeCommandResult:
    """Run closeout for real, in the order the preview promised.

    Nothing moved across the claim on line "THE CLAIM" below, and nothing may: the ordering
    it enforces is the whole point of 260731-EFA-L5 R3.
    """
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    _validate_closeout_source_heads(contract)
    if args.dry_run:
        return WorktreeCommandResult(0, closeout_preview_payload(contract, args))
    approval_note = _closeout_approval_note(args)
    _refuse_unsatisfied_closeout_gate(contract, args)

    worklist = closeout_changed_paths(contract)
    changed_paths = worklist["all"]
    attestations = _closeout_attestations(contract, worklist)
    code_would_commit = worktree_dirty(contract.code_worktree)
    code_quality_gate = code_quality_gate_preview(
        contract.code_worktree,
        code_would_commit=code_would_commit,
        diff_base=contract.code_base_commit,
        plan=QualityGatePlan(mode="targeted"),
    )
    # The citation gate is working-tree semantics and rejects in seconds, so it runs BEFORE the
    # expensive wrapper and the code commit: the curator clears it during the leaf with the same
    # memory_quality_check, and a failure here is the exception, not the rule.
    memory_quality_before_refresh: dict[str, Any] = {}
    if contract.memory_mode == "external":
        before_checks, _ = worktree_services().memory_quality.check_groups()
        memory_quality_before_refresh = _run_memory_quality_phase(
            _closeout_contract_context(contract),
            before_checks,
            unstamped_code_commit=contract.code_base_commit,
        )
    if requires_strict_code_quality(contract.code_worktree, code_would_commit=code_would_commit):
        code_quality_gate = _gate_staged_code(
            contract.code_worktree, diff_base=contract.code_base_commit
        )
    # THE CLAIM, and it goes exactly here: the last line before the first irreversible act.
    # Everything above only reads or touches the index of the task's own disposable worktree, so a
    # refusal up there must not spend the approval; everything below writes a commit somebody
    # would have to undo, so none of it may run on an approval this closeout has not already
    # consumed. Do not move this line down past the commit -- that is R3, and it is what let a
    # closeout finish its mutations and leave the approval spendable.
    gate_guard = _claim_closeout_gate(contract, args)
    code_commit = commit_if_dirty(contract.code_worktree, args.code_commit_message)
    code_commit_date = commit_date(contract.code_worktree, code_commit)
    memory = _MemoryCloseoutOutcome()
    if contract.memory_mode == "external":
        memory = _external_closeout_commits(
            contract,
            args,
            VerifiedChange(
                commit=code_commit,
                commit_date=code_commit_date,
                changed_paths=changed_paths,
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
    updated = _amended_closeout_contract(
        contract,
        approval_note,
        code_commit,
        memory,
        bool(integration_reopen["reopened"]),
    )
    write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "closed",
            **status_payload(updated),
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            "code_commit": code_commit,
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
            "code_quality_gate": code_quality_gate,
            "integration_reopen": integration_reopen,
            "closeout_gate": _closeout_gate_payload(gate_guard),
        },
    )
