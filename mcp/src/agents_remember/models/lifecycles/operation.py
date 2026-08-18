"""Wire and durable vocabularies for task-addressed lifecycle operations."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.base import StrictResponseModel

LifecycleOperationKind = Literal["closeout", "integrate"]
IntegrateStrategy = Literal["ff-only", "replay"]
LifecycleOperationStatus = Literal[
    "queued", "running", "input-required", "completed", "failed", "cancelled"
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


class IntegrationQueueCompletionEvidence(BaseModel):
    """Durable queue-removal intent persisted before consuming a candidate."""

    model_config = ConfigDict(extra="forbid")

    requestId: str = Field(min_length=1, max_length=4096)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


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


class GatePolicyRuleSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    delegatedRole: str | None = None
    requireReviewerVerdict: bool = False


class CloseoutOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["closeout"] = "closeout"
    configPath: str
    contractPath: str
    codeCommitMessage: str
    memoryCommitMessage: str = ""
    ledgerCommitMessage: str = ""
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
    CloseoutOperationInput | IntegrateOperationInput, Field(discriminator="kind")
]


class LifecycleOperationRecord(BaseModel):
    """The validated, internal operation snapshot stored in an enclosure."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["2.0"] = "2.0"
    taskId: str
    taskName: str
    contractPath: str
    operationKind: LifecycleOperationKind
    candidateState: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidateTree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationKey: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    recoveryCommits: LifecycleOperationRecoveryCommits | None = None
    qualityCertification: IntegrationQualityCertification | None = None
    queueCompletion: IntegrationQueueCompletionEvidence | None = None
    organizationalRepair: OrganizationalCompletionRepairEvidence | None = None
    attempt: int = Field(default=1, ge=1)
    workerPid: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_altitude_authority(self) -> LifecycleOperationRecord:
        _require_altitude_authority(self)
        return self


def _require_altitude_authority(record: LifecycleOperationRecord) -> None:
    if record.operationKind == "integrate" and record.integrationAuthority is None:
        raise ValueError("integrate operation requires exact integrationAuthority")
    if record.operationKind == "closeout" and record.integrationAuthority is not None:
        raise ValueError("closeout operation has no integrationAuthority")
    if (
        record.operationKind != "integrate"
        and isinstance(record.result, dict)
        and record.result.get("state") == "organizational-completion-gate-failed"
    ):
        raise ValueError("organizational completion quality failure belongs to integration only")
    if record.organizationalRepair is None:
        return
    if record.operationKind != "integrate":
        raise ValueError("organizational completion repair evidence belongs to integration")
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
    )


def _require_canonical_cancellation_handoff(
    result: dict[str, Any],
    expected_path: str,
) -> None:
    next_args = result.get("nextArgs")
    apply_step = result.get("applyStep")
    next_args = next_args if isinstance(next_args, dict) else {}
    apply_step = apply_step if isinstance(apply_step, dict) else {}
    apply_args = apply_step.get("nextArgs")
    apply_args = apply_args if isinstance(apply_args, dict) else {}
    canonical = all(
        (
            result.get("developer_decision_required") is True,
            result.get("safeToReplace") is False,
            result.get("superRefsMoved") is False,
            result.get("ok") is False,
            result.get("operation") == "worktree_integrate",
            result.get("nextTool") == "worktree_operation_cancel",
            next_args.get("contract_path") == expected_path,
            next_args.get("operation_kind") == "integrate",
            next_args.get("dry_run") is True,
            apply_step.get("nextTool") == "worktree_operation_cancel",
            apply_args.get("contract_path") == expected_path,
            apply_args.get("operation_kind") == "integrate",
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
    status: LifecycleOperationStatus
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
