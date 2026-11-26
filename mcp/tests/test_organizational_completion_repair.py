"""Focused tests for journal-owned organizational completion repair."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.models.lifecycles.door import (
    CloseoutDoorDisposition,
    CloseoutDoorGeneration,
    DoorAdmissionProvenance,
    DoorDependencyInputs,
    DoorProvenance,
    DoorPublicationEvidence,
    DoorSchedulingProvenance,
    closeout_door_dependencies,
)
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationOperationAuthority,
    LifecycleOperationRecord,
    OrganizationalCompletionRepairEvidence,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.task_intent import TaskIntentIdentity
from agents_remember.worktrees.integration import (
    organizational_completion_repair as repair_module,
)
from agents_remember.worktrees.integration.closeout.door import (
    live_closeout_door,
    publish_door_intent,
    write_published_door,
)
from agents_remember.worktrees.integration.integration_branch_authority import integration_targets
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    operation_key,
    operation_state_fingerprint,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.organizational_completion_repair import (
    OrganizationalRepairPublicationError,
    classify_organizational_completion_repair,
    organizational_completion_repair_evidence,
    prepare_organizational_completion_repair,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    load_contract,
    write_contract,
)
from test_source_lineage import _commit_on, _git, _organizational_fixture


def _digest(seed: bytes) -> str:
    return hashlib.sha256(seed).hexdigest()


def _contract_sha256(contract: WorktreeContract) -> str:
    published = contract_publication_text(contract.contract_path, contract)
    return hashlib.sha256(published.encode("utf-8")).hexdigest()


def _closed_organizational_leaf(root: Path) -> tuple[WorktreeContract, Path]:
    """Create a closed organizational leaf ready for sprint-super integration."""

    fixture = _organizational_fixture(root)
    memory_repo = fixture.leaf_contract.memory_repo_path
    assert memory_repo is not None
    _commit_on(fixture.code_repo, "leaf", "leaf-code.txt")
    _commit_on(memory_repo, "leaf", "leaf-memory.md")
    code_commit = _git(fixture.code_repo, "rev-parse", "leaf")
    memory_commit = _git(memory_repo, "rev-parse", "leaf")
    closed = amend_contract(
        replace(
            fixture.leaf_contract,
            approved_for_commit=True,
            commit_approval_note="approved for organizational closeout",
            code_commit=code_commit,
            memory_content_commit=memory_commit,
            memory_state="committed",
        ),
        ContractCells(
            human_review_status="approved",
            closeout_status="completed",
            integration_status="not-started",
        ),
    )
    write_contract(closed.contract_path, closed)
    return load_contract(closed.contract_path), root / "settings.json"


def _door_generation(
    contract: WorktreeContract,
    *,
    disposition: CloseoutDoorDisposition,
) -> CloseoutDoorGeneration:
    """Build one structurally valid closeout-door generation bound to ``contract``."""

    not_applicable = DoorProvenance(
        state="not-applicable",
        fingerprint=_digest(b"repair-door-provenance"),
    )
    task_intent = TaskIntentIdentity(digest=_digest(b"repair-task-intent"))
    admission = DoorAdmissionProvenance(fingerprint=_digest(b"repair-door-admission"))
    scheduling = DoorSchedulingProvenance(
        priority="normal",
        judgmentId="REPAIR-FIXTURE",
        fingerprint=_digest(b"repair-door-scheduling"),
    )
    code_tree = _git(contract.code_repo_path, "rev-parse", f"{contract.code_commit}^{{tree}}")
    assert contract.memory_repo_path is not None
    memory_tree = _git(
        contract.memory_repo_path,
        "rev-parse",
        f"{contract.memory_content_commit}^{{tree}}",
    )
    topology_fingerprint = _digest(b"repair-door-topology")
    dependencies = closeout_door_dependencies(
        DoorDependencyInputs(
            candidate_tree=code_tree,
            memory_candidate_tree=memory_tree,
            task_topology_fingerprint=topology_fingerprint,
            task_intent=task_intent,
            review=not_applicable,
            memory=not_applicable,
            admission=admission,
            scheduling=scheduling,
            predecessor="",
        )
    )
    claimed = disposition == "claimed"
    return CloseoutDoorGeneration(
        generationId=_digest(b"repair-door-generation"),
        disposition=disposition,
        taskId=contract.task_id,
        taskName=contract.task_name,
        taskDocumentRef=TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
        owningMasterTaskDocumentRef=TaskDocumentRef(repository="repo", path="master/task.json"),
        sprintTaskDocumentRef=TaskDocumentRef(repository="repo", path="sprint/task.json"),
        contractPath=contract.contract_path.as_posix(),
        candidateTree=code_tree,
        memoryCandidateTree=memory_tree,
        codeBaseCommit=contract.code_base_commit,
        memoryBaseCommit=contract.memory_base_commit,
        taskTopologyFingerprint=topology_fingerprint,
        taskIntent=task_intent,
        reviewProvenance=not_applicable,
        memoryProvenance=not_applicable,
        admissionProvenance=admission,
        schedulingProvenance=scheduling,
        dependencies=dependencies,
        declaredBy="test-fixture:organizational-repair",
        declaredAt="2026-09-10T00:00:00+00:00",
        operationKind="closeout" if claimed else None,
        operationFingerprint=_digest(b"repair-closeout-fingerprint") if claimed else "",
        claimedOperationKey=_digest(b"repair-closeout-key") if claimed else "",
    )


def _canonical_failure_result(contract_path: str, generation: int = 1) -> dict[str, object]:
    """Build the canonical gate-failure handoff expected by the durable record schema."""

    cancel_args = {
        "contract_path": contract_path,
        "operation_kind": "integrate",
        "action": "cancel",
        "expected_generation": generation,
    }
    return {
        "ok": False,
        "operation": "worktree_integrate",
        "state": "organizational-completion-gate-failed",
        "developerDecisionRequired": True,
        "safeToReplace": False,
        "superRefsMoved": False,
        "nextTool": "worktree_operation_control",
        "nextArgs": {**cancel_args, "dry_run": True},
        "applyStep": {
            "nextTool": "worktree_operation_control",
            "nextArgs": {**cancel_args, "dry_run": False},
        },
    }


def _integration_record(
    contract: WorktreeContract,
    config_path: Path,
    *,
    cancelled: bool = False,
    evidence: OrganizationalCompletionRepairEvidence | None = None,
) -> LifecycleOperationRecord:
    """Build one sprint-super integration operation record for ``contract``."""

    assert contract.memory_repo_path is not None
    contract_path = contract.contract_path.as_posix()
    fingerprint = _digest(b"repair-integrate-fingerprint")
    op_key = operation_key(contract.contract_path, "integrate", fingerprint)
    targets = {target.side: target for target in integration_targets(contract)}
    authority = IntegrationOperationAuthority(
        targetKind="sprint-super",
        codeRepository=targets["code"].repository.as_posix(),
        codeSourceBranch=contract.code_source_branch,
        codeSourceRef=f"refs/heads/{contract.code_source_branch}",
        codeSourceCommit=contract.code_base_commit,
        codeCandidateCommit=contract.code_commit,
        memoryRepository=targets["memory"].repository.as_posix(),
        memorySourceBranch=contract.memory_source_branch,
        memorySourceRef=f"refs/heads/{contract.memory_source_branch}",
        memorySourceCommit=contract.memory_base_commit,
        memoryContentCommit=contract.memory_content_commit,
    )
    return LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract_path,
        operationKind="integrate",
        candidateState=operation_state_fingerprint(contract),
        fingerprint=fingerprint,
        operationKey=op_key,
        integrationAuthority=authority,
        input=IntegrateOperationInput(
            configPath=config_path.as_posix(),
            contractPath=contract_path,
        ),
        status="cancelled" if cancelled else "running",
        phase="cancelled" if cancelled else "integration-quality",
        generationDisposition="cancelled" if cancelled else "active",
        queuedAt="2026-09-10T00:00:00+00:00",
        finishedAt="2026-09-10T00:05:00+00:00" if cancelled else None,
        cancelRequested=cancelled,
        reportPath=(contract.worktree_group / "reports" / "integrate.log").as_posix(),
        result=_canonical_failure_result(contract_path) if evidence is not None else None,
        organizationalRepair=evidence,
    )


def _expected_reset_contract(contract: WorktreeContract) -> WorktreeContract:
    return amend_contract(
        replace(
            contract,
            approved_for_commit=False,
            commit_approval_note="",
            code_commit="",
            memory_content_commit="",
            integration_strategy="",
            integrated_code_commit="",
            integrated_memory_content_commit="",
            memory_state="",
        ),
        ContractCells(
            human_review_status="pending-review",
            closeout_status="not-started",
            integration_status="not-started",
        ),
    )


def test_organizational_completion_repair_evidence_builds_reset_digest_without_mutating_disk(
    tmp_path: Path,
) -> None:
    """Repair evidence computes the exact reopened contract hash without writing files."""

    contract, config_path = _closed_organizational_leaf(tmp_path)
    claimed = _door_generation(contract, disposition="claimed")
    write_published_door(contract, claimed)
    record = _integration_record(contract, config_path)
    before_bytes = contract.contract_path.read_bytes()

    evidence = organizational_completion_repair_evidence(contract, record)

    assert evidence.operationKey == record.operationKey
    assert evidence.candidateState == record.candidateState
    assert evidence.codeCommit == contract.code_commit
    assert evidence.memoryContentCommit == contract.memory_content_commit
    assert evidence.sprintTaskDocument == "repo/sprint/task.json"
    assert evidence.candidateTaskDocument == "repo/master/leaf-1.json"
    assert evidence.owningMasterTaskDocument == "repo/master/task.json"
    assert evidence.acceptedContractSha256 == _contract_sha256(contract)
    assert evidence.resetContractSha256 == _contract_sha256(_expected_reset_contract(contract))
    assert contract.contract_path.read_bytes() == before_bytes
    assert live_closeout_door(contract) == claimed


def test_prepare_organizational_completion_repair_resets_contract_and_publishes_waiting_door(
    tmp_path: Path,
) -> None:
    """Repair publication reopens the contract and writes the waiting successor door journal."""

    contract, config_path = _closed_organizational_leaf(tmp_path)
    claimed = _door_generation(contract, disposition="claimed")
    write_published_door(contract, claimed)
    base_record = _integration_record(contract, config_path)
    evidence = OrganizationalCompletionRepairEvidence(
        operationKey=base_record.operationKey,
        candidateState=base_record.candidateState,
        contractPath=base_record.contractPath,
        taskId=base_record.taskId,
        taskName=base_record.taskName,
        sprintTaskDocument="repo/sprint/task.json",
        candidateTaskDocument="repo/master/leaf-1.json",
        owningMasterTaskDocument="repo/master/task.json",
        codeCommit=contract.code_commit,
        memoryContentCommit=contract.memory_content_commit,
        acceptedContractSha256=_contract_sha256(contract),
        resetContractSha256=_contract_sha256(_expected_reset_contract(contract)),
    )
    cancelled_record = _integration_record(
        contract,
        config_path,
        cancelled=True,
        evidence=evidence,
    )
    store = located_lifecycle_operation_store(contract, "integrate")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(cancelled_record.model_dump_json(), encoding="utf-8")

    repaired = prepare_organizational_completion_repair(contract)

    assert repaired.human_review_status == "pending-review"
    assert repaired.closeout_status == "not-started"
    assert repaired.integration_status == "not-started"
    assert repaired.approved_for_commit is False
    assert repaired.commit_approval_note == ""
    assert repaired.code_commit == ""
    assert repaired.memory_content_commit == ""
    assert repaired.integration_strategy == ""
    assert repaired.integrated_code_commit == ""
    assert repaired.integrated_memory_content_commit == ""
    assert repaired.memory_state == ""
    assert _contract_sha256(repaired) == evidence.resetContractSha256

    successor = live_closeout_door(repaired)
    assert successor is not None
    assert successor.disposition == "waiting"
    assert successor.predecessorGenerationId == claimed.generationId
    assert successor.declaredBy == "organizational-quality-repair"
    assert successor.declaredAt == cancelled_record.queuedAt
    assert successor.operationKind is None
    assert successor.operationFingerprint == ""
    assert successor.claimedOperationKey == ""

    # A second call converges idempotently on the already-reset contract and door.
    again = prepare_organizational_completion_repair(repaired)
    assert again == repaired
    assert live_closeout_door(again) == successor


def test_organizational_completion_repair_refuses_unclaimed_live_door(tmp_path: Path) -> None:
    """Repair refuses when the live door journal is not in the claimed disposition."""

    contract, config_path = _closed_organizational_leaf(tmp_path)
    write_published_door(contract, _door_generation(contract, disposition="waiting"))
    record = _integration_record(contract, config_path)

    with pytest.raises(CloseoutQueueError) as refused:
        organizational_completion_repair_evidence(contract, record)

    assert refused.value.status == "organizational-completion-door-mismatch"


def test_prepare_organizational_completion_repair_recovers_after_interrupted_door_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interruption after the contract reset write is recovered on retry so the waiting door publishes."""

    contract, config_path = _closed_organizational_leaf(tmp_path)
    claimed = _door_generation(contract, disposition="claimed")
    write_published_door(contract, claimed)
    base_record = _integration_record(contract, config_path)
    evidence = OrganizationalCompletionRepairEvidence(
        operationKey=base_record.operationKey,
        candidateState=base_record.candidateState,
        contractPath=base_record.contractPath,
        taskId=base_record.taskId,
        taskName=base_record.taskName,
        sprintTaskDocument="repo/sprint/task.json",
        candidateTaskDocument="repo/master/leaf-1.json",
        owningMasterTaskDocument="repo/master/task.json",
        codeCommit=contract.code_commit,
        memoryContentCommit=contract.memory_content_commit,
        acceptedContractSha256=_contract_sha256(contract),
        resetContractSha256=_contract_sha256(_expected_reset_contract(contract)),
    )
    cancelled_record = _integration_record(
        contract,
        config_path,
        cancelled=True,
        evidence=evidence,
    )
    store = located_lifecycle_operation_store(contract, "integrate")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(cancelled_record.model_dump_json(), encoding="utf-8")

    calls = 0

    def fail_once(
        contract_path: Path,
        intent: DoorPublicationEvidence,
    ) -> DoorPublicationEvidence:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated ENOSPC during door publication")
        return publish_door_intent(contract_path, intent)

    monkeypatch.setattr(repair_module, "publish_door_intent", fail_once)

    with pytest.raises(OrganizationalRepairPublicationError) as interrupted:
        prepare_organizational_completion_repair(contract)

    assert interrupted.value.status == "organizational-completion-door-publication-interrupted"
    assert interrupted.value.next_action == "cancel"
    assert calls == 1

    # Contract reset landed on disk while the old claimed door is still live.
    reloaded = load_contract(contract.contract_path)
    assert _contract_sha256(reloaded) == evidence.resetContractSha256
    assert live_closeout_door(reloaded) == claimed
    assert (
        classify_organizational_completion_repair(reloaded, cancelled_record).state
        == "reset-door-pending"
    )

    # Retrying on the reloaded contract completes the door publication.
    repaired = prepare_organizational_completion_repair(reloaded)
    assert calls == 2
    assert _contract_sha256(repaired) == evidence.resetContractSha256

    successor = live_closeout_door(repaired)
    assert successor is not None
    assert successor.disposition == "waiting"
    assert successor.predecessorGenerationId == claimed.generationId
    assert successor.declaredBy == "organizational-quality-repair"
    assert successor.declaredAt == cancelled_record.queuedAt
    assert classify_organizational_completion_repair(repaired, cancelled_record).state == "reset"

    # A subsequent retry is now a pure no-op and does not republish the door.
    again = prepare_organizational_completion_repair(repaired)
    assert again == repaired
    assert calls == 2
    assert live_closeout_door(again) == successor
