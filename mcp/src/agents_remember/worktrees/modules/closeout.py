from __future__ import annotations

from dataclasses import replace
from pathlib import Path
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
from agents_remember.worktrees.modules.code_quality_gate import (
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
    code_quality_gate = code_quality_gate_preview(
        contract.code_worktree,
        code_would_commit=code_dirty,
        diff_base=contract.code_base_commit,
    )
    return {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": "Closeout preview only; no commits were created. The staging step and its two refusals belong to the strict code-quality gate, so they apply exactly when this preview reports the gate as enforced: when code would commit and this checkout carries the quality wrapper, closeout refuses outright if the code checkout is not the task's own worktree or has unresolved merge conflicts; otherwise it resets the index and stages the whole task worktree so the gate's scope is the commit's content -- files the task created included, not only the ones it edited -- and runs strict project-owned code quality over exactly that. A refused gate leaves the worktree staged and commits nothing; nothing is unstaged, because a retry resets and restages from the working tree and so reaches the same content a first run would. A checkout carrying no wrapper runs no gate, stages nothing early, and commits as it always has. External-memory closeout then refreshes onboarding verification metadata, affected entity fingerprints, route overview metadata, and route indexes to that code commit, runs memory_quality_check, and commits memory and ledger.",
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
            "refuse-if-gate-would-run-and-code-checkout-is-not-the-tasks-own-worktree",
            "refuse-if-gate-would-run-and-code-worktree-has-unresolved-merge-conflicts",
            "reset-and-stage-whole-task-worktree-if-gate-would-run",
            "run-strict-code-quality-over-that-staged-content",
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
        "code_quality_gate": code_quality_gate,
        "integration_reopen": _preview_integration_reopen(
            contract, code_dirty=code_dirty, memory_would_commit=memory_would_commit
        ),
        "closeout_gate": _closeout_gate_payload(_closeout_gate_guard(contract, args)),
        "proposed_commits": {
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


def _enforce_closeout_gate(contract, args: WorktreeArgs) -> CloseoutGuard | None:
    """Server-side gate enforcement (slice 6b): block closeout on an unsatisfied gate.

    A dashboard-opened ``closeout-approval`` gate is binding; a gateless lifecycle
    falls back to the chat commit gate (``--approved`` + note), unchanged. The agent
    cannot satisfy the gate itself: its own ``gate_decide`` is ``decidedBy="model"``,
    which :func:`evaluate_closeout_gate` rejects.
    """
    guard = _closeout_gate_guard(contract, args)
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
    change: VerifiedChange,
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
    """Reset and stage the task worktree, then run the strict gate over exactly what it commits.

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
    return run_strict_code_quality_gate(code_worktree, diff_base=diff_base)


def closeout_result(args: WorktreeArgs) -> WorktreeCommandResult:
    assert args.contract_path is not None
    contract = load_contract(args.contract_path)
    _validate_closeout_source_heads(contract)
    if args.dry_run:
        return WorktreeCommandResult(0, closeout_preview_payload(contract, args))
    approval_note = _closeout_approval_note(args)
    gate_guard = _enforce_closeout_gate(contract, args)

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
    code_would_commit = worktree_dirty(contract.code_worktree)
    code_quality_gate = code_quality_gate_preview(
        contract.code_worktree,
        code_would_commit=code_would_commit,
        diff_base=contract.code_base_commit,
    )
    if requires_strict_code_quality(contract.code_worktree, code_would_commit=code_would_commit):
        code_quality_gate = _gate_staged_code(
            contract.code_worktree, diff_base=contract.code_base_commit
        )
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
            VerifiedChange(
                commit=code_commit,
                commit_date=code_commit_date,
                changed_paths=changed_paths,
                working_paths=worklist["working"],
            ),
        )
    integration_reopen = _completed_integration_reopen(
        contract,
        code_commit=code_commit,
        memory_content_commit=memory_commit,
        ledger_commit=ledger_commit,
    )
    reopened = bool(integration_reopen["reopened"])
    updated = amend_contract(
        replace(
            contract,
            approved_for_commit=True,
            commit_approval_note=approval_note,
            code_commit=code_commit,
            memory_content_commit=memory_commit,
            ledger_commit=ledger_commit,
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
            "code_quality_gate": code_quality_gate,
            "integration_reopen": integration_reopen,
            "closeout_gate": _closeout_gate_payload(gate_guard),
        },
    )
