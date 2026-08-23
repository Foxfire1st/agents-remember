"""Application boundary for the isolated legacy lifecycle bridge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.operation_kinds import LifecycleOperationKind
from agents_remember.worktrees.integration.configured_contract_authority import (
    require_configured_contract_repositories,
)
from agents_remember.worktrees.integration.legacy.legacy_operation_bridge import (
    LegacyOperationCommand,
    legacy_operation_action,
)
from agents_remember.worktrees.integration.legacy.legacy_operation_failures import LegacyBridgeError
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

from .lifecycle_operation_location import (
    LifecycleOperationPublicAddress,
    configured_lifecycle_operation_location,
    location_decision_payload,
    unreadable_operation_refusal,
)


class _LegacyEntryFailure(RuntimeError):
    def __init__(self, stage: str, error: Exception) -> None:
        super().__init__(stage)
        self.stage = stage
        self.error = error


LegacyOperationAction = Literal["inspect", "migrate", "archive"]


@dataclass(frozen=True)
class LegacyOperationRequest:
    """Public application request for the isolated schema-one lifecycle bridge."""

    operation_kind: LifecycleOperationKind
    action: LegacyOperationAction
    expected_digest: str = ""
    memory_commit_message: str | None = None
    ledger_commit_message: str | None = None
    audit_reason: str = ""
    dry_run: bool = False


def worktree_legacy_operation_tool(
    config: McpRuntimeConfig,
    contract_path: str,
    request: LegacyOperationRequest,
) -> dict[str, Any]:
    """Inspect or apply schema-1 only after exact contract authority is revalidated."""

    try:
        confined = require_within_coordination(config, contract_path, "contract_path")
    except (AuthorityError, OSError, RuntimeError, ValueError) as error:
        return _legacy_entry_refusal(
            config,
            None,
            request,
            stage="legacy-contract-confinement",
            error=error,
        )
    try:
        contract = _load_bound_legacy_contract(config, confined)
        result = legacy_operation_action(
            contract,
            request=_legacy_command(request),
            revalidate_contract=lambda: _revalidate_legacy_contract(config, confined),
        )
    except _LegacyEntryFailure as failure:
        return _legacy_entry_refusal(
            config,
            confined,
            request,
            stage=failure.stage,
            error=failure.error,
        )
    except LifecycleOperationLocationError as error:
        return {"operation": "worktree_legacy_operation", **location_decision_payload(error)}
    except LegacyBridgeError as error:
        return {
            "ok": False,
            "operation": "worktree_legacy_operation",
            "state": "refused",
            "status": error.status,
            "detail": error.detail,
            "expected": error.expected,
            "observed": error.observed,
            **_legacy_next_action(confined, request, error),
        }
    return {
        "ok": True,
        "operation": "worktree_legacy_operation",
        "summary": "Legacy lifecycle record was inspected through the bounded bridge.",
        **result,
    }


def _legacy_command(request: LegacyOperationRequest) -> LegacyOperationCommand:
    """Translate the public DTO to the domain's one strict bridge command."""

    return LegacyOperationCommand(
        operation_kind=request.operation_kind,
        action=request.action,
        expected_digest=request.expected_digest,
        memory_commit_message=request.memory_commit_message,
        ledger_commit_message=request.ledger_commit_message,
        audit_reason=request.audit_reason,
        dry_run=request.dry_run,
    )


def _load_bound_legacy_contract(
    config: McpRuntimeConfig,
    contract_path: Path,
) -> WorktreeContract:
    try:
        contract = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        raise _LegacyEntryFailure("legacy-contract-initial-read", error) from error
    try:
        require_configured_contract_repositories(contract, config.config_path.as_posix())
    except (AuthorityError, OSError, RuntimeError, ValueError) as error:
        raise _LegacyEntryFailure("legacy-contract-authority", error) from error
    return contract


def _revalidate_legacy_contract(
    config: McpRuntimeConfig,
    contract_path: Path,
) -> WorktreeContract:
    try:
        contract = load_contract(contract_path)
    except (ContractError, OSError, UnicodeError, ValueError) as error:
        raise _LegacyEntryFailure("legacy-contract-reload", error) from error
    try:
        require_configured_contract_repositories(contract, config.config_path.as_posix())
    except (AuthorityError, OSError, RuntimeError, ValueError) as error:
        raise _LegacyEntryFailure("legacy-contract-reload-authority", error) from error
    return contract


def _legacy_entry_refusal(
    config: McpRuntimeConfig,
    contract_path: Path | None,
    request: LegacyOperationRequest,
    *,
    stage: str,
    error: Exception,
) -> dict[str, Any]:
    """Translate confinement/read/binding/reload failures through one bounded owner."""

    if contract_path is None:
        detail = "the legacy operation contract address is outside configured authority"
        return _legacy_decision(
            status="legacy-contract-address-invalid",
            detail=detail,
            expected={"field": "contract_path", "state": "confined"},
            observed={
                "failure": public_failure_evidence(
                    stage=stage,
                    side="contract",
                    name="configured-contract-address",
                    error_type=type(error).__name__,
                    observed={"state": "invalid"},
                )
            },
        )
    if stage in {"legacy-contract-initial-read", "legacy-contract-reload"}:
        try:
            _confined, location = configured_lifecycle_operation_location(config, contract_path)
        except LifecycleOperationLocationError as location_error:
            if location_error.status != "operation-location-adoption-required":
                return {
                    "operation": "worktree_legacy_operation",
                    **location_decision_payload(location_error),
                }
            detail = (
                "the pre-adoption contract is unreadable; its legacy journal location "
                "cannot be inferred"
            )
            return _legacy_decision(
                status="legacy-contract-unreadable-before-adoption",
                detail=detail,
                expected={
                    "contractPath": contract_path.as_posix(),
                    "locatorState": "explicit-adoption-required",
                },
                observed={
                    "failure": public_failure_evidence(
                        stage=stage,
                        side="contract",
                        name=contract_path.name,
                        error_type=type(error).__name__,
                        observed={"state": "unreadable"},
                    )
                },
            )
        refusal = unreadable_operation_refusal(
            config,
            contract_path,
            LifecycleOperationPublicAddress(
                "worktree_legacy_operation",
                request.operation_kind,
            ),
            error,
        )
        expected = refusal.get("expected")
        if isinstance(expected, dict):
            refusal["expected"] = {
                **expected,
                "route": "locator -> root manifest -> strict journal",
                "locatorId": location.locator.locatorId,
                "publicationRequestId": location.locator.publicationRequestId,
                "bindingFingerprint": location.locator.bindingFingerprint,
            }
        return refusal
    detail = "the legacy operation contract does not match configured repository authority"
    return _legacy_decision(
        status="legacy-contract-authority-invalid",
        detail=detail,
        expected={
            "contractPath": contract_path.as_posix(),
            "state": "configured-repository-binding",
        },
        observed={
            "failure": public_failure_evidence(
                stage=stage,
                side="contract",
                name=contract_path.name,
                error_type=type(error).__name__,
                observed={"state": "mismatch"},
            )
        },
    )


def _legacy_decision(
    *,
    status: str,
    detail: str,
    expected: dict[str, object],
    observed: dict[str, object],
) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": "worktree_legacy_operation",
        "state": "refused",
        "status": status,
        "detail": detail,
        "expected": expected,
        "observed": observed,
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": detail,
    }


def _legacy_next_action(
    contract_path: Path,
    request: LegacyOperationRequest,
    error: LegacyBridgeError,
) -> dict[str, object]:
    if error.next_action == "developer-decision":
        return {
            "nextAction": "developer-decision",
            "developerDecisionRequired": True,
            "decisionSurface": error.detail,
        }
    if error.next_action == "recover":
        return {
            "nextAction": "recover",
            "nextTool": "worktree_operation_control",
            "nextArgs": {
                "contract_path": contract_path.as_posix(),
                "operation_kind": request.operation_kind,
                "action": "recover",
                "expected_generation": error.observed.get("generation", 1),
                "intent_note": "<developer intent>",
                "dry_run": False,
            },
        }
    return {
        "nextAction": error.next_action,
        "nextTool": "worktree_legacy_operation",
        "nextArgs": {
            "contract_path": contract_path.as_posix(),
            "operation_kind": request.operation_kind,
            "action": error.next_action,
            "expected_digest": request.expected_digest or "<digest from inspect>",
            "memory_commit_message": request.memory_commit_message,
            "ledger_commit_message": request.ledger_commit_message,
            "audit_reason": request.audit_reason,
            "dry_run": False,
        },
    }
