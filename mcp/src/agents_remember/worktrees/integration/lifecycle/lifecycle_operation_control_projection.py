"""Public legal-control projection from durable lifecycle evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration.closeout.recovery_projection import (
    closeout_generation_retained,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_recovery_state import (
    classify_direct_landing_recovery,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    completed_direct_owner,
    retired_direct_record,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    classify_migrated_lifecycle,
)
from agents_remember.worktrees.modules.git import branch_commit, require_git
from agents_remember.worktrees.worktree_contract import WorktreeContract


@dataclass(frozen=True)
class LifecycleControlProjectionContext:
    """Caller authority for the direct-landing controls projection."""

    allow_completed_disposition: bool = False
    caller: DeclaredCaller | None = None


def legal_operation_controls(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    *,
    context: LifecycleControlProjectionContext | None = None,
) -> list[dict[str, Any]]:
    """Derive only controls whose public handler can revalidate this generation."""

    context = context or LifecycleControlProjectionContext()
    if record.operationKind != "direct-landing":
        # Only a direct landing keeps a generation in the journal; a closeout or integration
        # record left by an older build is refused with the tool that replaces it, so offering
        # a control here would advertise a call that always refuses.
        return []
    base = _control_arguments(contract, record, context.caller)
    if record.doorPublication is not None:
        # A legacy direct-landing generation with a door publication stays visible for audit.
        return []
    if classify_direct_landing_recovery(contract, record).state == "developer-decision":
        return []
    migration = classify_migrated_lifecycle(record)
    if migration.state == "terminal" and record.status == "failed":
        return []
    if record.status == "termination-required":
        controls = [_control("cancel", base, "Complete exact same-generation cancellation.")]
    elif record.status == "cancelled":
        controls = [_direct_successor_control(contract, record)]
    elif record.status == "completed":
        controls = _completed_direct_controls(
            contract,
            record,
            base,
            allow_completed_disposition=context.allow_completed_disposition,
        )
    else:
        controls = _nonterminal_controls(record, base)
    return _without_legacy_generation_reuse(record, controls)


def _without_legacy_generation_reuse(
    record: LifecycleOperationRecord,
    controls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(record.taskIntent, TaskIntentIdentity) or _direct_generation_bound(record):
        return controls
    # An older generation without a leaf binding cannot reuse recover or retry safely.
    return [control for control in controls if control["action"] not in {"recover", "retry"}]


def _direct_generation_bound(record: LifecycleOperationRecord) -> bool:
    """Whether a direct-landing generation carries its leaf binding."""

    operation_input = record.input
    return (
        isinstance(operation_input, DirectLandingOperationInput)
        and operation_input.leafTaskDocumentRef is not None
    )


def _control_arguments(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    caller: DeclaredCaller | None,
) -> dict[str, object]:
    base: dict[str, object] = {
        "contract_path": contract.contract_path.as_posix(),
        "operation_kind": record.operationKind,
        "expected_generation": record.generation,
        "intent_note": "<developer intent>",
        "dry_run": False,
    }
    if caller is not None:
        base["caller"] = caller.model_dump(mode="json")
    return base


def _nonterminal_controls(
    record: LifecycleOperationRecord,
    base: dict[str, object],
) -> list[dict[str, Any]]:
    """The two controls a live direct landing has; every other kind is refused before this."""

    if generation_requires_recovery(record):
        return [_control("recover", base, "Reconcile and continue this exact generation.")]
    return [
        _control("recover", base, "Execute the admitted synchronous generation."),
        _control("cancel", base, "Cancel after proving both Git legs unchanged."),
    ]


def generation_requires_recovery(record: LifecycleOperationRecord) -> bool:
    """Whether durable output authority requires same-generation recovery."""

    if record.operationKind in {"closeout", "direct-landing"}:
        return closeout_generation_retained(record)
    return record.irreversibleBoundaryEntered or record.integrationPublication is not None


def pending_door_action(
    record: LifecycleOperationRecord,
) -> str | None:
    """Return the one same-handler action that resumes a journaled door intent."""

    publication = record.doorPublication
    if publication is None or publication.state != "intent":
        return None
    if publication.generation.disposition == "claimed":
        return "resume" if record.operationKind == "closeout" else "recover"
    if publication.generation.disposition == "waiting":
        if record.generationDisposition == "superseded":
            return "supersede"
        if record.status == "cancelled":
            return "cancel"
    return None


def _completed_direct_controls(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    base: dict[str, object],
    *,
    allow_completed_disposition: bool,
) -> list[dict[str, Any]]:
    # Retire is case (a): the owner's disposition of a completed direct
    # landing. Supersede is case (b) and never applies: a direct landing
    # claims no door, so there is nothing to supersede (ruling e55c0093).
    if not allow_completed_disposition:
        return []
    if retired_direct_record(record):
        # The check re-answers an already retired generation before any other
        # guard, so the offer does the same (ruling e55c0093 point 6).
        return [_control("retire", base, "Retire this completed direct generation for audit.")]
    if not completed_direct_owner(record):
        return []
    if contract.integration_status == "completed" or _integration_claim_active(contract):
        # The check refuses integrated or claim-held contracts for the same
        # record; the offer agrees with it (rule 3a, finding L143-CODE-R1-F01).
        return []
    return [_control("retire", base, "Retire this completed direct generation for audit.")]


def direct_supersede_refusal(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> LifecycleControlError:
    """Refuse a supersede of a direct landing in words that say so and name
    the next step: a new ``direct_landing`` call (ruling e55c0093 point 3)."""

    successor = _direct_successor_control(contract, record)
    return LifecycleControlError(
        "lifecycle-direct-supersede-unavailable",
        "a direct landing is admitted by its own request and claims no door, "
        "so there is nothing to supersede; start a new direct_landing call "
        "for new code",
        next_action="direct-landing",
        next_tool="direct_landing",
        next_args=successor["arguments"],
    )


def _integration_claim_active(contract: WorktreeContract) -> bool:
    try:
        record = located_lifecycle_operation_store(contract, "integrate").read()
    except LifecycleOperationReadError:
        return True
    return bool(
        record is not None
        and record.integrationPublication is not None
        and record.status not in {"cancelled", "failed"}
    )


def _control(action: str, base: dict[str, object], summary: str) -> dict[str, Any]:
    generation_effect = {
        "retry": "same",
        "recover": "same",
        "cancel": "terminal-disposition",
        "resume": "successor",
        "retire": "terminal-disposition",
        "supersede": "terminal-disposition",
    }[action]
    return {
        "action": action,
        "generationEffect": generation_effect,
        "tool": "worktree_operation_control",
        "arguments": {**base, "action": action},
        "summary": summary,
    }


def _direct_successor_control(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> dict[str, Any]:
    code_commit = branch_commit(contract.code_repo_path, contract.code_work_branch)
    candidate_tree = require_git(
        contract.code_repo_path,
        ["rev-parse", f"{code_commit}^{{tree}}"],
    )
    return {
        "action": "direct-landing",
        "generationEffect": "successor",
        "tool": "direct_landing",
        "arguments": {
            "contract_path": record.contractPath,
            "code_commit": code_commit,
            "candidate_tree": candidate_tree,
            "memory_commit_message": "<nonblank memory commit message>",
            "intent_note": "<developer intent>",
            "dry_run": False,
        },
        "summary": "Start one fresh direct-landing successor after proven disposition.",
    }
