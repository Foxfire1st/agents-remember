from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from agents_remember.controlplane.enforcement import (
    CLOSEOUT_GATE_KIND,
    CloseoutGuard,
    evaluate_closeout_gate,
)
from agents_remember.controlplane.store import GateStore
from agents_remember.kernel.primitives.gate_vocab import STORED_GATE_WAY_OUT
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.observer.events import now_iso
from agents_remember.worktrees.closeout_input import (
    effective_message_arguments,
    require_effective_closeout_plan,
)
from agents_remember.worktrees.disabled_memory import (
    OPT_OUT_STATUS,
    is_disabled_contract,
    opt_out_detail_for_contract,
    opt_out_refusal_result,
    opt_out_result_fields,
    subject_for_contract,
)
from agents_remember.worktrees.integration.closeout.integration_reopen import (
    completed_integration_reopen,
    preview_integration_reopen,
)
from agents_remember.worktrees.integration.integration_branch_authority import (
    require_ordinary_worktree,
    require_series_contract_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.ledger_projection import inspect_ledger_projection
from agents_remember.worktrees.modules.args import WorktreeArgs, report_operation_progress
from agents_remember.worktrees.modules.closeout_external import (
    GATE_NOT_APPLICABLE,
    candidate_gate_verdict,
    external_closeout_commits,
    refuse_ungated_candidate,
    require_gated_recovery,
)
from agents_remember.worktrees.modules.closeout_lineage import (
    heal_current_source_lineage,
)
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    branch_commit,
    changed_worktree_paths,
    commit_date,
    committed_changed_paths,
    head_commit,
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
    RouteOverviewRefreshPlan,
    VerifiedChange,
    WorktreeCommandResult,
)
from agents_remember.worktrees.modules.onboarding import (
    entity_fingerprint_refresh_plan,
    onboarding_refresh_plan,
    route_index_refresh_plan_for_context,
)
from agents_remember.worktrees.modules.state_refusals import closeout_queue_refusal
from agents_remember.worktrees.queue.closeout_preview import (
    closeout_order,
    closeout_summary,
    proposed_closeout_commits,
)
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_recovery import (
    MemoryCloseoutOutcome,
    accepted_code_commit,
    prove_closeout_recovery_commits,
)
from agents_remember.worktrees.route_review import (
    code_candidate_tree,
)
from agents_remember.worktrees.series_closeout import (
    empty_landing_leaf_ids,
    publish_closeout_under_authority,
    refuse_series_workbench_commit,
    require_closeout_publication_authority,
)
from agents_remember.worktrees.task_leaf_binding import leaf_enclosure_binding_refusal
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
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
    if contract.kind == "series":
        return {"all": [], "working": [], "committed": []}
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
    """Count plus capped sample so committed-range lists never flood the payload.

    Every entry is a real path: a blank is refused here rather than counted, because a
    ``count`` that includes entries the sample cannot name is a number an operator cannot
    audit (`260918-TSIP` `T71`).
    """

    named = [path for path in paths if path.strip()]
    return {"count": len(named), "sample": named[:PATH_SAMPLE_LIMIT]}


def _refreshed_onboarding_paths(entries: list[dict[str, str]]) -> list[str]:
    """The refreshed onboarding documents, each named by the path that identifies it.

    A refresh entry is either a *source* file (``source_path``) or a regenerated **document**
    whose own path is the only identity it has -- the citation/carryover pass appends those
    with ``source_path: ""`` because there is no source file behind them
    (``worktrees/modules/onboarding.py::_refresh_regenerated_documents``). Reading
    ``source_path`` alone turned every document entry into a blank string, so the payload
    reported ``count 19`` with 2 real paths and 17 blanks (`T71`); the document path was
    already in ``onboarding_file`` and was discarded at exactly this boundary.
    """

    return [item.get("source_path") or item.get("onboarding_file") or "" for item in entries]


def _bounded_refresh_plan_view(plan: OnboardingRefreshPlan) -> dict[str, object]:
    """Payload view of the sidecar plan: blockers stay full, scaling lists bounded."""
    return {
        "required": _bounded_paths([item["source_path"] for item in plan["required"]]),
        "missing": plan["missing"],
        "unsupported": plan["unsupported"],
        "unonboarded": _bounded_paths(plan["unonboarded"]),
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


def _landed_source_heads(contract, base: str, integrated: str) -> set[str]:
    """The source heads this contract's own landing may legitimately have produced.

    Both a finished integration and a checkpoint move the recorded source branch forward and
    record the commit they moved it to, so both make that commit an expected head. Keying only
    on ``completed`` refused a checkpointed series on its next closeout with "source branch
    moved since task start", describing the checkpoint's own recorded landing as foreign
    movement. A source that genuinely moved elsewhere is still refused, because it matches
    neither head.
    """

    expected = {base}
    if contract.integration_status in {"completed", "checkpointed"} and integrated:
        expected.add(integrated)
    return expected


def _format_expected_heads(expected: set[str]) -> str:
    return ", ".join(sorted(expected))


@dataclass(frozen=True)
class _MemoryRefreshPreview:
    """What external-memory closeout would refresh.

    Related values computed by one step and consumed by one caller. They are grouped rather
    than returned as a tuple because the caller reads them by name, and grouped rather
    than left inline because the six ``contract.memory_mode == "external"`` conditionals
    that produce them were 66 of ``closeout_preview_payload``'s 153 lines.
    """

    metadata: OnboardingRefreshPlan
    entities: EntityFingerprintRefreshPlan
    route_overviews: RouteOverviewRefreshPlan
    route_indexes: dict[str, Any]


def _memory_refresh_preview(contract, worklist: dict[str, list[str]]) -> _MemoryRefreshPreview:
    """Plan the external-memory refresh and classify it, without touching anything.

    The plan is built for real when the task carries external memory. A
    contract that carries the removed mode never previews a refresh: the route
    entry refuses it with the opt-out refusal first (MIK-R50 R4a), and this
    function refuses it itself for a direct caller (MIK-R50 R4b) rather than
    describing an empty code-only plan.
    """
    if contract.memory_mode != "external":
        raise RuntimeError(opt_out_detail_for_contract(contract, "worktree_closeout"))
    changed_paths = worklist["all"]
    external_leaf = contract.memory_mode == "external" and contract.kind == "leaf"
    metadata_refresh: OnboardingRefreshPlan = (
        onboarding_refresh_plan(contract, changed_paths, working_paths=worklist["working"])
        if external_leaf
        else {
            "required": [],
            "missing": [],
            "unsupported": [],
            "unonboarded": [],
        }
    )
    entity_refresh: EntityFingerprintRefreshPlan = (
        entity_fingerprint_refresh_plan(contract, changed_paths)
        if external_leaf
        else {
            "required": [],
            "unsupported": [],
        }
    )
    route_overview_refresh: RouteOverviewRefreshPlan = {
        "required": [],
        "missing_metadata": [],
    }
    route_index_refresh: dict[str, Any] = (
        route_index_refresh_plan_for_context(_closeout_contract_context(contract))
        if external_leaf
        else {
            "routes": 0,
            "written": 0,
            "unchanged": 0,
            "indexes": [],
        }
    )
    return _MemoryRefreshPreview(
        metadata=metadata_refresh,
        entities=entity_refresh,
        route_overviews=route_overview_refresh,
        route_indexes=route_index_refresh,
    )


def closeout_preview_payload(contract, args: WorktreeArgs) -> dict[str, object]:
    """Answer what closeout would do, having done none of it.

    It refuses on the SAME eligibility the apply refuses on, read from
    :func:`require_closeout_publication_authority`: the dry run used to plan a closeout the apply
    then rejected on every completion blocker, which is exactly how a partial master's landing came to
    look available when it was not. A leaf owes nothing there, so no leaf preview changes.

    The same holds for the mandatory invariant gate (MIK-R09): a converted leaf's preview asks the
    gate the apply's preflight asks, over the same two trees, and answers
    ``knowledge-gate-refused`` with the findings instead of ``would-closeout`` when it refuses
    (:func:`_gate_refused_preview`). An unconverted leaf's preview is refused by the cutover
    lock first (MIK-R46 rule 1), so it never promises a closeout the apply refuses. The verdict
    is memoised, so the apply that follows does not evaluate it again.
    """

    if is_disabled_contract(contract):
        # A contract that carries the removed mode never previews a closeout
        # (MIK-R50 R4a): the preview refuses with the opt-out refusal, as the
        # apply does.
        return _opt_out_preview(contract, "worktree_closeout_preview")
    refuse_series_workbench_commit(contract)
    if contract.kind == "series":
        # MIK-R46 rule 1: the series format verdict precedes publication authority,
        # as the apply's preflight precedes publication -- an unconverted line's
        # true blocker is the conversion, and the preview must refuse what the
        # apply refuses. Converted lines answer not-applicable and read authority.
        early = candidate_gate_verdict(
            contract, args.candidate_tree or code_candidate_tree(contract)
        )
        if early is not None and early != GATE_NOT_APPLICABLE:
            return _gate_refused_preview(contract, early)
    try:
        require_closeout_publication_authority(contract)
    except CloseoutQueueError as error:
        return _closeout_queue_blocked_preview(contract, error)
    verdict = candidate_gate_verdict(contract, args.candidate_tree or code_candidate_tree(contract))
    if verdict is not None and verdict != GATE_NOT_APPLICABLE:
        return _gate_refused_preview(contract, verdict)
    gate_guard = _closeout_gate_guard(contract, args)
    if gate_guard is not None and not gate_guard.permitted:
        return _closeout_gate_blocked_preview(contract, gate_guard)
    code_dirty = contract.kind == "leaf" and worktree_dirty(contract.code_worktree)
    memory_dirty = (
        contract.kind == "leaf"
        and contract.memory_mode == "external"
        and worktree_dirty(contract.memory_worktree)
    )
    worklist = closeout_changed_paths(contract)
    changed_paths = worklist["all"]
    refresh = _memory_refresh_preview(contract, worklist)
    memory_would_commit = memory_dirty or _refresh_plans_have_work(
        refresh.metadata, refresh.entities, refresh.route_overviews, refresh.route_indexes
    )
    # A series whose landed leaves include empty landings lists them (MIK-R61 rule 1):
    # they stand on the chain without ordering, and the preview says so. The key is
    # present only when there is something to list, so worlds without empty landings
    # answer exactly as before.
    empty_landings = empty_landing_leaf_ids(contract) if contract.kind == "series" else []
    return {
        "state": "would-closeout",
        **status_payload(contract),
        "phase": "commit-approval-pending",
        "summary": closeout_summary(contract),
        **({"emptyLandings": empty_landings} if empty_landings else {}),
        **recovery_guidance(
            "request_commit_approval",
            tool="worktree_closeout_apply",
            args=contract_next_args(
                contract,
                **effective_message_arguments(_effective_closeout_input(args)),
            ),
            required_args=["intent_note"],
        ),
        "commit_approval_required": True,
        "approval_question": (
            "Approve recording the exact existing series commits in the closeout contract?"
            if contract.kind == "series"
            else "Approve creating the code and memory commits with these messages?"
        ),
        "closeout_order": closeout_order(contract),
        "changed_code_paths": _bounded_paths(changed_paths),
        "changed_code_paths_committed": _bounded_paths(worklist["committed"]),
        "onboarding_metadata_refresh": _bounded_refresh_plan_view(refresh.metadata),
        "entity_fingerprint_refresh": refresh.entities,
        "route_overview_metadata_refresh": refresh.route_overviews,
        "route_index_refresh": refresh.route_indexes,
        "integration_reopen": preview_integration_reopen(
            contract, code_dirty=code_dirty, memory_would_commit=memory_would_commit
        ),
        "closeout_gate": _closeout_gate_payload(_closeout_gate_guard(contract, args)),
        "proposed_commits": proposed_closeout_commits(
            contract, args, code_dirty, memory_would_commit
        ),
        # Present on a converted leaf only: an unconverted leaf's preview is refused by the
        # cutover lock (MIK-R46 rule 1), so it never promises a closeout.
        **({} if verdict == GATE_NOT_APPLICABLE else {"knowledge_gate": {"state": "pass"}}),
    }


GATE_REFUSED_PREVIEW = "knowledge-gate-refused"

# Every preview state that answers a refusal instead of a plan: the entry
# marks the preview returncode from this set, so a blocked preview never
# rides a success returncode.
REFUSED_CLOSEOUT_PREVIEW_STATES = frozenset(
    {
        GATE_REFUSED_PREVIEW,
        OPT_OUT_STATUS,
        "closeout-gate-blocked",
        # The series-authority previews answer state "blocked" with the
        # check's code beside it, like every other answered refusal.
        "blocked",
    }
)


MAX_PREVIEW_FINDINGS = 50


def _opt_out_preview(contract, operation: str) -> dict[str, object]:
    """The preview of a closeout the removed mode refuses: never ``would-closeout``.

    It carries the opt-out status with the contract, the reason and the known
    way out, and its own next step, so the response is not followed by the hint
    to ask for the commit approval.
    """

    fields = opt_out_result_fields(
        subject_for_contract(contract),
        operation,
        "series" if contract.kind == "series" else "leaf",
    )
    status = {
        key: value
        for key, value in status_payload(contract).items()
        if key not in ("nextTool", "nextArgs", "nextRequiredArgs")
    }
    return {
        "state": OPT_OUT_STATUS,
        **status,
        **fields,
        "summary": fields["detail"],
        "commit_approval_required": False,
        "nextStep": {"summary": str(fields.get("nextAction"))},
    }


def _closeout_gate_blocked_preview(contract, guard: CloseoutGuard) -> dict[str, object]:
    """The preview of a closeout the closeout-approval gate blocks: never ``would-closeout``.

    This file is the right home: the preview builder already reports the
    gate verdict, and the apply refuses on this same guard. A preview that
    promised ``would-closeout`` while the apply would refuse on the gate is
    exactly the dry/real disagreement MIK-R111 rule 1 forbids. The shape
    mirrors :func:`_gate_refused_preview`: the gate's own sentence, its
    verdict facts, one coded blocker, and its own next step, so the response
    is not followed by the hint to ask for the commit approval.
    """

    summary = f"The closeout would be refused, and nothing would be committed: {guard.reason}"
    step = f"{summary}. {STORED_GATE_WAY_OUT}."
    status = {
        key: value
        for key, value in status_payload(contract).items()
        if key not in ("nextTool", "nextArgs", "nextRequiredArgs")
    }
    sentence = (
        f"Closeout-approval gate {guard.gate_id or 'unaddressed'} is undecided "
        f"({guard.reason}). {STORED_GATE_WAY_OUT}."
    )
    return {
        "state": "closeout-gate-blocked",
        "code": "closeout-gate-blocked",
        "error": summary,
        **status,
        "nextOperation": "continue_work",
        "summary": summary,
        "commit_approval_required": False,
        "closeout_gate": _closeout_gate_payload(guard),
        "blockers": [{"code": "closeout-gate-blocked", "sentence": sentence}],
        "nextStep": {"summary": step},
    }


def _closeout_queue_blocked_preview(contract, error: CloseoutQueueError) -> dict[str, object]:
    """The preview of a closeout the series authority refuses: never ``would-closeout``.

    The check's code rides as the state and the code, its sentence as the
    error, unchanged. The next call exists only where exactly one follows
    (an abandoned master is abandoned); otherwise the sentence names the
    repair. Like the gate previews, no commit-approval hint follows.
    """

    summary = f"The closeout would be refused, and nothing would be committed: {error.detail}"
    step = f"{summary}. "
    if error.status == "atomic-series-closeout-master-abandoned":
        step += "Reclaim the abandoned master with worktree_abandon."
        next_step: dict[str, object] = {
            "summary": step,
            "nextTool": "worktree_abandon",
            "nextArgs": {"contract_path": contract.contract_path.as_posix()},
        }
    else:
        step += "Repair the master the sentence names, then preview the closeout again."
        next_step = {"summary": step}
    status = {
        key: value
        for key, value in status_payload(contract).items()
        if key not in ("nextTool", "nextArgs", "nextRequiredArgs")
    }
    return {
        "state": "blocked",
        "code": error.status,
        "error": error.detail,
        **status,
        "nextOperation": "continue_work",
        "summary": summary,
        "commit_approval_required": False,
        "blockers": [{"code": error.status, "sentence": error.detail}],
        "nextStep": next_step,
    }


def _gate_refused_preview(contract, refusal: str) -> dict[str, object]:
    """The preview of a closeout the mandatory gate refuses: never ``would-closeout``.

    It carries the gate's own sentence, the number of findings and the first
    :data:`MAX_PREVIEW_FINDINGS` of them (a leaf at intake holds hundreds), and its own next step,
    so the response is not followed by the hint to ask for the commit approval. The contract's
    own next call (an apply, or the integration of an earlier closeout) is not offered either.
    """

    header, *lines = refusal.splitlines()
    findings = [line.removeprefix("- ") for line in lines if line.startswith("- ")]
    summary = f"The closeout would be refused, and nothing would be committed: {header}"
    step = (
        f"{summary}. Answer each finding (a row through the knowledge writer, or the repair it "
        "names), rerun memory_quality_check, then preview the closeout again."
    )
    status = {
        key: value
        for key, value in status_payload(contract).items()
        if key not in ("nextTool", "nextArgs", "nextRequiredArgs")
    }
    return {
        "state": GATE_REFUSED_PREVIEW,
        **status,
        "nextOperation": "continue_work",
        "summary": summary,
        "commit_approval_required": False,
        "knowledge_gate": {
            "state": "refused",
            "findingCount": len(findings),
            "findings": findings[:MAX_PREVIEW_FINDINGS],
            "truncated": len(findings) > MAX_PREVIEW_FINDINGS,
        },
        "nextStep": {"summary": step},
    }


def _validate_closeout_source_heads(contract) -> None:
    current_code_source = branch_commit(contract.code_repo_path, contract.code_source_branch)
    expected_code_heads = _landed_source_heads(
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
        current_memory_source = branch_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        expected_memory_heads = _landed_source_heads(
            contract, contract.memory_base_commit, contract.integrated_memory_content_commit
        )
        if current_memory_source not in expected_memory_heads:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, "
                f"expected {_format_expected_heads(expected_memory_heads)}"
            )


def _validate_closeout_source_state(contract, *, dry_run: bool) -> WorktreeContract:
    """Prove the full super -> master -> leaf chain, self-healing a plain fast-forward.

    Ordered deliberately: the lineage guard runs first, so a source branch that moved
    while only its ancestry is stale is settled by the sync rather than refused as a
    moved source. The exact immediate source heads are then re-proved on the contract
    identity the sync left on disk.
    """
    healed = heal_current_source_lineage(contract, operation="closeout", dry_run=dry_run)
    _validate_closeout_source_heads(healed.contract)
    return healed.contract


def _closeout_approval_note(args: WorktreeArgs) -> str:
    if not args.approved:
        raise RuntimeError("closeout requires journaled explicit commit approval")
    approval_note = args.approval_note.replace("\n", " ").strip()
    if not approval_note:
        raise RuntimeError(
            "closeout requires a journaled note describing the developer's explicit commit approval"
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
    binding; a gateless lifecycle uses the approval and note persisted by the canonical
    journaled closeout operation. The agent cannot satisfy the gate itself: a decision
    it recorded is
    ``decidedBy="model"``, which :func:`evaluate_closeout_gate` rejects.

    THIS IS NOT THE ENFORCEMENT ANY MORE, and reading it as such is the check-then-act mistake
    leaf 260731-EFA-L5 R2 was called in to remove. The approval is consumed by
    :func:`_claim_closeout_gate`, under the gate log's lock, immediately before the first
    irreversible act. This call exists only so an unapproved closeout is refused before it stages
    the worktree and performs any transaction mutation.

    It is safe to keep precisely because it can only DENY. Its read is unlocked and therefore
    already stale by the time it returns, but a stale read here has exactly two outcomes: it
    refuses a gate that has since been approved (the operator reruns, and nothing was consumed),
    or it permits and the claim re-evaluates the same policy under the lock and refuses there. It
    can never be the reason an approval is spent, because it never writes.
    """
    guard = _closeout_gate_guard(contract, args)
    if guard is not None and not guard.permitted:
        raise RuntimeError(
            f"closeout blocked by gate enforcement: {guard.reason}. {STORED_GATE_WAY_OUT}"
        )


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
    memory commit failure, a git error, an ENOSPC -- leaves the approval consumed, and the next
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
    source-head validation, the onboarding/route plans, and candidate staging -- either only reads
    or only touches the index of the task's own disposable worktree, so a refusal there changes
    nothing and must not cost the developer their approval.
    Both halves hold: the gate is already ``applied`` by
    the time the transaction commit runs, and a gate failure leaves it ``approved``.
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
        raise RuntimeError(
            f"closeout blocked by gate enforcement: {guard.reason}. {STORED_GATE_WAY_OUT}"
        )
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
            integration_strategy="" if reopened else contract.integration_strategy,
            integrated_code_commit="" if reopened else contract.integrated_code_commit,
            integrated_memory_content_commit=""
            if reopened
            else contract.integrated_memory_content_commit,
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


@dataclass(frozen=True)
class _CloseoutResultFacts:
    code_commit: str
    memory: MemoryCloseoutOutcome
    integration_reopen: dict[str, Any]
    gate_guard: CloseoutGuard | None


def _recover_closeout_finalization(contract, args: WorktreeArgs) -> WorktreeCommandResult | None:
    """Finalize an already-committed detached closeout exactly once."""
    operation = "worktree_closeout_preview" if args.dry_run else "worktree_closeout_apply"
    if contract.memory_mode != "external":
        # A contract that carries the removed mode never recovers a closeout
        # (MIK-R50 R4b): the entry refuses it first, and this function refuses
        # it itself for a direct caller rather than replaying code-only logic.
        raise RuntimeError(opt_out_detail_for_contract(contract, operation))
    commits = args.recovery_commits
    if commits is None or (contract.memory_mode == "external" and not commits.memoryContentCommit):
        return None
    if contract.closeout_status == "completed":
        if (
            contract.code_commit != commits.codeCommit
            or contract.memory_content_commit != commits.memoryContentCommit
        ):
            raise RuntimeError(
                "completed closeout contract does not match its recorded recovery commits"
            )
        payload = {
            "state": "already-closed",
            "recovered": True,
            **status_payload(contract),
        }
        repair = _memory_ledger_repair(contract, MemoryCloseoutOutcome(), operation=operation)
        if repair is not None:
            payload["memory_ledger_repair"] = repair
        return WorktreeCommandResult(0, payload)
    approval_note = _closeout_approval_note(args)

    def publication():
        current = load_contract(contract.contract_path)
        if current != contract:
            raise RuntimeError("closeout contract changed before recovery finalization")
        if current.kind == "series":
            require_series_contract_authority(current, operation="worktree_closeout")
        else:
            require_ordinary_worktree(current, operation="worktree_closeout")
        # MIK-R09 rule 5: a recovered closeout finalizes only a memory commit the gate passes.
        require_gated_recovery(current, commits.codeCommit, commits.memoryContentCommit)
        memory = prove_closeout_recovery_commits(current, commits)
        integration_reopen = completed_integration_reopen(
            current,
            code_commit=commits.codeCommit,
            memory_content_commit=commits.memoryContentCommit,
        )
        updated = _amended_closeout_contract(
            current,
            approval_note,
            commits.codeCommit,
            memory,
            bool(integration_reopen["reopened"]),
        )
        report_operation_progress(
            args,
            "contract-finalization",
            current_command="finalize recovered closeout contract edge",
            recovery_commits=commits.model_dump(mode="json"),
            closeout_finalized_contract_sha256=closeout_contract_sha256(updated),
        )
        write_contract(current.contract_path, updated)
        return memory, integration_reopen, updated

    try:
        memory, integration_reopen, updated = publish_closeout_under_authority(
            contract, publication
        )
    except CloseoutQueueError as error:
        # The series authority the recovery re-proves answers with its code
        # and sentence, never raises.
        return closeout_queue_refusal(contract, error, next_args=contract_next_args(contract))
    payload = _closed_result_payload(
        updated,
        _CloseoutResultFacts(
            code_commit=commits.codeCommit,
            memory=memory,
            integration_reopen=integration_reopen,
            gate_guard=_closeout_gate_guard(contract, args),
        ),
    )
    payload["recovered"] = True
    return WorktreeCommandResult(0, payload)


def _closed_result_payload(updated, facts: _CloseoutResultFacts) -> dict[str, Any]:
    """Build the completed-closeout response after all durable writes finish."""
    memory = facts.memory
    payload = {
        "state": "closed",
        # The response's OWN task address, in the envelope's spelling. `status_payload` emits
        # the snake_case `contract_path`, while the guidance guard that a seat's next move
        # depends on reads `contractPath`/`enclosurePath` (`application/tool_response.py::
        # bound_next_step`) -- so without this key a closed closeout declared no address at
        # all, the guard had nothing to compare against, and guidance derived from whatever
        # enclosure the *process* happened to hold was emitted verbatim. That is exactly how a
        # closeout for one leaf shipped a `nextStep` naming another master's contract
        # (`260918-TSIP` `T54`).
        "contractPath": updated.contract_path.as_posix(),
        **status_payload(updated),
        "summary": "Closeout completed; integrate the task branches back into their source branches.",
        "code_commit": facts.code_commit,
        "memory_content_commit": memory.memory_commit,
        "refreshed_onboarding": _bounded_paths(
            _refreshed_onboarding_paths(memory.refreshed_onboarding)
        ),
        "refreshed_entities": memory.refreshed_entities,
        "refreshed_route_overviews": memory.refreshed_route_overviews,
        "route_index_refresh": memory.route_index_refresh,
        "integration_reopen": facts.integration_reopen,
        "closeout_gate": _closeout_gate_payload(facts.gate_guard),
    }
    repair = _memory_ledger_repair(updated, memory, operation="worktree_closeout_apply")
    if repair is not None:
        payload["memory_ledger_repair"] = repair
    return payload


def _memory_ledger_repair(
    updated, memory: MemoryCloseoutOutcome, *, operation: str
) -> dict[str, object] | None:
    """What recomputing the ledger changed, or an explicit statement that it was not touched.

    External-memory closeout always speaks about its ledger. A contract that
    carries the removed mode has no ledger to speak about and never reaches
    this repair: the route entry refuses it first (MIK-R50 R4a), and this
    function refuses it itself for a direct caller (MIK-R50 R4b) rather than
    answering a populated repair or omitting the key with code-only semantics.
    """

    if updated.memory_mode != "external":
        raise RuntimeError(opt_out_detail_for_contract(updated, operation))
    if memory.ledger_repair:
        return memory.ledger_repair
    return inspect_ledger_projection(updated)


def _revalidate_candidate(
    contract, accepted_candidate_tree: str, *, dry_run: bool
) -> WorktreeContract:
    """Re-prove source lineage and candidate identity before Git mutation."""
    contract = _validate_closeout_source_state(contract, dry_run=dry_run)
    if code_candidate_tree(contract) != accepted_candidate_tree:
        raise RuntimeError(
            "closeout candidate changed after admission; restart from the current candidate"
        )
    return contract


@dataclass(frozen=True)
class _CloseoutCommitPhase:
    code_commit: str
    memory: MemoryCloseoutOutcome
    integration_reopen: dict[str, Any]
    gate_guard: CloseoutGuard | None


def _closeout_commit_phase(
    contract,
    args: WorktreeArgs,
    effective_input: EffectiveCloseoutInput,
    *,
    worklist: dict[str, list[str]],
) -> _CloseoutCommitPhase:
    resuming = args.approval_claimed or args.recovery_commits is not None
    report_operation_progress(
        args,
        "approval-claim",
        current_command="resume claimed closeout" if resuming else "claim closeout approval",
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
    report_operation_progress(args, "code-commit", current_command="commit code tree")
    code_commit = accepted_code_commit(
        contract,
        args,
        effective_input,
    )
    code_repository = (
        contract.code_repo_path if contract.kind == "series" else contract.code_worktree
    )
    code_commit_date = commit_date(code_repository, code_commit)
    # Every closeout commits memory (MIK-R50 R4b): there is no code-only branch
    # left. A contract that carries the removed mode never reaches this commit
    # phase -- the route entry refuses it with the opt-out refusal first.
    memory = external_closeout_commits(
        contract,
        args,
        effective_input,
        VerifiedChange(
            commit=code_commit,
            commit_date=code_commit_date,
            changed_paths=worklist["all"],
            working_paths=worklist["working"],
        ),
    )
    integration_reopen = completed_integration_reopen(
        contract,
        code_commit=code_commit,
        memory_content_commit=memory.memory_commit,
    )
    return _CloseoutCommitPhase(code_commit, memory, integration_reopen, gate_guard)


def _closeout_contract(
    args: WorktreeArgs,
    current_contract: WorktreeContract,
) -> tuple[Path, WorktreeContract]:
    contract_path = args.contract_path
    assert contract_path is not None
    contract = current_contract
    if contract_path.resolve() != contract.contract_path.resolve():
        raise RuntimeError(
            "closeout contract path does not match the path claimed by the contract document"
        )
    if contract.kind == "series":
        require_series_contract_authority(contract, operation="worktree_closeout")
    else:
        require_ordinary_worktree(contract, operation="worktree_closeout")
    return contract_path, contract


def _effective_closeout_input(args: WorktreeArgs) -> EffectiveCloseoutInput:
    effective = args.closeout_input
    if effective is None:
        raise RuntimeError("closeout execution requires normalized effective input")
    return effective


def _closeout_contract_context(contract):
    context = contract_context(contract)
    return replace(context, code_repository_root=contract.code_worktree)


def closeout_result(
    args: WorktreeArgs,
    current_contract: WorktreeContract,
) -> WorktreeCommandResult:
    """Run closeout for real, in the order the preview promised.

    Nothing moved across the claim on line "THE CLAIM" below, and nothing may: the ordering
    it enforces is the whole point of 260731-EFA-L5 R3.
    """
    contract, effective_input, early_result = _closeout_entry(args, current_contract)
    if early_result is not None:
        return early_result
    report_operation_progress(args, "preflight", current_command="validate closeout eligibility")
    if args.operation_key and not args.candidate_tree:
        raise RuntimeError("closeout operation is missing its accepted candidate tree")
    if not args.candidate_tree:
        # The head is read before the tree: a commit that moves the branch at any later moment
        # then refuses the code publication instead of becoming its parent (L26 review R3-2).
        head = head_commit(contract.code_worktree) if contract.kind == "leaf" else None
        args = replace(args, candidate_tree=code_candidate_tree(contract), candidate_head=head)
    approval_note = _closeout_approval_note(args)
    resuming = args.approval_claimed or args.recovery_commits is not None
    if not resuming:
        _refuse_unsatisfied_closeout_gate(contract, args)
    worklist = closeout_changed_paths(contract)
    accepted_candidate_tree = cast(str, args.candidate_tree)
    refuse_series_workbench_commit(contract)
    contract = _revalidate_candidate(contract, accepted_candidate_tree, dry_run=args.dry_run)
    # MIK-R09: an open worklist item, an incomplete run or a validator failure refuses the closeout
    # before it claims its approval or commits either side; the memory commit asks again.
    refuse_ungated_candidate(contract, accepted_candidate_tree)

    try:
        return _publish_closeout_candidate(
            contract,
            _CloseoutPublicationFacts(
                args=args,
                effective_input=effective_input,
                worklist=worklist,
                approval_note=approval_note,
            ),
        )
    except CloseoutQueueError as error:
        # The series authority the publication re-proves answers with its
        # code and sentence, never raises.
        return closeout_queue_refusal(contract, error, next_args=contract_next_args(contract))


@dataclass(frozen=True)
class _CloseoutPublicationFacts:
    """Inputs already checked before the existing closeout writer claims approval."""

    args: WorktreeArgs
    effective_input: EffectiveCloseoutInput
    worklist: dict[str, list[str]]
    approval_note: str


def _publish_closeout_candidate(
    contract: WorktreeContract,
    facts: _CloseoutPublicationFacts,
) -> WorktreeCommandResult:
    """Publish under one owner with a final contract and candidate check before claiming."""
    args = facts.args
    effective_input = facts.effective_input
    worklist = facts.worklist
    approval_note = facts.approval_note
    accepted_candidate_tree = cast(str, args.candidate_tree)

    def publication() -> tuple[_CloseoutCommitPhase, Any]:
        current = load_contract(contract.contract_path)
        if current != contract:
            raise RuntimeError("closeout contract changed before candidate commit")
        if current.kind == "series":
            require_series_contract_authority(current, operation="worktree_closeout")
        else:
            require_ordinary_worktree(current, operation="worktree_closeout")
        refuse_series_workbench_commit(current)
        current = _revalidate_candidate(current, accepted_candidate_tree, dry_run=args.dry_run)
        committed = _closeout_commit_phase(
            current,
            args,
            effective_input,
            worklist=worklist,
        )
        updated = _amended_closeout_contract(
            current,
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
            },
            closeout_finalized_contract_sha256=closeout_contract_sha256(updated),
        )
        write_contract(current.contract_path, updated)
        return committed, updated

    committed, updated = publish_closeout_under_authority(contract, publication)
    return WorktreeCommandResult(
        0,
        _closed_result_payload(
            updated,
            _CloseoutResultFacts(
                code_commit=committed.code_commit,
                memory=committed.memory,
                integration_reopen=committed.integration_reopen,
                gate_guard=committed.gate_guard,
            ),
        ),
    )


def _closeout_entry(
    args: WorktreeArgs,
    current_contract: WorktreeContract,
) -> tuple[WorktreeContract, EffectiveCloseoutInput, WorktreeCommandResult | None]:
    if is_disabled_contract(current_contract):
        # A contract that carries the removed mode never closes out (MIK-R50
        # R4a): the apply refuses with the opt-out refusal, as the preview does.
        # First, before any worktree or branch read, so the refusal does not
        # depend on what else is wrong with the enclosure.
        effective_input = _effective_closeout_input(args)
        operation = "worktree_closeout_preview" if args.dry_run else "worktree_closeout_apply"
        return (
            current_contract,
            effective_input,
            opt_out_refusal_result(current_contract, operation),
        )
    _contract_path, contract = _closeout_contract(args, current_contract)
    effective_input = _effective_closeout_input(args)
    if contract.kind == "leaf":
        refusal = leaf_enclosure_binding_refusal(contract)
        if refusal is not None:
            return contract, effective_input, refusal
    if args.recovery_commits is None:
        require_effective_closeout_plan(contract, effective_input, route="worktree")
    recovered = _recover_closeout_finalization(contract, args)
    if recovered is not None:
        return contract, effective_input, recovered
    contract = _validate_closeout_source_state(contract, dry_run=args.dry_run)
    refuse_series_workbench_commit(contract)
    if args.dry_run:
        preview = closeout_preview_payload(contract, args)
        refused = preview["state"] in REFUSED_CLOSEOUT_PREVIEW_STATES
        return contract, effective_input, WorktreeCommandResult(2 if refused else 0, preview)
    return contract, effective_input, None
