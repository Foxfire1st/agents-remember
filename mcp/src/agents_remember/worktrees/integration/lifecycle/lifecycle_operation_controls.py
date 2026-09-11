"""Task-addressed lifecycle controls derived from durable and live evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.certification.corrective import RedCatalogDisposition
from agents_remember.models.closeout.input import (
    CloseoutCorrectedCall,
    CloseoutMessageInput,
)
from agents_remember.models.closeout.source import CandidateAdmissionFacts, SchedulingGradeInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.door import CloseoutDoorRequest
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    GatePolicyRuleSnapshot,
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.operation_kinds import LifecycleControlAction
from agents_remember.worktrees.closeout_input import (
    CloseoutInputError,
    corrected_closeout_arguments,
)
from agents_remember.worktrees.integration.closeout.door import (
    DoorPublicationClassification,
    DoorPublicationError,
    classify_door_publication,
    live_closeout_door,
    prepare_door_publication,
    publish_door_intent,
)
from agents_remember.worktrees.integration.closeout.door_source import (
    authorize_door_actor,
    door_task_context,
    superseding_door_generation,
    updated_door_generation,
)
from agents_remember.worktrees.integration.closeout.ledger_recovery import (
    classify_closeout_ledger_recovery,
)
from agents_remember.worktrees.integration.closeout.operation_admission import (
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
from agents_remember.worktrees.integration.integration_ref_state import classify_integration_refs
from agents_remember.worktrees.integration.lifecycle.control.cancellation import cancel_operation
from agents_remember.worktrees.integration.lifecycle.generation.resume import (
    requeued_same_generation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_completed_disposition import (
    require_completed_disposition,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    fingerprint_payload,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_projection import (
    LifecycleControlProjectionContext,
    generation_requires_recovery,
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_door_control import (
    complete_pending_door,
    complete_pending_door_locked,
    project_closeout_refresh,
    record_door_intent,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_live_decision import (
    raise_live_evidence_decision,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    bind_projection_result,
    operation_projection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_recovery import (
    reconcile_control_mutations,
    recover_direct_landing_under_authority,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    launch_detached_worker,
    start_closeout_successor_under_lease,
)
from agents_remember.worktrees.integration.lifecycle.worker.state import (
    project_worker_exit,
    reconcile_worker_exit,
)
from agents_remember.worktrees.integration.mutation_evidence import closeout_requires_recovery
from agents_remember.worktrees.queue.closeout_projection_publication import (
    refresh_closeout_projection,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


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
    resume_messages: CloseoutMessageInput | None = None
    resume_gate_policy: list[GatePolicyRuleSnapshot] | None = None
    corrective_dispositions: tuple[RedCatalogDisposition, ...] = ()
    supersede_grade: SchedulingGradeInput | None = None
    supersede_admission: CandidateAdmissionFacts | None = None
    allow_completed_disposition: bool = False
    caller: DeclaredCaller | None = None


@dataclass(frozen=True)
class _ControlObservation:
    contract: WorktreeContract
    store: LifecycleOperationStore
    record: LifecycleOperationRecord
    integration: IntegrationOperationObservation | None
    door: DoorPublicationClassification | None


@dataclass(frozen=True)
class _SupersedeSource:
    grade: SchedulingGradeInput
    admission: CandidateAdmissionFacts
    caller: DeclaredCaller
    declaration_fingerprint: str


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
    if not command.intent_note.strip() and command.action != "resume":
        raise LifecycleControlError(
            "lifecycle-control-intent-required",
            "lifecycle control requires a nonblank developer intent note",
            next_action=command.action,
        )
    contract, location = _reload_control_contract(command)
    store = LifecycleOperationStore(location.journal_path(command.kind))
    observed = _observe_control_under_lease(command, contract=contract, store=store)
    legal_rows = legal_operation_controls(
        observed.contract,
        observed.record,
        context=LifecycleControlProjectionContext(
            allow_completed_disposition=command.allow_completed_disposition,
            caller=command.caller,
            integration=observed.integration,
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
            return cancel_operation(
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
        preserve_recovery_intent=command.action in {"recover", "resume"},
    )
    integration = None
    if command.kind == "integrate":
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


def _execute_legal_control(  # noqa: PLR0911
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    command: LifecycleControlCommand,
) -> LifecycleOperationProjection:
    action = command.action
    if action == "cancel":
        return cancel_operation(contract, store, record, dry_run=command.dry_run)
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
    if action == "resume":
        if record.operationKind == "closeout" and not closeout_requires_recovery(record):
            return _resume_closeout_successor(
                contract,
                store,
                record,
                command=command,
            )
        return _resume(
            contract,
            store,
            record,
            action="resume",
            dry_run=command.dry_run,
        )
    if action == "retire":
        return _dispose_completed(
            contract,
            store,
            record,
            command=command,
        )
    if action == "supersede":
        return _dispose_completed(
            contract,
            store,
            record,
            command=command,
        )
    raise AssertionError(f"unhandled lifecycle control action: {action}")


def _resume(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    action: Literal["retry", "recover", "resume"],
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
    _require_resumable(record, action)
    if dry_run:
        return operation_projection(record, contract=contract)
    contract, record = _resume_closeout_publications(contract, store, record)
    if record.operationKind == "direct-landing":
        current_contract, _location = reread_configured_contract(
            contract,
            record.input.configPath,
        )
        _require_proven_closeout_door_for_launch(record)
        current = recover_direct_landing_under_authority(current_contract, store, record)
        return operation_projection(
            current,
            contract=load_contract(current_contract.contract_path),
        )
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
            next_action=action,
        )
    launch_detached_worker(contract, requeued)
    current = store.read() or requeued
    return operation_projection(current, contract=load_contract(contract.contract_path))


def _resume_closeout_publications(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
) -> tuple[WorktreeContract, LifecycleOperationRecord]:
    if record.operationKind not in {"closeout", "direct-landing"}:
        return contract, record
    if record.legacyMigration is not None and record.doorPublication is None:
        # The explicit schema-1 bridge proves a retained generation that predates
        # contract-owned closeout doors.  Normal schema-3 records never bypass the
        # claimed-door launch gate.
        return contract, record
    if record.doorPublication is None:
        raise LifecycleControlError(
            "closeout-initial-door-intent-missing",
            "the canonical closeout record is missing its create-time claimed-door intent",
            expected={"doorPublication": "create-time-claimed-intent-or-proof"},
            observed={"doorPublication": "absent", "generation": record.generation},
            next_action="developer-decision",
        )
    elif record.doorPublication.state == "intent":
        record = complete_pending_door(contract, store, record, dry_run=False)
        contract = load_contract(contract.contract_path)
    _require_proven_closeout_door_for_launch(record)
    return contract, record


def _require_proven_closeout_door_for_launch(
    record: LifecycleOperationRecord,
) -> None:
    publication = record.doorPublication
    if (
        publication is not None
        and publication.state == "proven"
        and publication.generation.disposition == "claimed"
        and publication.generation.operationKind == record.operationKind
        and publication.generation.operationFingerprint == record.fingerprint
        and publication.generation.claimedOperationKey == record.operationKey
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
        },
        next_action="developer-decision",
    )


def _require_resumable(
    record: LifecycleOperationRecord,
    action: Literal["retry", "recover", "resume"],
) -> None:
    if record.status in {"completed", "cancelled"}:
        raise LifecycleControlError(
            "lifecycle-generation-terminal",
            "same-generation retry/recover cannot replace this terminal disposition",
            next_action="resume" if record.status == "cancelled" else "retire",
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
    command: LifecycleControlCommand,
) -> LifecycleOperationProjection:
    if command.action == "retire":
        return _retire_completed(contract, store, record, dry_run=command.dry_run)
    assert command.action == "supersede"
    source = _supersede_source(command, record)
    if record.generationDisposition == "superseded":
        return _resume_completed_supersede(
            contract,
            store,
            record,
            declaration_fingerprint=source.declaration_fingerprint,
            dry_run=command.dry_run,
        )
    require_completed_disposition(contract, record, "supersede")
    runtime = load_config(command.configured_authority)
    if command.dry_run:
        return _preview_completed_supersede(
            runtime,
            contract,
            record,
            source,
        )
    return _publish_completed_supersede(
        runtime,
        contract,
        store,
        record,
        source,
    )


def _retire_completed(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    dry_run: bool,
) -> LifecycleOperationProjection:
    if record.generationDisposition == "retired":
        return operation_projection(record, contract=contract)
    require_completed_disposition(contract, record, "retire")
    if dry_run:
        return operation_projection(record, contract=contract)
    updated = store.update(
        lambda current: current.model_copy(
            update={
                "generationDisposition": "retired",
                "guidance": "This completed generation is retired for audit only.",
            }
        )
    )
    return operation_projection(updated, contract=contract)


def _supersede_source(
    command: LifecycleControlCommand,
    record: LifecycleOperationRecord,
) -> _SupersedeSource:
    grade = command.supersede_grade
    admission = command.supersede_admission
    caller = command.caller
    if grade is None or admission is None or caller is None:
        raise LifecycleControlError(
            "lifecycle-supersede-source-required",
            "supersede requires fresh scheduling, admission, and authorized caller evidence",
            next_action="supersede",
        )
    return _SupersedeSource(
        grade,
        admission,
        caller,
        _supersede_declaration_fingerprint(
            record,
            grade=grade,
            admission=admission,
            caller=caller,
        ),
    )


def _resume_completed_supersede(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    declaration_fingerprint: str,
    dry_run: bool,
) -> LifecycleOperationProjection:
    _require_supersede_declaration_match(record, declaration_fingerprint)
    current_contract = contract
    if not dry_run:
        operation_input = record.input
        current_contract, _location = reread_configured_contract(
            contract,
            operation_input.configPath,
        )
        current_record = store.read()
        if current_record is None or current_record.generationDisposition != "superseded":
            raise LifecycleControlError(
                "lifecycle-generation-changed",
                "the superseded generation changed before replay",
                next_action="developer-decision",
            )
        _require_supersede_declaration_match(current_record, declaration_fingerprint)
        record = complete_pending_door_locked(current_contract, store, current_record)
        current_contract = load_contract(current_contract.contract_path)
    _require_waiting_supersede_proof(record)
    projection = operation_projection(record, contract=current_contract)
    return project_closeout_refresh(
        projection,
        current_contract,
        record,
        dry_run=dry_run,
    )


def _require_waiting_supersede_proof(
    record: LifecycleOperationRecord,
) -> None:
    publication = record.doorPublication
    if publication is None or publication.generation.disposition != "waiting":
        raise LifecycleControlError(
            "closeout-door-supersede-proof-required",
            "superseded journal history does not retain its exact waiting door successor",
            next_action="developer-decision",
        )


def _preview_completed_supersede(
    runtime,
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    source: _SupersedeSource,
) -> LifecycleOperationProjection:
    try:
        successor = superseding_door_generation(
            runtime,
            contract,
            actor=source.caller,
            grade=source.grade,
            admission=source.admission,
        )
    except CloseoutQueueError as exc:
        raise LifecycleControlError(
            exc.status,
            "fresh supersede door evidence is not admissible",
            next_action="supersede",
        ) from exc
    return bind_projection_result(
        operation_projection(record, contract=contract),
        {
            "state": "would-supersede",
            "doorGeneration": successor.model_dump(mode="json"),
        },
    )


def _publish_completed_supersede(
    runtime,
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    source: _SupersedeSource,
) -> LifecycleOperationProjection:

    operation_input = record.input
    current_contract, _location = reread_configured_contract(
        contract,
        operation_input.configPath,
    )
    current_record = store.read()
    if current_record is not None and current_record.generationDisposition == "superseded":
        _require_supersede_declaration_match(
            current_record,
            source.declaration_fingerprint,
        )
        updated = complete_pending_door_locked(
            current_contract,
            store,
            current_record,
        )
        current_contract = load_contract(current_contract.contract_path)
    elif current_record is None or current_record != record:
        raise LifecycleControlError(
            "lifecycle-generation-changed",
            "the completed generation changed before supersede publication",
            expected={"generation": record.generation, "fingerprint": record.fingerprint},
            observed={
                "generation": current_record.generation if current_record is not None else 0,
                "fingerprint": current_record.fingerprint if current_record is not None else "",
            },
            next_action="developer-decision",
        )
    else:
        require_completed_disposition(current_contract, current_record, "supersede")
        try:
            successor = superseding_door_generation(
                runtime,
                current_contract,
                actor=source.caller,
                grade=source.grade,
                admission=source.admission,
            )
        except CloseoutQueueError as exc:
            raise LifecycleControlError(
                exc.status,
                "fresh supersede door evidence is not admissible",
                next_action="supersede",
            ) from exc
        intent = prepare_door_publication(current_contract, successor)
        updated = store.update(
            lambda current: record_door_intent(
                current.model_copy(
                    update={
                        "generationDisposition": "superseded",
                        "supersedeDeclarationFingerprint": source.declaration_fingerprint,
                        "guidance": (
                            "A distinct current-source waiting door successor is published."
                        ),
                    }
                ),
                intent,
                generation_disposition="superseded",
            )
        )
        updated = complete_pending_door_locked(current_contract, store, updated)
        current_contract = load_contract(current_contract.contract_path)
    projection = operation_projection(updated, contract=current_contract)
    return project_closeout_refresh(
        projection,
        current_contract,
        updated,
        dry_run=False,
    )


def _supersede_declaration_fingerprint(
    record: LifecycleOperationRecord,
    *,
    grade: SchedulingGradeInput,
    admission: CandidateAdmissionFacts,
    caller: DeclaredCaller,
) -> str:
    return fingerprint_payload(
        {
            "schema": "closeout-supersede-declaration/v1",
            "operationFingerprint": record.fingerprint,
            "grade": grade.model_dump(mode="json"),
            "admission": admission.model_dump(mode="json"),
            "caller": caller.model_dump(mode="json"),
        }
    )


def _require_supersede_declaration_match(
    record: LifecycleOperationRecord,
    requested: str,
) -> None:
    accepted = record.supersedeDeclarationFingerprint
    if accepted is None:
        raise LifecycleControlError(
            "lifecycle-supersede-declaration-proof-missing",
            "the canonical superseded journal is missing its accepted declaration fingerprint",
            next_action="developer-decision",
        )
    if accepted != requested:
        raise LifecycleControlError(
            "lifecycle-supersede-declaration-conflict",
            "a competing supersede declaration cannot replay the accepted successor",
            expected={"supersedeDeclarationFingerprint": accepted},
            observed={"supersedeDeclarationFingerprint": requested},
            next_action="developer-decision",
        )


def _resume_closeout_successor(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    command: LifecycleControlCommand,
) -> LifecycleOperationProjection:
    if record.operationKind != "closeout" or command.resume_messages is None:
        raise LifecycleControlError(
            "lifecycle-resume-input-required",
            "closeout resume requires fresh explicit commit-message fields",
            next_action="resume",
        )
    if command.dry_run:
        validated = _validated_resume(contract, record, command)
        return bind_projection_result(
            operation_projection(record, contract=contract),
            {
                "state": "would-resume",
                "summary": "Resume would cancel this attempt and start the repaired candidate.",
                "nextAction": "resume",
                "nextTool": "worktree_operation_control",
                "nextArgs": _resume_arguments(validated, record),
            },
        )

    if record.status == "cancelled":
        complete_pending_door(contract, store, record, dry_run=False)
    else:
        cancel_operation(contract, store, record, dry_run=False)
    record = store.read() or record
    current_contract = _refresh_resume_door(contract, command)
    record = store.read() or record
    validated = _validated_resume(current_contract, record, command)
    candidate_tree = validated.candidate.tree
    if candidate_tree is None:
        raise LifecycleControlError(
            "lifecycle-resume-candidate-missing",
            "closeout resume requires one exact repaired code candidate",
            next_action="resume",
        )
    return start_closeout_successor_under_lease(
        current_contract,
        validated.operation_input,
        validated.candidate,
    )


def _refresh_resume_door(
    contract: WorktreeContract,
    command: LifecycleControlCommand,
) -> WorktreeContract:
    """Bind resume to the current source candidate before claiming its successor."""

    current_door = live_closeout_door(contract)
    if current_door is None:
        raise LifecycleControlError(
            "closeout-resume-door-missing",
            "closeout resume requires the cancelled generation's waiting door",
            next_action="developer-decision",
        )
    actor = command.caller or DeclaredCaller(
        role="orchestrator",
        task_document_ref=current_door.sprintTaskDocumentRef,
    )
    request = CloseoutDoorRequest(
        action="update-provenance",
        contract_path=contract.contract_path.as_posix(),
        candidate_task_document_ref=(
            current_door.taskDocumentRef if contract.kind == "series" else None
        ),
        expected_generation_id=current_door.generationId,
        grade=SchedulingGradeInput(
            priority=current_door.schedulingProvenance.priority,
            judgmentId=current_door.schedulingProvenance.judgmentId,
        ),
        admission=CandidateAdmissionFacts(
            resourceReady=current_door.admissionProvenance.resourceReady,
            resourceReason=current_door.admissionProvenance.resourceReason,
            admissionReady=current_door.admissionProvenance.admissionReady,
            admissionReason=current_door.admissionProvenance.admissionReason,
        ),
        caller=actor,
    )
    runtime = load_config(command.configured_authority)
    current, _location = reread_configured_contract(
        contract,
        command.configured_authority,
    )
    try:
        context = door_task_context(runtime, current, request)
        authorize_door_actor(actor, context, "update-provenance")
        generation = updated_door_generation(context, request, actor)
        proof = publish_door_intent(
            current.contract_path,
            prepare_door_publication(current, generation),
        )
    except DoorPublicationError as exc:
        classification = exc.classification
        raise LifecycleControlError(
            exc.status,
            exc.detail,
            expected=classification.expected,
            observed=classification.observed,
            next_action="resume"
            if classification.state == "accepted-before"
            else "developer-decision",
        ) from exc
    except CloseoutQueueError as exc:
        raise LifecycleControlError(
            exc.status,
            str(exc),
            next_action="resume",
        ) from exc
    if proof.state != "proven":
        raise LifecycleControlError(
            "closeout-resume-door-unproven",
            "resume could not prove its source-door successor",
            next_action="resume",
        )
    updated = load_contract(contract.contract_path)
    door = live_closeout_door(updated)
    if door is None:
        raise LifecycleControlError(
            "closeout-resume-door-missing",
            "resume could not reload its waiting successor door",
            next_action="developer-decision",
        )
    try:
        effect = refresh_closeout_projection(
            updated.coordination_root,
            door.sprintTaskDocumentRef,
        )
    except Exception as exc:
        raise LifecycleControlError(
            "closeout-resume-projection-failed",
            "resume could not rebuild the exact current closeout projection",
            next_action="resume",
        ) from exc
    if effect.rebuild.outcome not in {"published", "already-current"}:
        raise LifecycleControlError(
            "closeout-resume-projection-invalid",
            "resume requires an exact current closeout projection before claiming",
            observed={"rebuild": effect.rebuild.outcome},
            next_action="resume",
        )
    return load_contract(updated.contract_path)


def _validated_resume(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
    command: LifecycleControlCommand,
) -> ValidatedCloseoutAdmission:
    admission = _closeout_resume_admission(record, command)
    try:
        validated = prevalidate_closeout_operation_admission(contract, admission)
    except CloseoutInputError as exc:
        raise LifecycleControlError(
            exc.status,
            "closeout resume input is invalid; use the corrected fields",
            expected=exc.response_fields(),
            next_action="resume",
        ) from exc
    return validated


def _resume_arguments(
    validated: ValidatedCloseoutAdmission,
    record: LifecycleOperationRecord,
) -> dict[str, object]:
    operation_input = validated.operation_input
    assert isinstance(operation_input, CloseoutOperationInput)
    args: dict[str, object] = {
        "contract_path": operation_input.contractPath,
        "operation_kind": "closeout",
        "action": "resume",
        "intent_note": operation_input.approvalNote,
        "dry_run": False,
    }
    for leg, field in (
        ("code", "code_commit_message"),
        ("memory", "memory_commit_message"),
        ("ledger", "ledger_commit_message"),
    ):
        accepted = getattr(operation_input.effectiveInput, leg)
        if accepted.state == "enabled":
            args[field] = accepted.message
    if operation_input.correctiveDispositions:
        args["corrective_dispositions"] = [
            item.model_dump(mode="json") for item in operation_input.correctiveDispositions
        ]
    args["expected_generation"] = record.generation
    return args


def _closeout_resume_admission(
    record: LifecycleOperationRecord,
    command: LifecycleControlCommand,
) -> CloseoutOperationAdmission:
    operation_input = record.input
    assert isinstance(operation_input, CloseoutOperationInput)
    config_path = operation_input.configPath
    gate_policy = command.resume_gate_policy or operation_input.gatePolicy
    messages = command.resume_messages or CloseoutMessageInput()
    accepted = operation_input.effectiveInput
    messages = CloseoutMessageInput(
        code=messages.code or (accepted.message_for("code") if accepted.enabled("code") else None),
        memory=(
            messages.memory
            or (accepted.message_for("memory") if accepted.enabled("memory") else None)
        ),
        ledger=(
            messages.ledger
            or (accepted.message_for("ledger") if accepted.enabled("ledger") else None)
        ),
    )
    return CloseoutOperationAdmission(
        config_path=config_path,
        contract_path=Path(record.contractPath),
        messages=messages,
        approval_note=command.intent_note.strip() or operation_input.approvalNote,
        gate_policy=gate_policy,
        corrective_dispositions=command.corrective_dispositions,
        corrected_call=CloseoutCorrectedCall(
            tool="worktree_operation_control",
            arguments={
                **corrected_closeout_arguments(
                    record.contractPath,
                    operation_kind="closeout",
                    action="resume",
                    expected_generation=record.generation,
                    intent_note="<fresh developer intent>",
                )
            },
        ),
    )


def _require_generation(
    store: LifecycleOperationStore,
    command: LifecycleControlCommand,
    *,
    contract: WorktreeContract,
) -> LifecycleOperationRecord:
    record = store.read()
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
