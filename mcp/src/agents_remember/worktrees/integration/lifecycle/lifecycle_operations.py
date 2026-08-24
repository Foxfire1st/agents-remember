"""Task-addressed start, observe, recover, cancel, and projection for lifecycle jobs."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.task_publication_lock import task_publication_lock
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.git_command import git_environment
from agents_remember.kernel.platform_subprocess import (
    native_command,
    native_subprocess_environment,
)
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.operation import (
    CloseoutOperationInput,
    IntegrateOperationInput,
    IntegrationConflictTransaction,
    IntegrationOperationAuthority,
    LifecycleOperationInput,
    LifecycleOperationKind,
    LifecycleOperationProjection,
    LifecycleOperationRecord,
)
from agents_remember.models.lifecycles.termination import WorkerTerminationEvidence
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.closeout_input import require_effective_closeout_plan
from agents_remember.worktrees.integration.closeout_door import (
    DoorContractReadFailure,
    DoorPublicationError,
    classify_door_publication,
    door_generation_for_operation,
    prepare_door_publication,
    publish_door_intent,
)
from agents_remember.worktrees.integration.closeout_operation_admission import (
    CloseoutOperationAdmission,
    prevalidate_closeout_operation_admission,
    resolve_closeout_operation_admission,
)
from agents_remember.worktrees.integration.closeout_recovery_projection import (
    closeout_generation_retained,
    derive_closeout_recovery_commits,
)
from agents_remember.worktrees.integration.configured_contract_authority import (
    reread_configured_contract,
)
from agents_remember.worktrees.integration.integration_branch_authority import integration_targets
from agents_remember.worktrees.integration.integration_publication_fence import (
    classify_integration_door_authority,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_worker_launch
from agents_remember.worktrees.integration.lifecycle.lifecycle_closeout_claim_evidence import (
    claimed_predecessor_for_waiting_successor,
    closeout_preview_args,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_generation_resume import (
    requeued_same_generation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_candidate import (
    LifecycleOperationCandidate,
    LifecycleOperationCandidateBinding,
    fingerprint_payload,
    lifecycle_operation_candidate,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
    operation_state_fingerprint,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_lease import (
    contract_lifecycle_lease,
    require_lifecycle_operation_compatible,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
    LifecycleOperationLocationError,
    located_lifecycle_operation_report_path,
    located_lifecycle_operation_store,
    require_contract_matches_lifecycle_operation_location,
    resolve_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_projection import (
    OperationProjectionContext,
    operation_projection,
    parse_operation_stamp,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_read_decision import (
    lifecycle_journal_read_decision,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
    LifecycleOperationStore,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_worker_termination import (
    worker_process_fingerprint,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    initial_closeout_mutation_evidence,
    reconcile_closeout_mutations,
)
from agents_remember.worktrees.modules.git import branch_commit, is_ancestor
from agents_remember.worktrees.queue.closeout_projection_publication import (
    projection_refresh_failure_effect,
    refresh_closeout_projection,
)
from agents_remember.worktrees.queue.closeout_queue import require_first_ready_generation
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
)

STALE_HEARTBEAT_SECONDS = 30.0
OperationLauncher = Callable[[WorktreeContract, LifecycleOperationRecord], None]


@dataclass(frozen=True)
class _OperationExecution:
    timestamp: datetime
    launcher: OperationLauncher


class _CloseoutResumeNoLongerRequired(Exception):
    """The current record advanced while an exact duplicate was being resumed."""


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def operation_fingerprint(operation_input: LifecycleOperationInput) -> str:
    return fingerprint_payload(operation_input.model_dump(mode="json"))


def operation_key(contract_path: Path, kind: LifecycleOperationKind, fingerprint: str) -> str:
    identity = f"{contract_path.resolve().as_posix()}\0{kind}\0{fingerprint}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def start_or_observe_operation(
    operation_input: LifecycleOperationInput,
    admitted_contract: WorktreeContract,
    *,
    launcher: OperationLauncher | None = None,
    now: datetime | None = None,
) -> LifecycleOperationProjection:
    if not isinstance(operation_input, IntegrateOperationInput):
        raise RuntimeError(
            "closeout operations require lease-bound raw-input admission through "
            "start_or_observe_closeout_operation"
        )
    with contract_lifecycle_lease(admitted_contract):
        contract, _location = reread_configured_contract(
            admitted_contract,
            operation_input.configPath,
        )
        _validate_input_identity(contract, operation_input)
        door_authority = classify_integration_door_authority(contract, None)
        if not door_authority.valid:
            raise LifecycleControlError(
                door_authority.status,
                door_authority.detail,
                expected=door_authority.expected,
                observed=door_authority.observed,
                next_action="developer-decision",
            )
        require_lifecycle_operation_compatible(
            contract,
            operation_kind=operation_input.kind,
        )
        store = _store(contract, "integrate")
        retained = _retained_integration_recovery_record(store.read(), operation_input)
        if retained is None:
            integration_authority = _integration_authority(contract, operation_input)
            candidate = lifecycle_operation_candidate(
                LifecycleOperationCandidateBinding(
                    operation_input=operation_input,
                    candidate_state=operation_state_fingerprint(contract),
                    integration_authority=integration_authority,
                )
            )
        else:
            integration_authority = retained.integrationAuthority
            candidate = LifecycleOperationCandidate(
                retained.candidateState,
                retained.candidateTree,
                retained.fingerprint,
            )
        return _start_or_observe_operation(
            contract,
            operation_input,
            candidate=candidate,
            integration_authority=integration_authority,
            execution=_operation_execution(launcher, now),
        )


def start_or_observe_closeout_operation(
    admission: CloseoutOperationAdmission,
    admitted_contract: WorktreeContract,
    *,
    launcher: OperationLauncher | None = None,
    now: datetime | None = None,
) -> LifecycleOperationProjection:
    """Normalize and admit one closeout generation under its lifecycle lease."""
    with contract_lifecycle_lease(admitted_contract):
        current_contract, _location = reread_configured_contract(
            admitted_contract,
            admission.config_path,
        )
        validated = prevalidate_closeout_operation_admission(current_contract, admission)
        _validate_input_identity(current_contract, validated.operation_input)
        store = _store(current_contract, "closeout")
        _require_pending_initial_door_convergent(current_contract, store.read())
        operation_input, candidate = resolve_closeout_operation_admission(
            current_contract,
            store.read(),
            admission,
            validated,
        )
        require_lifecycle_operation_compatible(
            current_contract,
            operation_kind="closeout",
        )
        return _start_or_observe_operation(
            current_contract,
            operation_input,
            candidate=candidate,
            integration_authority=None,
            execution=_operation_execution(launcher, now),
        )


def _require_pending_initial_door_convergent(
    contract: WorktreeContract,
    record: LifecycleOperationRecord | None,
) -> None:
    if (
        record is None
        or record.operationKind != "closeout"
        or record.status not in {"queued", "running"}
        or record.doorPublication is None
        or record.doorPublication.state != "intent"
    ):
        return
    classification = classify_door_publication(record.doorPublication, contract)
    if classification.state != "developer-decision":
        return
    payload = classification.decision_payload()
    raise LifecycleControlError(
        str(payload["state"]),
        str(payload["decisionSurface"]),
        expected=classification.expected,
        observed=classification.observed,
        next_action="developer-decision",
    )


def _start_or_observe_operation(
    contract: WorktreeContract,
    operation_input: LifecycleOperationInput,
    *,
    candidate: LifecycleOperationCandidate,
    integration_authority: IntegrationOperationAuthority | None,
    execution: _OperationExecution,
) -> LifecycleOperationProjection:
    store = _store(contract, operation_input.kind)
    timestamp = execution.timestamp
    if operation_input.kind == "closeout":
        _reconcile_closeout_store(store, now=timestamp, fresh_dead_worker=False)
        assert isinstance(operation_input, CloseoutOperationInput)
        current, contract, created, sprint_ref = _claim_closeout_operation(
            contract,
            store,
            operation_input,
            candidate=candidate,
            timestamp=timestamp,
        )
        try:
            projection_effect = refresh_closeout_projection(
                contract.coordination_root,
                sprint_ref,
            )
        except Exception as exc:
            projection_effect = projection_refresh_failure_effect(
                contract.coordination_root,
                sprint_ref,
                exc,
            )
        projection = _recover_launch_and_project(
            contract,
            store,
            current,
            created=created,
            execution=execution,
        )
        return projection.model_copy(update={"projectionEffects": [projection_effect]})
    queued = queued_operation_record(
        contract,
        operation_input,
        candidate,
        integration_authority,
        timestamp,
    )
    current, created = _create_or_replace_generation(
        store,
        queued,
        contract=contract,
        operation_input=operation_input,
        candidate=candidate,
    )
    return _recover_launch_and_project(
        contract,
        store,
        current,
        created=created,
        execution=execution,
    )


def _claim_closeout_operation(
    admitted_contract: WorktreeContract,
    store: LifecycleOperationStore,
    operation_input: CloseoutOperationInput,
    *,
    candidate: LifecycleOperationCandidate,
    timestamp: datetime,
) -> tuple[LifecycleOperationRecord, WorktreeContract, bool, TaskDocumentRef]:
    """Transfer one first-ready waiting generation into root-journal authority."""

    with task_publication_lock(admitted_contract.coordination_root, admitted_contract.repo_name):
        contract, _location = reread_configured_contract(
            admitted_contract,
            operation_input.configPath,
        )
        _validate_input_identity(contract, operation_input)
        queued = queued_operation_record(
            contract,
            operation_input,
            candidate,
            None,
            timestamp,
        )
        door = contract.closeout_door
        if door is None:
            raise LifecycleControlError(
                "closeout-door-missing",
                "closeout claim requires one current waiting door generation",
                expected={"disposition": "waiting"},
                observed={"disposition": "absent"},
                next_action="developer-decision",
            )
        sprint_ref = door.sprintTaskDocumentRef
        if door.disposition == "waiting":
            if candidate.state != closeout_contract_sha256(contract):
                raise LifecycleControlError(
                    "closeout-candidate-state-moved",
                    "closeout contract bytes changed after operation admission",
                    expected={"candidateState": candidate.state},
                    observed={"candidateState": closeout_contract_sha256(contract)},
                    next_action="retry-closeout-preview",
                    next_tool="worktree_closeout_preview",
                    next_args=closeout_preview_args(operation_input),
                )
            if candidate.tree != door.candidateTree:
                raise LifecycleControlError(
                    "closeout-door-candidate-moved",
                    "the admitted Git candidate no longer equals the waiting door candidate",
                    expected={"candidateTree": door.candidateTree},
                    observed={"candidateTree": candidate.tree or ""},
                    next_action="retry-closeout-preview",
                    next_tool="worktree_closeout_preview",
                    next_args=closeout_preview_args(operation_input),
                )
            require_first_ready_generation(
                contract.coordination_root,
                sprint_ref=sprint_ref,
                generation_id=door.generationId,
            )
            claimed = door_generation_for_operation(contract, queued, "claimed")
            queued = queued.model_copy(
                update={"doorPublication": prepare_door_publication(contract, claimed)}
            )
        elif door.disposition == "claimed":
            existing = store.read()
            if (
                existing is None
                or existing.operationKind != "closeout"
                or existing.fingerprint != door.operationFingerprint
                or existing.operationKey != door.claimedOperationKey
                or existing.fingerprint != candidate.fingerprint
            ):
                raise LifecycleControlError(
                    "closeout-door-claim-owner-conflict",
                    "the claimed door does not match the exact retained root-journal owner",
                    expected={
                        "operationKind": door.operationKind,
                        "operationFingerprint": door.operationFingerprint,
                        "operationKey": door.claimedOperationKey,
                    },
                    observed={
                        "operationKind": existing.operationKind if existing is not None else "",
                        "operationFingerprint": existing.fingerprint
                        if existing is not None
                        else "",
                        "operationKey": existing.operationKey if existing is not None else "",
                    },
                    next_action="developer-decision",
                )
        else:
            raise LifecycleControlError(
                "closeout-door-not-waiting",
                "closeout claim requires a waiting door generation",
                expected={"disposition": "waiting"},
                observed={"disposition": door.disposition},
                next_action="developer-decision",
            )

        current, created = _create_or_replace_generation(
            store,
            queued,
            contract=contract,
            operation_input=operation_input,
            candidate=candidate,
        )
        if not created:
            current, created = _resume_exact_duplicate_closeout(
                store,
                current,
                operation_input=operation_input,
                candidate=candidate,
            )
        if current.status not in {"queued", "running"}:
            return current, contract, created, sprint_ref
        current, contract = _publish_initial_closeout_door(contract, store, current)
        return current, contract, created, sprint_ref


def _publish_initial_closeout_door(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
) -> tuple[LifecycleOperationRecord, WorktreeContract]:
    """Publish/prove the claimed door before a closeout worker can execute."""

    publication = record.doorPublication
    if publication is None:
        raise LifecycleControlError(
            "closeout-initial-door-intent-missing",
            "the canonical schema-3 closeout journal is missing its create-time claimed-door "
            "intent; normal recovery cannot synthesize lifecycle authority",
            expected={"doorPublication": "create-time-intent-or-proof"},
            observed={"doorPublication": "absent", "generation": record.generation},
            next_action="developer-decision",
        )
    if publication.generation.disposition != "claimed":
        raise LifecycleControlError(
            "closeout-door-publication-conflict",
            "active closeout generation does not own a claimed door intent",
            expected={"disposition": "claimed"},
            observed={"disposition": publication.generation.disposition},
            next_action="developer-decision",
        )
    if publication.state == "intent":
        try:
            proof = publish_door_intent(contract.contract_path, publication)
        except DoorPublicationError as exc:
            classification = exc.classification
            recoverable = classification.state == "accepted-before"
            raise LifecycleControlError(
                exc.status,
                exc.detail,
                expected=classification.expected,
                observed=classification.observed,
                next_action="recover" if recoverable else "developer-decision",
                next_tool="worktree_operation_control" if recoverable else None,
                next_args=(
                    {
                        "contract_path": contract.contract_path.as_posix(),
                        "operation_kind": "closeout",
                        "action": "recover",
                        "expected_generation": record.generation,
                        "intent_note": "<developer intent>",
                        "dry_run": False,
                    }
                    if recoverable
                    else None
                ),
            ) from exc
        record = store.update(lambda current: current.model_copy(update={"doorPublication": proof}))
        contract = load_contract(contract.contract_path)
    return record, contract


def _create_or_replace_generation(
    store: LifecycleOperationStore,
    queued: LifecycleOperationRecord,
    *,
    contract: WorktreeContract,
    operation_input: LifecycleOperationInput,
    candidate: LifecycleOperationCandidate,
) -> tuple[LifecycleOperationRecord, bool]:
    """Create one generation or replace only a terminal, advanced generation."""

    current, created = store.create(queued)
    if current.fingerprint == candidate.fingerprint:
        return current, created
    if current.status not in {"completed", "failed", "cancelled"}:
        raise RuntimeError(
            f"conflicting {operation_input.kind} operation already exists for task "
            f"{contract.task_name}; wait for or resolve that task-bound operation"
        )
    if current.status == "cancelled" and operation_input.kind == "integrate":
        if current.candidateState == candidate.state:
            raise RuntimeError(
                "cancelled integrate generation requires an advanced task state before "
                "a fresh integration successor"
            )
        return store.replace_terminal(queued), True
    if current.status == "cancelled" and operation_input.kind == "closeout":
        successor = contract.closeout_door
        predecessor = claimed_predecessor_for_waiting_successor(current, successor)
        eligible = (
            successor is not None
            and successor.disposition == "waiting"
            and predecessor is not None
            and predecessor.state == "proven"
            and predecessor.generation.disposition == "claimed"
            and successor.predecessorGenerationId == predecessor.generation.generationId
            and current.generationDisposition == "cancelled"
            and current.cancellationEvidence is not None
            and current.cancellationEvidence.workerExitProven
        )
        if not eligible:
            raise RuntimeError(
                "cancelled closeout can advance only through an exact waiting door successor "
                "after proven worker exit"
            )
        return store.replace_terminal(queued), True
    if current.status != "completed":
        raise RuntimeError(
            f"terminal {operation_input.kind} generation requires the explicit "
            "task-addressed retry/recover/revise control"
        )
    if (
        operation_input.kind == "closeout"
        and current.generationDisposition == "active"
        and contract.integration_status != "completed"
        and closeout_generation_retained(current)
    ):
        raise RuntimeError(
            "completed closeout generation still owns unintegrated output; choose the "
            "advertised integrate, retire, or supersede disposition before a successor"
        )
    if (
        operation_input.kind == "integrate"
        and current.status == "completed"
        and current.candidateState == candidate.state
    ):
        raise RuntimeError(
            f"conflicting {operation_input.kind} parameters target an already completed "
            f"task state for {contract.task_name}; the task state has not advanced"
        )
    return store.replace_terminal(queued), True


def _resume_exact_duplicate_closeout(
    store: LifecycleOperationStore,
    record: LifecycleOperationRecord,
    *,
    operation_input: CloseoutOperationInput,
    candidate: LifecycleOperationCandidate,
) -> tuple[LifecycleOperationRecord, bool]:
    """Resume only the exact accepted closeout generation proven by admission."""

    if not _exact_duplicate_closeout_requires_resume(
        record,
        operation_input=operation_input,
        candidate=candidate,
    ):
        return record, False

    def resume(current: LifecycleOperationRecord) -> LifecycleOperationRecord:
        if not _exact_duplicate_closeout_requires_resume(
            current,
            operation_input=operation_input,
            candidate=candidate,
        ):
            raise _CloseoutResumeNoLongerRequired
        return requeued_same_generation(current)

    try:
        return store.resume_generation(resume, expected_generation=record.generation)
    except _CloseoutResumeNoLongerRequired:
        return store.observe_current() or record, False


def _exact_duplicate_closeout_requires_resume(
    record: LifecycleOperationRecord,
    *,
    operation_input: CloseoutOperationInput,
    candidate: LifecycleOperationCandidate,
) -> bool:
    """Classify the two mechanically resumable duplicate-apply states."""

    result = record.result if isinstance(record.result, dict) else {}
    exact_identity = (
        record.operationKind == "closeout"
        and record.input == operation_input
        and record.candidateState == candidate.state
        and record.candidateTree == candidate.tree
        and record.fingerprint == candidate.fingerprint
    )
    worker_authority_released = (
        record.workerPid is None
        and record.workerLease is None
        and record.workerProcessFingerprint is None
        and (record.workerTermination is None or record.workerTermination.state == "exited")
    )
    retained_output = closeout_generation_retained(record)
    resumable_status = (record.status == "failed" and not retained_output) or (
        record.status == "input-required" and retained_output
    )
    return bool(
        exact_identity
        and worker_authority_released
        and resumable_status
        and not record.cancelRequested
        and result.get("developerDecisionRequired") is not True
    )


def _recover_launch_and_project(
    contract: WorktreeContract,
    store: LifecycleOperationStore,
    current: LifecycleOperationRecord,
    *,
    created: bool,
    execution: _OperationExecution,
) -> LifecycleOperationProjection:
    """Recover an existing generation if needed, launch once, and project it."""

    timestamp = execution.timestamp
    should_launch = created
    if should_launch:
        lifecycle_worker_launch.launch_or_fail(contract, current, execution.launcher, store)
        current = store.read() or current
    return operation_projection(
        current,
        contract=contract,
        context=OperationProjectionContext(now=timestamp),
    )


def _operation_execution(
    launcher: OperationLauncher | None,
    now: datetime | None,
) -> _OperationExecution:
    return _OperationExecution(
        timestamp=(now or datetime.now(UTC)).replace(microsecond=0),
        launcher=launcher or launch_detached_worker,
    )


def _retained_integration_recovery_record(
    current: LifecycleOperationRecord | None,
    operation_input: IntegrateOperationInput,
) -> LifecycleOperationRecord | None:
    """Keep one accepted integration identity after its irreversible boundary."""

    if current is None or current.input != operation_input:
        return None
    if not current.irreversibleBoundaryEntered:
        return None
    if current.status in {"queued", "running", "input-required", "completed"}:
        return current
    return None


def observe_operation(
    contract_path: Path, kind: LifecycleOperationKind
) -> LifecycleOperationProjection | None:
    contract = load_contract(contract_path)
    try:
        current = _project_observed_record(_store(contract, kind).read())
    except LifecycleOperationReadError as error:
        return lifecycle_journal_read_decision(kind, error).projection()
    return None if current is None else operation_projection(current, contract=contract)


def latest_operation_projection(contract_path: Path) -> LifecycleOperationProjection | None:
    contract = load_contract(contract_path)
    records: list[LifecycleOperationRecord] = []
    for kind in ("closeout", "integrate", "direct-landing"):
        try:
            record = _project_observed_record(_store(contract, kind).read())
        except LifecycleOperationReadError as error:
            return lifecycle_journal_read_decision(kind, error).projection()
        if record is not None:
            records.append(record)
    if not records:
        return None
    active = [
        record
        for record in records
        if record.status in {"queued", "running", "input-required", "termination-required"}
    ]
    selected = max(active or records, key=_record_sort_stamp)
    return operation_projection(selected, contract=contract)


def current_operation_projections(
    contract_path: Path,
    *,
    allow_completed_disposition: bool = False,
    caller: DeclaredCaller | None = None,
    contract: WorktreeContract | None = None,
    location: LifecycleOperationLocation | None = None,
) -> list[LifecycleOperationProjection]:
    """Return every current operation kind; task status must not hide actionable siblings."""

    contract = contract or load_contract(contract_path)
    try:
        location = location or resolve_lifecycle_operation_location(
            contract.coordination_root,
            contract.contract_path,
        )
    except LifecycleOperationLocationError:
        return []
    records: list[LifecycleOperationRecord] = []
    decisions: list[LifecycleOperationProjection] = []
    for kind in ("closeout", "integrate", "direct-landing"):
        try:
            record = _project_observed_record(
                LifecycleOperationStore(location.journal_path(kind)).read()
            )
        except LifecycleOperationReadError as error:
            decisions.append(lifecycle_journal_read_decision(kind, error).projection())
            continue
        if record is not None:
            records.append(record)
    try:
        require_contract_matches_lifecycle_operation_location(contract, location)
    except LifecycleOperationLocationError as exc:
        return [
            *[_operation_location_decision(record, exc) for record in records],
            *decisions,
        ]
    projected = [
        operation_projection(
            record,
            contract=contract,
            context=OperationProjectionContext(
                allow_completed_disposition=allow_completed_disposition,
                caller=caller,
            ),
        )
        for record in sorted(records, key=lambda item: item.operationKind)
    ]
    return sorted([*projected, *decisions], key=lambda item: item.kind)


def _operation_location_decision(
    record: LifecycleOperationRecord,
    error: LifecycleOperationLocationError,
) -> LifecycleOperationProjection:
    result = {
        "state": error.status,
        "developerDecisionRequired": True,
        "decisionSurface": error.detail,
        "nextAction": "developer-decision",
        "expected": error.expected,
        "observed": error.observed,
    }
    return operation_projection(record).model_copy(
        update={
            "result": result,
            "failure": error.detail,
            "guidance": error.detail,
            "cancellable": False,
            "legalControls": [],
        }
    )


def unreadable_contract_operation_projections(
    location: LifecycleOperationLocation,
    *,
    error_type: str,
    name: str,
) -> list[LifecycleOperationProjection]:
    """Project every retained exact-path generation while contract authority is unreadable."""

    projections: list[LifecycleOperationProjection] = []
    contract_path = location.contract_path.resolve(strict=False)
    for kind in ("closeout", "integrate", "direct-landing"):
        store = LifecycleOperationStore(location.journal_path(kind))
        try:
            # Contract-invalid status cannot safely run Git reconciliation: that
            # reconciliation deliberately revalidates repository authority by
            # reading the contract.  Retain only the read-only worker-exit
            # projection here, then expose zero controls below.
            record = _project_worker_observed_record(store.read())
        except LifecycleOperationReadError as error:
            projections.append(lifecycle_journal_read_decision(kind, error).projection())
            continue
        if (
            record is None
            or record.operationKind != kind
            or Path(record.contractPath).resolve(strict=False) != contract_path
        ):
            continue
        publication = record.doorPublication
        if kind == "closeout" and publication is not None and publication.state == "intent":
            observation = classify_door_publication(
                publication,
                DoorContractReadFailure(error_type, ""),
            )
            projections.append(
                operation_projection(
                    record,
                    context=OperationProjectionContext(door=observation),
                )
            )
            continue
        surface = "the canonical task contract is unreadable for this retained operation"
        result = {
            "state": f"{kind}-contract-invalid",
            "developerDecisionRequired": True,
            "decisionSurface": surface,
            "nextAction": "developer-decision",
            "expected": {
                "contractPath": contract_path.as_posix(),
                "worktreeGroup": location.worktree_group.as_posix(),
                "operationKind": kind,
                "generation": record.generation,
            },
            "observed": public_failure_evidence(
                stage="contract-read",
                side="contract",
                name=name,
                error_type=error_type,
                observed={"state": "unreadable"},
            ),
        }
        projected = operation_projection(record)
        projections.append(
            projected.model_copy(
                update={
                    "result": result,
                    "failure": surface,
                    "guidance": surface,
                    "cancellable": False,
                    "legalControls": [],
                }
            )
        )
    return projections


def _project_observed_record(
    record: LifecycleOperationRecord | None,
) -> LifecycleOperationRecord | None:
    projected = _project_worker_observed_record(record)
    if projected is None:
        return None
    if projected.operationKind not in {"closeout", "direct-landing"}:
        return projected
    reconciled = reconcile_closeout_mutations(projected, temporary_indices=True)
    recovery_commits = derive_closeout_recovery_commits(projected, mutations=reconciled)
    if reconciled == projected.mutationEvidence and recovery_commits == projected.recoveryCommits:
        return projected
    return projected.model_copy(
        update={
            "mutationEvidence": reconciled,
            "recoveryCommits": recovery_commits,
            "irreversibleBoundaryEntered": (
                projected.irreversibleBoundaryEntered
                or any(item.state == "commit-proven" for item in reconciled.values())
            ),
        }
    )


def _project_worker_observed_record(
    record: LifecycleOperationRecord | None,
) -> LifecycleOperationRecord | None:
    if record is None:
        return None
    from agents_remember.worktrees.integration.lifecycle.lifecycle_worker_state import (  # noqa: PLC0415
        project_worker_exit,
    )

    return project_worker_exit(record)


def _reconcile_closeout_store(
    store: LifecycleOperationStore,
    *,
    now: datetime,
    fresh_dead_worker: bool,
) -> None:
    current = store.observe_current()
    for _attempt in range(3):
        if current is None or current.operationKind != "closeout":
            return
        if current.status in {"queued", "running"}:
            stale = _recoverable_stale(current, now)
            worker_dead = current.workerPid is not None and not _worker_process_group_alive(
                current.workerPid
            )
            if not stale and not (fresh_dead_worker and worker_dead):
                return
        reconciled = reconcile_closeout_mutations(current)
        recovery_commits = derive_closeout_recovery_commits(current, mutations=reconciled)
        if reconciled == current.mutationEvidence and recovery_commits == current.recoveryCommits:
            return
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
        updated, matched = store.update_if_current(
            current,
            lambda _record, projected=projected: projected,
        )
        if matched:
            return
        current = updated


def launch_detached_worker(contract: WorktreeContract, record: LifecycleOperationRecord) -> None:
    if record.operationKind == "direct-landing":
        raise RuntimeError("direct landing is synchronous and cannot launch a detached worker")
    reports_root = contract.worktree_group / "reports"
    report = Path(record.reportPath)
    atomic_write_text(report, "")
    env = native_subprocess_environment(git_environment(), temp_root=reports_root / "tmp")
    worker_lease = fingerprint_payload(
        {
            "operationKey": record.operationKey,
            "generation": record.generation,
            "attempt": record.attempt,
            "queuedAt": record.queuedAt,
        }
    )
    command = native_command(
        [
            sys.executable,
            "-m",
            "agents_remember.application.lifecycle.lifecycle_operation_worker",
            "--contract-path",
            record.contractPath,
            "--kind",
            record.operationKind,
            "--worker-lease",
            worker_lease,
        ],
        env,
    )
    with report.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            cwd=contract.code_worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    fingerprint = worker_process_fingerprint(process.pid)
    if fingerprint is None:
        fallback_fingerprint = fingerprint_payload(
            {
                "state": "unverified-spawned-worker",
                "pid": process.pid,
                "workerLease": worker_lease,
                "operationKey": record.operationKey,
                "generation": record.generation,
                "attempt": record.attempt,
            }
        )
        _store(contract, record.operationKind).update(
            lambda current: current.model_copy(
                update={
                    "status": "termination-required",
                    "phase": "termination-required",
                    "workerPid": process.pid,
                    "workerLease": worker_lease,
                    "workerProcessFingerprint": fallback_fingerprint,
                    "workerTermination": WorkerTerminationEvidence(
                        state="termination-required",
                        pid=process.pid,
                        lease=worker_lease,
                        processFingerprint=fallback_fingerprint,
                        requestedAt=now_iso(),
                        detail=(
                            "spawned worker identity could not be captured; no further "
                            "worker may launch until this exact process group exits"
                        ),
                    ),
                    "terminationReturnStatus": current.status,
                    "terminationReturnPhase": current.phase,
                    "currentCommand": "prove unverified spawned worker group exited",
                }
            )
        )
        raise RuntimeError(
            "detached worker process identity could not be captured; "
            "termination-required authority was retained"
        )
    _store(contract, record.operationKind).update(
        lambda current: (
            current.model_copy(
                update={
                    "workerPid": process.pid,
                    "workerLease": worker_lease,
                    "workerProcessFingerprint": fingerprint,
                }
            )
            if current.status == "queued" and current.fingerprint == record.fingerprint
            else current
        )
    )


def queued_operation_record(
    contract: WorktreeContract,
    operation_input: LifecycleOperationInput,
    candidate: LifecycleOperationCandidate,
    integration_authority: IntegrationOperationAuthority | None,
    timestamp: datetime,
) -> LifecycleOperationRecord:
    stamp = timestamp.isoformat()
    return LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        operationKind=operation_input.kind,
        candidateState=candidate.state,
        candidateTree=candidate.tree,
        fingerprint=candidate.fingerprint,
        operationKey=operation_key(
            contract.contract_path, operation_input.kind, candidate.fingerprint
        ),
        integrationAuthority=integration_authority,
        input=operation_input,
        status="queued",
        phase="queued",
        queuedAt=stamp,
        currentCommand=f"waiting to start {operation_input.kind}",
        reportPath=located_lifecycle_operation_report_path(
            contract,
            operation_input.kind,
        ).as_posix(),
        mutationEvidence=(
            initial_closeout_mutation_evidence(contract, operation_input.effectiveInput)
            if isinstance(operation_input, CloseoutOperationInput)
            else {}
        ),
    )


def _integration_authority(
    contract: WorktreeContract, operation_input: IntegrateOperationInput
) -> IntegrationOperationAuthority:
    if contract.closeout_status != "completed" or not contract.code_commit:
        raise RuntimeError("integration authority requires a completed closeout code commit")
    targets = {target.side: target for target in integration_targets(contract)}
    code_target = targets["code"]
    code_source_commit = branch_commit(contract.code_repo_path, code_target.branch)
    code_replay_required = not is_ancestor(
        contract.code_repo_path, code_source_commit, contract.code_commit
    )
    memory_source_commit = ""
    memory_replay_required = False
    if contract.memory_mode == "external":
        if (
            contract.memory_repo_path is None
            or not contract.memory_content_commit
            or not contract.ledger_commit
        ):
            raise RuntimeError(
                "external-memory integration authority requires repo and closeout commits"
            )
        memory_target = targets["memory"]
        memory_source_commit = branch_commit(contract.memory_repo_path, memory_target.branch)
        memory_replay_required = not is_ancestor(
            contract.memory_repo_path, memory_source_commit, contract.ledger_commit
        )
    else:
        memory_target = None
    conflict = None
    if operation_input.strategy == "replay" and (code_replay_required or memory_replay_required):
        if contract.kind == "series":
            raise RuntimeError(
                "atomic series integration cannot open a leaf conflict worktree; source drift "
                "requires orchestrator-owned block recovery or graph reshape"
            )
        conflict = IntegrationConflictTransaction(
            codeReplayRequired=code_replay_required,
            memoryReplayRequired=memory_replay_required,
            codeSourceRef=f"refs/heads/{code_target.branch}",
            codeSourceCommit=code_source_commit,
            codeCandidateCommit=contract.code_commit,
            memorySourceRef=(
                f"refs/heads/{memory_target.branch}" if memory_target is not None else ""
            ),
            memorySourceCommit=memory_source_commit,
            memoryContentCommit=contract.memory_content_commit,
            ledgerCommit=contract.ledger_commit,
            codeWorktree=contract.code_worktree.resolve().as_posix(),
            memoryWorktree=(
                contract.memory_worktree.resolve().as_posix()
                if contract.memory_worktree is not None
                else ""
            ),
        )
    return IntegrationOperationAuthority(
        targetKind=code_target.kind,
        codeRepository=code_target.repository.as_posix(),
        codeSourceBranch=code_target.branch,
        codeSourceRef=f"refs/heads/{code_target.branch}",
        codeSourceCommit=code_source_commit,
        codeCandidateCommit=contract.code_commit,
        memoryRepository=(memory_target.repository.as_posix() if memory_target is not None else ""),
        memorySourceBranch=(memory_target.branch if memory_target is not None else ""),
        memorySourceRef=(f"refs/heads/{memory_target.branch}" if memory_target is not None else ""),
        memorySourceCommit=memory_source_commit,
        memoryContentCommit=contract.memory_content_commit,
        ledgerCommit=contract.ledger_commit,
        conflictTransaction=conflict,
    )


def _recoverable_stale(record: LifecycleOperationRecord, now: datetime) -> bool:
    if record.status not in {"queued", "running"}:
        return False
    stamp = parse_operation_stamp(record.heartbeatAt or record.queuedAt)
    stale = (now - stamp).total_seconds() > STALE_HEARTBEAT_SECONDS
    if not stale or record.workerPid is None:
        return stale
    return not _worker_process_group_alive(record.workerPid)


def _worker_process_group_alive(pid: int) -> bool:
    """Treat a reused/live process group as owned until a human resolves the stale record."""

    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _validate_input_identity(
    contract: WorktreeContract, operation_input: LifecycleOperationInput
) -> None:
    if Path(operation_input.contractPath).resolve() != contract.contract_path.resolve():
        raise RuntimeError("lifecycle operation input does not resolve to its loaded contract")
    if isinstance(operation_input, CloseoutOperationInput):
        if not operation_input.approvalNote.strip():
            raise RuntimeError("closeout apply requires a non-empty approval intent note")
        require_effective_closeout_plan(contract, operation_input.effectiveInput, route="worktree")


def _store(contract: WorktreeContract, kind: LifecycleOperationKind) -> LifecycleOperationStore:
    return located_lifecycle_operation_store(contract, kind)


def _record_sort_stamp(record: LifecycleOperationRecord) -> str:
    return record.finishedAt or record.heartbeatAt or record.startedAt or record.queuedAt
