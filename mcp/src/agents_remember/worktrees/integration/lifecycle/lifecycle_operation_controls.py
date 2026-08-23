"""Task-addressed lifecycle controls derived from durable and live evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.models.closeout_input import (
    CloseoutCorrectedCall,
    CloseoutMessageInput,
)
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.door import CloseoutDoorGeneration
from agents_remember.models.lifecycles.operation import (
    GatePolicyRuleSnapshot,
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.termination import WorkerTerminationEvidence
from agents_remember.worktrees.closeout_input import (
    CloseoutInputError,
    corrected_closeout_arguments,
)
from agents_remember.worktrees.integration.closeout_door import (
    DoorPublicationClassification,
    DoorPublicationError,
    classify_door_publication,
    door_generation_for_operation,
    prepare_door_publication,
    publish_door_intent,
    successor_waiting_door,
)
from agents_remember.worktrees.integration.closeout_ledger_recovery import (
    classify_closeout_ledger_recovery,
)
from agents_remember.worktrees.integration.closeout_operation_admission import (
    CloseoutOperationAdmission,
    ValidatedCloseoutAdmission,
    prevalidate_closeout_operation_admission,
)
from agents_remember.worktrees.integration.configured_contract_authority import (
    reread_configured_contract,
)
from agents_remember.worktrees.integration.integration_operation_decision import (
    IntegrationOperationObservation,
    classify_integration_operation,
    raise_integration_decision,
    require_integration_operation_convergent,
)
from agents_remember.worktrees.integration.integration_ref_state import (
    IntegrationRefDecisionError,
    classify_integration_refs,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_completed_disposition import (
    require_completed_disposition,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_generation_resume import (
    requeued_same_generation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_evidence import (
    prove_cancellable_git,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    LifecycleControlProjectionContext,
    generation_requires_recovery,
    legal_operation_controls,
    pending_door_action,
    revision_successor_publication_pending,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_lease import (
    contract_lifecycle_lease,
    require_lifecycle_operation_compatible,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_live_decision import (
    raise_live_evidence_decision,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_recovery import (
    reconcile_control_mutations,
    recover_direct_landing,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    ensure_initial_closeout_door_intent,
    launch_detached_worker,
    queued_operation_record,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_successor_control import (
    AcceptedSuccessorReplay,
    accept_revision_successor,
    resume_accepted_revision_successor,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_worker_state import (
    project_worker_exit,
    reconcile_worker_exit,
    release_worker_after_exit,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_worker_termination import (
    bounded_worker_termination_outcome,
    public_worker_termination_evidence,
    signal_worker_and_prove_exit,
    worker_termination_request,
)
from agents_remember.worktrees.integration.organizational_completion_repair import (
    OrganizationalRepairPublicationError,
    prepare_organizational_completion_repair,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

LifecycleControlAction = Literal["retry", "recover", "cancel", "revise", "retire", "supersede"]


@dataclass(frozen=True)
class LifecycleControlCommand:
    admitted_contract: WorktreeContract
    admitted_location: LifecycleOperationLocation
    configured_authority: str
    kind: LifecycleOperationKind
    action: LifecycleControlAction
    expected_generation: int
    intent_note: str
    dry_run: bool = False
    revision_messages: CloseoutMessageInput | None = None
    revision_gate_policy: list[GatePolicyRuleSnapshot] | None = None
    allow_completed_disposition: bool = False
    caller: DeclaredCaller | None = None


@dataclass(frozen=True)
class _ControlObservation:
    contract: WorktreeContract
    store: LifecycleOperationStore
    record: LifecycleOperationRecord
    integration: IntegrationOperationObservation | None
    door: DoorPublicationClassification | None


def _reload_control_contract(
    command: LifecycleControlCommand,
) -> tuple[WorktreeContract, LifecycleOperationLocation]:
    """Revalidate current configured contract and locator truth under the lease."""
    return reread_configured_contract(
        command.admitted_contract,
        command.configured_authority,
    )


def control_operation(
    command: LifecycleControlCommand,
) -> LifecycleOperationProjection:
    """Execute one same-generation or terminal-disposition control."""
    if not command.intent_note.strip():
        raise LifecycleControlError(
            "lifecycle-control-intent-required",
            "lifecycle control requires a nonblank developer intent note",
            next_action=command.action,
        )
    with contract_lifecycle_lease(
        command.admitted_contract,
        location=command.admitted_location,
    ):
        contract, location = _reload_control_contract(command)
        require_lifecycle_operation_compatible(
            contract,
            operation_kind=command.kind,
            publish_worker_exits=not command.dry_run,
        )
        store = LifecycleOperationStore(location.journal_path(command.kind))
        pending_successor = store.read_successor_intent()
        if (
            command.action == "revise"
            and pending_successor is not None
            and command.expected_generation == pending_successor.predecessor.generation
        ):
            validated = _validated_revision(contract, pending_successor.predecessor, command)
            return resume_accepted_revision_successor(
                contract,
                store,
                pending_successor,
                AcceptedSuccessorReplay(
                    operation_input=validated.operation_input,
                    candidate_fingerprint=validated.candidate.fingerprint,
                    dry_run=command.dry_run,
                    complete_publications=_complete_revision_successor_publications,
                    prove_publications=_require_proven_closeout_door_for_launch,
                    launch_worker=launch_detached_worker,
                ),
            )
        observed = _observe_control_under_lease(command, contract=contract, store=store)
        legal_rows = legal_operation_controls(
            observed.contract,
            observed.record,
            context=LifecycleControlProjectionContext(
                allow_completed_disposition=command.allow_completed_disposition,
                caller=command.caller,
                integration=observed.integration,
                door=observed.door,
            ),
        )
        legal = {item["action"] for item in legal_rows}
        if command.action not in legal:
            raise_live_evidence_decision(
                observed.contract,
                observed.record,
                integration_observation=observed.integration,
            )
            if (
                command.action == "cancel"
                and observed.record.status == "cancelled"
                and observed.record.operationKind == "integrate"
                and observed.record.organizationalRepair is not None
            ):
                # A previously advertised repair payload must still classify an
                # exact post-crash reset or third byte state even when status no
                # longer advertises cancellation for the contradiction.
                return _cancel(
                    observed.contract,
                    observed.store,
                    observed.record,
                    dry_run=command.dry_run,
                )
            next_row = legal_rows[0] if legal_rows else None
            next_action = next_row["action"] if next_row else "developer-decision"
            raise LifecycleControlError(
                "lifecycle-control-not-legal",
                "the requested action is not legal for the current generation evidence",
                expected={"legalActions": sorted(legal)},
                observed={"requestedAction": command.action, "status": observed.record.status},
                next_action=next_action,
                next_tool=next_row["tool"] if next_row else None,
                next_args=next_row["arguments"] if next_row else None,
            )
        return _execute_legal_control(
            observed.contract,
            observed.store,
            observed.record,
            command,
        )


def _observe_control_under_lease(
    command: LifecycleControlCommand,
    *,
    contract: WorktreeContract,
    store: LifecycleOperationStore,
) -> _ControlObservation:
    """Return one newest record and every legal-action classifier derived from it."""

    record = _require_generation(store, command, contract=contract)
    record = (
        project_worker_exit(record) if command.dry_run else reconcile_worker_exit(store) or record
    )
    record = reconcile_control_mutations(
        store,
        record,
        dry_run=command.dry_run,
        preserve_recovery_intent=command.action == "recover",
    )
    integration = None
    if command.kind == "integrate":
        with integration_authority_lock(
            contract.coordination_root,
            contract.repo_name,
            create=not command.dry_run,
        ):
            contract, location = _reload_control_contract(command)
            store = LifecycleOperationStore(location.journal_path(command.kind))
            record = _require_generation(store, command, contract=contract)
            record = (
                project_worker_exit(record)
                if command.dry_run
                else reconcile_worker_exit(store) or record
            )
            integration = classify_integration_operation(contract, record)
            require_integration_operation_convergent(integration)
    publication = record.doorPublication
    door = (
        classify_door_publication(publication, contract)
        if publication is not None and publication.state == "intent"
        else None
    )
    if door is not None and door.state == "developer-decision":
        payload = door.decision_payload()
        raise LifecycleControlError(
            str(payload["state"]),
            str(payload["decisionSurface"]),
            expected=door.expected,
            observed=door.observed,
            next_action="developer-decision",
        )
    return _ControlObservation(contract, store, record, integration, door)


def _execute_legal_control(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    command: LifecycleControlCommand,
) -> LifecycleOperationProjection:
    action = command.action
    if action == "cancel":
        return _cancel(contract, store, record, dry_run=command.dry_run)
    if action == "retry":
        return _resume(
            contract,
            store,
            record,
            action="retry",
            dry_run=command.dry_run,
        )
    if action == "recover":
        return _resume(
            contract,
            store,
            record,
            action="recover",
            dry_run=command.dry_run,
        )
    if action == "retire":
        return _dispose_completed(
            contract,
            store,
            record,
            action="retire",
            dry_run=command.dry_run,
        )
    if action == "supersede":
        return _dispose_completed(
            contract,
            store,
            record,
            action="supersede",
            dry_run=command.dry_run,
        )
    return _revise_closeout(
        contract,
        store,
        record,
        command=command,
    )


def _cancel(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationProjection:
    if record.status == "cancelled":
        completed = _complete_pending_door(contract, store, record, dry_run=dry_run)
        current_contract = _complete_organizational_repair(
            contract,
            completed,
            dry_run=dry_run,
        )
        return operation_projection(completed, contract=current_contract)
    if record.status == "completed":
        if record.workerPid is not None:
            terminated = _terminate_worker(
                store,
                record,
                dry_run=dry_run,
                cancellation_pending=False,
            )
            return operation_projection(terminated, contract=contract)
        raise LifecycleControlError(
            "lifecycle-cancel-completed",
            "a completed operation cannot be cancelled; choose retire or supersede",
            next_action="retire",
        )
    record = _terminate_worker(
        store,
        record,
        dry_run=dry_run,
        cancellation_pending=True,
    )
    evidence, record = prove_cancellable_git(store, record, publish=not dry_run)
    if dry_run:
        return operation_projection(record, contract=contract)
    contract = load_contract(contract.contract_path)
    intent = None
    if record.operationKind == "closeout":
        door = door_generation_for_operation(contract, record, "cancelled")
        intent = prepare_door_publication(contract, door)
    stamp = _stamp()

    def publish_cancelled(current: LifecycleOperationRecord) -> LifecycleOperationRecord:
        terminal = current.model_copy(
            update={
                "status": "cancelled",
                "phase": "cancelled",
                "finishedAt": stamp,
                "cancelRequested": True,
                "currentCommand": "publish cancelled closeout-door disposition",
                "generationDisposition": "cancelled",
                "cancellationEvidence": evidence,
                "terminationReturnStatus": None,
                "terminationReturnPhase": None,
                "guidance": _cancelled_guidance(record.operationKind),
            }
        )
        if intent is None:
            return terminal
        return _record_door_intent(
            terminal,
            intent,
            generation_disposition="cancelled",
        )

    cancelled = store.update(publish_cancelled)
    contract = _complete_organizational_repair(
        contract,
        cancelled,
        dry_run=False,
    )
    if intent is None:
        return operation_projection(cancelled, contract=contract)
    return operation_projection(
        _complete_pending_door(contract, store, cancelled, dry_run=False),
        contract=load_contract(contract.contract_path),
    )


def _complete_organizational_repair(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> WorktreeContract:
    if record.operationKind != "integrate" or record.organizationalRepair is None or dry_run:
        return contract
    try:
        return prepare_organizational_completion_repair(load_contract(contract.contract_path))
    except IntegrationRefDecisionError as exc:
        raise_integration_decision(exc.classification.decision_payload())
    except OrganizationalRepairPublicationError as exc:
        raise LifecycleControlError(
            exc.status,
            exc.detail,
            expected=exc.expected,
            observed=exc.observed,
            next_action=exc.next_action,
        ) from exc
    except CloseoutQueueError as exc:
        observed = load_contract(contract.contract_path)
        raise LifecycleControlError(
            exc.status,
            "organizational reset evidence contradicts the live contract",
            expected={
                "candidateState": record.candidateState,
                "acceptedContractSha256": (record.organizationalRepair.acceptedContractSha256),
                "resetContractSha256": record.organizationalRepair.resetContractSha256,
            },
            observed={
                "contractSha256": closeout_contract_sha256(observed),
                "closeoutStatus": observed.closeout_status,
                "integrationStatus": observed.integration_status,
                "doorDisposition": (
                    observed.closeout_door.disposition if observed.closeout_door else ""
                ),
            },
            next_action="developer-decision",
        ) from exc


def _terminate_worker(
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
    cancellation_pending: bool,
) -> LifecycleOperationRecord:
    if record.workerPid is None:
        if record.workerTermination is not None and record.workerTermination.state != "exited":
            raise LifecycleControlError(
                "worker-termination-ambiguous",
                "worker termination authority is unproven",
                observed=public_worker_termination_evidence(record.workerTermination),
                next_action="cancel",
            )
        return record
    request = record.workerTermination or worker_termination_request(record)
    if dry_run:
        return record
    needs_intent = record.workerTermination is None or (
        cancellation_pending and not record.cancelRequested
    )
    if needs_intent:
        record = store.update(
            lambda current: _request_worker_termination(
                current,
                request=request,
                cancellation_pending=cancellation_pending,
            )
        )
    outcome = bounded_worker_termination_outcome(signal_worker_and_prove_exit(request))
    if outcome.state != "exited":
        store.update(lambda current: current.model_copy(update={"workerTermination": outcome}))
        raise LifecycleControlError(
            "worker-termination-required",
            outcome.detail,
            expected={"state": "exited", "workerAuthority": "retained-until-proof"},
            observed=public_worker_termination_evidence(outcome),
            next_action="cancel",
        )
    return store.update(lambda current: release_worker_after_exit(current, outcome))


def _request_worker_termination(
    current: LifecycleOperationRecord,
    *,
    request: WorkerTerminationEvidence,
    cancellation_pending: bool,
) -> LifecycleOperationRecord:
    if current.status == "termination-required":
        return current
    return current.model_copy(
        update={
            "status": "termination-required",
            "phase": "termination-required",
            "cancelRequested": cancellation_pending or current.cancelRequested,
            "workerTermination": request,
            "terminationReturnStatus": current.status,
            "terminationReturnPhase": current.phase,
            "currentCommand": "terminate exact lifecycle worker process",
        }
    )


def _cancelled_guidance(kind: LifecycleOperationKind) -> str:
    if kind == "closeout":
        return "Use revise to create one fresh approved successor."
    if kind == "direct-landing":
        return "Use direct_landing with freshly validated input to create one successor."
    return "Advance the task state, then use worktree_integrate for one fresh successor."


def _resume(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    action: Literal["retry", "recover"],
    dry_run: bool,
) -> LifecycleOperationProjection:
    if action == "recover" and record.operationKind == "closeout":
        ledger_recovery = classify_closeout_ledger_recovery(contract, record)
        if ledger_recovery.state == "developer-decision":
            raise LifecycleControlError(
                ledger_recovery.status,
                ledger_recovery.detail,
                expected=ledger_recovery.expected,
                observed=ledger_recovery.observed,
                next_action="developer-decision",
            )
    pending_successor = store.read_successor_intent()
    if pending_successor is not None:
        if record.fingerprint != pending_successor.successor.fingerprint:
            raise LifecycleControlError(
                "lifecycle-successor-publication-conflict",
                "the requested generation does not match the accepted successor intent",
                expected={
                    "generation": pending_successor.successor.generation,
                    "fingerprint": pending_successor.successor.fingerprint,
                },
                observed={
                    "generation": record.generation,
                    "fingerprint": record.fingerprint,
                },
                next_action="developer-decision",
            )
        return resume_accepted_revision_successor(
            contract,
            store,
            pending_successor,
            AcceptedSuccessorReplay(
                operation_input=record.input,
                candidate_fingerprint=record.fingerprint,
                dry_run=dry_run,
                complete_publications=_complete_revision_successor_publications,
                prove_publications=_require_proven_closeout_door_for_launch,
                launch_worker=launch_detached_worker,
            ),
        )
    _require_resumable(record, action)
    if dry_run:
        return operation_projection(record, contract=contract)
    contract, record = _resume_closeout_publications(contract, store, record)
    if record.operationKind == "direct-landing":
        current = recover_direct_landing(contract, store, record)
        return operation_projection(current, contract=load_contract(contract.contract_path))
    requeued, changed = store.resume_generation(
        requeued_same_generation,
        expected_generation=record.generation,
    )
    if not changed:
        raise LifecycleControlError(
            "lifecycle-generation-changed",
            "a newer lifecycle generation replaced the advertised action",
            expected={"generation": record.generation},
            observed={"generation": requeued.generation},
            next_action="recover",
        )
    launch_detached_worker(contract, requeued)
    current = store.read() or requeued
    return operation_projection(current, contract=load_contract(contract.contract_path))


def _resume_closeout_publications(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
) -> tuple[WorktreeContract, LifecycleOperationRecord]:
    if record.operationKind != "closeout":
        return contract, record
    if record.legacyMigration is not None and record.doorPublication is None:
        # The explicit schema-1 bridge proves a retained generation that predates
        # contract-owned closeout doors.  Normal schema-3 records never bypass the
        # claimed-door launch gate.
        return contract, record
    if record.doorPublication is None:
        record = ensure_initial_closeout_door_intent(contract, store, record)
    if revision_successor_publication_pending(contract, record):
        record = _complete_revision_successor_publications(contract, store, record)
        contract = load_contract(contract.contract_path)
    elif record.doorPublication is not None and record.doorPublication.state == "intent":
        record = _complete_pending_door(contract, store, record, dry_run=False)
        contract = load_contract(contract.contract_path)
    _require_proven_closeout_door_for_launch(contract, record)
    return contract, record


def _require_proven_closeout_door_for_launch(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> None:
    publication = record.doorPublication
    observed_door = contract.closeout_door
    if (
        publication is not None
        and publication.state == "proven"
        and publication.generation.disposition == "claimed"
        and observed_door == publication.generation
    ):
        return
    raise LifecycleControlError(
        "closeout-claimed-door-proof-required",
        "closeout recovery cannot launch before its exact claimed door is proven",
        expected={
            "generation": record.generation,
            "operationFingerprint": record.fingerprint,
            "publicationState": "proven",
            "doorDisposition": "claimed",
            "doorGenerationId": (
                publication.generation.generationId if publication is not None else ""
            ),
        },
        observed={
            "publication": (
                publication.model_dump(mode="json") if publication is not None else None
            ),
            "contractDoor": (
                observed_door.model_dump(mode="json") if observed_door is not None else None
            ),
        },
        next_action="developer-decision",
    )


def _require_resumable(
    record: LifecycleOperationRecord,
    action: Literal["retry", "recover"],
) -> None:
    if record.status in {"completed", "cancelled"}:
        raise LifecycleControlError(
            "lifecycle-generation-terminal",
            "same-generation retry/recover cannot replace this terminal disposition",
            next_action="revise" if record.status == "cancelled" else "retire",
        )
    if record.workerPid is not None:
        raise LifecycleControlError(
            "lifecycle-worker-still-authoritative",
            "the exact worker binding is still live; retry would create duplicate authority",
            observed={"pid": record.workerPid, "lease": record.workerLease or ""},
            next_action="cancel",
        )
    if record.operationKind == "integrate":
        facts = classify_integration_refs(record)
        if facts.state == "conflict":
            raise_integration_decision(facts.decision_payload())
        if action == "retry" and facts.state == "intended":
            raise LifecycleControlError(
                "integration-ref-recovery-required",
                "protected refs moved within this generation; retry cannot replace it",
                expected={"before": facts.before, "intended": facts.intended},
                observed=facts.observed_payload(),
                next_action="recover",
            )
    if action == "retry" and generation_requires_recovery(record):
        raise LifecycleControlError(
            "lifecycle-recover-required",
            "output intent/proof belongs to recover, not retry",
            next_action="recover",
        )


def _dispose_completed(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    action: Literal["retire", "supersede"],
    dry_run: bool,
) -> LifecycleOperationProjection:
    require_completed_disposition(contract, record, action)
    resumed = _resume_disposition_publication(
        contract,
        store,
        record,
        action=action,
        dry_run=dry_run,
    )
    if resumed is not None:
        return operation_projection(
            resumed,
            contract=load_contract(contract.contract_path),
        )
    if dry_run:
        return operation_projection(record, contract=contract)
    updated = _publish_completed_disposition(contract, store, record, action)
    return operation_projection(updated, contract=load_contract(contract.contract_path))


def _publish_completed_disposition(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    action: Literal["retire", "supersede"],
) -> LifecycleOperationRecord:
    disposition = "retired" if action == "retire" else "superseded"
    generation = door_generation_for_operation(contract, record, disposition)
    if action == "supersede":
        successor = successor_waiting_door(contract, generation)
        generation = generation.model_copy(update={"successorGenerationId": successor.generationId})
    intent = prepare_door_publication(contract, generation)
    updated = store.update(
        lambda current: _record_door_intent(
            current,
            intent,
            generation_disposition=disposition,
        )
    )
    updated = _complete_pending_door(contract, store, updated, dry_run=False)
    if action == "supersede":
        resumed = _resume_disposition_publication(
            load_contract(contract.contract_path),
            store,
            updated,
            action="supersede",
            dry_run=False,
        )
        if resumed is None:
            raise RuntimeError("supersede predecessor proof lost its successor publication")
        updated = resumed
    return updated


def _resume_disposition_publication(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    action: Literal["retire", "supersede"],
    dry_run: bool,
) -> LifecycleOperationRecord | None:
    expected_disposition = "retired" if action == "retire" else "superseded"
    publication = record.doorPublication
    if record.generationDisposition != expected_disposition or publication is None:
        return None
    if action == "retire":
        return _resume_retire_publication(
            contract,
            store,
            record,
            dry_run=dry_run,
        )
    return _resume_supersede_publication(
        contract,
        store,
        record,
        dry_run=dry_run,
    )


def _resume_retire_publication(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationRecord | None:
    publication = record.doorPublication
    if publication is None or publication.generation.disposition != "retired":
        return None
    return _complete_pending_door(contract, store, record, dry_run=dry_run)


def _resume_supersede_publication(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationRecord | None:
    publication = record.doorPublication
    if publication is None:
        return None
    door = publication.generation
    if door.disposition == "waiting":
        return _complete_pending_door(contract, store, record, dry_run=dry_run)
    if door.disposition != "superseded":
        return None
    predecessor = _complete_pending_door(contract, store, record, dry_run=dry_run)
    if dry_run:
        return predecessor
    current_contract = load_contract(contract.contract_path)
    successor = successor_waiting_door(current_contract, door)
    successor_intent = prepare_door_publication(current_contract, successor)
    pending = store.update(
        lambda current: _record_door_intent(
            current,
            successor_intent,
            generation_disposition="superseded",
        )
    )
    return _complete_pending_door(current_contract, store, pending, dry_run=False)


def _revise_closeout(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    command: LifecycleControlCommand,
) -> LifecycleOperationProjection:
    if record.operationKind != "closeout" or command.revision_messages is None:
        raise LifecycleControlError(
            "lifecycle-revision-input-required",
            "closeout revise requires fresh explicit commit-message fields",
            next_action="revise",
        )
    if record.status == "cancelled":
        record = _complete_pending_door(contract, store, record, dry_run=command.dry_run)
        cancellation_projection = operation_projection(record, contract=contract)
    else:
        cancellation_projection = _cancel(
            contract,
            store,
            record,
            dry_run=command.dry_run,
        )
        record = store.read() or record
    current_contract = load_contract(contract.contract_path)
    validated = _validated_revision(current_contract, record, command)
    if command.dry_run:
        return cancellation_projection
    successor = _revision_successor_record(current_contract, record, validated)
    successor = accept_revision_successor(contract, store, successor)
    successor = _complete_revision_successor_publications(
        current_contract,
        store,
        successor,
    )
    current_contract = load_contract(contract.contract_path)
    launch_detached_worker(current_contract, successor)
    return operation_projection(store.read() or successor, contract=current_contract)


def _validated_revision(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    command: LifecycleControlCommand,
) -> ValidatedCloseoutAdmission:
    admission = _closeout_revision_admission(record, command)
    try:
        validated = prevalidate_closeout_operation_admission(contract, admission)
    except CloseoutInputError as exc:
        raise LifecycleControlError(
            exc.status,
            "closeout revision input is invalid; use the corrected fields",
            expected=exc.response_fields(),
            next_action="revise",
        ) from exc
    if (
        validated.operation_input == record.input
        or validated.candidate.fingerprint == record.fingerprint
    ):
        raise LifecycleControlError(
            "lifecycle-revision-unchanged",
            "revise requires genuinely fresh approved intent",
            next_action="revise",
        )
    return validated


def _revision_successor_record(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    validated: ValidatedCloseoutAdmission,
) -> LifecycleOperationRecord:
    successor = queued_operation_record(
        contract,
        validated.operation_input,
        validated.candidate,
        None,
        datetime.now(UTC).replace(microsecond=0),
    ).model_copy(
        update={
            "generation": record.generation + 1,
            "predecessorFingerprint": record.fingerprint,
        }
    )
    predecessor_door = contract.closeout_door
    if predecessor_door is None:
        raise LifecycleControlError(
            "closeout-door-publication-missing",
            "cancelled predecessor has no proven door generation",
            next_action="revise",
        )
    successor_door = door_generation_for_operation(
        contract,
        successor,
        "claimed",
        predecessor_generation_id=predecessor_door.generationId,
    )
    linked_predecessor = CloseoutDoorGeneration.model_validate(
        {
            **predecessor_door.model_dump(mode="json"),
            "disposition": "superseded",
            "successorGenerationId": successor_door.generationId,
            "operationKind": None,
            "operationFingerprint": "",
            "claimedOperationKey": "",
        }
    )
    predecessor_intent = prepare_door_publication(
        contract,
        linked_predecessor,
    )
    return successor.model_copy(
        update={
            "doorPublication": predecessor_intent,
            "generationDisposition": "active",
        }
    )


def _complete_revision_successor_publications(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
) -> LifecycleOperationRecord:
    if not revision_successor_publication_pending(contract, record):
        return record
    record = _complete_pending_door(contract, store, record, dry_run=False)
    current_contract = load_contract(contract.contract_path)
    predecessor = record.doorPublication
    assert predecessor is not None
    successor_door = door_generation_for_operation(
        current_contract,
        record,
        "claimed",
        predecessor_generation_id=predecessor.generation.generationId,
    )
    successor_intent = prepare_door_publication(current_contract, successor_door)
    pending = store.update(
        lambda current: _record_door_intent(
            current,
            successor_intent,
            generation_disposition="active",
        )
    )
    return _complete_pending_door(current_contract, store, pending, dry_run=False)


def _closeout_revision_admission(
    record: LifecycleOperationRecord,
    command: LifecycleControlCommand,
) -> CloseoutOperationAdmission:
    operation_input = record.input
    config_path = operation_input.configPath
    gate_policy = command.revision_gate_policy
    if gate_policy is None:
        raise LifecycleControlError(
            "lifecycle-revision-policy-required",
            "closeout revise requires the current configured gate policy snapshot",
            next_action="revise",
        )
    return CloseoutOperationAdmission(
        config_path=config_path,
        contract_path=Path(record.contractPath),
        messages=command.revision_messages or CloseoutMessageInput(),
        approval_note=command.intent_note.strip(),
        gate_policy=gate_policy,
        corrected_call=CloseoutCorrectedCall(
            tool="worktree_operation_control",
            arguments={
                **corrected_closeout_arguments(
                    record.contractPath,
                    operation_kind="closeout",
                    action="revise",
                    expected_generation=record.generation,
                    intent_note="<fresh developer intent>",
                )
            },
        ),
    )


def _publish_record_door(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    generation,
    *,
    generation_disposition: str,
) -> LifecycleOperationRecord:
    current = store.read()
    if (
        current is not None
        and current.doorPublication is not None
        and current.doorPublication.generation == generation
    ):
        return _complete_pending_door(contract, store, current, dry_run=False)
    intent = prepare_door_publication(contract, generation)
    updated = store.update(
        lambda current: _record_door_intent(
            current,
            intent,
            generation_disposition=generation_disposition,
        )
    )
    return _complete_pending_door(contract, store, updated, dry_run=False)


def _record_door_intent(
    record: LifecycleOperationRecord,
    intent,
    *,
    generation_disposition: str,
) -> LifecycleOperationRecord:
    history = list(record.doorPublicationHistory)
    if record.doorPublication is not None:
        if record.doorPublication.state != "proven":
            raise RuntimeError("unfinished door publication must complete before another begins")
        history.append(record.doorPublication)
    return record.model_copy(
        update={
            "doorPublication": intent,
            "doorPublicationHistory": history,
            "generationDisposition": generation_disposition,
        }
    )


def _complete_pending_door(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationRecord:
    intent = record.doorPublication
    if intent is None or intent.state == "proven" or dry_run:
        return record
    try:
        proof = publish_door_intent(contract.contract_path, intent)
    except DoorPublicationError as exc:
        classification = exc.classification
        if classification.state == "accepted-before":
            action = pending_door_action(record, contract) or "recover"
            next_row = next(
                (
                    item
                    for item in legal_operation_controls(
                        contract,
                        record,
                        context=LifecycleControlProjectionContext(
                            allow_completed_disposition=True,
                            door=classification,
                        ),
                    )
                    if item["action"] == action
                ),
                None,
            )
            raise LifecycleControlError(
                "closeout-door-publication-interrupted",
                "the journaled closeout-door publication did not change contract bytes",
                expected=classification.expected,
                observed=classification.observed,
                next_action=action,
                next_tool=next_row["tool"] if next_row else None,
                next_args=next_row["arguments"] if next_row else None,
            ) from exc
        raise LifecycleControlError(
            "closeout-door-publication-conflict",
            exc.detail,
            expected=classification.expected,
            observed=classification.observed,
            next_action="developer-decision",
        ) from exc
    return store.update(lambda current: current.model_copy(update={"doorPublication": proof}))


def _require_generation(
    store: LifecycleOperationStore,
    command: LifecycleControlCommand,
    *,
    contract: WorktreeContract,
) -> LifecycleOperationRecord:
    pending = store.read_successor_intent()
    if (
        pending is not None
        and command.action == "revise"
        and command.expected_generation == pending.predecessor.generation
    ):
        return pending.predecessor
    record = store.effective_read()
    if record is None:
        raise LifecycleControlError(
            "lifecycle-operation-missing",
            "no lifecycle operation exists for the task and kind",
            next_action="developer-decision",
        )
    if record.generation != command.expected_generation:
        legal_rows = legal_operation_controls(
            contract,
            record,
            context=LifecycleControlProjectionContext(
                allow_completed_disposition=command.allow_completed_disposition,
                caller=command.caller,
            ),
        )
        next_row = legal_rows[0] if legal_rows else None
        raise LifecycleControlError(
            "lifecycle-generation-changed",
            "the advertised lifecycle generation is stale",
            expected={"generation": command.expected_generation},
            observed={"generation": record.generation},
            next_action=next_row["action"] if next_row else "developer-decision",
            next_tool=next_row["tool"] if next_row else None,
            next_args=next_row["arguments"] if next_row else None,
        )
    return record


def _stamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
