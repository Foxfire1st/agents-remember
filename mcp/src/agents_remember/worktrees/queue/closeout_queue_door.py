"""Exact closeout-door fence for disposable certified queue projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from agents_remember.models.lifecycles.door import CloseoutDoorGeneration
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.queue.closeout_queue import CloseoutCandidateRecord
from agents_remember.worktrees.integration.closeout_recovery_projection import (
    closeout_generation_retained,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_identity import (
    closeout_contract_sha256,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    PublicEvidencePair,
    public_lifecycle_evidence_pair,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

from .closeout_queue_candidate_evidence import operation_owner_fingerprint


@dataclass(frozen=True)
class CloseoutDoorCandidateEvidence:
    """Expected and observed scheduling authority for one certified candidate."""

    valid: bool
    expected: dict[str, object]
    observed: dict[str, object]

    def public_pair(self) -> PublicEvidencePair:
        return public_lifecycle_evidence_pair(self.expected, self.observed)


def owned_candidate_lifecycle_operation(
    candidate: CloseoutCandidateRecord,
) -> LifecycleOperationRecord | None:
    """Resolve the exact journal owner for one in-flight disposable projection."""

    kind = "closeout" if candidate.state == "closeout-in-flight" else "integrate"
    try:
        contract = load_contract(Path(candidate.contractPath))
        record = located_lifecycle_operation_store(contract, kind).read()
    except (ContractError, OSError, RuntimeError, ValidationError):
        return None
    if (
        record is None
        or record.operationKind != kind
        or Path(record.contractPath) != Path(candidate.contractPath)
        or operation_owner_fingerprint(record.operationKey) != candidate.inFlightOwnerFingerprint
    ):
        return None
    return record


def closeout_door_candidate_evidence(
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
) -> CloseoutDoorCandidateEvidence:
    """Classify whether the candidate's completed closeout door remains claimed."""

    record = located_lifecycle_operation_store(contract, "closeout").read()
    door = contract.closeout_door
    journal_door = (
        record.doorPublication.generation
        if record is not None and record.doorPublication is not None
        else None
    )
    expected = _expected_closeout_door_evidence(contract, candidate, record, journal_door)
    observed = _observed_closeout_door_evidence(contract, candidate, record)
    valid = _closeout_door_candidate_is_valid(contract, candidate, record, journal_door, door)
    return CloseoutDoorCandidateEvidence(valid, expected, observed)


def _expected_closeout_door_evidence(
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
    record: LifecycleOperationRecord | None,
    journal_door: CloseoutDoorGeneration | None,
) -> dict[str, object]:
    return {
        "taskDocument": candidate.taskDocumentRef.key,
        "operationKind": "closeout",
        "generation": record.generation if record is not None else 0,
        "disposition": "claimed",
        "contractPath": contract.contract_path.as_posix(),
        "codeCommit": candidate.closeoutCodeCommit or "",
        "memoryContentCommit": candidate.closeoutMemoryContentCommit or "",
        "ledgerCommit": candidate.closeoutLedgerCommit or "",
        "generationId": journal_door.generationId if journal_door is not None else "",
        "operationFingerprint": (
            journal_door.operationFingerprint if journal_door is not None else ""
        ),
        "claimedOperationKey": journal_door.claimedOperationKey if journal_door else "",
    }


def _observed_closeout_door_evidence(
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
    record: LifecycleOperationRecord | None,
) -> dict[str, object]:
    door = contract.closeout_door
    return {
        "taskDocument": candidate.taskDocumentRef.key,
        "operationKind": "closeout",
        "generation": record.generation if record is not None else 0,
        "disposition": door.disposition if door is not None else "missing",
        "generationId": door.generationId if door is not None else "",
        "operationFingerprint": door.operationFingerprint if door is not None else "",
        "claimedOperationKey": door.claimedOperationKey if door is not None else "",
        "operationStatus": record.status if record is not None else "missing",
        "generationDisposition": (
            record.generationDisposition if record is not None else "missing"
        ),
        "contractSha256": closeout_contract_sha256(contract),
    }


def _closeout_door_candidate_is_valid(
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
    record: LifecycleOperationRecord | None,
    journal_door: CloseoutDoorGeneration | None,
    door: CloseoutDoorGeneration | None,
) -> bool:
    return bool(
        _record_certifies_contract(contract, record)
        and _claimed_door_matches_record(contract, record, journal_door, door)
        and _candidate_output_tuple_matches(contract, candidate, record)
    )


def _record_certifies_contract(
    contract: WorktreeContract,
    record: LifecycleOperationRecord | None,
) -> bool:
    """Bind one running-finalization or terminal closeout to exact contract bytes."""

    return bool(
        record is not None
        and record.operationKind == "closeout"
        # Queue certification is the last closeout publication performed by the
        # running worker, immediately before its terminal journal update.  The
        # finalized-contract digest and complete retained recovery proof are the
        # durable authority here; requiring ``completed`` would make the
        # production publication order impossible.
        and _record_is_certifiable(record)
        and closeout_generation_retained(record)
        and record.closeoutFinalizedContractSha256 == closeout_contract_sha256(contract)
        and contract.closeout_status == "completed"
        and contract.integration_status != "completed"
    )


def _claimed_door_matches_record(
    contract: WorktreeContract,
    record: LifecycleOperationRecord | None,
    journal_door: CloseoutDoorGeneration | None,
    door: CloseoutDoorGeneration | None,
) -> bool:
    """Bind the live claimed door to the exact journaled closeout generation."""

    return bool(
        record is not None
        and door is not None
        and door.disposition == "claimed"
        and door.contractPath == contract.contract_path.as_posix()
        and door.operationKind == "closeout"
        and door.operationFingerprint == record.fingerprint
        and door.claimedOperationKey == record.operationKey
        and journal_door == door
    )


def _candidate_output_tuple_matches(
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
    record: LifecycleOperationRecord | None,
) -> bool:
    """Bind queue projection commits to the journal's retained three-leg output."""

    return bool(
        record is not None
        and (candidate.closeoutCodeCommit or "") == contract.code_commit
        and (candidate.closeoutMemoryContentCommit or "") == contract.memory_content_commit
        and (candidate.closeoutLedgerCommit or "") == contract.ledger_commit
        and _recovery_commits_match(record, contract, candidate)
    )


def _record_is_certifiable(record: LifecycleOperationRecord) -> bool:
    """Accept the exact production finalization cut or its proven terminal result."""

    if record.status == "running":
        return record.phase == "contract-finalization"
    return bool(
        record.status == "completed"
        and record.phase == "completed"
        and isinstance(record.result, dict)
        and record.result.get("state") in {"closed", "already-closed"}
    )


def _recovery_commits_match(
    record: LifecycleOperationRecord,
    contract: WorktreeContract,
    candidate: CloseoutCandidateRecord,
) -> bool:
    """Bind queue certification to the journal and contract's one output tuple."""

    recovery = record.recoveryCommits
    return bool(
        recovery is not None
        and recovery.codeCommit == contract.code_commit == (candidate.closeoutCodeCommit or "")
        and recovery.memoryContentCommit
        == contract.memory_content_commit
        == (candidate.closeoutMemoryContentCommit or "")
        and recovery.ledgerCommit
        == contract.ledger_commit
        == (candidate.closeoutLedgerCommit or "")
    )


def candidate_closeout_door_blocker(candidate: CloseoutCandidateRecord) -> str | None:
    """Return a bounded status blocker without mutating the stale queue row."""

    if candidate.state not in {"certified", "integration-in-flight"}:
        return None
    try:
        contract = load_contract(Path(candidate.contractPath))
        evidence = closeout_door_candidate_evidence(contract, candidate)
    except (OSError, RuntimeError, ValueError) as error:
        return f"closeout-door-unreadable:{type(error).__name__}"
    if evidence.valid:
        return None
    return (
        "closeout-door-not-claimed:"
        f"{evidence.observed['disposition']}:"
        f"{evidence.observed['generationId']}"
    )
