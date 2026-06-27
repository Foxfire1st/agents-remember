from __future__ import annotations

from dataclasses import replace
from typing import Any

from agents_remember.controlplane.enforcement import CloseoutGuard, evaluate_closeout_gate
from agents_remember.controlplane.records import apply_gate
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.memory_ledger import (
    find_mapping,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.observer.events import now_iso
from agents_remember.observer.paths import observer_logs_root
from agents_remember.worktrees.modules.args import WorktreeArgs
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
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import (
    PATH_SAMPLE_LIMIT,
    EntityFingerprintRefreshPlan,
    OnboardingRefreshPlan,
    RouteOverviewBodyClassification,
    RouteOverviewRefreshPlan,
    SidecarBodyClassification,
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
from agents_remember.worktrees.worktree_contract import load_contract, write_contract


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


def closeout_preview_payload(contract, args: WorktreeArgs) -> dict[str, object]:
    ledger_message = (
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: <code_commit> -> <memory_commit>"
    )
    code_dirty = worktree_dirty(contract.code_worktree)
    memory_dirty = contract.memory_mode == "external" and worktree_dirty(contract.memory_worktree)
    worklist = closeout_changed_paths(contract)
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
    memory_would_commit = memory_dirty or _refresh_plans_have_work(
        metadata_refresh, entity_refresh, route_overview_refresh, route_index_refresh
    )
    return {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": "Closeout preview only; no commits were created. External-memory closeout will commit code first, refresh onboarding verification metadata, affected entity fingerprints, route overview metadata, and route indexes to that code commit, run memory_quality_check, then commit memory and ledger.",
        **next_guidance(
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
            "commit-code",
            "refresh-onboarding-metadata-and-entity-fingerprints",
            "refresh-route-overview-metadata-and-indexes",
            "run-memory-quality-check",
            "commit-memory-content",
            "update-ledger",
            "commit-ledger",
            "update-contract",
        ],
        "changed_code_paths": _bounded_paths(changed_paths),
        "changed_code_paths_committed": _bounded_paths(worklist["committed"]),
        "onboarding_metadata_refresh": _bounded_refresh_plan_view(metadata_refresh),
        "sidecar_body_gate": _bounded_classification_view(sidecar_body_gate),
        "sidecars_attested_no_impact": _bounded_paths(sidecar_body_gate["attested_no_impact"]),
        "entity_fingerprint_refresh": entity_refresh,
        "route_overview_metadata_refresh": route_overview_refresh,
        "route_overview_body_gate": route_overview_body_gate,
        "route_overviews_attested_no_impact": route_overview_body_gate["attested_no_impact"],
        "route_index_refresh": route_index_refresh,
        "integration_reopen": _preview_integration_reopen(
            contract, code_dirty=code_dirty, memory_would_commit=memory_would_commit
        ),
        "closeout_gate": _closeout_gate_payload(_closeout_gate_guard(contract)),
        "proposed_commits": {
            "code": {
                "would_commit": code_dirty,
                "message": args.code_commit_message,
                "worktree": contract.code_worktree.as_posix(),
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
        },
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


def _closeout_gate_guard(contract) -> CloseoutGuard | None:
    """The lifecycle's closeout-gate verdict, or ``None`` when the lifecycle is gateless.

    Reads the same gate log the dashboard writes -- the observer root under the
    contract's coordination root, keyed by ``contract.lifecycle_id``. A pure read;
    whether an unsatisfied verdict raises is the caller's choice, so the preview can
    surface the verdict without failing.
    """
    if not contract.lifecycle_id:
        return None
    store = GateStore(observer_logs_root(contract.coordination_root))
    return evaluate_closeout_gate(store.current(contract.lifecycle_id))


def _enforce_closeout_gate(contract) -> CloseoutGuard | None:
    """Server-side gate enforcement (slice 6b): block closeout on an unsatisfied gate.

    A dashboard-opened ``closeout-approval`` gate is binding; a gateless lifecycle
    falls back to the chat commit gate (``--approved`` + note), unchanged. The agent
    cannot satisfy the gate itself: its own ``gate_decide`` is ``decidedBy="model"``,
    which :func:`evaluate_closeout_gate` rejects.
    """
    guard = _closeout_gate_guard(contract)
    if guard is not None and not guard.permitted:
        raise RuntimeError(f"closeout blocked by gate enforcement: {guard.reason}")
    return guard


def _mark_closeout_gate_applied(contract, gate_id: str) -> None:
    """Append an ``applied`` snapshot so one approval cannot be replayed by a second closeout."""
    store = GateStore(observer_logs_root(contract.coordination_root))
    gate = store.current(contract.lifecycle_id).get(gate_id)
    if gate is not None:
        store.append(apply_gate(gate, now=now_iso()))


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


def _run_memory_quality_gate(context) -> dict[str, Any]:
    result = run_memory_quality_check(
        context.onboarding_root,
        drift_context=DriftCheckContext(
            code_repository_root=context.code_repository_root,
            context=context,
            detail_limit=50,
        ),
    )
    if not result.get("ok", False):
        raise RuntimeError(_memory_quality_failure_message(result))
    return result


def _external_closeout_commits(
    contract,
    args: WorktreeArgs,
    changed_paths: list[str],
    code_commit: str,
    code_commit_date: str,
    *,
    working_paths: list[str],
) -> tuple[
    str,
    str,
    list[dict[str, str]],
    list[dict[str, object]],
    list[dict[str, str]],
    dict[str, object],
    dict[str, object],
]:
    if contract.memory_worktree is None or contract.ledger_path is None:
        raise RuntimeError("external-memory closeout requires memory worktree and ledger path")
    context = _closeout_contract_context(contract)
    refreshed_onboarding = refresh_onboarding_metadata(
        contract, changed_paths, code_commit, code_commit_date, working_paths=working_paths
    )
    refreshed_route_overviews = refresh_route_overview_metadata_for_context(
        context,
        changed_paths,
        code_commit,
        code_commit_date,
        memory_tree=contract.memory_worktree,
        memory_verified_commit=contract_memory_verified_commit(contract),
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(context, changed_paths)
    route_index_refresh = refresh_route_indexes_for_context(context)
    memory_quality = _run_memory_quality_gate(context)
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
    return (
        memory_commit,
        ledger_commit,
        refreshed_onboarding,
        refreshed_entities,
        refreshed_route_overviews,
        route_index_refresh,
        memory_quality,
    )


def closeout_result(args: WorktreeArgs) -> WorktreeCommandResult:
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    _validate_closeout_source_heads(contract)
    if args.dry_run:
        return WorktreeCommandResult(0, closeout_preview_payload(contract, args))
    approval_note = _closeout_approval_note(args)
    gate_guard = _enforce_closeout_gate(contract)

    worklist = closeout_changed_paths(contract)
    changed_paths = worklist["all"]
    attested_sidecars: list[str] = []
    attested_overviews: list[str] = []
    stamped_overviews: list[str] = []
    unonboarded_paths: list[str] = []
    if contract.memory_mode == "external":
        sidecar_plan = validate_onboarding_refresh_plan(
            contract, changed_paths, working_paths=worklist["working"]
        )
        unonboarded_paths = sidecar_plan["unonboarded"]
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
        attested_overviews = overview_gate["attested_no_impact"]
        stamped_overviews = overview_gate["stamped_without_body_review"]
    code_commit = commit_if_dirty(contract.code_worktree, args.code_commit_message)
    code_commit_date = commit_date(contract.code_worktree, code_commit)
    memory_commit = ""
    ledger_commit = ""
    refreshed_onboarding: list[dict[str, str]] = []
    refreshed_entities: list[dict[str, object]] = []
    refreshed_route_overviews: list[dict[str, str]] = []
    route_index_refresh: dict[str, object] = {}
    memory_quality: dict[str, object] = {}
    if contract.memory_mode == "external":
        (
            memory_commit,
            ledger_commit,
            refreshed_onboarding,
            refreshed_entities,
            refreshed_route_overviews,
            route_index_refresh,
            memory_quality,
        ) = _external_closeout_commits(
            contract,
            args,
            changed_paths,
            code_commit,
            code_commit_date,
            working_paths=worklist["working"],
        )
    integration_reopen = _completed_integration_reopen(
        contract,
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    reopened = bool(integration_reopen["reopened"])
    updated = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        commit_approval_note=approval_note,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
        integration_status="not-started" if reopened else contract.integration_status,
        integration_strategy="" if reopened else contract.integration_strategy,
        integrated_code_commit="" if reopened else contract.integrated_code_commit,
        integrated_memory_content_commit=""
        if reopened
        else contract.integrated_memory_content_commit,
        integrated_ledger_commit="" if reopened else contract.integrated_ledger_commit,
        cleanup="pending" if reopened else contract.cleanup,
    )
    write_contract(contract.contract_path, updated)
    if gate_guard is not None and gate_guard.gate_id is not None:
        _mark_closeout_gate_applied(contract, gate_guard.gate_id)
    return WorktreeCommandResult(
        0,
        {
            "state": "closed",
            **status_payload(updated),
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            "code_commit": code_commit,
            "memory_content_commit": memory_commit,
            "ledger_commit": ledger_commit,
            "refreshed_onboarding": _bounded_paths(
                [item["source_path"] for item in refreshed_onboarding]
            ),
            "sidecars_attested_no_impact": _bounded_paths(attested_sidecars),
            "unonboarded_changed_paths": _bounded_paths(unonboarded_paths),
            "refreshed_entities": refreshed_entities,
            "refreshed_route_overviews": refreshed_route_overviews,
            "route_overviews_attested_no_impact": attested_overviews,
            "route_overviews_stamped_without_body_review": stamped_overviews,
            "route_index_refresh": route_index_refresh,
            "memory_quality": memory_quality,
            "integration_reopen": integration_reopen,
            "closeout_gate": _closeout_gate_payload(gate_guard),
        },
    )
