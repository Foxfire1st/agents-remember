"""Task-addressed lifecycle controls derived from durable and live evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NoReturn

from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.certification.corrective import RedCatalogDisposition
from agents_remember.models.closeout.source import CandidateAdmissionFacts, SchedulingGradeInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.operation_kinds import LifecycleControlAction
from agents_remember.worktrees.disabled_memory import OPT_OUT_STATUS, opt_out_detail_for_contract
from agents_remember.worktrees.integration.closeout.door import (
    DoorPublicationClassification,
    classify_door_publication,
    prepare_door_publication,
)
from agents_remember.worktrees.integration.closeout.door_source import (
    superseding_door_generation,
)
from agents_remember.worktrees.integration.configured_contract_authority import (
    reread_configured_contract,
)
from agents_remember.worktrees.integration.lifecycle.control.cancellation import cancel_operation
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
    direct_supersede_refusal,
    generation_requires_recovery,
    legal_operation_controls,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_door_control import (
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
from agents_remember.worktrees.knowledge_gate import settle_direct_closing
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
    if command.admitted_contract.memory_mode != "external":
        # A contract that carries the removed mode takes no operation control
        # (MIK-R50 R4a): the way out is abandon and a fresh start, not control.
        raise LifecycleControlError(
            OPT_OUT_STATUS,
            opt_out_detail_for_contract(command.admitted_contract, "worktree_operation_control"),
        )
    contract, location = _reload_control_contract(command)
    store = LifecycleOperationStore(location.journal_path(command.kind))
    observed = _observe_control_under_lease(command, contract=contract, store=store)
    if command.kind == "direct-landing" and command.action == "supersede":
        # A direct landing claims no door, so there is nothing to supersede
        # (ruling e55c0093 point 3); retire is its owner's disposition.
        raise direct_supersede_refusal(observed.contract, observed.record)
    legal_rows = legal_operation_controls(
        observed.contract,
        observed.record,
        context=LifecycleControlProjectionContext(
            allow_completed_disposition=command.allow_completed_disposition,
            caller=command.caller,
        ),
    )
    legal = {item["action"] for item in legal_rows}
    if command.action not in legal:
        raise_live_evidence_decision(
            observed.contract,
            observed.record,
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


# The tool that replaces a generation this journal no longer runs.
_RETIRED_GENERATION_TOOLS: dict[LifecycleOperationKind, str] = {
    "closeout": "worktree_closeout_apply",
    "integrate": "worktree_integrate",
}


def _retired_generation_refusal(
    kind: LifecycleOperationKind,
    contract_path: str,
    *,
    generation: int | None = None,
    status: str | None = None,
) -> NoReturn:
    """Name the call that replaces a kind this journal no longer runs.

    A closeout and an integration run inside their own call and record nothing, so neither leaves a
    generation here: the journal may hold one an older build wrote, or none at all, and both answers
    point at the replacing tool. The generation and status appear only when a record exists, so an
    absent record is not reported as an observed one.
    """

    next_tool = _RETIRED_GENERATION_TOOLS[kind]
    arguments: dict[str, object] = {"contract_path": contract_path}
    if kind == "closeout":
        arguments["intent_note"] = "<fresh developer intent>"
    observed: dict[str, object] = {"operationKind": kind}
    if generation is not None:
        observed["generation"] = generation
    if status is not None:
        observed["status"] = status
    raise LifecycleControlError(
        "lifecycle-operation-retired",
        f"the operation journal no longer runs {kind} generations: a closeout and an integration "
        "run inside one call and record nothing, so this journal holds no such generation to "
        f"control; run the operation again with {next_tool} and keep the journal for audit",
        expected={"operationKind": kind, "servedBy": next_tool},
        observed=observed,
        next_action="re-apply",
        next_tool=next_tool,
        next_args=arguments,
    )


def _refuse_retired_generation(record: LifecycleOperationRecord, *, contract_path: str) -> None:
    """Answer a closeout or integration record an older build left in the journal.

    Such a record still parses, and refusing it here -- after it was read, before anything
    reconciles it -- lets the answer name the exact record and the call that replaces it, instead of
    offering a control that cannot run. The new call uses the admitted contract location because
    the retained record can still name its original location.
    """

    if record.operationKind not in _RETIRED_GENERATION_TOOLS:
        return
    _retired_generation_refusal(
        record.operationKind,
        contract_path,
        generation=record.generation,
        status=record.status,
    )


def _observe_control_under_lease(
    command: LifecycleControlCommand,
    *,
    contract: WorktreeContract,
    store: LifecycleOperationStore,
) -> _ControlObservation:
    """Return one newest record and every legal-action classifier derived from it."""

    record = _require_generation(store, command, contract=contract)
    _refuse_retired_generation(record, contract_path=contract.contract_path.as_posix())
    record = reconcile_control_mutations(
        store,
        record,
        dry_run=command.dry_run,
        preserve_recovery_intent=command.action in {"recover", "resume"},
    )
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
    return _ControlObservation(contract, store, record, door)


def _execute_legal_control(
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
    _require_resumable(record, action)
    if dry_run:
        return operation_projection(record, contract=contract)
    if record.operationKind == "direct-landing":
        current_contract, _location = reread_configured_contract(
            contract,
            record.input.configPath,
        )
        # Direct-landing authority is the grant plus the admitted evidence,
        # proven by the recovery classifier inside the recover call: the
        # route never claims closeout doors, so no door proof is required
        # for launch (F04). A direct landing claims no door by design, so the
        # two door checks a record without a door cannot pass do not apply to
        # it; they stay exactly as they are for a closeout. The recovery runs
        # on what the record carries and settles the kept closing receipt in
        # the same call (MIK-R143 rule 3b, rulings 08236bba and e55c0093).
        current = recover_direct_landing_under_authority(current_contract, store, record)
        if current.status == "completed":
            settle_direct_closing(current_contract, current=current.fingerprint, state="landed")
        return operation_projection(
            current,
            contract=load_contract(current_contract.contract_path),
        )
    # Only a direct landing keeps a generation in the journal; a closeout and an integration
    # record left by an older build was already refused with the call that replaces it.
    _refuse_retired_generation(record, contract_path=contract.contract_path.as_posix())
    raise AssertionError(f"unhandled lifecycle operation kind: {record.operationKind}")


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


def _require_generation(
    store: LifecycleOperationStore,
    command: LifecycleControlCommand,
    *,
    contract: WorktreeContract,
) -> LifecycleOperationRecord:
    record = store.read()
    if record is None:
        # A closeout and an integration record nothing, so their absent case is the ordinary state
        # after a synchronous call, not a missing generation: answer it with the replacing tool.
        if command.kind in _RETIRED_GENERATION_TOOLS:
            _retired_generation_refusal(command.kind, contract.contract_path.as_posix())
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
