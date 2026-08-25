"""Journal-owned contract repair after a final organizational quality failure."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.task_publication_lock import task_publication_lock
from agents_remember.models.lifecycles.door import CloseoutDoorGeneration
from agents_remember.models.lifecycles.operation import (
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
    OrganizationalCompletionRepairEvidence,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.integration.closeout.door import successor_waiting_door
from agents_remember.worktrees.integration.integration_branch_authority import integration_targets
from agents_remember.worktrees.integration.integration_branch_types import IntegrationTarget
from agents_remember.worktrees.integration.integration_ref_state import (
    require_unchanged_integration_refs,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    operation_state_fingerprint,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    load_contract,
    write_contract,
)


class OrganizationalRepairPublicationError(RuntimeError):
    """Exact contract-reset interruption or third-state contradiction."""

    def __init__(
        self,
        status: str,
        detail: str,
        *,
        evidence: OrganizationalCompletionRepairEvidence,
        observed: WorktreeContract,
        **outcome: object,
    ) -> None:
        unexpected = set(outcome) - {"next_action", "publication_failure"}
        if unexpected:
            raise TypeError(f"unsupported organizational repair outcome: {sorted(unexpected)}")
        next_action = _required_next_action(outcome.get("next_action"))
        publication = _publication_failure(outcome.get("publication_failure"))
        self.status = status
        self.detail = detail
        classification = _classify_organizational_repair_evidence(observed, evidence)
        self.expected = classification.expected
        self.observed = {
            **classification.observed,
            **publication,
        }
        self.next_action = next_action
        super().__init__(detail)


def _required_next_action(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("organizational repair next_action must be a string")
    return value


def _publication_failure(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("publication_failure must be a mapping or None")
    return {"publicationFailure": dict(value)}


@dataclass(frozen=True)
class OrganizationalRepairState:
    """Pure exact accepted/reset/third contract classification."""

    state: Literal["not-applicable", "accepted", "reset", "developer-decision"]
    expected: dict[str, object]
    observed: dict[str, object]

    def decision_payload(self) -> dict[str, object]:
        detail = "the contract is neither the accepted failed generation nor its exact reset"
        return {
            "state": "organizational-completion-contract-conflict",
            "reason": detail,
            "summary": detail,
            "developerDecisionRequired": True,
            "decisionSurface": detail,
            "nextAction": "developer-decision",
            "expected": self.expected,
            "observed": self.observed,
        }


def classify_organizational_completion_repair(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> OrganizationalRepairState:
    """Classify live reset bytes from journal evidence without mutating them."""

    evidence = record.organizationalRepair
    if evidence is None:
        return OrganizationalRepairState("not-applicable", {}, {})
    return _classify_organizational_repair_evidence(contract, evidence)


def _classify_organizational_repair_evidence(
    contract: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
) -> OrganizationalRepairState:
    expected: dict[str, object] = {
        "acceptedContractSha256": evidence.acceptedContractSha256,
        "resetContractSha256": evidence.resetContractSha256,
    }
    observed: dict[str, object] = {
        "contractSha256": _contract_sha256(contract),
        "closeoutStatus": contract.closeout_status,
        "integrationStatus": contract.integration_status,
        "doorDisposition": (contract.closeout_door.disposition if contract.closeout_door else ""),
    }
    live_sha = observed["contractSha256"]
    if live_sha == evidence.acceptedContractSha256:
        state: Literal["accepted", "reset", "developer-decision"] = "accepted"
    elif live_sha == evidence.resetContractSha256:
        state = "reset"
    else:
        state = "developer-decision"
    return OrganizationalRepairState(state, expected, observed)


def record_organizational_completion_repair(
    contract: WorktreeContract,
    *,
    operation_key: str,
    failure: Mapping[str, object],
    progress: Callable[[str, Mapping[str, object]], None],
) -> None:
    """Persist the reset generation at the exact organizational gate-failure seam."""

    record = located_lifecycle_operation_store(contract, "integrate").read()
    if record is None or record.operationKey != operation_key:
        raise RuntimeError("organizational quality failure lost its lifecycle operation identity")
    evidence = organizational_completion_repair_evidence(contract, record)
    terminal_failure = {**failure, "ok": False, "operation": "worktree_integrate"}
    progress(
        "integration-quality",
        {
            "current_command": "persist exact organizational quality repair generation",
            "organizational_repair": evidence.model_dump(mode="json"),
            "organizational_failure": terminal_failure,
        },
    )


def organizational_completion_repair_evidence(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> OrganizationalCompletionRepairEvidence:
    """Build the durable exact reset identity before the first repair publication."""

    authority = _require_operation_identity(contract, record)
    if operation_state_fingerprint(contract) != record.candidateState:
        raise CloseoutQueueError(
            "organizational-completion-operation-state-mismatch",
            "quality repair requires the exact contract state accepted by its integration",
        )
    expected_commits = _repair_commits(contract, authority)
    reset = _quality_repair_contract(
        contract,
        expected_commits=expected_commits,
        repair_record=record,
    )
    sprint_ref, candidate_ref, owning_master = _repair_binding(contract)
    return OrganizationalCompletionRepairEvidence(
        operationKey=record.operationKey,
        candidateState=record.candidateState,
        contractPath=record.contractPath,
        taskId=record.taskId,
        taskName=record.taskName,
        sprintTaskDocument=sprint_ref.key,
        candidateTaskDocument=candidate_ref.key,
        owningMasterTaskDocument=owning_master,
        codeCommit=expected_commits[0],
        memoryContentCommit=expected_commits[1],
        ledgerCommit=expected_commits[2],
        acceptedContractSha256=_contract_sha256(contract),
        resetContractSha256=_contract_sha256(reset),
    )


def prepare_organizational_completion_repair(
    contract: WorktreeContract,
) -> WorktreeContract:
    """Publish only the exact contract/door reset proven by journal evidence."""

    record = _durable_cancelled_repair_record(contract)
    _require_failed_gate_result(record)
    evidence = _required_repair_evidence(record)
    _require_repair_evidence(record, evidence)
    classification = classify_organizational_completion_repair(contract, record)
    if classification.state == "developer-decision":
        raise OrganizationalRepairPublicationError(
            "organizational-completion-contract-conflict",
            "the contract is neither the accepted failed generation nor its exact reset",
            evidence=evidence,
            observed=contract,
            next_action="developer-decision",
        )
    if classification.state == "reset":
        return contract
    authority = _require_operation_identity(contract, record)
    expected_commits = (
        authority.codeCandidateCommit,
        authority.memoryContentCommit,
        authority.ledgerCommit,
    )
    _require_matching_repair_commits(expected_commits, evidence)
    sprint_ref, candidate_ref, owning_master = _repair_binding(contract)
    _require_matching_repair_binding(
        sprint_ref,
        candidate_ref,
        owning_master,
        evidence,
    )
    _require_matching_candidate_state(contract, record)
    reset = _quality_repair_contract(
        contract,
        expected_commits=expected_commits,
        repair_record=record,
    )
    _require_complete_reset(reset, evidence)
    _publish_reset(contract=contract, reset=reset, evidence=evidence, record=record)
    return load_contract(contract.contract_path)


def _require_failed_gate_result(record: LifecycleOperationRecord) -> None:
    result = record.result
    if not isinstance(result, dict) or result.get("state") != (
        "organizational-completion-gate-failed"
    ):
        raise CloseoutQueueError(
            "organizational-completion-operation-identity-mismatch",
            "quality repair requires the exact failed integration operation and contract",
        )


def _required_repair_evidence(
    record: LifecycleOperationRecord,
) -> OrganizationalCompletionRepairEvidence:
    evidence = record.organizationalRepair
    if evidence is None:
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-missing",
            "quality repair requires its durable exact reset generation",
        )
    return evidence


def _require_matching_repair_commits(
    expected: tuple[str, str, str],
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    observed = (evidence.codeCommit, evidence.memoryContentCommit, evidence.ledgerCommit)
    if observed != expected:
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-mismatch",
            "quality repair evidence does not match its integration authority",
        )


def _require_matching_repair_binding(
    sprint_ref: TaskDocumentRef,
    candidate_ref: TaskDocumentRef,
    owning_master: str,
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    observed = (sprint_ref.key, candidate_ref.key, owning_master)
    expected = (
        evidence.sprintTaskDocument,
        evidence.candidateTaskDocument,
        evidence.owningMasterTaskDocument,
    )
    if observed != expected:
        raise CloseoutQueueError(
            "organizational-completion-repair-binding-mismatch",
            "quality repair contract no longer names its claimed door task binding",
        )


def _require_matching_candidate_state(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> None:
    if operation_state_fingerprint(contract) != record.candidateState:
        raise CloseoutQueueError(
            "organizational-completion-operation-state-mismatch",
            "quality repair contract no longer matches its accepted operation state",
        )


def _require_complete_reset(
    reset: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    if not _quality_repair_is_complete(reset, evidence):
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-mismatch",
            "quality repair contract does not produce its durable reset generation",
        )


def _durable_cancelled_repair_record(contract: WorktreeContract) -> LifecycleOperationRecord:
    record = located_lifecycle_operation_store(contract, "integrate").read()
    invalid = any(
        (
            record is None,
            getattr(record, "status", None) != "cancelled",
            not getattr(record, "cancelRequested", False),
            not getattr(record, "finishedAt", ""),
        )
    )
    if invalid:
        raise CloseoutQueueError(
            "organizational-completion-durable-cancellation-required",
            "quality repair requires its exact durable cancelled integration operation",
        )
    assert record is not None
    return record


def _publish_reset(
    *,
    contract: WorktreeContract,
    reset: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
    record: LifecycleOperationRecord,
) -> None:
    with task_publication_lock(contract.coordination_root, contract.repo_name):
        current = load_contract(contract.contract_path)
        require_unchanged_integration_refs(record)
        classification = _classify_organizational_repair_evidence(current, evidence)
        if _reset_already_published(classification, evidence=evidence, observed=current):
            return
        _require_sources_unmoved(current)
        _write_reset_contract(contract, reset, evidence)
        observed = load_contract(contract.contract_path)
        _require_published_reset(observed, evidence)


def _reset_already_published(
    classification: OrganizationalRepairState,
    *,
    evidence: OrganizationalCompletionRepairEvidence,
    observed: WorktreeContract,
) -> bool:
    if classification.state == "developer-decision":
        raise OrganizationalRepairPublicationError(
            "organizational-completion-contract-conflict",
            "the contract is neither the accepted failed generation nor its exact reset",
            evidence=evidence,
            observed=observed,
            next_action="developer-decision",
        )
    return classification.state == "reset"


def _write_reset_contract(
    contract: WorktreeContract,
    reset: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    try:
        write_contract(reset.contract_path, reset)
    except (OSError, RuntimeError) as exc:
        _raise_reset_write_failure(contract, evidence, exc)


def _raise_reset_write_failure(
    contract: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
    error: OSError | RuntimeError,
) -> None:
    observed = load_contract(contract.contract_path)
    if _quality_repair_is_complete(observed, evidence):
        return
    if _contract_sha256(observed) == evidence.acceptedContractSha256:
        raise OrganizationalRepairPublicationError(
            "organizational-completion-contract-publication-interrupted",
            "the exact reset write left the accepted contract bytes unchanged",
            evidence=evidence,
            observed=observed,
            next_action="cancel",
            publication_failure=public_failure_evidence(
                stage="organizational-reset-publication",
                side="contract",
                name=contract.contract_path.name,
                error_type=type(error).__name__,
                observed={"state": "accepted-before"},
            ),
        ) from error
    raise OrganizationalRepairPublicationError(
        "organizational-completion-contract-conflict",
        "the contract changed to a third byte state during exact reset publication",
        evidence=evidence,
        observed=observed,
        next_action="developer-decision",
    ) from error


def _require_published_reset(
    observed: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    if not _quality_repair_is_complete(observed, evidence):
        raise OrganizationalRepairPublicationError(
            "organizational-completion-contract-conflict",
            "the reset publication did not produce its exact journaled bytes",
            evidence=evidence,
            observed=observed,
            next_action="developer-decision",
        )


def _require_operation_identity(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> IntegrationOperationAuthority:
    authority = _require_base_operation_identity(contract, record)
    targets = {target.side: target for target in integration_targets(contract)}
    _require_code_operation_authority(contract, authority, targets["code"])
    memory = targets.get("memory")
    if contract.memory_mode == "external":
        _require_memory_operation_authority(contract, authority, memory)
    else:
        _require_no_memory_operation_authority(authority)
    return authority


def _require_base_operation_identity(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> IntegrationOperationAuthority:
    authority = record.integrationAuthority
    actual_path = contract.contract_path.resolve()
    mismatch = any(
        (
            record.operationKind != "integrate",
            authority is None,
            Path(record.contractPath).resolve() != actual_path,
            Path(record.input.contractPath).resolve() != actual_path,
            record.taskId != contract.task_id,
            record.taskName != contract.task_name,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-operation-identity-mismatch",
            "quality repair requires the exact failed integration operation and contract",
        )
    assert authority is not None
    return authority


def _require_code_operation_authority(
    contract: WorktreeContract,
    authority: IntegrationOperationAuthority,
    code: IntegrationTarget,
) -> None:
    mismatch = any(
        (
            code.kind != authority.targetKind,
            code.repository.resolve() != Path(authority.codeRepository).resolve(),
            code.branch != authority.codeSourceBranch,
            authority.codeSourceRef != f"refs/heads/{code.branch}",
            authority.codeSourceCommit != contract.code_base_commit,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-operation-authority-mismatch",
            "quality repair contract no longer matches its code integration authority",
        )


def _require_memory_operation_authority(
    contract: WorktreeContract,
    authority: IntegrationOperationAuthority,
    memory: IntegrationTarget | None,
) -> None:
    mismatch = any(
        (
            memory is None,
            getattr(memory, "repository", Path()).resolve()
            != Path(authority.memoryRepository).resolve(),
            getattr(memory, "branch", None) != authority.memorySourceBranch,
            authority.memorySourceRef != f"refs/heads/{getattr(memory, 'branch', '')}",
            authority.memorySourceCommit != contract.memory_base_commit,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-operation-authority-mismatch",
            "quality repair contract no longer matches its memory integration authority",
        )


def _require_no_memory_operation_authority(authority: IntegrationOperationAuthority) -> None:
    unexpected = any(
        (
            authority.memoryRepository,
            authority.memorySourceBranch,
            authority.memorySourceRef,
            authority.memorySourceCommit,
            authority.memoryContentCommit,
            authority.ledgerCommit,
        )
    )
    if unexpected:
        raise CloseoutQueueError(
            "organizational-completion-operation-authority-mismatch",
            "code-only quality repair carries unexpected memory integration authority",
        )


def _require_repair_evidence(
    record: LifecycleOperationRecord,
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    mismatch = any(
        (
            evidence.operationKey != record.operationKey,
            evidence.candidateState != record.candidateState,
            Path(evidence.contractPath).resolve() != Path(record.contractPath).resolve(),
            evidence.taskId != record.taskId,
            evidence.taskName != record.taskName,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-mismatch",
            "quality repair evidence does not match its durable operation identity",
        )


def _repair_binding(
    contract: WorktreeContract,
) -> tuple[TaskDocumentRef, TaskDocumentRef, str]:
    door = _required_claimed_door(contract.closeout_door)
    topology = TaskDocumentTopology(contract.coordination_root)
    master = topology.parent(door.taskDocumentRef)
    if master is None:
        raise CloseoutQueueError(
            "organizational-completion-master-mismatch",
            "quality repair candidate has no exact owning master",
        )
    expected = (door.owningMasterTaskDocumentRef, door.sprintTaskDocumentRef)
    observed = (master, topology.parent(master))
    if observed != expected:
        raise CloseoutQueueError(
            "organizational-completion-master-mismatch",
            "quality repair candidate has no exact owning master",
        )
    return door.sprintTaskDocumentRef, door.taskDocumentRef, master.key


def _required_claimed_door(door: CloseoutDoorGeneration | None) -> CloseoutDoorGeneration:
    if door is None or door.disposition != "claimed":
        raise CloseoutQueueError(
            "organizational-completion-candidate-required",
            "organizational completion repair requires its exact claimed door candidate",
        )
    return door


def _contract_sha256(contract: WorktreeContract) -> str:
    published = contract_publication_text(contract.contract_path, contract)
    return hashlib.sha256(published.encode("utf-8")).hexdigest()


def _repair_commits(
    contract: WorktreeContract,
    authority: IntegrationOperationAuthority,
) -> tuple[str, str, str]:
    if authority.targetKind != "sprint-super":
        raise CloseoutQueueError(
            "organizational-completion-authority-mismatch",
            "organizational completion repair requires sprint-super integration authority",
        )
    expected = (
        authority.codeCandidateCommit,
        authority.memoryContentCommit,
        authority.ledgerCommit,
    )
    observed = (contract.code_commit, contract.memory_content_commit, contract.ledger_commit)
    if observed != expected:
        raise CloseoutQueueError(
            "organizational-completion-authority-mismatch",
            "the failed final-leaf contract no longer matches its integration authority",
        )
    return expected


def _quality_repair_contract(
    contract: WorktreeContract,
    *,
    expected_commits: tuple[str, str, str],
    repair_record: LifecycleOperationRecord,
) -> WorktreeContract:
    _require_reopenable_contract(contract, expected_commits)
    door = _successor_repair_door(contract.closeout_door, repair_record)
    return amend_contract(
        replace(
            contract,
            approved_for_commit=False,
            commit_approval_note="",
            code_commit="",
            memory_content_commit="",
            ledger_commit="",
            integration_strategy="",
            integrated_code_commit="",
            integrated_memory_content_commit="",
            integrated_ledger_commit="",
            memory_state="",
            closeout_door=door,
        ),
        ContractCells(
            human_review_status="pending-review",
            closeout_status="not-started",
            integration_status="not-started",
        ),
    )


def _require_reopenable_contract(
    contract: WorktreeContract,
    expected_commits: tuple[str, str, str],
) -> None:
    mismatch = any(
        (
            contract.kind != "leaf",
            contract.closeout_status != "completed",
            contract.integration_status != "not-started",
            not contract.approved_for_commit,
            not contract.code_commit,
            (contract.code_commit, contract.memory_content_commit, contract.ledger_commit)
            != expected_commits,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-contract-mismatch",
            "only the exact closed, unintegrated final leaf can be reopened for quality repair",
        )


def _successor_repair_door(
    door: CloseoutDoorGeneration | None,
    repair_record: LifecycleOperationRecord,
) -> CloseoutDoorGeneration | None:
    if door is None:
        return None
    if getattr(door, "disposition", None) != "claimed":
        raise CloseoutQueueError(
            "organizational-completion-door-mismatch",
            "quality repair requires the existing claimed closeout-door generation",
        )
    publication = repair_record.integrationPublication
    declared_at = getattr(publication, "preparedAt", repair_record.queuedAt)
    return successor_waiting_door(
        door,
        declared_by="organizational-quality-repair",
        declared_at=declared_at,
    )


def _quality_repair_is_complete(
    contract: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
) -> bool:
    return _contract_sha256(contract) == evidence.resetContractSha256


def _require_sources_unmoved(contract: WorktreeContract) -> None:
    targets = {target.side: target for target in integration_targets(contract)}
    _require_code_source_unmoved(contract, targets["code"])
    if contract.memory_mode != "external":
        return
    _require_memory_source_unmoved(contract, targets.get("memory"))


def _require_code_source_unmoved(contract: WorktreeContract, code: IntegrationTarget) -> None:
    mismatch = any(
        (
            code.kind != "sprint-super",
            branch_commit(contract.code_repo_path, code.branch) != contract.code_base_commit,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-source-moved",
            "quality repair refuses because the code super moved after the failed gate",
        )


def _require_memory_source_unmoved(
    contract: WorktreeContract,
    memory: IntegrationTarget | None,
) -> None:
    repo = contract.memory_repo_path
    mismatch = any(
        (
            memory is None,
            repo is None,
            branch_commit(repo, getattr(memory, "branch", "")) != contract.memory_base_commit
            if repo is not None
            else True,
        )
    )
    if mismatch:
        raise CloseoutQueueError(
            "organizational-completion-memory-source-moved",
            "quality repair refuses because the memory super moved after the failed gate",
        )
