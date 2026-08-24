"""Wire and durable vocabularies for task-addressed lifecycle operations."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.base import StrictResponseModel
from agents_remember.models.closeout_input import EffectiveCloseoutInput
from agents_remember.models.closeout_projection import TaskDocProjectionEffect
from agents_remember.models.lifecycles.direct_landing import (
    DirectLandingLedgerIntent,
    DirectLandingOperationInput,
)
from agents_remember.models.lifecycles.door import DoorPublicationEvidence
from agents_remember.models.lifecycles.legacy import LegacyCloseoutMigrationProof
from agents_remember.models.lifecycles.mutation_evidence import (
    CloseoutMutationLeg,
    GitMutationEvidence,
)
from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind
from agents_remember.models.lifecycles.policy import GatePolicyRuleSnapshot
from agents_remember.models.lifecycles.termination import (
    LifecycleCancellationEvidence,
    WorkerTerminationEvidence,
)

IntegrateStrategy = Literal["ff-only", "replay"]
LifecycleOperationStatus = Literal[
    "queued",
    "running",
    "input-required",
    "termination-required",
    "completed",
    "failed",
    "cancelled",
]
LifecycleOperationPhase = Literal[
    "queued",
    "preflight",
    "memory-preflight",
    "quality",
    "approval-claim",
    "recovering-after-claim",
    "code-commit",
    "memory-refresh",
    "memory-commit",
    "ledger-commit",
    "integration-replay",
    "integration-quality",
    "source-merge",
    "contract-finalization",
    "door-publication",
    "termination-required",
    "direct-preflight",
    "direct-memory-commit",
    "direct-ledger-commit",
    "direct-terminal-publication",
    "completed",
    "failed",
    "cancelled",
]


class LifecycleOperationRecoveryCommits(BaseModel):
    """Exact irreversible outputs persisted before contract finalization."""

    model_config = ConfigDict(extra="forbid")

    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")


class OrganizationalTaskPublicationIntent(BaseModel):
    """Exact before/intended bytes for one organizational master completion."""

    model_config = ConfigDict(extra="forbid")

    masterTaskDocument: str = Field(min_length=1, max_length=4096)
    sprintTaskDocument: str = Field(min_length=1, max_length=4096)
    candidateTaskDocument: str = Field(min_length=1, max_length=4096)
    completionFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    certificationResultSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completedAt: str = Field(min_length=1, max_length=128)
    acceptedJson: str
    acceptedJsonSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intendedJson: str
    intendedJsonSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acceptedMarkdown: str
    acceptedMarkdownSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    intendedMarkdown: str
    intendedMarkdownSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _bytes_match_hashes(self) -> OrganizationalTaskPublicationIntent:
        for content, expected in (
            (self.acceptedJson, self.acceptedJsonSha256),
            (self.intendedJson, self.intendedJsonSha256),
            (self.acceptedMarkdown, self.acceptedMarkdownSha256),
            (self.intendedMarkdown, self.intendedMarkdownSha256),
        ):
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected:
                raise ValueError("organizational task publication byte digest does not match")
        return self


class IntegrationPublicationIntent(BaseModel):
    """Journal authority transferred from one claimed door and source operation."""

    model_config = ConfigDict(extra="forbid")

    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: int = Field(ge=1)
    preparedAt: str = Field(min_length=1, max_length=128)
    claimState: Literal["not-applicable", "intent", "proven"]
    claimTransferredAt: str | None = Field(default=None, min_length=1, max_length=128)
    sprintTaskDocument: str = Field(default="", max_length=4096)
    candidateTaskDocument: str = Field(default="", max_length=4096)
    doorGenerationId: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    sourceOperationKind: LifecycleOperationKind | None = None
    sourceOperationGeneration: int | None = Field(default=None, ge=1)
    sourceOperationFingerprint: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    sourceOperationKey: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    sourceJournalSha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    organizationalCompletion: OrganizationalTaskPublicationIntent | None = None

    @model_validator(mode="after")
    def _source_identity_is_complete(self) -> IntegrationPublicationIntent:
        cells = (
            self.sprintTaskDocument,
            self.candidateTaskDocument,
            self.doorGenerationId,
            self.sourceOperationKind,
            self.sourceOperationGeneration,
            self.sourceOperationFingerprint,
            self.sourceOperationKey,
            self.sourceJournalSha256,
        )
        if any(cells) != all(cells):
            raise ValueError("integration publication source claim identity is partial")
        if bool(self.doorGenerationId) != (self.claimState != "not-applicable"):
            raise ValueError("integration publication claim state contradicts source identity")
        if (self.claimState == "proven") != (self.claimTransferredAt is not None):
            raise ValueError("proven integration claim transfer requires its timestamp")
        return self


class IntegrationConflictTransaction(BaseModel):
    """A durable, non-mutating handoff back to the exact leaf worktree."""

    model_config = ConfigDict(extra="forbid")

    state: Literal["resolution-required"] = "resolution-required"
    codeReplayRequired: bool
    memoryReplayRequired: bool
    codeSourceRef: str = Field(pattern=r"^refs/heads/.+$", max_length=4096)
    codeSourceCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeCandidateCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memorySourceRef: str = Field(default="", pattern=r"^$|^refs/heads/.+$", max_length=4096)
    memorySourceCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    codeWorktree: str = Field(min_length=1, max_length=4096)
    memoryWorktree: str = Field(default="", max_length=4096)
    resolutionOwner: Literal["leaf-closeout"] = "leaf-closeout"


class IntegrationOperationAuthority(BaseModel):
    """Exact source tips and closed candidate accepted by one integration operation."""

    model_config = ConfigDict(extra="forbid")

    targetKind: Literal["sprint-super", "atomic-integration"]
    codeRepository: str = Field(min_length=1, max_length=4096)
    codeSourceBranch: str = Field(min_length=1, max_length=4096)
    codeSourceRef: str = Field(pattern=r"^refs/heads/.+$", max_length=4096)
    codeSourceCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    codeCandidateCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryRepository: str = Field(default="", max_length=4096)
    memorySourceBranch: str = Field(default="", max_length=4096)
    memorySourceRef: str = Field(default="", pattern=r"^$|^refs/heads/.+$", max_length=4096)
    memorySourceCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    conflictTransaction: IntegrationConflictTransaction | None = None


class OrganizationalCompletionRepairEvidence(BaseModel):
    """Immutable identity of the one reset generation authorized by cancellation."""

    model_config = ConfigDict(extra="forbid")

    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    contractPath: str = Field(min_length=1, max_length=4096)
    taskId: str = Field(min_length=1, max_length=4096)
    taskName: str = Field(min_length=1, max_length=4096)
    sprintTaskDocument: str = Field(min_length=1, max_length=4096)
    candidateTaskDocument: str = Field(min_length=1, max_length=4096)
    owningMasterTaskDocument: str = Field(min_length=1, max_length=4096)
    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    memoryContentCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    ledgerCommit: str = Field(default="", pattern=r"^$|^[0-9a-f]{40,64}$")
    acceptedContractSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resetContractSha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class IntegrationQualityCertification(BaseModel):
    """Durable proof that one exact organizational completion candidate passed full Dagger."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["organizational-master-completion"] = "organizational-master-completion"
    completionFingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    codeCommit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    candidateTree: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    attestation: dict[str, str]
    resultSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: dict[str, Any]

    @model_validator(mode="after")
    def _passed_result_is_exact(self) -> IntegrationQualityCertification:
        payload = json.dumps(self.result, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        _require_quality_certification_attestation(self)
        _require_quality_certification_result(self)
        _require_quality_certification_memory(self)
        if self.resultSha256 != digest:
            raise ValueError("integration quality certification result digest does not match")
        return self


_QUALITY_ATTESTATION_KEYS = {
    "kind",
    "completionFingerprint",
    "codeCommit",
    "candidateTree",
    "diffBase",
    "mode",
    "executor",
    "memoryCapBytes",
}


def _require_quality_certification_attestation(
    certification: IntegrationQualityCertification,
) -> None:
    attestation = certification.attestation
    if set(attestation) != _QUALITY_ATTESTATION_KEYS:
        raise ValueError("integration quality certification attestation is incomplete")
    if (
        attestation["kind"] != certification.kind
        or attestation["completionFingerprint"] != certification.completionFingerprint
        or attestation["codeCommit"] != certification.codeCommit
        or attestation["candidateTree"] != certification.candidateTree
        or attestation["mode"] != "full"
        or attestation["executor"] != "dagger"
    ):
        raise ValueError("integration quality certification attestation is inconsistent")


def _require_quality_certification_result(
    certification: IntegrationQualityCertification,
) -> None:
    result = certification.result
    if (
        result.get("required") is not True
        or result.get("status") != "enforced"
        or result.get("passed") is not True
        or result.get("mode") != "full"
        or result.get("executor") != "dagger"
        or result.get("diffBase") != certification.attestation["diffBase"]
    ):
        raise ValueError("integration quality certification requires the exact full Dagger gate")


def _require_quality_certification_memory(
    certification: IntegrationQualityCertification,
) -> None:
    cap = certification.attestation["memoryCapBytes"]
    memory_cap = certification.result.get("memoryCap")
    memory_policy = certification.result.get("memoryPolicy")
    if not isinstance(memory_policy, dict):
        raise ValueError("integration quality certification has no exact memory policy")
    if cap:
        if (
            memory_policy.get("mode") != "explicit-cap"
            or not isinstance(memory_cap, dict)
            or str(memory_cap.get("capBytes")) != cap
        ):
            raise ValueError("integration quality certification memory cap does not match")
    elif memory_cap is not None or memory_policy.get("mode") != "container-host-managed":
        raise ValueError("integration quality certification memory policy does not match")


class CloseoutOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["closeout"] = "closeout"
    configPath: str
    contractPath: str
    effectiveInput: EffectiveCloseoutInput
    approvalNote: str
    gatePolicy: list[GatePolicyRuleSnapshot] = Field(default_factory=list)


class IntegrateOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["integrate"] = "integrate"
    configPath: str
    contractPath: str
    strategy: IntegrateStrategy = "ff-only"
    ledgerCommitMessage: str = ""
    gatePolicy: list[GatePolicyRuleSnapshot] = Field(default_factory=list)
    autoCompleteSeats: bool = True


LifecycleOperationInput = Annotated[
    CloseoutOperationInput | IntegrateOperationInput | DirectLandingOperationInput,
    Field(discriminator="kind"),
]


class LifecycleOperationRecord(BaseModel):
    """The validated, internal operation snapshot stored in an enclosure."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["3.0"] = "3.0"
    taskId: str
    taskName: str
    contractPath: str
    operationKind: LifecycleOperationKind
    candidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: int = Field(default=1, ge=1)
    predecessorFingerprint: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    successorFingerprint: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")
    generationDisposition: Literal["active", "cancelled", "retired", "superseded"] = "active"
    supersedeDeclarationFingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    integrationAuthority: IntegrationOperationAuthority | None = None
    input: LifecycleOperationInput
    status: LifecycleOperationStatus
    phase: LifecycleOperationPhase
    queuedAt: str
    startedAt: str | None = None
    heartbeatAt: str | None = None
    finishedAt: str | None = None
    currentCommand: str = ""
    reportPath: str
    result: dict[str, Any] | None = None
    failure: str | None = None
    guidance: str | None = None
    cancelRequested: bool = False
    irreversibleBoundaryEntered: bool = False
    approvalClaimed: bool = False
    mutationEvidence: dict[CloseoutMutationLeg, GitMutationEvidence] = Field(default_factory=dict)
    mutationHistory: dict[CloseoutMutationLeg, list[GitMutationEvidence]] = Field(
        default_factory=dict
    )
    recoveryCommits: LifecycleOperationRecoveryCommits | None = None
    closeoutFinalizedContractSha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    qualityCertification: IntegrationQualityCertification | None = None
    integrationPublication: IntegrationPublicationIntent | None = None
    organizationalRepair: OrganizationalCompletionRepairEvidence | None = None
    doorPublication: DoorPublicationEvidence | None = None
    doorPublicationHistory: list[DoorPublicationEvidence] = Field(
        default_factory=list,
        max_length=256,
    )
    directLandingLedgerIntent: DirectLandingLedgerIntent | None = None
    attempt: int = Field(default=1, ge=1)
    workerPid: int | None = Field(default=None, ge=1)
    workerLease: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    workerProcessFingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    workerTermination: WorkerTerminationEvidence | None = None
    workerTerminationHistory: list[WorkerTerminationEvidence] = Field(default_factory=list)
    terminationReturnStatus: (
        Literal["queued", "running", "input-required", "failed", "completed"] | None
    ) = None
    terminationReturnPhase: LifecycleOperationPhase | None = None
    cancellationEvidence: LifecycleCancellationEvidence | None = None
    legacyMigration: LegacyCloseoutMigrationProof | None = None

    @model_validator(mode="after")
    def _require_altitude_authority(self) -> LifecycleOperationRecord:
        _require_altitude_authority(self)
        return self


def _require_altitude_authority(record: LifecycleOperationRecord) -> None:
    if record.operationKind != record.input.kind:
        raise ValueError("lifecycle operation kind must equal its accepted input kind")
    if record.operationKind != "direct-landing" and record.directLandingLedgerIntent is not None:
        raise ValueError("direct landing ledger intent belongs only to direct landing")
    if record.operationKind not in {"closeout", "direct-landing"} and (
        record.doorPublication is not None or record.doorPublicationHistory
    ):
        raise ValueError("door publication belongs only to schedulable commit operations")
    if record.operationKind == "integrate" and record.integrationAuthority is None:
        raise ValueError("integrate operation requires exact integrationAuthority")
    if record.operationKind != "integrate" and record.integrationAuthority is not None:
        raise ValueError("only integrate operations may carry integrationAuthority")
    if record.operationKind != "closeout" and record.closeoutFinalizedContractSha256 is not None:
        raise ValueError("closeout finalized contract SHA-256 belongs to closeout operations only")
    if (
        record.operationKind != "integrate"
        and isinstance(record.result, dict)
        and record.result.get("state") == "organizational-completion-gate-failed"
    ):
        raise ValueError("organizational completion quality failure belongs to integration only")
    _require_organizational_repair_evidence(record)
    _require_integration_publication(record)
    if record.operationKind in {"closeout", "direct-landing"}:
        _require_closeout_mutation_evidence(record)
    elif record.mutationEvidence:
        raise ValueError("integration operation cannot carry closeout mutation evidence")
    _require_worker_authority(record)
    _require_cancellation_evidence(record)
    _require_legacy_migration(record)


def _require_closeout_mutation_evidence(record: LifecycleOperationRecord) -> None:
    closeout_input = record.input
    if not isinstance(closeout_input, (CloseoutOperationInput, DirectLandingOperationInput)):
        raise ValueError("commit operation requires normalized closeout input")
    expected_legs = {
        leg for leg in ("code", "memory", "ledger") if closeout_input.effectiveInput.enabled(leg)
    }
    if set(record.mutationEvidence) != expected_legs:
        raise ValueError("closeout mutation evidence must match every enabled commit leg")
    if any(leg not in expected_legs for leg in record.mutationHistory):
        raise ValueError("closeout mutation history must match an enabled commit leg")
    for leg, attempts in record.mutationHistory.items():
        if any(item.leg != leg or item.state != "reconciled-unchanged" for item in attempts):
            raise ValueError(
                "closeout mutation history may preserve only reconciled-unchanged attempts"
            )
    commit_proven = any(
        evidence.state == "commit-proven" for evidence in record.mutationEvidence.values()
    )
    irreversible = commit_proven or record.legacyMigration is not None
    if record.irreversibleBoundaryEntered != irreversible:
        raise ValueError(
            "closeout irreversible boundary must be derived from commit proof or legacy output proof"
        )
    if record.recoveryCommits is None:
        return
    recovery_field = {
        "code": "codeCommit",
        "memory": "memoryContentCommit",
        "ledger": "ledgerCommit",
    }
    for leg, evidence in record.mutationEvidence.items():
        recovered = getattr(record.recoveryCommits, recovery_field[leg])
        if evidence.state == "commit-proven" and recovered and recovered != evidence.commit:
            raise ValueError("closeout recovery commit contradicts commit-proven evidence")


def _require_legacy_migration(record: LifecycleOperationRecord) -> None:
    proof = record.legacyMigration
    if proof is None:
        return
    if record.operationKind != "closeout" or not isinstance(record.input, CloseoutOperationInput):
        raise ValueError("legacy migration proof belongs only to a closeout operation")
    if (
        record.operationKey != proof.legacyOperationKey
        or record.fingerprint != proof.legacyFingerprint
        or record.candidateState != proof.legacyCandidateState
        or record.candidateTree != proof.legacyCandidateTree
    ):
        raise ValueError("legacy migration proof must retain the legacy generation identity")
    commits = record.recoveryCommits
    if commits is None or commits.codeCommit != proof.codeCommit:
        raise ValueError("legacy migration recovery code commit must equal its live proof")
    effective = record.input.effectiveInput
    if effective.code.state != "not-applicable" or effective.code.reason != (
        "verified-existing legacy code output"
    ):
        raise ValueError("legacy migration code leg must be typed verified-existing")
    if (
        effective.memory.state != "enabled"
        or effective.ledger.state != "enabled"
        or effective.memory.message != proof.memoryCommitMessage
        or effective.ledger.message != proof.ledgerCommitMessage
        or record.input.approvalNote != proof.legacyApprovalNote
    ):
        raise ValueError("legacy migration must bind both unfinished message cells exactly")
    if record.status == "cancelled" or record.generationDisposition != "active":
        raise ValueError("legacy migration proof cannot be cancelled, retired, or superseded")


def _require_worker_authority(record: LifecycleOperationRecord) -> None:
    if record.operationKind == "direct-landing":
        _require_no_direct_worker_authority(record)
        return
    binding = (record.workerPid, record.workerLease, record.workerProcessFingerprint)
    if any(value is not None for value in binding) and not all(
        value is not None for value in binding
    ):
        raise ValueError("detached worker pid, lease, and process fingerprint are one authority")
    termination = record.workerTermination
    return_status = record.terminationReturnStatus
    return_phase = record.terminationReturnPhase
    if (record.status == "termination-required") != (
        return_status is not None and return_phase is not None
    ):
        raise ValueError(
            "termination-required status must retain the status and phase that requested "
            "termination"
        )
    if (return_status is None) != (return_phase is None):
        raise ValueError("termination return status and phase are one durable identity")
    if termination is None:
        if return_status is not None:
            raise ValueError("termination return status requires durable termination evidence")
        return
    if termination.lease != record.workerLease and termination.state != "exited":
        raise ValueError("worker termination intent must retain the exact worker lease")
    if termination.state != "exited" and termination.pid != record.workerPid:
        raise ValueError("unproven termination must retain the exact worker pid")
    if termination.state == "exited" and any(value is not None for value in binding):
        raise ValueError("proven worker exit must release pid and lease authority")


def _require_no_direct_worker_authority(record: LifecycleOperationRecord) -> None:
    if any(
        value is not None
        for value in (
            record.workerPid,
            record.workerLease,
            record.workerProcessFingerprint,
            record.workerTermination,
            record.terminationReturnStatus,
            record.terminationReturnPhase,
        )
    ):
        raise ValueError("synchronous direct landing cannot carry detached worker authority")


def _require_cancellation_evidence(record: LifecycleOperationRecord) -> None:
    evidence = record.cancellationEvidence
    if evidence is None:
        return
    if (
        evidence.operationKind != record.operationKind
        or evidence.generation != record.generation
        or not evidence.workerExitProven
    ):
        raise ValueError("cancellation evidence must bind this generation and proven worker exit")
    if record.status != "cancelled":
        raise ValueError("cancellation evidence belongs only to a cancelled generation")


def _require_organizational_repair_evidence(record: LifecycleOperationRecord) -> None:
    if record.organizationalRepair is None:
        return
    if record.operationKind != "integrate":
        raise ValueError("organizational completion repair evidence belongs to integration")
    if (
        record.integrationPublication is not None
        or record.recoveryCommits is not None
        or record.irreversibleBoundaryEntered
    ):
        raise ValueError(
            "organizational repair is an exact preclaim mode without publication or output"
        )
    if record.status not in {"queued", "running", "input-required", "cancelled"}:
        raise ValueError("organizational repair evidence has an invalid lifecycle status")
    if (
        record.result is None
        or record.result.get("state") != "organizational-completion-gate-failed"
    ):
        raise ValueError("organizational repair evidence requires its exact failure result")
    _require_canonical_cancellation_handoff(
        record.result,
        record.organizationalRepair.contractPath,
        record.generation,
    )


def _require_integration_publication(record: LifecycleOperationRecord) -> None:
    publication = record.integrationPublication
    if publication is None:
        return
    if record.operationKind != "integrate" or record.integrationAuthority is None:
        raise ValueError("integration publication intent belongs only to integration")
    if (
        publication.operationKey != record.operationKey
        or publication.generation != record.generation
        or record.recoveryCommits is None
    ):
        raise ValueError("integration publication intent does not bind this generation")
    authority = record.integrationAuthority
    commits = record.recoveryCommits
    if (
        commits.codeCommit != authority.codeCandidateCommit
        or commits.memoryContentCommit != authority.memoryContentCommit
        or commits.ledgerCommit != authority.ledgerCommit
    ):
        raise ValueError("integration publication ref intent contradicts accepted authority")
    organizational = publication.organizationalCompletion
    if organizational is None:
        return
    certification = record.qualityCertification
    if certification is None or (
        certification.completionFingerprint != organizational.completionFingerprint
        or certification.resultSha256 != organizational.certificationResultSha256
    ):
        raise ValueError("organizational publication lacks its exact quality certification")


def _require_canonical_cancellation_handoff(
    result: dict[str, Any],
    expected_path: str,
    expected_generation: int,
) -> None:
    next_args = result.get("nextArgs")
    apply_step = result.get("applyStep")
    next_args = next_args if isinstance(next_args, dict) else {}
    apply_step = apply_step if isinstance(apply_step, dict) else {}
    apply_args = apply_step.get("nextArgs")
    apply_args = apply_args if isinstance(apply_args, dict) else {}
    canonical = all(
        (
            result.get("developerDecisionRequired") is True,
            result.get("safeToReplace") is False,
            result.get("superRefsMoved") is False,
            result.get("ok") is False,
            result.get("operation") == "worktree_integrate",
            result.get("nextTool") == "worktree_operation_control",
            next_args.get("contract_path") == expected_path,
            next_args.get("operation_kind") == "integrate",
            next_args.get("action") == "cancel",
            next_args.get("expected_generation") == expected_generation,
            next_args.get("dry_run") is True,
            apply_step.get("nextTool") == "worktree_operation_control",
            apply_args.get("contract_path") == expected_path,
            apply_args.get("operation_kind") == "integrate",
            apply_args.get("action") == "cancel",
            apply_args.get("expected_generation") == expected_generation,
            apply_args.get("dry_run") is False,
        )
    )
    if not canonical:
        raise ValueError(
            "organizational repair evidence requires its canonical cancellation handoff"
        )


class LifecycleOperationProjection(StrictResponseModel):
    """Public task-addressed view; process and resume identities never cross the wire."""

    kind: LifecycleOperationKind
    status: LifecycleOperationStatus | Literal["unreadable"]
    phase: LifecycleOperationPhase
    startedAt: str | None = None
    heartbeatAt: str | None = None
    finishedAt: str | None = None
    elapsedSeconds: float
    currentCommand: str = ""
    reportPath: str
    result: dict[str, Any] | None = None
    failure: str | None = None
    guidance: str | None = None
    cancellable: bool = False
    generation: int | None = None
    legalControls: list[dict[str, Any]] = Field(default_factory=list)
    projectionEffects: list[TaskDocProjectionEffect] = Field(default_factory=list, max_length=8)
