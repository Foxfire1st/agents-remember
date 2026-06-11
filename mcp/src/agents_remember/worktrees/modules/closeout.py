from __future__ import annotations

from dataclasses import replace
from typing import Any

from agents_remember.kernel.memory_ledger import (
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.context import contract_context, resolve_context
from agents_remember.worktrees.modules.git import (
    changed_worktree_paths,
    commit_date,
    commit_if_dirty,
    current_branch,
    head_commit,
    require_git,
    worktree_dirty,
)
from agents_remember.worktrees.modules.guidance import (
    contract_next_args,
    next_guidance,
    status_payload,
)
from agents_remember.worktrees.modules.models import (
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
    entity_fingerprint_refresh_plan,
    entity_fingerprint_refresh_plan_for_context,
    onboarding_refresh_plan,
    onboarding_refresh_plan_for_context,
    refresh_entity_fingerprints_for_context,
    refresh_onboarding_metadata,
    refresh_onboarding_metadata_for_context,
    refresh_route_indexes_for_context,
    refresh_route_overview_metadata_for_context,
    route_index_refresh_plan_for_context,
    route_overview_metadata_refresh_plan,
    route_overview_metadata_refresh_plan_for_context,
    validate_onboarding_refresh_plan,
    validate_onboarding_refresh_plan_for_context,
    validate_route_overview_refresh_plan,
    validate_route_overview_refresh_plan_for_context,
)
from agents_remember.worktrees.worktree_contract import load_contract, write_contract


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


def closeout_preview_payload(contract, args: WorktreeArgs) -> dict[str, object]:
    ledger_message = (
        args.ledger_commit_message
        or f"[{contract.task_id}] Ledger sync: <code_commit> -> <memory_commit>"
    )
    code_dirty = worktree_dirty(contract.code_worktree)
    memory_dirty = contract.memory_mode == "external" and worktree_dirty(contract.memory_worktree)
    changed_paths = changed_worktree_paths(contract.code_worktree)
    metadata_refresh: OnboardingRefreshPlan = (
        onboarding_refresh_plan(contract, changed_paths)
        if contract.memory_mode == "external"
        else {
            "required": [],
            "missing": [],
            "unsupported": [],
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
        )
        if contract.memory_mode == "external"
        else {
            "stale": [],
            "untraced": [],
            "attested_no_impact": [],
            "stamped_without_body_review": [],
        }
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
        "changed_code_paths": changed_paths,
        "onboarding_metadata_refresh": metadata_refresh,
        "sidecar_body_gate": sidecar_body_gate,
        "sidecars_attested_no_impact": sidecar_body_gate["attested_no_impact"],
        "entity_fingerprint_refresh": entity_refresh,
        "route_overview_metadata_refresh": route_overview_refresh,
        "route_overview_body_gate": route_overview_body_gate,
        "route_overviews_attested_no_impact": route_overview_body_gate["attested_no_impact"],
        "route_index_refresh": route_index_refresh,
        "proposed_commits": {
            "code": {
                "would_commit": code_dirty,
                "message": args.code_commit_message,
                "worktree": contract.code_worktree.as_posix(),
            },
            "memory": {
                "would_commit": memory_dirty
                or _refresh_plans_have_work(
                    metadata_refresh, entity_refresh, route_overview_refresh, route_index_refresh
                ),
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
    if current_code_source != contract.code_base_commit:
        raise RuntimeError(
            "code source branch moved since task start: "
            f"{contract.code_source_branch} is {current_code_source}, expected {contract.code_base_commit}"
        )
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_base_commit
    ):
        current_memory_source = head_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        if current_memory_source != contract.memory_base_commit:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, expected {contract.memory_base_commit}"
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
        contract, changed_paths, code_commit, code_commit_date
    )
    refreshed_route_overviews = refresh_route_overview_metadata_for_context(
        context, changed_paths, code_commit, code_commit_date
    )
    refreshed_entities = refresh_entity_fingerprints_for_context(
        context, changed_paths
    )
    route_index_refresh = refresh_route_indexes_for_context(context)
    memory_quality = _run_memory_quality_gate(context)
    memory_commit = commit_if_dirty(contract.memory_worktree, args.memory_commit_message)
    ledger = load_ledger(contract.ledger_path)
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

    changed_paths = changed_worktree_paths(contract.code_worktree)
    attested_sidecars: list[str] = []
    attested_overviews: list[str] = []
    stamped_overviews: list[str] = []
    if contract.memory_mode == "external":
        sidecar_plan = validate_onboarding_refresh_plan(contract, changed_paths)
        attested_sidecars = classify_sidecar_updates(
            contract_context(contract), sidecar_plan, memory_tree=contract.memory_worktree
        )["attested_no_impact"]
        overview_plan = validate_route_overview_refresh_plan(contract, changed_paths)
        overview_gate = classify_route_overview_updates(
            contract_context(contract),
            overview_plan,
            changed_paths,
            memory_tree=contract.memory_worktree,
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
        ) = (
            _external_closeout_commits(contract, args, changed_paths, code_commit, code_commit_date)
        )
    updated = replace(
        contract,
        human_review_status="approved",
        approved_for_commit=True,
        commit_approval_note=approval_note,
        closeout_status="completed",
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    write_contract(contract.contract_path, updated)
    return WorktreeCommandResult(
        0,
        {
            "state": "closed",
            **status_payload(updated),
            "summary": "Closeout completed; integrate the task branches back into their source branches.",
            "code_commit": code_commit,
            "memory_content_commit": memory_commit,
            "ledger_commit": ledger_commit,
            "refreshed_onboarding": refreshed_onboarding,
            "sidecars_attested_no_impact": attested_sidecars,
            "refreshed_entities": refreshed_entities,
            "refreshed_route_overviews": refreshed_route_overviews,
            "route_overviews_attested_no_impact": attested_overviews,
            "route_overviews_stamped_without_body_review": stamped_overviews,
            "route_index_refresh": route_index_refresh,
            "memory_quality": memory_quality,
        },
    )
