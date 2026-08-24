"""Same-generation journal recovery and direct-landing execution ownership."""

from __future__ import annotations

from agents_remember.models.lifecycles.mutation_evidence import (
    CloseoutMutationLeg,
    GitMutationEvidence,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.closeout_recovery_projection import (
    derive_closeout_recovery_commits,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_errors import (
    DirectLandingError,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_execution import (
    execute_or_require_direct_landing_recovery,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    DirectLandingRecoveryClassification,
    classify_direct_landing_recovery,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_generation_resume
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    reconcile_closeout_mutations,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


def recover_direct_landing_under_authority(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
) -> LifecycleOperationRecord:
    """Recover one direct generation while the caller owns its Git landing authority."""

    current_contract = load_contract(contract.contract_path)
    classification = classify_direct_landing_recovery(current_contract, record)
    if classification.state == "developer-decision":
        raise direct_recovery_refusal(classification)
    requeued, changed = store.resume_generation(
        lifecycle_generation_resume.requeued_same_generation,
        expected_generation=record.generation,
    )
    if not changed:
        raise LifecycleControlError(
            "lifecycle-generation-changed",
            "a newer lifecycle generation replaced the advertised action",
            expected={"generation": record.generation},
            observed={"generation": requeued.generation},
            next_action="developer-decision",
        )
    runtime = DirectLandingRuntime(current_contract, requeued)
    try:
        execute_or_require_direct_landing_recovery(runtime.contract, runtime)
    except DirectLandingError as exc:
        classification = classify_direct_landing_recovery(
            current_contract,
            store.read() or requeued,
        )
        if classification.state == "developer-decision":
            raise direct_recovery_refusal(classification) from exc
        raise LifecycleControlError(
            exc.status,
            exc.detail,
            expected=exc.expected,
            observed=exc.observed,
            next_action="recover",
        ) from exc
    except RuntimeError as exc:
        classification = classify_direct_landing_recovery(
            current_contract,
            store.read() or requeued,
        )
        if classification.state == "developer-decision":
            raise direct_recovery_refusal(classification) from exc
        detail = "direct landing was interrupted and requires same-generation recovery"
        observed = public_failure_evidence(
            stage="direct-recovery-control",
            side="direct-landing",
            name="accepted-generation",
            error_type=type(exc).__name__,
            observed={"state": "interrupted"},
        )
        runtime.require_input(
            status="direct-landing-recovery-required",
            detail=detail,
            observed=observed,
        )
        raise LifecycleControlError(
            "direct-landing-recovery-required",
            detail,
            observed=observed,
            next_action="recover",
        ) from exc
    return store.read() or requeued


def direct_recovery_refusal(
    classification: DirectLandingRecoveryClassification,
) -> LifecycleControlError:
    """Map one exact direct evidence contradiction to the public typed boundary."""
    return LifecycleControlError(
        classification.status,
        classification.detail,
        expected=classification.expected,
        observed=classification.observed,
        next_action="developer-decision",
    )


def reconcile_control_mutations(
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
    preserve_recovery_intent: bool = False,
) -> LifecycleOperationRecord:
    """Project or publish exact ordinary closeout mutation reconciliation."""
    current = record
    for _attempt in range(3):
        if current.operationKind != "closeout":
            return current
        reconciled: dict[CloseoutMutationLeg, GitMutationEvidence] = reconcile_closeout_mutations(
            current
        )
        if preserve_recovery_intent:
            reconciled = {
                leg: (
                    current.mutationEvidence[leg]
                    if evidence.state == "reconciled-unchanged"
                    and current.mutationEvidence[leg].state == "mutation-intent"
                    else evidence
                )
                for leg, evidence in reconciled.items()
            }
        recovery_commits = derive_closeout_recovery_commits(current, mutations=reconciled)
        projected = current.model_copy(
            update={
                "mutationEvidence": reconciled,
                "recoveryCommits": recovery_commits,
                "irreversibleBoundaryEntered": (
                    current.irreversibleBoundaryEntered
                    or any(item.state == "commit-proven" for item in reconciled.values())
                ),
            }
        )
        observed = store.read() if dry_run else store.observe_current()
        if observed is None:
            raise RuntimeError("lifecycle operation disappeared during reconciliation")
        if observed != current:
            current = observed
            continue
        if dry_run or projected == current:
            return projected
        updated, matched = store.update_if_current(
            current,
            lambda _current, projected=projected: projected,
        )
        if matched:
            return updated
        current = updated
    # A busy worker may keep advancing heartbeats.  Returning its newest durable
    # record is conservative: mutation intent remains recovery authority and no
    # stale projection is ever published over it.
    return (store.read() if dry_run else store.observe_current()) or current
