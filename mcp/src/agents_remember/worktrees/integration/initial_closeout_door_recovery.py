"""Pure classifier for the sole recoverable initial closeout-door intent gap."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.worktrees.integration.closeout_door import (
    door_generation_for_operation,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_lifecycle_evidence_pair,
)
from agents_remember.worktrees.integration.mutation_evidence import (
    ephemeral_git_mutation_snapshot,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

InitialDoorRecoveryState = Literal[
    "not-applicable",
    "synthesizable",
    "developer-decision",
]


@dataclass(frozen=True)
class InitialCloseoutDoorRecoveryClassification:
    """Exact read-only result shared by status and mutating recovery."""

    state: InitialDoorRecoveryState
    expected: dict[str, object] | None = None
    observed: dict[str, object] | None = None

    def decision_payload(self) -> dict[str, object]:
        detail = "the closeout record cannot prove the sole pre-intent publication cut"
        public = public_lifecycle_evidence_pair(
            self.expected or {},
            self.observed or {},
        )
        return {
            "state": "closeout-initial-door-intent-missing",
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "expected": public.expected,
            "observed": public.observed,
        }


def classify_initial_closeout_door_recovery(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> InitialCloseoutDoorRecoveryClassification:
    """Permit synthesis only for exact generation-one create-before-intent state."""

    if (
        record.operationKind != "closeout"
        or record.doorPublication is not None
        or record.legacyMigration is not None
    ):
        return InitialCloseoutDoorRecoveryClassification("not-applicable")
    expected_door = door_generation_for_operation(contract, record, "claimed")
    expected = {
        "operationKind": "closeout",
        "generation": 1,
        "attempt": 1,
        "status": "queued",
        "phase": "queued",
        "generationDisposition": "active",
        "doorGenerationId": expected_door.generationId,
        "contractDoor": None,
        "candidateState": record.candidateState,
        "mutationStates": {leg: "pre-mutation" for leg in sorted(record.mutationEvidence)},
    }
    observed = _initial_door_gap_observed(contract, record)
    return InitialCloseoutDoorRecoveryClassification(
        "developer-decision" if observed else "synthesizable",
        expected=expected,
        observed=observed,
    )


def _initial_door_gap_observed(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> dict[str, object]:
    unexpected = _initial_door_record_drift(record)
    unexpected.update(_initial_door_history_drift(record))
    if contract.closeout_door is not None:
        unexpected["contractDoor"] = contract.closeout_door.model_dump(mode="json")
    current_state = closeout_contract_sha256(contract)
    if current_state != record.candidateState:
        unexpected["contractCandidateState"] = current_state
    live_git = _initial_door_live_git_drift(record)
    if live_git:
        unexpected["liveGit"] = live_git
    return unexpected


def _initial_door_record_drift(record: LifecycleOperationRecord) -> dict[str, object]:
    unexpected: dict[str, object] = {}
    exact_fields = {
        "operationKind": (record.operationKind, "closeout"),
        "generation": (record.generation, 1),
        "attempt": (record.attempt, 1),
        "status": (record.status, "queued"),
        "phase": (record.phase, "queued"),
        "generationDisposition": (record.generationDisposition, "active"),
    }
    for name, (actual, expected) in exact_fields.items():
        if actual != expected:
            unexpected[name] = actual
    absent_fields = {
        "predecessorFingerprint": record.predecessorFingerprint,
        "successorFingerprint": record.successorFingerprint,
        "startedAt": record.startedAt,
        "finishedAt": record.finishedAt,
        "result": record.result,
        "failure": record.failure,
        "recoveryCommits": record.recoveryCommits,
        "closeoutFinalizedContractSha256": record.closeoutFinalizedContractSha256,
        "workerPid": record.workerPid,
        "workerLease": record.workerLease,
        "workerProcessFingerprint": record.workerProcessFingerprint,
        "workerTermination": record.workerTermination,
        "cancellationEvidence": record.cancellationEvidence,
        "legacyMigration": record.legacyMigration,
    }
    unexpected.update({name: value for name, value in absent_fields.items() if value})
    if record.approvalClaimed:
        unexpected["approvalClaimed"] = True
    if record.cancelRequested:
        unexpected["cancelRequested"] = True
    if record.irreversibleBoundaryEntered:
        unexpected["irreversibleBoundaryEntered"] = True
    mutation_states = {
        leg: evidence.state
        for leg, evidence in record.mutationEvidence.items()
        if evidence.state != "pre-mutation"
    }
    if mutation_states:
        unexpected["mutationStates"] = mutation_states
    return unexpected


def _initial_door_history_drift(record: LifecycleOperationRecord) -> dict[str, object]:
    unexpected: dict[str, object] = {}
    if record.doorPublicationHistory:
        unexpected["doorPublicationHistory"] = [
            item.model_dump(mode="json") for item in record.doorPublicationHistory
        ]
    if record.workerTerminationHistory:
        unexpected["workerTerminationHistory"] = [
            item.model_dump(mode="json") for item in record.workerTerminationHistory
        ]
    if record.mutationHistory:
        unexpected["mutationHistory"] = {
            leg: [item.model_dump(mode="json") for item in attempts]
            for leg, attempts in record.mutationHistory.items()
        }
    return unexpected


def _initial_door_live_git_drift(
    record: LifecycleOperationRecord,
) -> dict[str, object]:
    observed: dict[str, object] = {}
    for leg, evidence in record.mutationEvidence.items():
        accepted = evidence.acceptedBefore
        if accepted is None:
            observed[leg] = {"status": "accepted-prestate-missing"}
            continue
        try:
            current = ephemeral_git_mutation_snapshot(Path(evidence.repository))
        except (OSError, RuntimeError) as exc:
            observed[leg] = {
                "status": "unreadable",
                "side": leg,
                "name": Path(evidence.repository).name,
                "errorType": type(exc).__name__,
            }
            continue
        if current != accepted:
            observed[leg] = current.model_dump(mode="json")
    return observed
