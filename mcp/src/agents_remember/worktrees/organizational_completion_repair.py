"""Queue-owned repair transition after a final organizational quality failure."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.controlplane.integration_authority_lock import integration_authority_lock
from agents_remember.models.closeout_queue import CloseoutQueueState
from agents_remember.models.lifecycles.operation import (
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
    OrganizationalCompletionRepairEvidence,
)
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import CloseoutQueueError, now_iso
from agents_remember.worktrees.closeout_queue_lifecycle import (
    QueueBinding,
    _graph_context,
    _initial_state,
    _internal_event,
    _operation_owner,
    _required_operation_key,
    contract_queue_binding,
)
from agents_remember.worktrees.integration_branch_authority import integration_targets
from agents_remember.worktrees.lifecycle_operation_identity import operation_state_fingerprint
from agents_remember.worktrees.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.modules.git import branch_commit
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    contract_to_text,
    load_contract,
    write_contract,
)


@dataclass(frozen=True)
class _RepairContext:
    contract: WorktreeContract
    reset: WorktreeContract
    binding: QueueBinding
    owner: str
    topology: TaskDocumentTopology
    expected_commits: tuple[str, str, str]
    record: LifecycleOperationRecord
    evidence: OrganizationalCompletionRepairEvidence


def record_organizational_completion_repair(
    contract: WorktreeContract,
    *,
    operation_key: str,
    failure: Mapping[str, object],
    progress: Callable[[str, Mapping[str, object]], None],
) -> None:
    """Persist the reset generation at the exact organizational gate-failure seam."""

    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "integrate")
    ).read()
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
    reset = _quality_repair_contract(contract, expected_commits=expected_commits)
    binding, owning_master = _repair_binding(contract)
    return OrganizationalCompletionRepairEvidence(
        operationKey=record.operationKey,
        candidateState=record.candidateState,
        contractPath=record.contractPath,
        taskId=record.taskId,
        taskName=record.taskName,
        sprintTaskDocument=binding.sprint_ref.key,
        candidateTaskDocument=binding.candidate_ref.key,
        owningMasterTaskDocument=owning_master,
        codeCommit=expected_commits[0],
        memoryContentCommit=expected_commits[1],
        ledgerCommit=expected_commits[2],
        resetContractSha256=_contract_sha256(reset),
    )


def prepare_organizational_completion_repair(
    contract: WorktreeContract,
) -> WorktreeContract:
    """Retire the failed candidate and reopen only its exact leaf closeout."""

    record = _durable_cancelled_repair_record(contract)
    authority = _require_operation_identity(contract, record)
    if (
        not isinstance(record.result, dict)
        or record.result.get("state") != "organizational-completion-gate-failed"
    ):
        raise CloseoutQueueError(
            "organizational-completion-operation-identity-mismatch",
            "quality repair requires the exact failed integration operation and contract",
        )
    evidence = record.organizationalRepair
    if evidence is None:
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-missing",
            "quality repair requires its durable exact reset generation",
        )
    _require_repair_evidence(record, evidence)
    key = _required_operation_key(record.operationKey, "integration")
    owner = _operation_owner(key)
    expected_commits = (
        authority.codeCandidateCommit,
        authority.memoryContentCommit,
        authority.ledgerCommit,
    )
    if expected_commits != (
        evidence.codeCommit,
        evidence.memoryContentCommit,
        evidence.ledgerCommit,
    ):
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-mismatch",
            "quality repair evidence does not match its integration authority",
        )
    binding, owning_master = _repair_binding(contract)
    if (
        binding.sprint_ref.key != evidence.sprintTaskDocument
        or binding.candidate_ref.key != evidence.candidateTaskDocument
        or owning_master != evidence.owningMasterTaskDocument
    ):
        raise CloseoutQueueError(
            "organizational-completion-repair-binding-mismatch",
            "quality repair contract no longer names its accepted queue binding",
        )
    if _quality_repair_is_complete(contract, evidence):
        reset = contract
    else:
        if operation_state_fingerprint(contract) != record.candidateState:
            raise CloseoutQueueError(
                "organizational-completion-operation-state-mismatch",
                "quality repair contract no longer matches its accepted operation state",
            )
        reset = _quality_repair_contract(contract, expected_commits=expected_commits)
        if not _quality_repair_is_complete(reset, evidence):
            raise CloseoutQueueError(
                "organizational-completion-repair-evidence-mismatch",
                "quality repair contract does not produce its durable reset generation",
            )
    topology = TaskDocumentTopology(contract.coordination_root)
    graph = _graph_context(topology, binding.sprint_ref)
    initial = _initial_state(binding.sprint_ref, graph.revision, now_iso())
    context = _RepairContext(
        contract,
        reset,
        binding,
        owner,
        topology,
        expected_commits,
        record,
        evidence,
    )

    CloseoutQueueStore(contract.coordination_root, binding.sprint_ref).transact_with_publication(
        initial=initial,
        event=_internal_event(
            "prepare-quality-repair",
            f"organizational-quality-repair:{owner}",
            {
                "candidate": binding.candidate_ref.key,
                "codeCommit": expected_commits[0],
                "memoryContentCommit": expected_commits[1],
                "ledgerCommit": expected_commits[2],
                "repairGeneration": evidence.resetContractSha256,
            },
        ),
        transform=partial(_retire_candidate, context=context),
        publication=partial(_publish_reset, context=context),
    )
    return load_contract(contract.contract_path)


def _durable_cancelled_repair_record(contract: WorktreeContract) -> LifecycleOperationRecord:
    record = LifecycleOperationStore(
        operation_record_path(contract.worktree_group, "integrate")
    ).read()
    if (
        record is None
        or record.status != "cancelled"
        or not record.cancelRequested
        or not record.finishedAt
    ):
        raise CloseoutQueueError(
            "organizational-completion-durable-cancellation-required",
            "quality repair requires its exact durable cancelled integration operation",
        )
    return record


def _publish_reset(*, context: _RepairContext) -> None:
    contract = context.contract
    with integration_authority_lock(contract.coordination_root, contract.repo_name):
        current = load_contract(contract.contract_path)
        if _quality_repair_is_complete(current, context.evidence):
            return
        if current != contract:
            raise CloseoutQueueError(
                "organizational-completion-contract-changed",
                "the failed final-leaf contract changed before repair was prepared",
            )
        _require_sources_unmoved(current)
        write_contract(context.reset.contract_path, context.reset)


def _retire_candidate(
    state: CloseoutQueueState,
    *,
    context: _RepairContext,
) -> CloseoutQueueState:
    live_graph = _graph_context(context.topology, context.binding.sprint_ref)
    candidate = state.candidates.get(context.binding.candidate_ref.key)
    if candidate is None:
        if _quality_repair_is_complete(
            load_contract(context.contract.contract_path), context.evidence
        ):
            return state
        raise CloseoutQueueError(
            "organizational-completion-candidate-missing",
            "the failed final-leaf candidate disappeared before repair",
        )
    if (
        candidate.state != "integration-in-flight"
        or candidate.inFlightOwnerFingerprint != context.owner
    ):
        raise CloseoutQueueError(
            "organizational-completion-owner-mismatch",
            "only the failed final-leaf integration owner may reopen this closeout",
        )
    if (
        Path(candidate.contractPath).resolve() != Path(context.record.contractPath).resolve()
        or candidate.taskDocumentRef.key != context.evidence.candidateTaskDocument
        or candidate.owningMaster.key != context.evidence.owningMasterTaskDocument
    ):
        raise CloseoutQueueError(
            "organizational-completion-candidate-identity-mismatch",
            "the failed final-leaf candidate no longer matches its accepted operation identity",
        )
    master = live_graph.masters.get(candidate.owningMaster)
    if master is None or master.document.executionNature != "organizational":
        raise CloseoutQueueError(
            "organizational-completion-master-mismatch",
            "quality repair requires the live candidate's organizational master",
        )
    observed = (
        candidate.closeoutCodeCommit,
        candidate.closeoutMemoryContentCommit or "",
        candidate.closeoutLedgerCommit or "",
    )
    if observed != context.expected_commits:
        raise CloseoutQueueError(
            "organizational-completion-candidate-mismatch",
            "the failed final-leaf candidate no longer matches its closed contract",
        )
    candidates = dict(state.candidates)
    candidates.pop(context.binding.candidate_ref.key)
    return state.model_copy(update={"candidates": candidates})


def _require_operation_identity(
    contract: WorktreeContract,
    record: LifecycleOperationRecord,
) -> IntegrationOperationAuthority:
    authority = record.integrationAuthority
    input_contract_path = Path(record.input.contractPath)
    actual_path = contract.contract_path.resolve()
    identity_mismatch = any(
        (
            record.operationKind != "integrate",
            authority is None,
            Path(record.contractPath).resolve() != actual_path,
            input_contract_path.resolve() != actual_path,
            record.taskId != contract.task_id,
            record.taskName != contract.task_name,
        )
    )
    if identity_mismatch:
        raise CloseoutQueueError(
            "organizational-completion-operation-identity-mismatch",
            "quality repair requires the exact failed integration operation and contract",
        )
    assert authority is not None
    targets = {target.side: target for target in integration_targets(contract)}
    code = targets["code"]
    code_mismatch = any(
        (
            code.kind != authority.targetKind,
            code.repository.resolve() != Path(authority.codeRepository).resolve(),
            code.branch != authority.codeSourceBranch,
            authority.codeSourceRef != f"refs/heads/{code.branch}",
            authority.codeSourceCommit != contract.code_base_commit,
        )
    )
    if code_mismatch:
        raise CloseoutQueueError(
            "organizational-completion-operation-authority-mismatch",
            "quality repair contract no longer matches its code integration authority",
        )
    memory = targets.get("memory")
    if contract.memory_mode == "external":
        if memory is None:
            raise CloseoutQueueError(
                "organizational-completion-operation-authority-mismatch",
                "quality repair contract no longer matches its memory integration authority",
            )
        memory_mismatch = any(
            (
                memory.repository.resolve() != Path(authority.memoryRepository).resolve(),
                memory.branch != authority.memorySourceBranch,
                authority.memorySourceRef != f"refs/heads/{memory.branch}",
                authority.memorySourceCommit != contract.memory_base_commit,
            )
        )
        if memory_mismatch:
            raise CloseoutQueueError(
                "organizational-completion-operation-authority-mismatch",
                "quality repair contract no longer matches its memory integration authority",
            )
    elif any(
        (
            authority.memoryRepository,
            authority.memorySourceBranch,
            authority.memorySourceRef,
            authority.memorySourceCommit,
            authority.memoryContentCommit,
            authority.ledgerCommit,
        )
    ):
        raise CloseoutQueueError(
            "organizational-completion-operation-authority-mismatch",
            "code-only quality repair carries unexpected memory integration authority",
        )
    return authority


def _require_repair_evidence(
    record: LifecycleOperationRecord,
    evidence: OrganizationalCompletionRepairEvidence,
) -> None:
    if (
        evidence.operationKey != record.operationKey
        or evidence.candidateState != record.candidateState
        or Path(evidence.contractPath).resolve() != Path(record.contractPath).resolve()
        or evidence.taskId != record.taskId
        or evidence.taskName != record.taskName
    ):
        raise CloseoutQueueError(
            "organizational-completion-repair-evidence-mismatch",
            "quality repair evidence does not match its durable operation identity",
        )


def _repair_binding(contract: WorktreeContract) -> tuple[QueueBinding, str]:
    binding = contract_queue_binding(contract)
    if binding is None:
        raise CloseoutQueueError(
            "organizational-completion-candidate-required",
            "organizational completion repair requires its exact sprint queue candidate",
        )
    master = TaskDocumentTopology(contract.coordination_root).parent(binding.candidate_ref)
    if master is None:
        raise CloseoutQueueError(
            "organizational-completion-master-mismatch",
            "quality repair candidate has no exact owning master",
        )
    return binding, master.key


def _contract_sha256(contract: WorktreeContract) -> str:
    return hashlib.sha256(contract_to_text(contract).encode("utf-8")).hexdigest()


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
) -> WorktreeContract:
    if (
        contract.kind != "leaf"
        or contract.closeout_status != "completed"
        or contract.integration_status != "not-started"
        or not contract.approved_for_commit
        or not contract.code_commit
        or (contract.code_commit, contract.memory_content_commit, contract.ledger_commit)
        != expected_commits
    ):
        raise CloseoutQueueError(
            "organizational-completion-contract-mismatch",
            "only the exact closed, unintegrated final leaf can be reopened for quality repair",
        )
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
        ),
        ContractCells(
            human_review_status="pending-review",
            closeout_status="not-started",
            integration_status="not-started",
        ),
    )


def _quality_repair_is_complete(
    contract: WorktreeContract,
    evidence: OrganizationalCompletionRepairEvidence,
) -> bool:
    return _contract_sha256(contract) == evidence.resetContractSha256


def _require_sources_unmoved(contract: WorktreeContract) -> None:
    targets = {target.side: target for target in integration_targets(contract)}
    code = targets["code"]
    if (
        code.kind != "sprint-super"
        or branch_commit(contract.code_repo_path, code.branch) != contract.code_base_commit
    ):
        raise CloseoutQueueError(
            "organizational-completion-source-moved",
            "quality repair refuses because the code super moved after the failed gate",
        )
    if contract.memory_mode != "external":
        return
    memory = targets.get("memory")
    if (
        memory is None
        or contract.memory_repo_path is None
        or branch_commit(contract.memory_repo_path, memory.branch) != contract.memory_base_commit
    ):
        raise CloseoutQueueError(
            "organizational-completion-memory-source-moved",
            "quality repair refuses because the memory super moved after the failed gate",
        )
