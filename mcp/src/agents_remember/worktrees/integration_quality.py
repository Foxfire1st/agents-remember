"""Altitude-aware acceptance for atomic and organizational integration candidates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.models.lifecycles.operation import IntegrationQualityCertification
from agents_remember.worktrees.integration_quality_checkout import (
    integration_quality_checkout,
)
from agents_remember.worktrees.modules.code_quality_gate import (
    GATE_FULL,
    QualityGatePlan,
    QualityGateTarget,
    code_quality_gate_preview,
    recover_strict_code_quality_gate,
    requires_integrated_acceptance,
    requires_strict_code_quality,
    run_strict_code_quality_gate,
)
from agents_remember.worktrees.organizational_completion import OrganizationalCompletionPlan
from agents_remember.worktrees.organizational_completion_integration import (
    preview_organizational_completion,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


class IntegrationQualityFailure(RuntimeError):
    """The exact integration candidate failed its required acceptance."""

    def __init__(self, message: str, *, organizational_completion: bool) -> None:
        self.organizational_completion = organizational_completion
        super().__init__(message)


@dataclass(frozen=True)
class IntegrationQualityOutcome:
    result: dict[str, object]
    certification: IntegrationQualityCertification | None = None


def quality_gate_mode(contract: WorktreeContract) -> str:
    """Return the accepting mode for a branch-owning master integration."""

    if contract.kind == "leaf":
        raise ValueError("leaf integration reuses the exact leaf-closeout acceptance")
    return GATE_FULL


def quality_gate_preview(contract: WorktreeContract) -> dict[str, object]:
    completion = preview_organizational_completion(contract) if contract.kind == "leaf" else None
    if contract.kind == "leaf" and completion is None:
        return _leaf_closeout_certification()
    mode = GATE_FULL
    settings = quality_gate_settings(contract)
    with integration_quality_checkout(contract, commit=contract.code_commit) as checkout:
        preview = code_quality_gate_preview(
            checkout,
            code_would_commit=True,
            diff_base=contract.code_base_commit,
            plan=QualityGatePlan(
                mode=mode,
                memory_cap_bytes=settings.memory_cap_bytes,
                executor=settings.executor,
            ),
            required_when_missing=requires_integrated_acceptance(contract.repo_name),
        )
    if completion is not None:
        preview["scope"] = "organizational-master-completion"
        preview["completionFingerprint"] = completion.fingerprint
        preview["masterTaskDocumentRef"] = completion.master_ref.model_dump(mode="json")
    return preview


def run_integration_quality_gate(
    contract: WorktreeContract,
    *,
    completion: OrganizationalCompletionPlan | None = None,
    certification: IntegrationQualityCertification | None = None,
    certification_sink: Callable[[IntegrationQualityCertification], None] | None = None,
    memory_cap_bytes: int | None = None,
) -> IntegrationQualityOutcome:
    """Run or reuse the one exact full integration gate.

    Ordinary leaf integration consumes its targeted closeout certification. A final
    organizational leaf and an atomic series use a detached checkout of the exact commit.
    Only the organizational result is persisted for crash-safe reuse because its gate and
    logical-master publication occur inside the queue-owned leaf transaction.
    """

    if contract.kind == "leaf" and completion is None:
        return IntegrationQualityOutcome(_leaf_closeout_certification())
    settings = quality_gate_settings(contract)
    cap = settings.memory_cap_bytes if memory_cap_bytes is None else memory_cap_bytes
    plan = QualityGatePlan(
        mode=GATE_FULL,
        memory_cap_bytes=cap,
        executor=settings.executor,
    )
    if completion is not None and certification is not None:
        try:
            _require_matching_certification(contract, completion, certification, plan=plan)
        except RuntimeError as error:
            raise IntegrationQualityFailure(
                str(error),
                organizational_completion=True,
            ) from error
        return IntegrationQualityOutcome(
            {**certification.result, "reusedCertification": True},
            certification,
        )
    required_when_missing = completion is not None or requires_integrated_acceptance(
        contract.repo_name
    )
    try:
        with integration_quality_checkout(contract, commit=contract.code_commit) as checkout:
            if not requires_strict_code_quality(
                checkout,
                code_would_commit=True,
                required_when_missing=required_when_missing,
            ):
                preview = code_quality_gate_preview(
                    checkout,
                    code_would_commit=True,
                    diff_base=contract.code_base_commit,
                    plan=plan,
                    required_when_missing=required_when_missing,
                )
                return IntegrationQualityOutcome(preview)
            target = QualityGateTarget(
                code_worktree=checkout,
                worktree_group=contract.worktree_group,
            )
            attestation = (
                _quality_attestation(completion, contract, plan) if completion is not None else None
            )
            recovered = (
                recover_strict_code_quality_gate(
                    target,
                    diff_base=contract.code_base_commit,
                    plan=plan,
                    attestation=attestation,
                )
                if attestation is not None
                else None
            )
            gate = recovered or run_strict_code_quality_gate(
                target,
                diff_base=contract.code_base_commit,
                plan=plan,
                invocation="master-integration",
                attestation=attestation,
            )
    except RuntimeError as error:
        raise IntegrationQualityFailure(
            str(error),
            organizational_completion=completion is not None,
        ) from error
    if completion is None:
        return IntegrationQualityOutcome(gate)
    if recovered is not None:
        gate = {**gate, "recoveredPublishedReport": True}
    assert attestation is not None
    certificate = _certification(completion, gate, attestation=attestation)
    if certification_sink is not None:
        certification_sink(certificate)
    return IntegrationQualityOutcome(gate, certificate)


def _leaf_closeout_certification() -> dict[str, object]:
    return {
        "required": False,
        "status": "certified-at-leaf-closeout",
        "command": "",
        "mode": "targeted",
        "reason": (
            "leaf integration lands the exact commit certified once at leaf closeout; "
            "integration does not rerun acceptance"
        ),
    }


def quality_gate_settings(contract: WorktreeContract):
    settings = load_agentic_settings(
        contract.coordination_root,
        repo_root=contract.code_repo_path,
    )
    return settings.quality_gate


def organizational_quality_failure_payload(
    contract: WorktreeContract,
    reason: str,
) -> dict[str, object]:
    """Return the repair handoff for a failed exact final-leaf gate."""

    summary = (
        "The exact proposed organizational-master completion failed its full quality gate. "
        "No sprint-super ref moved. Cancel this pre-boundary integration to reopen the same "
        "leaf, repair it there, then declare and close the leaf again."
    )
    cancel_note = (
        "Cancel the failed organizational completion so its certified candidate is retired "
        "and the same leaf closeout is reset for repair."
    )
    cancel_args = {
        "contract_path": contract.contract_path.as_posix(),
        "operation_kind": "integrate",
        "intent_note": cancel_note,
        "dry_run": False,
    }
    return {
        "state": "organizational-completion-gate-failed",
        "reason": reason,
        "summary": summary,
        "developer_decision_required": True,
        "safeToReplace": False,
        "superRefsMoved": False,
        "nextOperation": "cancel_failed_completion_for_leaf_repair",
        "nextTool": "worktree_operation_cancel",
        "nextArgs": {**cancel_args, "dry_run": True},
        "applyStep": {
            "summary": cancel_note,
            "nextOperation": "cancel_failed_completion_for_leaf_repair",
            "nextTool": "worktree_operation_cancel",
            "nextArgs": cancel_args,
        },
    }


def _certification(
    completion: OrganizationalCompletionPlan,
    result: dict[str, object],
    *,
    attestation: dict[str, str],
) -> IntegrationQualityCertification:
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
    return IntegrationQualityCertification(
        completionFingerprint=completion.fingerprint,
        codeCommit=completion.code_commit,
        candidateTree=completion.code_tree,
        attestation=attestation,
        resultSha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        result=result,
    )


def _quality_attestation(
    completion: OrganizationalCompletionPlan,
    contract: WorktreeContract,
    plan: QualityGatePlan,
) -> dict[str, str]:
    return {
        "kind": "organizational-master-completion",
        "completionFingerprint": completion.fingerprint,
        "codeCommit": completion.code_commit,
        "candidateTree": completion.code_tree,
        "diffBase": contract.code_base_commit,
        "mode": plan.mode,
        "executor": plan.executor,
        "memoryCapBytes": "" if plan.memory_cap_bytes is None else str(plan.memory_cap_bytes),
    }


def _require_matching_certification(
    contract: WorktreeContract,
    completion: OrganizationalCompletionPlan,
    certification: IntegrationQualityCertification,
    *,
    plan: QualityGatePlan,
) -> None:
    expected_attestation = _quality_attestation(completion, contract, plan)
    try:
        validated = IntegrationQualityCertification.model_validate(
            certification.model_dump(mode="json")
        )
    except ValueError as error:
        raise RuntimeError(
            "recorded organizational full-gate certification is not an exact Dagger result"
        ) from error
    if (
        validated.completionFingerprint != completion.fingerprint
        or validated.codeCommit != contract.code_commit
        or validated.candidateTree != completion.code_tree
        or validated.attestation != expected_attestation
    ):
        raise RuntimeError(
            "recorded organizational full-gate certification targets another candidate or "
            "does not match the current Dagger quality plan"
        )
